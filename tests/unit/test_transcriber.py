from __future__ import annotations

import asyncio

import pytest

from cqk1af.audio.vad import Utterance
from cqk1af.events import EventBus, TranscriptFinal
from cqk1af.stt.transcriber import Transcriber, TranscriberConfig
from cqk1af.stt.whisper_engine import MockSTTEngine, TranscriptionResult


@pytest.mark.asyncio
async def test_transcribe_publishes_transcript() -> None:
    bus = EventBus()
    seen: list[TranscriptFinal] = []
    async def collect(ev):
        if isinstance(ev, TranscriptFinal):
            seen.append(ev)
    await bus.subscribe("transcript.final", collect, name="t")

    engine = MockSTTEngine(
        lambda _pcm, _sr: TranscriptionResult(text="kilo one alpha foxtrot", avg_logprob=-0.3, no_speech_prob=0.1)
    )
    tr = Transcriber(bus, engine)
    utt = Utterance(pcm=b"\x00" * 320, sample_rate=16000, started_ts="", ended_ts="", frame_count=1)
    result = await tr.transcribe_utterance(utt)
    assert result is not None
    assert result.text == "kilo one alpha foxtrot"
    await asyncio.sleep(0.02)
    assert seen and seen[0].text == "kilo one alpha foxtrot"
    await bus.close()


@pytest.mark.asyncio
async def test_empty_text_is_skipped() -> None:
    bus = EventBus()
    seen: list = []
    async def collect(ev):
        seen.append(ev)
    await bus.subscribe("transcript.final", collect, name="t")

    engine = MockSTTEngine(lambda _pcm, _sr: TranscriptionResult(text=""))
    tr = Transcriber(bus, engine, cfg=TranscriberConfig(min_text_chars=1))
    utt = Utterance(pcm=b"\x00" * 320, sample_rate=16000, started_ts="", ended_ts="", frame_count=1)
    out = await tr.transcribe_utterance(utt)
    assert out is None
    await asyncio.sleep(0.02)
    assert seen == []
    await bus.close()


@pytest.mark.asyncio
async def test_confidence_derivation() -> None:
    # High avg_logprob, low no_speech_prob -> high confidence
    r_high = TranscriptionResult(text="ok", avg_logprob=-0.1, no_speech_prob=0.05)
    r_low = TranscriptionResult(text="ok", avg_logprob=-1.5, no_speech_prob=0.7)
    assert r_high.confidence > r_low.confidence
    assert 0.0 <= r_low.confidence <= 1.0
    assert 0.0 <= r_high.confidence <= 1.0


@pytest.mark.asyncio
async def test_hallucination_dropped_before_publish() -> None:
    bus = EventBus()
    seen: list = []
    async def collect(ev):
        seen.append(ev)
    await bus.subscribe("transcript.final", collect, name="t")

    # Classic Whisper-on-silence hallucination.
    engine = MockSTTEngine(
        lambda _pcm, _sr: TranscriptionResult(
            text="Thanks for watching!", avg_logprob=-0.4, no_speech_prob=0.8
        )
    )
    tr = Transcriber(bus, engine)
    utt = Utterance(pcm=b"\x00" * 1600, sample_rate=16000, started_ts="", ended_ts="", frame_count=5)
    out = await tr.transcribe_utterance(utt)
    assert out is None  # filtered, not published
    await asyncio.sleep(0.02)
    assert seen == []
    await bus.close()


@pytest.mark.asyncio
async def test_hallucination_filter_can_be_disabled() -> None:
    bus = EventBus()
    seen: list = []
    async def collect(ev):
        seen.append(ev)
    await bus.subscribe("transcript.final", collect, name="t")

    engine = MockSTTEngine(
        lambda _pcm, _sr: TranscriptionResult(
            text="Thanks for watching!", avg_logprob=-0.4, no_speech_prob=0.8
        )
    )
    tr = Transcriber(bus, engine, cfg=TranscriberConfig(drop_hallucinations=False))
    utt = Utterance(pcm=b"\x00" * 1600, sample_rate=16000, started_ts="", ended_ts="", frame_count=5)
    out = await tr.transcribe_utterance(utt)
    assert out is not None
    await asyncio.sleep(0.02)
    assert len(seen) == 1
    await bus.close()


@pytest.mark.asyncio
async def test_stage_hint_passed_to_prompt_builder() -> None:
    from cqk1af.stt.prompt_builder import PromptBuilder

    bus = EventBus()
    seen_prompt: dict[str, str] = {}

    def fake_engine(_pcm: bytes, _sr: int) -> TranscriptionResult:
        return TranscriptionResult(text="hello", avg_logprob=-0.2, no_speech_prob=0.05)

    engine = MockSTTEngine(fake_engine)

    class CapturingPromptBuilder(PromptBuilder):
        def build(self, *, extra="", own_callsign=None, stage=None):
            seen_prompt["stage"] = stage or ""
            return super().build(extra=extra, own_callsign=own_callsign, stage=stage)

    tr = Transcriber(bus, engine, CapturingPromptBuilder())
    tr.set_stage("calling_cq")
    utt = Utterance(pcm=b"\x00" * 320, sample_rate=16000, started_ts="", ended_ts="", frame_count=1)
    await tr.transcribe_utterance(utt)
    assert seen_prompt["stage"] == "calling_cq"
    await bus.close()


@pytest.mark.asyncio
async def test_submit_runs_in_background() -> None:
    bus = EventBus()
    seen: list = []
    async def collect(ev):
        seen.append(ev)
    await bus.subscribe("transcript.final", collect, name="t")
    engine = MockSTTEngine(
        lambda _pcm, _sr: TranscriptionResult(text="hello", avg_logprob=-0.2, no_speech_prob=0.05)
    )
    tr = Transcriber(bus, engine)
    utt = Utterance(pcm=b"\x00" * 320, sample_rate=16000, started_ts="", ended_ts="", frame_count=1)
    task = tr.submit(utt)
    await tr.drain()
    assert task.done()
    await asyncio.sleep(0.02)
    assert seen and seen[0].text == "hello"
    await bus.close()
