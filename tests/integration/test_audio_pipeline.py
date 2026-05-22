"""AudioPipeline behaviour: graceful degradation, PTT timing, narration fallback."""
from __future__ import annotations

import asyncio

import pytest

from cqk1af.app import App
from cqk1af.audio.pipeline import AudioPipeline
from cqk1af.config import Settings
from cqk1af.events import EventBus, PttChanged, TranscriptFinal
from cqk1af.mcp_server.tools import MCPTools
from cqk1af.radio.mock_client import MockRadioClient, MockRadioConfig


@pytest.fixture
async def app_fixture(tmp_path):
    settings = Settings()
    settings.safety.require_manual_tx_approval = False
    settings.data_dir = tmp_path
    app = App.build(settings)
    await app.start_background_tasks()
    yield app
    await app.shutdown()


@pytest.mark.asyncio
async def test_pipeline_disabled_in_mock_mode(app_fixture) -> None:
    """Mock-mode App disables the pipeline so no real audio devices are touched."""
    app = app_fixture
    tools = MCPTools(app)
    await tools.connect_radio()
    await tools.app.interlock.arm(True)
    # Pipeline should report disabled
    assert app.pipeline.can_tx is False
    assert app.pipeline.can_rx is False
    assert app.pipeline.status.rx_capture == "disabled_mock_mode"


@pytest.mark.asyncio
async def test_narration_only_when_pipeline_cant_tx(app_fixture) -> None:
    """In mock mode the tools should narrate planned TX, not key PTT."""
    app = app_fixture
    tools = MCPTools(app)
    await tools.connect_radio()
    await app.interlock.arm(True)
    await tools.tune_slice(14_270_000, "USB")

    seen_ptt: list[bool] = []
    seen_tx: list[str] = []

    async def collect_ptt(ev):
        if isinstance(ev, PttChanged):
            seen_ptt.append(ev.on)

    async def collect_tx(ev):
        if isinstance(ev, TranscriptFinal) and ev.direction == "tx":
            seen_tx.append(ev.text)

    await app.bus.subscribe("radio.ptt", collect_ptt, name="t.ptt")
    await app.bus.subscribe("transcript.final", collect_tx, name="t.tx")

    await tools.check_frequency_in_use(14_270_000, attempts=3)
    await asyncio.sleep(0.05)
    # Three TX narrations should have been published
    assert len([t for t in seen_tx if "freq_check" in t]) == 3
    # PTT must NOT have been keyed (no audio to send)
    assert seen_ptt == []


@pytest.mark.asyncio
async def test_pipeline_status_probes_devices() -> None:
    """Pipeline reports useful status when enabled.

    Skipped automatically when sounddevice/PortAudio can't initialise on this
    host. (sounddevice 0.5.x had a Windows PortAudio binary that crashed with
    STATUS_HEAP_CORRUPTION against DAX drivers; pinned to 0.4.x to avoid it.)
    """
    try:
        import sounddevice as sd  # noqa: F401

        sd.query_devices()
    except Exception as e:
        pytest.skip(f"sounddevice init failed in this environment: {e}")

    bus = EventBus()
    settings = Settings()
    pipeline = AudioPipeline(bus, settings)
    pipeline.status.enabled = True
    await pipeline.start()
    assert pipeline.status.rx_capture != "ok" or pipeline.status.rx_device
    assert pipeline.status.tx_playback != "ok" or pipeline.status.tx_device
    await pipeline.stop()
    await bus.close()


@pytest.mark.asyncio
async def test_session_state_includes_audio_status(app_fixture) -> None:
    app = app_fixture
    tools = MCPTools(app)
    await tools.connect_radio()
    s = await tools.get_session_state()
    assert s.audio is not None
    assert s.audio.can_rx is False  # mock mode
    assert s.audio.can_tx is False
    assert "disabled" in s.audio.rx_capture.lower()


@pytest.mark.asyncio
async def test_speak_uses_pipeline_when_available(tmp_path) -> None:
    """When pipeline.can_tx is True, _narrate_tx keys PTT, calls speak(), releases."""
    from cqk1af.tts.piper_engine import MockTTSEngine

    settings = Settings()
    settings.safety.require_manual_tx_approval = False
    settings.data_dir = tmp_path
    app = App.build(settings)
    await app.start_background_tasks()
    try:
        tools = MCPTools(app)
        await tools.connect_radio()
        await app.interlock.arm(True)

        # Force the pipeline into a "can_tx=true" state by claiming the Piper
        # engine and marking devices as present, but route synthesis through
        # the MockTTSEngine so no real Piper binary is needed. We do NOT
        # actually open a sounddevice stream.
        app.pipeline.status.enabled = True
        app.pipeline.status.tts_engine = "piper"
        app.pipeline.status.tx_playback = "ok"
        app.pipeline.status.tx_device = "FAKE TX (test)"
        app.pipeline.tts = MockTTSEngine(silence_ms=10)
        app.pipeline._tx_device_index = 0

        # Patch _play_pcm so we don't actually call sounddevice.
        async def fake_play(_result):
            return 0.01

        app.pipeline._play_pcm = fake_play  # type: ignore[assignment]

        seen_ptt: list[bool] = []

        async def collect(ev):
            if isinstance(ev, PttChanged):
                seen_ptt.append(ev.on)

        await app.bus.subscribe("radio.ptt", collect, name="t")

        await tools.tune_slice(14_270_000, "USB")
        await tools.check_frequency_in_use(14_270_000, attempts=1)
        await asyncio.sleep(0.05)
        # Expect at least one PTT on/off pair
        assert seen_ptt[:2] == [True, False]
    finally:
        await app.shutdown()
