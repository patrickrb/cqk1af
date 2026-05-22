"""Scripted mock session that walks the FSM from IDLE through LOGGED.

Used by ``python -m cqk1af demo`` and the integration tests. No audio, no STT,
no network — exercises only the FSM, the mock radio, and the event bus.
"""
from __future__ import annotations

import asyncio
import uuid

from .config import Settings
from .events import (
    CallsignHeard,
    EventBus,
    QSOLogged,
    QSOStarted,
    StateChanged,
)
from .radio.band_plan import BandPlan
from .radio.mock_client import FrequencyActivity, MockRadioClient, MockRadioConfig
from .state.context import HeardCallsign, QSOContext
from .state.machine import StateMachine
from .state.states import Event, State
from .util.logging import get_logger

log = get_logger(__name__)


async def run_demo(settings: Settings, *, with_pileup: bool = True) -> list[StateChanged]:
    """Drive the FSM end-to-end against a scripted mock radio.

    Returns the list of StateChanged events observed, in order.
    """
    bus = EventBus()
    observed: list[StateChanged] = []

    async def collect(ev):
        observed.append(ev)

    await bus.subscribe("state.changed", collect, name="demo.collect")

    band_plan = BandPlan.load(settings.band_plan_path)

    # First candidate frequency on 20m is busy; subsequent ones are clear.
    candidates_20m = band_plan.find_candidate_frequencies(
        "20m", settings.operator.license_class, "USB"
    )
    busy_freq = candidates_20m[0]
    clear_freq = candidates_20m[1]

    radio = MockRadioClient(
        bus,
        MockRadioConfig(
            initial_slice_freq_hz=busy_freq,
            initial_slice_mode="USB",
            activity=[
                FrequencyActivity(
                    low_hz=busy_freq - 2000,
                    high_hz=busy_freq + 2000,
                    dbm=-60.0,
                    is_voice=True,
                    start_s=0.0,
                    end_s=float("inf"),
                ),
            ],
        ),
    )

    fsm = StateMachine(bus)

    # --- Walk the flow ------------------------------------------------
    await radio.connect()
    await fsm.dispatch(Event.CONNECT, reason="radio_connected")

    # Operator arms the session and starts.
    fsm.session.armed = True
    await fsm.dispatch(Event.START_SESSION, reason="operator_armed_and_started")

    # SCANNING: pick first candidate
    await radio.tune(0, busy_freq)
    fsm.session.current_freq_hz = busy_freq
    fsm.session.current_mode = "USB"
    fsm.session.current_band = "20m"
    await fsm.dispatch(Event.CANDIDATE_PICKED, reason=f"candidate={busy_freq}")

    # LISTENING: detect signal at busy freq -> busy
    s_dbm = await radio.read_s_meter(0)
    log.info("demo.listen", freq_hz=busy_freq, dbm=s_dbm)
    await fsm.dispatch(Event.LISTEN_DONE_BUSY, reason="signal_detected")

    # Try next candidate (clear)
    await radio.tune(0, clear_freq)
    fsm.session.current_freq_hz = clear_freq
    await fsm.dispatch(Event.CANDIDATE_PICKED, reason=f"candidate={clear_freq}")
    s_dbm = await radio.read_s_meter(0)
    log.info("demo.listen", freq_hz=clear_freq, dbm=s_dbm)
    await fsm.dispatch(Event.LISTEN_DONE_CLEAR, reason="noise_floor")

    # CHECKING_FREQUENCY: three TX attempts of "Is this frequency in use?"
    # In the demo we simulate three clean attempts immediately.
    for attempt in range(1, 4):
        fsm.session.freq_check_attempts = attempt
        log.info("demo.freq_check", attempt=attempt)
    await fsm.dispatch(Event.CHECK_DONE_CLEAR, reason="three_clean_attempts")

    # CALLING_CQ: simulate one CQ that gets a reply
    fsm.session.cq_attempts = 1
    await fsm.dispatch(Event.CQ_SENT, reason="cq_template=default")

    # RECEIVING_REPLY: one or more callsigns heard
    fsm.session.pileup = [
        HeardCallsign(callsign="W1AW", confidence=0.92, snr_dbm=-50.0, raw_text="whiskey one alpha whiskey"),
    ]
    if with_pileup:
        fsm.session.pileup.append(
            HeardCallsign(callsign="K9XYZ", confidence=0.71, snr_dbm=-65.0, raw_text="kilo nine x-ray yankee zulu")
        )
    for h in fsm.session.pileup:
        await bus.publish(
            CallsignHeard(callsign=h.callsign, confidence=h.confidence, snr_dbm=h.snr_dbm, source_text=h.raw_text)
        )
    await fsm.dispatch(Event.REPLY_HEARD, reason=f"{len(fsm.session.pileup)}_callers")

    # HANDLING_PILEUP: pick top-ranked (sort by confidence desc, then SNR desc)
    fsm.session.pileup.sort(key=lambda h: (h.confidence, h.snr_dbm or -200.0), reverse=True)
    picked = fsm.session.pileup[0]
    qso = QSOContext(
        qso_id=uuid.uuid4(),
        callsign=picked.callsign,
        freq_hz=clear_freq,
        mode="USB",
        band="20m",
    )
    fsm.session.current_qso = qso
    await bus.publish(
        QSOStarted(qso_id=qso.qso_id, callsign=qso.callsign, freq_hz=qso.freq_hz, mode=qso.mode)
    )
    await fsm.dispatch(Event.PILEUP_SELECTED, reason=f"picked={picked.callsign}")

    # IN_QSO: exchange reports
    qso.rst_sent = "59"
    qso.rst_rcvd = "57"
    qso.name = "Joe"
    qso.qth = "Newington CT"
    await fsm.dispatch(Event.QSO_COMPLETED, reason="exchange_complete")

    # CONFIRMING_LOG -> LOGGED
    await fsm.dispatch(Event.LOG_CONFIRMED, reason="operator_confirmed")
    fsm.session.qsos_logged += 1
    fsm.session.qsos_completed += 1
    await bus.publish(QSOLogged(qso_id=qso.qso_id, cloudlog_id=None))
    await fsm.dispatch(Event.ACK, reason="back_to_idle")

    # Let bus drain
    await asyncio.sleep(0.05)
    await radio.disconnect()
    await bus.close()

    return observed


def _summarise(observed: list[StateChanged]) -> list[str]:
    return [f"{ev.prev} -> {ev.next} ({ev.reason})" for ev in observed]


async def demo_main(settings: Settings) -> int:
    observed = await run_demo(settings)
    log.info("demo.complete", transitions=len(observed))
    for line in _summarise(observed):
        log.info("demo.transition", line=line)
    return 0
