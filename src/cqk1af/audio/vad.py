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
    # Hard cap on a single utterance. When VAD never observes silence (e.g.
    # continuous band noise on SSB falsely flagged as speech), we still emit
    # an utterance every N seconds so STT keeps making progress.
    max_utterance_ms: int = 8000


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
        # Per-frame VAD verdict counter. The capture loop reads this so the
        # dashboard can show "frames=2400, voice=0" when webrtcvad is too
        # strict for the audio (e.g. aggressiveness=3 on SSB).
        self.voice_frame_count: int = 0

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
        if speech:
            self.voice_frame_count += 1

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
        utt_duration_ms = self._utt_frames * self.cfg.frame_ms
        if speech:
            self._silent_run_ms = 0
            # Even with no silent break, force-flush at the max-utterance cap
            # so STT still gets a chance. Keep the speaking state True and
            # start a fresh buffer immediately — this handles the case where
            # band noise is continuously classified as speech.
            if utt_duration_ms >= self.cfg.max_utterance_ms:
                return await self._cut_utterance(keep_speaking=True)
            return None
        # Silent frame
        self._silent_run_ms += self.cfg.frame_ms
        if (
            self._silent_run_ms < self.cfg.hangover_ms
            and utt_duration_ms < self.cfg.max_utterance_ms
        ):
            return None
        # Hangover expired (or max duration reached) — utterance ends
        return await self._cut_utterance(keep_speaking=False)

    async def _cut_utterance(self, *, keep_speaking: bool) -> Utterance:
        """Snapshot the current buffer as an Utterance and reset.

        If ``keep_speaking`` is True, we stay in the speaking state with a
        fresh empty buffer — used to chunk a continuous "speaking" run into
        max_utterance_ms windows so STT keeps making progress.
        """
        utt = Utterance(
            pcm=bytes(self._utt),
            sample_rate=self.cfg.sample_rate,
            started_ts=self._utt_started_ts,
            ended_ts=utcnow().isoformat(),
            frame_count=self._utt_frames,
        )
        duration_ms = int(self._utt_frames * self.cfg.frame_ms)
        # A max_duration cut isn't really the end of speech, but downstream
        # subscribers (transcriber, dashboard) treat it the same — utterance
        # ready to transcribe.
        await self.bus.publish(
            VoiceActivityStopped(channel=self.channel, duration_ms=duration_ms)
        )
        self._utt.clear()
        if not keep_speaking:
            self._is_speaking = False
            self._preroll.clear()
        else:
            # Continuous "speaking" — start the next buffer fresh.
            self._utt_started_ts = utcnow().isoformat()
            self._utt_frames = 0
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
