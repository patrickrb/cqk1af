"""Piper TTS engine.

Runs the Piper executable as a subprocess. Piper reads JSON-line input on stdin
and emits raw 22050 Hz int16 mono PCM on stdout. We use the streaming mode for
low latency but for safety we capture the entire output before playback.

For tests, ``MockTTSEngine`` returns deterministic silence at the configured
length.
"""
from __future__ import annotations

import asyncio
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ..config import TtsCfg
from ..util.logging import get_logger

log = get_logger(__name__)


@dataclass
class TTSResult:
    pcm: bytes
    sample_rate: int = 22050


class TTSEngine(ABC):
    @abstractmethod
    async def synthesize(self, text: str) -> TTSResult: ...

    async def aclose(self) -> None:
        pass


class PiperTTSEngine(TTSEngine):
    def __init__(self, cfg: TtsCfg, *, piper_bin: str | None = None) -> None:
        self.cfg = cfg
        self.piper_bin = piper_bin or shutil.which("piper") or "piper"
        self._lock = asyncio.Lock()

    async def synthesize(self, text: str) -> TTSResult:
        if not self.cfg.voice_path or not Path(self.cfg.voice_path).exists():
            raise RuntimeError(
                f"Piper voice model not found at {self.cfg.voice_path}. "
                "Download a voice and place it in runtime/piper/."
            )
        async with self._lock:
            cmd = [
                self.piper_bin,
                "--model",
                str(self.cfg.voice_path),
                "--output-raw",
            ]
            log.info("tts.piper.invoke", text_len=len(text))
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate(input=text.encode("utf-8"))
            if proc.returncode != 0:
                raise RuntimeError(
                    f"piper exited {proc.returncode}: {stderr.decode('utf-8', 'replace')[:500]}"
                )
            return TTSResult(pcm=stdout, sample_rate=22050)


class MockTTSEngine(TTSEngine):
    def __init__(self, sample_rate: int = 22050, *, silence_ms: int = 200) -> None:
        self.sample_rate = sample_rate
        self.silence_ms = silence_ms

    async def synthesize(self, text: str) -> TTSResult:
        nsamples = int(self.sample_rate * self.silence_ms / 1000)
        return TTSResult(pcm=b"\x00\x00" * nsamples, sample_rate=self.sample_rate)
