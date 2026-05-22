"""faster-whisper engine wrapper.

Runs inference in a ``ThreadPoolExecutor`` to keep the asyncio loop responsive.
Lazy-imports faster-whisper so unit tests don't require the heavy dependency.

For tests, use ``MockSTTEngine`` which accepts a callable mapping (pcm, lang)
to a deterministic transcript.
"""
from __future__ import annotations

import asyncio
import io
import wave
from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from ..config import SttCfg
from ..util.logging import get_logger

log = get_logger(__name__)


@dataclass
class TranscriptionResult:
    text: str
    language: str = "en"
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    duration_s: float = 0.0

    @property
    def confidence(self) -> float:
        """Heuristic 0..1 derived from avg_logprob (-0.6 ≈ 0.5) and no_speech_prob."""
        # avg_logprob is negative; map to (0..1) via exp, then dampen by no_speech_prob.
        import math

        try:
            base = math.exp(max(-3.0, min(0.0, self.avg_logprob)))
        except OverflowError:
            base = 0.0
        return max(0.0, base * (1.0 - max(0.0, min(1.0, self.no_speech_prob))))


def pcm16_to_wav_bytes(pcm: bytes, *, sample_rate: int, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


class STTEngine(ABC):
    @abstractmethod
    async def transcribe(
        self,
        pcm: bytes,
        *,
        sample_rate: int,
        initial_prompt: str = "",
        language: str = "en",
    ) -> TranscriptionResult: ...

    async def aclose(self) -> None:
        pass


class WhisperSTTEngine(STTEngine):
    def __init__(self, cfg: SttCfg) -> None:
        self.cfg = cfg
        self._model = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")
        self._init_lock = asyncio.Lock()

    async def _ensure_model(self) -> None:
        async with self._init_lock:
            if self._model is not None:
                return
            from faster_whisper import WhisperModel

            device = self.cfg.device
            if device == "auto":
                device = "cuda" if _cuda_available() else "cpu"
            compute_type = self.cfg.compute_type
            if compute_type == "auto":
                compute_type = "float16" if device == "cuda" else "int8"
            log.info(
                "stt.model_loading",
                model=self.cfg.model,
                device=device,
                compute_type=compute_type,
            )
            self._model = WhisperModel(
                self.cfg.model, device=device, compute_type=compute_type
            )

    async def transcribe(
        self,
        pcm: bytes,
        *,
        sample_rate: int,
        initial_prompt: str = "",
        language: str = "en",
    ) -> TranscriptionResult:
        await self._ensure_model()
        assert self._model is not None
        wav = pcm16_to_wav_bytes(pcm, sample_rate=sample_rate)
        path = Path(_temp_wav_path())
        path.write_bytes(wav)
        loop = asyncio.get_running_loop()
        try:
            segments, info = await loop.run_in_executor(
                self._executor, self._run_inference, str(path), initial_prompt, language
            )
        finally:
            try:
                path.unlink()
            except Exception:
                pass
        text = " ".join(s.text.strip() for s in segments).strip()
        # Use the first segment's logprobs if available.
        avg_logprob = segments[0].avg_logprob if segments else -1.0
        no_speech_prob = segments[0].no_speech_prob if segments else 1.0
        return TranscriptionResult(
            text=text,
            language=info.language if info else language,
            avg_logprob=float(avg_logprob),
            no_speech_prob=float(no_speech_prob),
            duration_s=float(info.duration) if info else 0.0,
        )

    def _run_inference(self, wav_path: str, prompt: str, language: str):
        assert self._model is not None
        segs, info = self._model.transcribe(
            wav_path,
            language=language or None,
            beam_size=self.cfg.beam_size,
            initial_prompt=prompt or None,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        # segments is a generator; materialise.
        return list(segs), info

    async def aclose(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


class MockSTTEngine(STTEngine):
    """Deterministic stub used in tests. Calls ``fn(pcm, sample_rate)``."""

    def __init__(self, fn: Callable[[bytes, int], TranscriptionResult]) -> None:
        self.fn = fn

    async def transcribe(
        self,
        pcm: bytes,
        *,
        sample_rate: int,
        initial_prompt: str = "",
        language: str = "en",
    ) -> TranscriptionResult:
        return self.fn(pcm, sample_rate)


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _temp_wav_path() -> str:
    import tempfile
    import uuid

    return str(Path(tempfile.gettempdir()) / f"cqk1af_{uuid.uuid4().hex}.wav")
