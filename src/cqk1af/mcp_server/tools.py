"""MCP tool implementations.

Each tool is a thin wrapper that:
  1. Reads/mutates the central state machine.
  2. May perform light radio actions (tune, read S-meter).
  3. NEVER bypasses the safety interlock or band-plan check.

Heavier orchestration (audio, STT, TTS) lives in state-handler coroutines that
subscribe to ``StateChanged`` events. Tools just advance the state machine.

The tools are exposed via FastMCP in ``server.py`` and can also be called
directly in tests via the ``MCPTools`` class.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..app import App
from ..events import (
    CallsignHeard,
    QSOLogged,
    QSOStarted,
    QSOUpdated,
    TranscriptFinal,
)
from ..radio.band_plan import Allowed
from ..state.context import HeardCallsign, QSOContext
from ..tts.phonetic import phonetic_callsign, render_cq_template, render_freq_check
from ..state.machine import IllegalTransition
from ..state.states import Event, State
from ..util.logging import get_logger
from ..util.time import utcnow

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class SliceDTO(BaseModel):
    slice_id: int
    freq_hz: int
    mode: str
    filter_low_hz: int
    filter_high_hz: int


class RadioStatus(BaseModel):
    connected: bool
    model: str = ""
    version: str = ""
    ptt_on: bool = False
    mic_source: str = ""
    slices: list[SliceDTO] = Field(default_factory=list)


class QSOContextDTO(BaseModel):
    qso_id: str
    callsign: str
    freq_hz: int
    mode: str
    band: str
    rst_sent: str
    rst_rcvd: str
    name: str
    qth: str
    grid: str
    comment: str
    start_ts: str
    end_ts: str


class HeardCallsignDTO(BaseModel):
    callsign: str
    confidence: float
    snr_dbm: float | None = None


class AudioStatusDTO(BaseModel):
    enabled: bool
    rx_capture: str
    tx_playback: str
    stt_engine: str
    tts_engine: str
    rx_device: str
    tx_device: str
    piper_bin: str
    can_rx: bool
    can_tx: bool
    notes: list[str]
    frames_seen: int = 0
    voice_frames: int = 0
    voice_utterances: int = 0
    stt_attempts: int = 0
    stt_results: int = 0
    stt_dropped: int = 0
    last_stt_error: str = ""
    rx_rms_dbfs: float = -120.0


class SessionStateDTO(BaseModel):
    state: str
    armed: bool
    require_approval: bool = True
    operator_callsign: str
    license_class: str
    current_slice_id: int
    current_freq_hz: int
    current_mode: str
    current_band: str
    pileup: list[HeardCallsignDTO]
    current_qso: QSOContextDTO | None
    cq_attempts: int
    qsos_completed: int
    qsos_logged: int
    error: str
    audio: AudioStatusDTO | None = None


class CandidateFreqDTO(BaseModel):
    freq_hz: int
    band: str
    mode: str


class CheckResultDTO(BaseModel):
    freq_hz: int
    clear: bool
    attempts: int
    reason: str = ""


class CQResultDTO(BaseModel):
    attempts: int
    replies_heard: int
    last_state: str


# ---------------------------------------------------------------------------
# ToolError — surfaced to the MCP client as a structured failure
# ---------------------------------------------------------------------------


class ToolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class MCPTools:
    """Tools bound to a shared ``App`` instance."""

    def __init__(self, app: App) -> None:
        self.app = app

    # --- READ ----------------------------------------------------------

    async def get_session_state(self) -> SessionStateDTO:
        s = self.app.fsm.session
        op = self.app.settings.operator
        qso = (
            QSOContextDTO.model_validate(s.current_qso.to_dict()) if s.current_qso else None
        )
        audio_status = None
        if self.app.pipeline is not None:
            audio_status = AudioStatusDTO(**self.app.pipeline.status_dict())
        return SessionStateDTO(
            state=str(self.app.fsm.state),
            armed=s.armed,
            require_approval=self.app.settings.safety.require_manual_tx_approval,
            operator_callsign=op.callsign,
            license_class=op.license_class,
            current_slice_id=s.current_slice_id,
            current_freq_hz=s.current_freq_hz,
            current_mode=s.current_mode,
            current_band=s.current_band,
            pileup=[
                HeardCallsignDTO(callsign=h.callsign, confidence=h.confidence, snr_dbm=h.snr_dbm)
                for h in s.pileup
            ],
            current_qso=qso,
            cq_attempts=s.cq_attempts,
            qsos_completed=s.qsos_completed,
            qsos_logged=s.qsos_logged,
            error=s.error,
            audio=audio_status,
        )

    # --- RADIO ---------------------------------------------------------

    async def connect_radio(self, host: str | None = None) -> RadioStatus:
        try:
            await self.app.radio.connect()
            try:
                await self.app.fsm.dispatch(Event.CONNECT, reason="connect_radio")
            except IllegalTransition:
                pass  # already connected / past IDLE
        except Exception as e:
            raise ToolError("radio_connect_failed", str(e)) from e
        return self._radio_status()

    async def disconnect_radio(self) -> RadioStatus:
        try:
            await self.app.radio.disconnect()
            try:
                await self.app.fsm.dispatch(Event.DISCONNECT, reason="disconnect_radio")
            except IllegalTransition:
                pass
        except Exception as e:
            raise ToolError("radio_disconnect_failed", str(e)) from e
        return self._radio_status()

    async def tune_slice(
        self,
        freq_hz: int,
        mode: Literal["USB", "LSB"],
        filter_low_hz: int = 200,
        filter_high_hz: int = 2900,
        slice_id: int = 0,
    ) -> SliceDTO:
        # Band-plan check first
        decision = self.app.band_plan.is_tx_allowed(
            freq_hz, mode, self.app.settings.operator.license_class
        )
        # NOTE: tuning RX is always allowed; the band-plan check is informational here
        # but we keep the band/mode info on the session for downstream TX gating.
        band = self.app.band_plan.band_for_freq(freq_hz) or ""
        try:
            s = await self.app.radio.tune(slice_id, freq_hz)
            await self.app.radio.set_mode(slice_id, mode)
            await self.app.radio.set_filter(slice_id, filter_low_hz, filter_high_hz)
        except Exception as e:
            raise ToolError("tune_failed", str(e)) from e
        sess = self.app.fsm.session
        sess.current_slice_id = slice_id
        sess.current_freq_hz = freq_hz
        sess.current_mode = mode
        sess.current_band = band
        if not isinstance(decision, Allowed):
            # Caller is tuning, but TX would be denied at this freq+mode.
            log.warning(
                "tune.tx_would_be_denied",
                freq_hz=freq_hz,
                mode=mode,
                reason=decision.reason,
            )
        return SliceDTO(
            slice_id=s.slice_id,
            freq_hz=s.freq_hz,
            mode=s.mode,
            filter_low_hz=s.filter_low_hz,
            filter_high_hz=s.filter_high_hz,
        )

    async def scan_for_open_frequency(
        self,
        band: str,
        mode: Literal["USB", "LSB"] = "USB",
    ) -> list[CandidateFreqDTO]:
        cands = self.app.band_plan.find_candidate_frequencies(
            band, self.app.settings.operator.license_class, mode
        )
        if self.app.fsm.state is State.IDLE:
            try:
                await self.app.fsm.dispatch(Event.START_SESSION, reason=f"scan_{band}")
            except IllegalTransition:
                pass
        return [CandidateFreqDTO(freq_hz=f, band=band, mode=mode) for f in cands]

    # --- TX (gated) ----------------------------------------------------

    async def check_frequency_in_use(
        self,
        freq_hz: int,
        attempts: int = 3,
        listen_seconds: float = 5.0,
    ) -> CheckResultDTO:
        """Run the three-attempt courtesy check.

        Each attempt asks the safety interlock for a TX token (this triggers the
        operator approval modal if configured). In Phase 3 we stubbed TTS; that
        stays stubbed until Phase 9 — here we exercise the full gate path so a
        denial still aborts the check.
        """
        self._require_armed()
        # Band-plan check
        decision = self.app.band_plan.is_tx_allowed(
            freq_hz, self.app.fsm.session.current_mode or "USB", self.app.settings.operator.license_class
        )
        if not isinstance(decision, Allowed):
            raise ToolError("tx_denied_by_band_plan", decision.reason)

        # Advance FSM to SCANNING from wherever we are (IDLE, CQ retry, etc.)
        if self.app.fsm.state is State.IDLE:
            await self.app.fsm.dispatch(Event.START_SESSION, reason="check_frequency_in_use")
        elif self.app.fsm.state is State.RECEIVING_REPLY:
            await self.app.fsm.dispatch(Event.REPLY_TIMEOUT, reason="retry_with_freq_check")
            await self.app.fsm.dispatch(Event.CQ_MAX_REACHED, reason="retry_with_freq_check")
        elif self.app.fsm.state is State.CALLING_CQ:
            await self.app.fsm.dispatch(Event.CQ_MAX_REACHED, reason="retry_with_freq_check")
        if self.app.fsm.state is not State.SCANNING:
            raise ToolError(
                "wrong_state",
                f"check_frequency_in_use cannot start from {self.app.fsm.state}",
            )
        await self.app.fsm.dispatch(Event.CANDIDATE_PICKED, reason=f"freq={freq_hz}")

        # Tune + listen on S-meter for noise vs. signal
        await self.app.radio.tune(self.app.fsm.session.current_slice_id, freq_hz)
        s_dbm = await self.app.radio.read_s_meter(self.app.fsm.session.current_slice_id)
        if s_dbm > -90.0:
            await self.app.fsm.dispatch(Event.LISTEN_DONE_BUSY, reason=f"s_dbm={s_dbm:.1f}")
            return CheckResultDTO(freq_hz=freq_hz, clear=False, attempts=0, reason="signal_present")
        await self.app.fsm.dispatch(Event.LISTEN_DONE_CLEAR, reason=f"s_dbm={s_dbm:.1f}")

        # Each attempt now goes through the interlock. If the operator denies any
        # one of them, we abort and return clear=False.
        slice_id = self.app.fsm.session.current_slice_id
        for i in range(1, attempts + 1):
            self.app.fsm.session.freq_check_attempts = i
            token = await self.app.interlock.request_tx(
                summary=f'Transmit "Is this frequency in use?" (attempt {i}/{attempts})',
                slice_id=slice_id,
                purpose=f"freq_check_attempt_{i}",
                payload={"freq_hz": freq_hz, "attempt": i, "of": attempts},
            )
            if token is None:
                # Deny is a normal operator action, not an error. Back out
                # gracefully to SCANNING so the operator can pick another freq.
                await self.app.fsm.dispatch(
                    Event.CHECK_DONE_BUSY, reason=f"tx_denied_on_attempt_{i}"
                )
                return CheckResultDTO(
                    freq_hz=freq_hz, clear=False, attempts=i, reason="tx_denied"
                )
            # Narrate the planned TX to the dashboard transcript so the operator
            # can see what the assistant *would* say. The real Piper → DAX TX
            # path lights up in Phase 9. Until then we deliberately do NOT key
            # PTT — a bare carrier with no audio is bad on real hardware.
            speak_text = render_freq_check(self.app.settings.operator.callsign, attempt=i)
            await self._narrate_tx(speak_text, purpose=f"freq_check_{i}", token=token)
            # Re-check S-meter between attempts; abandon if signal appears
            mid_dbm = await self.app.radio.read_s_meter(slice_id)
            if mid_dbm > -90.0:
                await self.app.fsm.dispatch(
                    Event.CHECK_DONE_BUSY, reason=f"signal_appeared s_dbm={mid_dbm:.1f}"
                )
                return CheckResultDTO(
                    freq_hz=freq_hz,
                    clear=False,
                    attempts=i,
                    reason="signal_appeared_during_check",
                )
        await self.app.fsm.dispatch(Event.CHECK_DONE_CLEAR, reason=f"{attempts}_clean_attempts")
        return CheckResultDTO(freq_hz=freq_hz, clear=True, attempts=attempts)

    async def call_cq(
        self,
        template_id: str | None = None,
        attempts: int = 1,
    ) -> CQResultDTO:
        self._require_armed()
        if self.app.fsm.state is not State.CALLING_CQ:
            raise ToolError(
                "wrong_state",
                f"call_cq requires CALLING_CQ but state is {self.app.fsm.state}",
            )
        slice_id = self.app.fsm.session.current_slice_id
        token = await self.app.interlock.request_tx(
            summary=f"Transmit CQ call (template={template_id or 'default'})",
            slice_id=slice_id,
            purpose="cq",
            payload={
                "template_id": template_id or "default",
                "freq_hz": self.app.fsm.session.current_freq_hz,
            },
        )
        if token is None:
            # Deny is a normal operator action. Step back to SCANNING so the
            # operator can change strategy (different freq, give up, etc.).
            await self.app.fsm.dispatch(Event.CQ_MAX_REACHED, reason="cq_tx_denied")
            return CQResultDTO(
                attempts=self.app.fsm.session.cq_attempts,
                replies_heard=0,
                last_state=str(self.app.fsm.state),
            )
        # Narrate the planned TX to the dashboard transcript. Real Piper → DAX
        # TX lands in Phase 9. No PTT key without audio.
        cq_text = render_cq_template(
            "CQ CQ CQ, this is {callsign_phonetic}, {callsign_phonetic}, calling CQ and standing by.",
            callsign=self.app.settings.operator.callsign,
        )
        await self._narrate_tx(cq_text, purpose="cq", token=token)
        self.app.id_timer.mark_id_sent()
        self.app.fsm.session.cq_attempts += 1
        await self.app.fsm.dispatch(Event.CQ_SENT, reason=f"template={template_id or 'default'}")
        return CQResultDTO(
            attempts=self.app.fsm.session.cq_attempts,
            replies_heard=0,
            last_state=str(self.app.fsm.state),
        )

    async def listen_for_reply(self, timeout_s: float = 7.0) -> list[HeardCallsignDTO]:
        """Phase-3 stub: returns the pileup that the caller (test) has populated.

        Phase 8 wires the STT-driven callsign extractor.
        """
        if self.app.fsm.state is not State.RECEIVING_REPLY:
            raise ToolError(
                "wrong_state",
                f"listen_for_reply requires RECEIVING_REPLY but state is {self.app.fsm.state}",
            )
        pileup = self.app.fsm.session.pileup
        if pileup:
            for h in pileup:
                await self.app.bus.publish(
                    CallsignHeard(callsign=h.callsign, confidence=h.confidence, snr_dbm=h.snr_dbm)
                )
            await self.app.fsm.dispatch(Event.REPLY_HEARD, reason=f"{len(pileup)}_callers")
            return [
                HeardCallsignDTO(callsign=h.callsign, confidence=h.confidence, snr_dbm=h.snr_dbm)
                for h in pileup
            ]
        await self.app.fsm.dispatch(Event.REPLY_TIMEOUT, reason="no_reply")
        return []

    async def select_caller(self, callsign: str) -> SessionStateDTO:
        if self.app.fsm.state is not State.HANDLING_PILEUP:
            raise ToolError(
                "wrong_state",
                f"select_caller requires HANDLING_PILEUP but state is {self.app.fsm.state}",
            )
        sess = self.app.fsm.session
        picked = next((h for h in sess.pileup if h.callsign.upper() == callsign.upper()), None)
        if picked is None:
            raise ToolError("unknown_caller", f"{callsign} not in pileup")
        qso = QSOContext(
            qso_id=uuid.uuid4(),
            callsign=picked.callsign,
            freq_hz=sess.current_freq_hz,
            mode=sess.current_mode,
            band=sess.current_band,
        )
        sess.current_qso = qso
        await self.app.bus.publish(
            QSOStarted(qso_id=qso.qso_id, callsign=qso.callsign, freq_hz=qso.freq_hz, mode=qso.mode)
        )
        await self.app.fsm.dispatch(Event.PILEUP_SELECTED, reason=f"picked={picked.callsign}")
        return await self.get_session_state()

    async def exchange_signal_report(
        self, rst_sent: str, expect_rst: bool = True
    ) -> dict[str, Any]:
        self._require_armed()
        if self.app.fsm.state is not State.IN_QSO:
            raise ToolError("wrong_state", f"requires IN_QSO, got {self.app.fsm.state}")
        qso = self._require_qso()
        token = await self.app.interlock.request_tx(
            summary=f"Send signal report {rst_sent} to {qso.callsign}",
            slice_id=self.app.fsm.session.current_slice_id,
            purpose="signal_report",
            payload={"callsign": qso.callsign, "rst_sent": rst_sent},
        )
        if token is None:
            # Deny stays in IN_QSO — operator can retry, edit, or complete.
            raise ToolError("tx_denied", "Signal report TX denied — try again or complete the QSO")
        speak = (
            f"{phonetic_callsign(qso.callsign)}, this is "
            f"{phonetic_callsign(self.app.settings.operator.callsign)}, "
            f"you are {_say_rst(rst_sent)}. "
            f"My name is {self.app.settings.operator.name or 'no name'}. Go ahead."
        )
        await self._narrate_tx(speak, purpose="signal_report", token=token)
        self.app.id_timer.mark_id_sent()
        qso.rst_sent = rst_sent
        await self.app.bus.publish(QSOUpdated(qso_id=qso.qso_id, fields={"rst_sent": rst_sent}))
        return {"rst_sent": rst_sent, "expect_rst": expect_rst}

    async def update_qso_field(self, field: str, value: str) -> QSOContextDTO:
        qso = self._require_qso()
        if field not in {"callsign", "rst_sent", "rst_rcvd", "name", "qth", "grid", "comment"}:
            raise ToolError("invalid_field", f"Cannot set {field}")
        setattr(qso, field, value)
        await self.app.bus.publish(QSOUpdated(qso_id=qso.qso_id, fields={field: value}))
        return QSOContextDTO.model_validate(qso.to_dict())

    async def complete_qso(self, closing_phrase: str | None = None) -> QSOContextDTO:
        self._require_armed()
        if self.app.fsm.state is not State.IN_QSO:
            raise ToolError("wrong_state", f"requires IN_QSO, got {self.app.fsm.state}")
        qso = self._require_qso()
        token = await self.app.interlock.request_tx(
            summary=f"Send closing transmission to {qso.callsign}",
            slice_id=self.app.fsm.session.current_slice_id,
            purpose="qso_close",
            payload={"callsign": qso.callsign, "closing_phrase": closing_phrase or ""},
        )
        if token is None:
            # Deny stays in IN_QSO — operator can retry the close or edit first.
            raise ToolError("tx_denied", "Closing TX denied — try again or send another report")
        speak = (
            f"Roger {qso.name or qso.callsign}, thanks for the call. "
            f"{closing_phrase or '73'}, this is "
            f"{phonetic_callsign(self.app.settings.operator.callsign)} QRZ?"
        )
        await self._narrate_tx(speak, purpose="qso_close", token=token)
        self.app.id_timer.mark_id_sent()  # Closing always IDs by convention
        qso.end_ts = utcnow().isoformat()
        if closing_phrase:
            qso.comment = (qso.comment + " | " if qso.comment else "") + closing_phrase
        await self.app.fsm.dispatch(Event.QSO_COMPLETED, reason="complete_qso")
        return QSOContextDTO.model_validate(qso.to_dict())

    async def confirm_and_log_qso(
        self, edits: dict[str, Any] | None = None
    ) -> QSOContextDTO:
        if self.app.fsm.state is not State.CONFIRMING_LOG:
            raise ToolError(
                "wrong_state",
                f"requires CONFIRMING_LOG, got {self.app.fsm.state}",
            )
        qso = self._require_qso()
        if edits:
            for k, v in edits.items():
                if k in {"callsign", "rst_sent", "rst_rcvd", "name", "qth", "grid", "comment"}:
                    setattr(qso, k, v)
        # Refuse to log a QSO without a callsign.
        if not qso.callsign:
            raise ToolError("missing_callsign", "QSO has no callsign; refusing to log")
        # Durable write to SQLite first.
        record = await self.app.qso_store.insert(qso)
        await self.app.fsm.dispatch(Event.LOG_CONFIRMED, reason="confirm_and_log_qso")
        self.app.fsm.session.qsos_logged += 1
        self.app.fsm.session.qsos_completed += 1
        await self.app.bus.publish(QSOLogged(qso_id=qso.qso_id, cloudlog_id=None))
        # Kick the background syncer
        self.app.syncer.enqueue()
        result = QSOContextDTO.model_validate(qso.to_dict())
        await self.app.fsm.dispatch(Event.ACK, reason="qso_logged")
        self.app.fsm.session.current_qso = None
        # Surface the cloudlog id (empty until syncer completes)
        _ = record  # record is the stored row; cloudlog_id fills in async
        return result

    # --- SAFETY --------------------------------------------------------

    async def emergency_stop(self, reason: str = "operator requested") -> SessionStateDTO:
        # Synchronous kill path: force PTT off, disarm, invalidate tokens, deny approvals.
        self.app.interlock.kill(source=f"tool:{reason}")
        try:
            await self.app.fsm.dispatch(Event.KILL, reason=reason)
        except IllegalTransition:
            pass
        self.app.fsm.session.armed = False
        return await self.get_session_state()

    # --- read-only auxiliary ------------------------------------------

    async def get_recent_qsos(self, limit: int = 20) -> list[QSOContextDTO]:
        rows = await self.app.qso_store.list_recent(limit=limit)
        out = []
        for r in rows:
            out.append(
                QSOContextDTO(
                    qso_id=r.qso_id,
                    callsign=r.callsign,
                    freq_hz=r.freq_hz,
                    mode=r.mode,
                    band=r.band,
                    rst_sent=r.rst_sent,
                    rst_rcvd=r.rst_rcvd,
                    name=r.name,
                    qth=r.qth,
                    grid=r.grid,
                    comment=r.comment,
                    start_ts=r.start_ts,
                    end_ts=r.end_ts,
                )
            )
        return out

    async def export_adif(self, start: str | None = None, end: str | None = None) -> str:
        from ..logging_qso.adif import serialize

        rows = await self.app.qso_store.list_recent(limit=10_000)
        return serialize(
            rows,
            station_callsign=self.app.settings.operator.callsign,
            my_name=self.app.settings.operator.name,
            my_grid=self.app.settings.operator.grid_square,
        )

    # ---- internal helpers --------------------------------------------

    def _radio_status(self) -> RadioStatus:
        st = self.app.radio.state
        return RadioStatus(
            connected=st.connected,
            model=st.model,
            version=st.version,
            ptt_on=st.ptt_on,
            mic_source=getattr(st, "mic_source", ""),
            slices=[
                SliceDTO(
                    slice_id=s.slice_id,
                    freq_hz=s.freq_hz,
                    mode=s.mode,
                    filter_low_hz=s.filter_low_hz,
                    filter_high_hz=s.filter_high_hz,
                )
                for s in st.slices.values()
            ],
        )

    def _require_armed(self) -> None:
        if not self.app.interlock.armed:
            raise ToolError(
                "session_not_armed",
                "TX-capable tools require the operator to arm the session from the dashboard",
            )
        # Keep session-context flag in sync (dashboard / tests may read either)
        self.app.fsm.session.armed = True

    def _require_qso(self) -> QSOContext:
        qso = self.app.fsm.session.current_qso
        if qso is None:
            raise ToolError("no_open_qso", "No QSO in progress")
        return qso

    async def _narrate_tx(self, text: str, *, purpose: str, token) -> None:  # noqa: ANN001
        """Speak ``text`` on the air if the audio pipeline is wired, otherwise
        narrate it to the transcript.

        Sequence when the pipeline can TX:
            1. Validate the gate token (interlock has already done this).
            2. radio.set_ptt(True, token) — keys the rig; consumes the token.
            3. pipeline.speak(text) — Piper synth → DAX TX (blocks until done).
            4. radio.set_ptt(False) — releases PTT.

        When the pipeline cannot TX (mock mode, Piper not installed, DAX device
        missing), the token is consumed and the text is published to the
        transcript only — no PTT, no audio. This keeps mock-mode behaviour and
        avoids a bare-carrier burst on real hardware.
        """
        pipeline = self.app.pipeline
        await self.app.bus.publish(
            TranscriptFinal(text=f"[{purpose}] {text}", confidence=1.0, direction="tx")
        )
        if pipeline is None or not pipeline.can_tx:
            self.app.interlock.consume_token(token)
            log.info("tx.narrated_only", purpose=purpose, text_len=len(text))
            return
        try:
            await self.app.radio.set_ptt(True, gate_token=token)
            try:
                # Settle delay only needed for the Windows DAX device path
                # (relay + driver bring-up). FlexLib's stream handles its own
                # buffering and the stream is already open before PTT.
                if not self.app.radio.supports_native_tx_audio:
                    await asyncio.sleep(0.3)
                duration = await pipeline.speak(text)
                log.info("tx.spoken", purpose=purpose, duration_s=duration)
            finally:
                await self.app.radio.set_ptt(False)
        except Exception:
            log.exception("tx.speak_failed")
            # On any error, ensure PTT is released.
            try:
                self.app.radio.force_ptt_off()
            except Exception:
                pass
            raise


def _say_rst(rst: str) -> str:
    """Render an RST like ``59`` as 'five nine' for narration."""
    return " ".join(
        {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
         "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "niner"}.get(c, c)
        for c in rst
    )
