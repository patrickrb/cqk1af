"""Live-pipeline integration: STT transcript -> callsign extractor -> pileup -> listen_for_reply.

These tests prove ``MCPTools.listen_for_reply`` wires up correctly with the
real ``Transcriber`` + bus subscription, not just direct pileup injection.
The Transcriber here is built with ``MockSTTEngine`` so we can drive
deterministic transcripts through the same code path Whisper would on live
audio. The ``App`` is built in mock-radio mode but with
``start_background_tasks()`` so its ``transcript.callsign`` subscriber is
active.
"""
from __future__ import annotations

import asyncio

import pytest

from cqk1af.app import App
from cqk1af.audio.vad import Utterance
from cqk1af.config import Settings
from cqk1af.mcp_server.tools import MCPTools
from cqk1af.state.context import HeardCallsign
from cqk1af.state.states import Event, State
from cqk1af.stt.transcriber import Transcriber, TranscriberConfig
from cqk1af.stt.whisper_engine import MockSTTEngine, TranscriptionResult


@pytest.fixture
async def app_fixture(tmp_path):
    settings = Settings()
    settings.safety.require_manual_tx_approval = False
    settings.data_dir = tmp_path
    app = App.build(settings)
    await app.start_background_tasks()
    yield app
    await app.shutdown()


def _make_utt() -> Utterance:
    return Utterance(
        pcm=b"\x00\x00" * 1600,
        sample_rate=16000,
        started_ts="2026-05-22T14:00:00Z",
        ended_ts="2026-05-22T14:00:01Z",
        frame_count=50,
    )


def _make_transcriber(app: App, text: str) -> Transcriber:
    engine = MockSTTEngine(
        lambda pcm, sr: TranscriptionResult(
            text=text, avg_logprob=-0.2, no_speech_prob=0.05, duration_s=1.0
        )
    )
    return Transcriber(
        app.bus,
        engine,
        cfg=TranscriberConfig(
            own_callsign=app.settings.operator.callsign,
            extract_callsigns=True,
            min_callsign_confidence=0.5,
        ),
    )


async def _drive_to_receiving_reply(app: App) -> None:
    """Walk the FSM straight to RECEIVING_REPLY without going through tools.

    listen_for_reply doesn't care about radio connection or arming, only the
    FSM state, so we skip the interlock/tune setup that the tool wrappers
    would do.
    """
    await app.fsm.dispatch(Event.CONNECT)
    await app.fsm.dispatch(Event.START_SESSION)
    await app.fsm.dispatch(Event.CANDIDATE_PICKED)
    await app.fsm.dispatch(Event.LISTEN_DONE_CLEAR)
    await app.fsm.dispatch(Event.CHECK_DONE_CLEAR)
    await app.fsm.dispatch(Event.CQ_SENT)
    assert app.fsm.state is State.RECEIVING_REPLY


async def _wait_for_pileup(app: App, *, min_count: int = 1, timeout_s: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if len(app.fsm.session.pileup) >= min_count:
            return
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_pileup_populated_from_live_transcript(app_fixture):
    """Transcriber -> CallsignHeard -> App subscriber -> pileup -> listen_for_reply."""
    app = app_fixture
    await _drive_to_receiving_reply(app)
    transcriber = _make_transcriber(app, "this is W1AW calling")

    await transcriber.transcribe_utterance(_make_utt())
    await _wait_for_pileup(app)

    tools = MCPTools(app)
    heard = await tools.listen_for_reply(timeout_s=1.0, settle_window_s=0.0)
    assert any(h.callsign == "W1AW" for h in heard)
    assert app.fsm.state is State.HANDLING_PILEUP


@pytest.mark.asyncio
async def test_listen_for_reply_waits_for_late_callsign(app_fixture):
    """timeout_s is respected: a callsign that lands mid-wait still gets returned."""
    app = app_fixture
    await _drive_to_receiving_reply(app)
    transcriber = _make_transcriber(app, "K9XYZ calling K1AF")

    async def fire_after_delay() -> None:
        await asyncio.sleep(0.2)
        await transcriber.transcribe_utterance(_make_utt())

    tools = MCPTools(app)
    fire_task = asyncio.create_task(fire_after_delay())
    try:
        heard = await tools.listen_for_reply(timeout_s=2.0, settle_window_s=0.0)
    finally:
        await fire_task

    callsigns = {h.callsign for h in heard}
    assert "K9XYZ" in callsigns
    # Own callsign must not appear in the pileup.
    assert "K1AF" not in callsigns


@pytest.mark.asyncio
async def test_listen_for_reply_times_out_when_silent(app_fixture):
    """No transcripts within timeout -> empty list, REPLY_TIMEOUT loops back to CALLING_CQ."""
    app = app_fixture
    await _drive_to_receiving_reply(app)
    tools = MCPTools(app)

    heard = await tools.listen_for_reply(timeout_s=0.3, settle_window_s=0.0)
    assert heard == []
    assert app.fsm.state is State.CALLING_CQ


@pytest.mark.asyncio
async def test_settle_window_collects_simultaneous_callers(app_fixture):
    """After the first caller, the settle window catches a second one before returning."""
    app = app_fixture
    await _drive_to_receiving_reply(app)
    t1 = _make_transcriber(app, "W1AW calling")
    t2 = _make_transcriber(app, "K9XYZ here")

    async def fire_two() -> None:
        await t1.transcribe_utterance(_make_utt())
        await asyncio.sleep(0.1)
        await t2.transcribe_utterance(_make_utt())

    tools = MCPTools(app)
    fire_task = asyncio.create_task(fire_two())
    try:
        heard = await tools.listen_for_reply(timeout_s=2.0, settle_window_s=0.5)
    finally:
        await fire_task

    callsigns = {h.callsign for h in heard}
    assert "W1AW" in callsigns
    assert "K9XYZ" in callsigns


@pytest.mark.asyncio
async def test_call_cq_clears_stale_pileup(app_fixture):
    """A fresh call_cq clears leftover pileup from a prior reply window."""
    app = app_fixture
    tools = MCPTools(app)
    await tools.connect_radio()
    await app.interlock.arm(True)
    await tools.tune_slice(14_270_000, "USB")
    result = await tools.check_frequency_in_use(14_270_000, attempts=1)
    assert result.clear is True
    assert app.fsm.state is State.CALLING_CQ

    # Inject as if a prior receiving-reply window had left a callsign behind.
    app.fsm.session.pileup.append(
        HeardCallsign(callsign="STALE1", confidence=0.9, snr_dbm=-60.0)
    )

    await tools.call_cq()
    assert app.fsm.session.pileup == []
