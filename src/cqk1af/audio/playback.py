"""Audio playback via WASAPI (DAX TX device).

Phase 9 wires Piper-generated PCM through this. For Phase 6 we provide the
plumbing only.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..util.logging import get_logger

log = get_logger(__name__)


@dataclass
class PlaybackConfig:
    device_name: str = "DAX Audio TX"
    sample_rate: int = 22050  # Piper default
    channels: int = 1


class AudioPlayback:
    def __init__(self, cfg: PlaybackConfig | None = None) -> None:
        self.cfg = cfg or PlaybackConfig()
        self._stream = None

    def _find_output_device(self) -> int | None:
        try:
            import sounddevice as sd
        except Exception:
            return None
        for i, d in enumerate(sd.query_devices()):
            if self.cfg.device_name.lower() in d["name"].lower() and d["max_output_channels"] > 0:
                return i
        return None

    def play_pcm_int16(self, pcm: bytes, *, sample_rate: int | None = None) -> None:
        import numpy as np
        import sounddevice as sd

        dev = self._find_output_device()
        if dev is None:
            raise RuntimeError(f'Audio output device "{self.cfg.device_name}" not found')
        arr = np.frombuffer(pcm, dtype=np.int16)
        sr = sample_rate or self.cfg.sample_rate
        sd.play(arr, samplerate=sr, device=dev, blocking=True)
