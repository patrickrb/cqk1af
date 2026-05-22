"""VAD tests using synthetic PCM.

webrtcvad is an optional dependency — skip if not installed.
"""
from __future__ import annotations

import math
import struct

import pytest

webrtcvad = pytest.importorskip("webrtcvad")

from cqk1af.audio.vad import VadConfig, VoiceActivityDetector, frame_pcm
from cqk1af.events import EventBus


def _gen_pcm(sample_rate: int, duration_ms: int, *, freq_hz: float = 0.0, amplitude: float = 0.0) -> bytes:
    n = int(sample_rate * duration_ms / 1000)
    samples = []
    for i in range(n):
        if freq_hz <= 0.0 or amplitude <= 0.0:
            s = 0
        else:
            v = amplitude * math.sin(2 * math.pi * freq_hz * (i / sample_rate))
            s = max(-32767, min(32767, int(v * 32767)))
        samples.append(s)
    return struct.pack(f"<{n}h", *samples)


@pytest.mark.asyncio
async def test_silence_does_not_start_speech() -> None:
    bus = EventBus()
    cfg = VadConfig(sample_rate=16000, frame_ms=20)
    vad = VoiceActivityDetector(bus, cfg)
    pcm = _gen_pcm(cfg.sample_rate, 200)
    frames = frame_pcm(pcm, int(cfg.sample_rate * 2 * cfg.frame_ms / 1000))
    utts = await vad.push_stream(frames)
    assert utts == []
    await bus.close()


@pytest.mark.asyncio
async def test_loud_tone_detected_then_silence_closes_utterance() -> None:
    bus = EventBus()
    cfg = VadConfig(sample_rate=16000, frame_ms=20, aggressiveness=1, hangover_ms=60, preroll_ms=0)
    vad = VoiceActivityDetector(bus, cfg)
    voice = _gen_pcm(cfg.sample_rate, 300, freq_hz=440.0, amplitude=0.9)
    silence = _gen_pcm(cfg.sample_rate, 300)
    frame_bytes = int(cfg.sample_rate * 2 * cfg.frame_ms / 1000)
    frames = frame_pcm(voice + silence, frame_bytes)
    utts = await vad.push_stream(frames)
    # Some VAD setups may still split this; at minimum we expect non-zero utterances.
    assert any(u.frame_count > 5 for u in utts)
    await bus.close()
