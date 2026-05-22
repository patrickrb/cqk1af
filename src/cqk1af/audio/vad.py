"""Voice activity detection.

Wraps ``webrtcvad`` with utterance assembly: maintains a state machine
(silent ↔ speaking) with a hangover period so brief gaps inside speech don't
fracture utterances. Emits ``VoiceActivityStarted/Stopped`` events on the bus.

PCM input is mono 16-bit signed little-endian. webrtcvad requires 8, 16, or
32 kHz; our pipeline downsamples 48 kHz → 16 kHz before VAD.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass

from ..events import EventBus, VoiceActivityStarted, VoiceActivityStopped
from ..util.logging import get_logger
from ..util.time import utcnow

log = get_logger(__name__)


@dataclass
class VadConfig:
    sample_rate: int = 16000  # webrtcvad: 8/16/32 kHz
    frame_ms: int = 20  # webrtcvad: 10/20/30 ms
    aggressiveness: int = 3  # 0..3 (3 = most aggressive)
    hangover_ms: int = 300  # bridge brief gaps inside speech
    preroll_ms: int = 200  # buffer N ms BEFORE speech start for STT context


@dataclass
class Utterance:
    pcm: bytes
    sample_rate: int
    started_ts: str
    ended_ts: str
    frame_count: int


class VoiceActivityDetector:
    """Stateful VAD. Feed mono int16 PCM frames; collect utterances."""

    def __init__(self, bus: EventBus, cfg: VadConfig | None = None, *, channel: str = "rx") -> None:
        self.bus = bus
        self.cfg = cfg or VadConfig()
        self.channel = channel
        self._frame_bytes = int(self.cfg.sample_rate * 2 * self.cfg.frame_ms / 1000)
        self._is_speaking = False
        self._silent_run_ms = 0
        self._preroll = bytearray()
        self._utt = bytearray()
        self._utt_started_ts = ""
        self._utt_frames = 0
        self._vad = None  # lazy: webrtcvad is optional

    def _ensure_vad(self) -> None:
        if self._vad is None:
            import webrtcvad

            self._vad = webrtcvad.Vad(self.cfg.aggressiveness)

    def _is_speech(self, frame: bytes) -> bool:
        self._ensure_vad()
        assert self._vad is not None
        return self._vad.is_speech(frame, self.cfg.sample_rate)

    async def push_frame(self, frame: bytes) -> Utterance | None:
        """Feed one frame. Returns a complete ``Utterance`` if speech just ended."""
        if len(frame) != self._frame_bytes:
            raise ValueError(
                f"expected {self._frame_bytes}-byte frame, got {len(frame)}"
            )
        speech = self._is_speech(frame)

        if not self._is_speaking:
            # Maintain pre-roll buffer
            self._preroll.extend(frame)
            preroll_max = int(self.cfg.sample_rate * 2 * self.cfg.preroll_ms / 1000)
            if len(self._preroll) > preroll_max:
                del self._preroll[: len(self._preroll) - preroll_max]
            if speech:
                self._is_speaking = True
                self._silent_run_ms = 0
                self._utt = bytearray(self._preroll)
                self._utt.extend(frame)
                self._utt_frames = 1
                self._utt_started_ts = utcnow().isoformat()
                await self.bus.publish(VoiceActivityStarted(channel=self.channel))
            return None

        # Already speaking
        self._utt.extend(frame)
        self._utt_frames += 1
        if speech:
            self._silent_run_ms = 0
            return None
        # Silent frame
        self._silent_run_ms += self.cfg.frame_ms
        if self._silent_run_ms < self.cfg.hangover_ms:
            return None
        # Hangover expired — utterance ends
        self._is_speaking = False
        utt = Utterance(
            pcm=bytes(self._utt),
            sample_rate=self.cfg.sample_rate,
            started_ts=self._utt_started_ts,
            ended_ts=utcnow().isoformat(),
            frame_count=self._utt_frames,
        )
        duration_ms = int(self._utt_frames * self.cfg.frame_ms)
        await self.bus.publish(VoiceActivityStopped(channel=self.channel, duration_ms=duration_ms))
        self._utt.clear()
        self._preroll.clear()
        self._silent_run_ms = 0
        return utt

    async def push_stream(self, frames: Iterable[bytes]) -> list[Utterance]:
        out: list[Utterance] = []
        for f in frames:
            u = await self.push_frame(f)
            if u is not None:
                out.append(u)
        return out


def frame_pcm(data: bytes, frame_bytes: int) -> list[bytes]:
    """Split a contiguous PCM blob into fixed-size frames (truncates remainder)."""
    return [data[i : i + frame_bytes] for i in range(0, len(data) - frame_bytes + 1, frame_bytes)]
