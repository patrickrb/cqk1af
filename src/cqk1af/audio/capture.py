"""WASAPI audio capture (DAX RX) via sounddevice.

The sounddevice ``InputStream`` runs its callback on a private thread. We
push frames to an ``asyncio.Queue`` using ``loop.call_soon_threadsafe`` so the
consumer runs on the main event loop.

Audio is captured at 48 kHz float32 and downsampled to 16 kHz int16 PCM for
webrtcvad and faster-whisper.

Defer all sounddevice/numpy imports until the capture starts, so test runs and
non-audio CLI paths don't require the audio extras.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass

from ..util.logging import get_logger

log = get_logger(__name__)


@dataclass
class CaptureConfig:
    device_name: str = "DAX Audio RX 1"
    sample_rate_in: int = 48000
    sample_rate_out: int = 16000
    channels: int = 1
    blocksize_ms: int = 20
    # Maximum frames buffered in the asyncio queue. A consumer that falls
    # behind will see frames dropped (audio is real-time; old frames are
    # useless).
    queue_maxsize: int = 200


class AudioCapture:
    """Capture audio from a WASAPI input device and yield 16 kHz int16 frames."""

    def __init__(self, cfg: CaptureConfig | None = None) -> None:
        self.cfg = cfg or CaptureConfig()
        self._stream = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=self.cfg.queue_maxsize)
        self._stop_event = threading.Event()
        self._frame_samples_in = int(self.cfg.sample_rate_in * self.cfg.blocksize_ms / 1000)
        self._frame_samples_out = int(self.cfg.sample_rate_out * self.cfg.blocksize_ms / 1000)
        self._frame_bytes_out = self._frame_samples_out * 2

    @property
    def frame_bytes(self) -> int:
        return self._frame_bytes_out

    @property
    def queue(self) -> asyncio.Queue[bytes]:
        return self._queue

    @staticmethod
    def list_devices() -> list[dict]:
        try:
            import sounddevice as sd

            return [
                dict(idx=i, name=d["name"], inputs=d["max_input_channels"], outputs=d["max_output_channels"])
                for i, d in enumerate(sd.query_devices())
            ]
        except Exception as e:
            log.warning("audio.list_devices_failed", error=str(e))
            return []

    @staticmethod
    def find_device(name: str) -> int | None:
        for d in AudioCapture.list_devices():
            if name.lower() in d["name"].lower() and d["inputs"] > 0:
                return int(d["idx"])
        return None

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        import numpy as np
        import sounddevice as sd

        self._loop = loop or asyncio.get_event_loop()
        dev = self.find_device(self.cfg.device_name)
        if dev is None:
            raise RuntimeError(f'Audio input device "{self.cfg.device_name}" not found')

        def callback(indata: "np.ndarray", _frames, _time_info, status) -> None:  # noqa: ANN001
            if status:
                log.warning("audio.capture.status", status=str(status))
            # indata shape: (frames, channels), float32
            mono = indata[:, 0] if indata.ndim > 1 else indata
            # Downsample 48 kHz -> 16 kHz by averaging 3-sample groups
            ratio = self.cfg.sample_rate_in // self.cfg.sample_rate_out
            if ratio > 1:
                n = (mono.shape[0] // ratio) * ratio
                mono = mono[:n].reshape(-1, ratio).mean(axis=1)
            # Float32 [-1, 1] -> int16
            clipped = np.clip(mono, -1.0, 1.0)
            pcm = (clipped * 32767.0).astype(np.int16).tobytes()
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._enqueue, pcm)

        self._stream = sd.InputStream(
            samplerate=self.cfg.sample_rate_in,
            blocksize=self._frame_samples_in,
            device=dev,
            channels=self.cfg.channels,
            dtype="float32",
            callback=callback,
        )
        self._stream.start()
        log.info("audio.capture.started", device=self.cfg.device_name, rate=self.cfg.sample_rate_in)

    def stop(self) -> None:
        self._stop_event.set()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                log.exception("audio.capture.stop_failed")
            self._stream = None
        log.info("audio.capture.stopped")

    def _enqueue(self, pcm: bytes) -> None:
        # Drop-oldest on overflow
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(pcm)
