"""WebSocket hub.

Subscribes to relevant bus events, transforms them into JSON messages, and
fans them out to connected clients. Inbound messages are dispatched to the
control routes.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ..app import App
from ..events import (
    AudioPipelineStatus,
    CallsignHeard,
    Event,
    KillSwitchTriggered,
    PileupChanged,
    PttChanged,
    QSOLogged,
    QSOStarted,
    QSOUpdated,
    RadioConnected,
    RadioDisconnected,
    SessionArmed,
    SliceUpdated,
    SMeterReading,
    StateChanged,
    TranscriptFinal,
    TxApprovalRequested,
    VoiceActivityStarted,
    VoiceActivityStopped,
)
from ..mcp_server.tools import MCPTools
from ..util.logging import get_logger

log = get_logger(__name__)

# Throttle S-meter to 4 Hz to bound WS bandwidth.
S_METER_INTERVAL_S = 0.25


class WSHub:
    """Singleton that owns connected clients and bus subscriptions."""

    def __init__(self, app: App) -> None:
        self.app = app
        self.tools = MCPTools(app)
        self.clients: set[WebSocket] = set()
        self.session_id = uuid.uuid4().hex
        self._last_s_meter_emit: dict[int, float] = {}
        self._started = False
        self._subs: list = []

    # ---- subscription wiring ---------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        b = self.app.bus
        self._subs.append(await b.subscribe("state.changed", self._on_state_changed, name="ws.state"))
        self._subs.append(await b.subscribe("radio.connected", self._on_radio_connected, name="ws.connected"))
        self._subs.append(await b.subscribe("radio.disconnected", self._on_radio_disconnected, name="ws.disconnected"))
        self._subs.append(await b.subscribe("radio.slice", self._on_radio_slice, name="ws.slice"))
        self._subs.append(await b.subscribe("radio.s_meter", self._on_s_meter, name="ws.s_meter", queue_size=64))
        self._subs.append(await b.subscribe("radio.ptt", self._on_ptt, name="ws.ptt"))
        self._subs.append(await b.subscribe("transcript.final", self._on_transcript_final, name="ws.tr"))
        self._subs.append(await b.subscribe("transcript.callsign", self._on_callsign, name="ws.call"))
        self._subs.append(await b.subscribe("pileup.changed", self._on_pileup_changed, name="ws.pileup"))
        self._subs.append(await b.subscribe("audio.status", self._on_audio_status, name="ws.audio"))
        self._subs.append(
            await b.subscribe("audio.voice_started", self._on_voice_started, name="ws.voice_started")
        )
        self._subs.append(
            await b.subscribe("audio.voice_stopped", self._on_voice_stopped, name="ws.voice_stopped")
        )
        self._subs.append(
            await b.subscribe("control.tx_approval_requested", self._on_tx_approval, name="ws.approve")
        )
        self._subs.append(await b.subscribe("control.kill", self._on_kill, name="ws.kill"))
        self._subs.append(await b.subscribe("control.armed", self._on_armed, name="ws.armed"))
        self._subs.append(await b.subscribe("qso.started", self._on_qso_started, name="ws.qso_started"))
        self._subs.append(await b.subscribe("qso.updated", self._on_qso_updated, name="ws.qso_updated"))
        self._subs.append(await b.subscribe("qso.logged", self._on_qso_logged, name="ws.qso_logged"))

    async def stop(self) -> None:
        for sub in self._subs:
            try:
                await self.app.bus.unsubscribe(sub)
            except Exception:
                pass
        self._subs.clear()
        self._started = False

    # ---- client lifecycle ------------------------------------------

    async def connect_client(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)
        # Send hello with current snapshot, including any in-flight approvals.
        try:
            state = await self.tools.get_session_state()
            radio = self.tools._radio_status()
            pending_approvals = [
                {
                    "req_id": str(r.req_id),
                    "summary": r.summary,
                    "payload": r.payload,
                    "expires_at": r.expires_at.isoformat(),
                }
                for r in self.app.interlock._pending.values()  # noqa: SLF001
                if not r.future.done()
            ]
            await ws.send_json(
                {
                    "type": "hello",
                    "payload": {
                        "session_id": self.session_id,
                        "state": state.model_dump(),
                        "radio": radio.model_dump(),
                        "pending_approvals": pending_approvals,
                    },
                }
            )
        except Exception:
            log.exception("ws.hello_failed")

    async def disconnect_client(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        if not self.clients:
            return
        dead: list[WebSocket] = []
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    # ---- inbound message dispatch ----------------------------------

    async def dispatch_inbound(self, ws: WebSocket, msg: dict[str, Any]) -> None:
        mtype = msg.get("type", "")
        payload = msg.get("payload", {}) or {}
        try:
            if mtype == "kill":
                await self.tools.emergency_stop(reason=payload.get("reason", "ws.kill"))
            elif mtype == "arm_session":
                await self.app.interlock.arm(bool(payload.get("armed", True)), source="ws")
                self.app.fsm.session.armed = self.app.interlock.armed
            elif mtype == "stop_session":
                await self.tools.emergency_stop(reason="ws.stop_session")
            elif mtype == "select_caller":
                await self.tools.select_caller(callsign=str(payload.get("callsign", "")))
            elif mtype == "set_field":
                await self.tools.update_qso_field(
                    field=str(payload.get("field", "")), value=str(payload.get("value", ""))
                )
            elif mtype == "approve_tx":
                import uuid as _uuid

                try:
                    req_id = _uuid.UUID(str(payload.get("req_id", "")))
                except ValueError:
                    await ws.send_json(
                        {"type": "error", "payload": {"message": "invalid req_id", "severity": "warn"}}
                    )
                    return
                ok = await self.app.interlock.resolve_approval(
                    req_id, bool(payload.get("approved", False)), by="operator"
                )
                if not ok:
                    await ws.send_json(
                        {"type": "error", "payload": {"message": "no such approval", "severity": "info"}}
                    )
            else:
                await ws.send_json(
                    {"type": "error", "payload": {"message": f"unknown type {mtype}", "severity": "warn"}}
                )
        except Exception as e:
            log.exception("ws.dispatch_error", mtype=mtype)
            await ws.send_json(
                {"type": "error", "payload": {"message": str(e), "severity": "error"}}
            )

    # ---- bus -> ws handlers ---------------------------------------

    async def _on_state_changed(self, ev: Event) -> None:
        assert isinstance(ev, StateChanged)
        await self._broadcast(
            {
                "type": "state_changed",
                "payload": {
                    "prev": ev.prev,
                    "next": ev.next,
                    "reason": ev.reason,
                    "ts": ev.ts.isoformat(),
                },
            }
        )

    async def _on_radio_connected(self, ev: Event) -> None:
        assert isinstance(ev, RadioConnected)
        await self._broadcast(
            {"type": "radio_slice", "payload": {"connected": True, "model": ev.model}}
        )

    async def _on_radio_disconnected(self, ev: Event) -> None:
        assert isinstance(ev, RadioDisconnected)
        await self._broadcast(
            {"type": "radio_slice", "payload": {"connected": False, "reason": ev.reason}}
        )

    async def _on_radio_slice(self, ev: Event) -> None:
        assert isinstance(ev, SliceUpdated)
        await self._broadcast(
            {
                "type": "radio_slice",
                "payload": {
                    "slice_id": ev.slice_id,
                    "freq_hz": ev.freq_hz,
                    "mode": ev.mode,
                    "filter_low_hz": ev.filter_low_hz,
                    "filter_high_hz": ev.filter_high_hz,
                    "rxant": ev.rxant,
                },
            }
        )

    async def _on_s_meter(self, ev: Event) -> None:
        assert isinstance(ev, SMeterReading)
        now = asyncio.get_event_loop().time()
        last = self._last_s_meter_emit.get(ev.slice_id, 0.0)
        if now - last < S_METER_INTERVAL_S:
            return
        self._last_s_meter_emit[ev.slice_id] = now
        await self._broadcast(
            {"type": "s_meter", "payload": {"slice_id": ev.slice_id, "dbm": ev.dbm}}
        )

    async def _on_ptt(self, ev: Event) -> None:
        assert isinstance(ev, PttChanged)
        await self._broadcast({"type": "radio_slice", "payload": {"ptt_on": ev.on}})

    async def _on_transcript_final(self, ev: Event) -> None:
        assert isinstance(ev, TranscriptFinal)
        await self._broadcast(
            {
                "type": "transcript",
                "payload": {
                    "text": ev.text,
                    "confidence": ev.confidence,
                    "direction": ev.direction,
                    "ts": ev.ts.isoformat(),
                },
            }
        )

    async def _on_callsign(self, ev: Event) -> None:
        assert isinstance(ev, CallsignHeard)
        await self._broadcast(
            {
                "type": "callsigns_heard",
                "payload": {
                    "entries": [
                        {
                            "callsign": ev.callsign,
                            "confidence": ev.confidence,
                            "snr_dbm": ev.snr_dbm,
                        }
                    ]
                },
            }
        )

    async def _on_pileup_changed(self, ev: Event) -> None:
        assert isinstance(ev, PileupChanged)
        await self._broadcast(
            {
                "type": "pileup_sync",
                "payload": {"entries": ev.entries},
            }
        )

    async def _on_tx_approval(self, ev: Event) -> None:
        assert isinstance(ev, TxApprovalRequested)
        from ..util.time import utcnow

        # Send a relative timeout alongside expires_at so the client doesn't
        # depend on the server/client wall clocks agreeing. Clock skew was
        # making the auto-deny setTimeout fire immediately on render.
        timeout_s = max(0.0, (ev.expires_at - utcnow()).total_seconds())
        await self._broadcast(
            {
                "type": "tx_approval_requested",
                "payload": {
                    "req_id": str(ev.req_id),
                    "summary": ev.summary,
                    "payload": ev.payload,
                    "expires_at": ev.expires_at.isoformat(),
                    "timeout_s": timeout_s,
                },
            }
        )

    async def _on_kill(self, ev: Event) -> None:
        assert isinstance(ev, KillSwitchTriggered)
        await self._broadcast(
            {"type": "error", "payload": {"message": f"Kill switch: {ev.source}", "severity": "warn"}}
        )

    async def _on_armed(self, ev: Event) -> None:
        assert isinstance(ev, SessionArmed)
        await self._broadcast({"type": "session_armed", "payload": {"armed": ev.armed}})

    async def _on_qso_started(self, ev: Event) -> None:
        assert isinstance(ev, QSOStarted)
        await self._broadcast(
            {
                "type": "qso_started",
                "payload": {
                    "qso_id": str(ev.qso_id),
                    "callsign": ev.callsign,
                    "freq_hz": ev.freq_hz,
                    "mode": ev.mode,
                },
            }
        )

    async def _on_qso_updated(self, ev: Event) -> None:
        assert isinstance(ev, QSOUpdated)
        await self._broadcast(
            {"type": "qso_updated", "payload": {"qso_id": str(ev.qso_id), "fields": ev.fields}}
        )

    async def _on_qso_logged(self, ev: Event) -> None:
        assert isinstance(ev, QSOLogged)
        await self._broadcast(
            {
                "type": "qso_logged",
                "payload": {"qso_id": str(ev.qso_id), "cloudlog_id": ev.cloudlog_id},
            }
        )

    async def _on_audio_status(self, ev: Event) -> None:
        assert isinstance(ev, AudioPipelineStatus)
        await self._broadcast({"type": "audio_status", "payload": ev.status})

    async def _on_voice_started(self, ev: Event) -> None:
        assert isinstance(ev, VoiceActivityStarted)
        await self._broadcast(
            {"type": "rx_voice", "payload": {"active": True, "channel": ev.channel}}
        )

    async def _on_voice_stopped(self, ev: Event) -> None:
        assert isinstance(ev, VoiceActivityStopped)
        await self._broadcast(
            {
                "type": "rx_voice",
                "payload": {
                    "active": False,
                    "channel": ev.channel,
                    "duration_ms": ev.duration_ms,
                },
            }
        )


async def ws_endpoint(ws: WebSocket, hub: WSHub) -> None:
    await hub.connect_client(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json(
                    {"type": "error", "payload": {"message": "invalid json", "severity": "warn"}}
                )
                continue
            await hub.dispatch_inbound(ws, data)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect_client(ws)
