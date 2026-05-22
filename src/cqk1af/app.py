"""Application orchestrator.

Wires together the event bus, state machine, radio adapter, safety interlock,
MCP server, and dashboard. Built up incrementally across phases.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .events import EventBus
from .radio.band_plan import BandPlan
from .radio.base import RadioClient
from .radio.mock_client import MockRadioClient, MockRadioConfig
from .audio.pipeline import AudioPipeline
from .logging_qso.cloudlog_client import CloudlogClient
from .logging_qso.sqlite_store import SQLiteQSOStore
from .logging_qso.sync import QSOSyncer
from .safety.id_timer import IdTimer
from .safety.interlock import SafetyInterlock
from .state.context import HeardCallsign
from .state.machine import StateMachine
from .state.states import State
from .util.logging import get_logger

log = get_logger(__name__)


@dataclass
class App:
    settings: Settings
    bus: EventBus
    band_plan: BandPlan
    fsm: StateMachine
    radio: RadioClient
    interlock: SafetyInterlock
    id_timer: IdTimer
    qso_store: SQLiteQSOStore
    cloudlog: CloudlogClient
    syncer: QSOSyncer
    pipeline: AudioPipeline
    _callsign_sub: object = None  # type: ignore[assignment]

    @classmethod
    def build(cls, settings: Settings, *, radio: RadioClient | None = None) -> "App":
        bus = EventBus()
        band_plan = BandPlan.load(settings.band_plan_path)
        if radio is None:
            # mock_mode wins for back-compat; otherwise honor radio.backend.
            backend = "mock" if settings.radio.mock_mode else settings.radio.backend
            if backend == "mock":
                radio = MockRadioClient(bus, MockRadioConfig())
            elif backend == "flexlib":
                from .radio.flexlib_client import FlexLibRadioClient

                radio = FlexLibRadioClient(
                    bus,
                    settings.radio.flexlib_path,
                    target_serial=settings.radio.target_serial or None,
                )
            else:  # "tcp"
                from .radio.tcp_client import TcpRadioClient, TcpRadioConfig

                host = settings.radio.host
                if host == "auto":
                    raise RuntimeError(
                        "Set radio.host to the radio's IP address (e.g. 192.168.1.50) "
                        "or use auto-discovery (not yet implemented). See "
                        "scripts/probe_discovery.py to find your radio's IP."
                    )
                radio = TcpRadioClient(
                    bus, TcpRadioConfig(host=host, port=settings.radio.port)
                )
        fsm = StateMachine(bus)
        interlock = SafetyInterlock(bus, radio, settings)
        if hasattr(radio, "bind_interlock"):
            radio.bind_interlock(interlock)  # type: ignore[attr-defined]
        id_timer = IdTimer(interval_seconds=settings.safety.id_interval_seconds)
        qso_store = SQLiteQSOStore(settings.data_dir / "qsos.sqlite")
        cloudlog = CloudlogClient(settings.cloudlog)
        syncer = QSOSyncer(qso_store, cloudlog, operator_callsign=settings.operator.callsign)
        pipeline = AudioPipeline(bus, settings, radio=radio)
        app = cls(
            settings=settings,
            bus=bus,
            band_plan=band_plan,
            fsm=fsm,
            radio=radio,
            interlock=interlock,
            id_timer=id_timer,
            qso_store=qso_store,
            cloudlog=cloudlog,
            syncer=syncer,
            pipeline=pipeline,
        )
        return app

    async def start_background_tasks(self) -> None:
        """Wire bus subscriptions that maintain session-level state."""
        from .events import CallsignHeard

        async def on_callsign(ev) -> None:
            if not isinstance(ev, CallsignHeard):
                return
            # Add to pileup queue when we're in a state where it makes sense.
            if self.fsm.state in (State.RECEIVING_REPLY, State.HANDLING_PILEUP, State.CALLING_CQ):
                pileup = self.fsm.session.pileup
                if not any(h.callsign.upper() == ev.callsign.upper() for h in pileup):
                    pileup.append(
                        HeardCallsign(
                            callsign=ev.callsign,
                            confidence=ev.confidence,
                            snr_dbm=ev.snr_dbm,
                            raw_text=ev.source_text,
                        )
                    )

        self._callsign_sub = await self.bus.subscribe(
            "transcript.callsign", on_callsign, name="app.callsign_pileup"
        )
        # Background Cloudlog sync (no-op when Cloudlog is not configured)
        self.syncer.start()
        # Audio capture + STT + TTS pipeline. Graceful: missing deps → degraded.
        # Skip when running against the MockRadioClient (no real audio devices
        # are being driven by mock anyway).
        if self.settings.radio.mock_mode:
            self.pipeline.status.enabled = False
        await self.pipeline.start()
        if self.settings.radio.mock_mode:
            # pipeline.start() stamps "disabled" when status.enabled is False;
            # re-stamp with the mock-mode-specific reason the dashboard expects.
            self.pipeline.status.rx_capture = "disabled_mock_mode"
            self.pipeline.status.tx_playback = "disabled_mock_mode"
            self.pipeline.status.notes.append(
                "Mock radio mode — audio pipeline skipped; TX is narrated to the transcript."
            )
            # Re-publish so the dashboard sees the mock-mode reason rather than
            # the generic "disabled" stamped at start().
            await self.pipeline._publish_status()

    async def shutdown(self) -> None:
        try:
            await self.pipeline.stop()
        except Exception:
            log.exception("app.shutdown.pipeline_stop_failed")
        try:
            await self.syncer.stop()
        except Exception:
            log.exception("app.shutdown.syncer_stop_failed")
        if self._callsign_sub is not None:
            try:
                await self.bus.unsubscribe(self._callsign_sub)
            except Exception:
                pass
        try:
            await self.radio.disconnect()
        except Exception:
            log.exception("app.shutdown.radio_disconnect_failed")
        await self.bus.close()
