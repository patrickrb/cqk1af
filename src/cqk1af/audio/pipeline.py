"""AudioPipeline — the live RX capture + TX synthesis orchestrator.

Capabilities are probed at start. Each leg degrades independently:

  - **RX**: capture (sounddevice + DAX RX device) → VAD (webrtcvad) → STT
    (faster-whisper) → transcript & callsign events on the bus.
  - **TX**: text → TTS (Piper) → PCM played out the DAX TX device.

Where any dependency is missing — sounddevice not installed, DAX devices not
present (SmartSDR isn't running), Piper binary not on PATH, etc. — that leg is
disabled and the pipeline exposes a status string explaining why. Tools fall
back to narration-only TX when ``can_tx`` is False.
"""
from __future__ import annotations

import asyncio
import shutil
import wave
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings
from ..events import EventBus
from ..radio.base import RadioClient
from ..stt.transcriber import Transcriber, TranscriberConfig
from ..stt.whisper_engine import MockSTTEngine, STTEngine, TranscriptionResult, WhisperSTTEngine
from ..tts.piper_engine import MockTTSEngine, PiperTTSEngine, TTSEngine, TTSResult
from ..util.logging import get_logger

log = get_logger(__name__)


@dataclass
class AudioStatus:
    enabled: bool = True
    rx_capture: str = "not_started"  # "ok" | "disabled" | "<error>"
    tx_playback: str = "not_started"  # "ok" | "disabled" | "narrate_only" | "<error>"
    stt_engine: str = "not_started"  # "whisper" | "mock" | "disabled"
    tts_engine: str = "not_started"  # "piper" | "mock" | "disabled"
    rx_device: str = ""
    tx_device: str = ""
    piper_bin: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def can_rx(self) -> bool:
        return self.rx_capture == "ok" and self.stt_engine in ("whisper", "mock")

    @property
    def can_tx(self) -> bool:
        # Require real TTS — Mock TTS plays silence, which would key PTT with
        # no modulation on a real radio (bad). When Piper isn't installed we
        # fall back to narration-only TX in the tools.
        return self.tx_playback == "ok" and self.tts_engine == "piper"

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "rx_capture": self.rx_capture,
            "tx_playback": self.tx_playback,
            "stt_engine": self.stt_engine,
            "tts_engine": self.tts_engine,
            "rx_device": self.rx_device,
            "tx_device": self.tx_device,
            "piper_bin": self.piper_bin,
            "can_rx": self.can_rx,
            "can_tx": self.can_tx,
            "notes": list(self.notes),
        }


class AudioPipeline:
    def __init__(
        self,
        bus: EventBus,
        settings: Settings,
        *,
        radio: RadioClient | None = None,
    ) -> None:
        self.bus = bus
        self.settings = settings
        # Radio is optional so existing tests that construct AudioPipeline
        # directly keep working. When provided and the backend supports
        # native TX audio (FlexLib on SmartSDR v4), speak() routes through
        # radio.add_tx_audio() instead of the Windows DAX device.
        self.radio = radio
        self.status = AudioStatus(enabled=settings.audio_pipeline_enabled if hasattr(settings, "audio_pipeline_enabled") else True)
        self.stt: STTEngine | None = None
        self.tts: TTSEngine | None = None
        self.transcriber: Transcriber | None = None
        self._sd = None
        self._np = None
        self._capture_stream = None
        self._capture_loop_task: asyncio.Task | None = None
        self._vad = None
        self._tx_lock = asyncio.Lock()
        self._tx_device_index: int | None = None
        self._rx_device_index: int | None = None
        self._started = False

    # ---- lifecycle --------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        if not self.status.enabled:
            self.status.rx_capture = "disabled"
            self.status.tx_playback = "disabled"
            self.status.stt_engine = "disabled"
            self.status.tts_engine = "disabled"
            self._started = True
            return
        self._probe_devices()
        self._init_stt()
        self._init_tts()
        await self._init_vad()
        await self._maybe_start_capture()
        self._started = True
        log.info("audio_pipeline.started", status=self.status.to_dict())

    async def stop(self) -> None:
        if self._capture_loop_task is not None:
            self._capture_loop_task.cancel()
            try:
                await self._capture_loop_task
            except (asyncio.CancelledError, Exception):
                pass
            self._capture_loop_task = None
        if self._capture_stream is not None:
            try:
                self._capture_stream.stop()
                self._capture_stream.close()
            except Exception:
                log.exception("audio_pipeline.capture_stop_failed")
            self._capture_stream = None
        if self.transcriber is not None:
            await self.transcriber.aclose()
        self._started = False

    # ---- TX ---------------------------------------------------------

    async def speak(self, text: str) -> float:
        """Synthesize ``text`` and play it. Returns duration in seconds.

        Two output paths:
          * FlexLib native TX (preferred on SmartSDR v4): hand PCM to
            ``radio.add_tx_audio()``. Optionally monitor on PC default device.
          * Windows DAX device: write PCM via sounddevice to "DAX Audio TX".
            Requires DAX.exe channel to be enabled (v4) or v3 install.

        The TX lock prevents two overlapping calls (e.g. parallel CQ + signal
        report). Caller must already have keyed PTT.
        """
        if self.tts is None:
            log.info("audio_pipeline.speak.skipped", reason="no_tts", text_len=len(text))
            return 0.0
        native_ok = self.radio is not None and self.radio.supports_native_tx_audio
        if not native_ok and self._tx_device_index is None:
            log.info("audio_pipeline.speak.skipped", reason="no_tx_path", text_len=len(text))
            return 0.0
        async with self._tx_lock:
            result = await self.tts.synthesize(text)
            if native_ok:
                return await self._send_via_radio(result)
            return await self._play_pcm(result)

    @property
    def can_tx(self) -> bool:
        # The FlexLib path doesn't need a Windows audio device — accept it
        # when the backend reports native TX audio support.
        if self.radio is not None and self.radio.supports_native_tx_audio:
            return self.status.tts_engine == "piper"
        return self.status.can_tx

    @property
    def can_rx(self) -> bool:
        return self.status.can_rx

    # ---- internals: device probe ------------------------------------

    def _probe_devices(self) -> None:
        try:
            import sounddevice as sd  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]

            self._sd = sd
            self._np = np
        except Exception as e:
            self.status.rx_capture = f"sounddevice not installed: {e}"
            self.status.tx_playback = f"sounddevice not installed: {e}"
            self.status.notes.append(
                "Install audio extras: pip install -e '.[audio]'"
            )
            return

        rx_name = self.settings.audio.rx_device
        tx_name = self.settings.audio.tx_device
        try:
            devices = self._sd.query_devices()
            hostapis = self._sd.query_hostapis()
        except Exception as e:
            self.status.rx_capture = f"query_devices failed: {e}"
            self.status.tx_playback = f"query_devices failed: {e}"
            return

        # Windows exposes each DAX device three times — one per host API (MME,
        # DirectSound, WASAPI). For RX capture, WASAPI is lower latency. For
        # TX playback into the FlexRadio DAX driver, MME is what actually
        # works — NAudio WaveOut (MME) is what smartsdr-mcp uses successfully;
        # WASAPI shared-mode resampling against the DAX 48 kHz native rate
        # drops frames silently.
        def host_api_rank_rx(hostapi_index: int) -> int:
            name = hostapis[hostapi_index]["name"].lower()
            if "wasapi" in name:
                return 0
            if "directsound" in name:
                return 1
            if "asio" in name:
                return 2
            return 3

        def host_api_rank_tx(hostapi_index: int) -> int:
            name = hostapis[hostapi_index]["name"].lower()
            if "mme" in name:
                return 0
            if "directsound" in name:
                return 1
            if "wasapi" in name:
                return 2
            return 3

        def candidate_score(d, target: str, ranker) -> tuple[int, int, int]:
            """Lower is better. (hostapi_rank, exact_match_penalty, name_length)."""
            name_lower = d["name"].lower()
            target_lower = target.lower()
            exact = 0 if name_lower.startswith(target_lower) else 1
            return (ranker(d["hostapi"]), exact, len(d["name"]))

        # RX: input channels > 0, name contains rx_name
        rx_candidates = [
            (i, d) for i, d in enumerate(devices)
            if rx_name.lower() in d["name"].lower() and d["max_input_channels"] > 0
        ]
        if rx_candidates:
            i, d = min(rx_candidates, key=lambda kv: candidate_score(kv[1], rx_name, host_api_rank_rx))
            self._rx_device_index = i
            self.status.rx_device = (
                f'{d["name"]} ({hostapis[d["hostapi"]]["name"]})'
            )

        # TX: output channels > 0, name contains tx_name. Skip "RESERVED"
        # devices — those are SmartSDR's internal placeholders, not actually
        # routed to the radio's TX path.
        tx_candidates = [
            (i, d) for i, d in enumerate(devices)
            if tx_name.lower() in d["name"].lower()
            and d["max_output_channels"] > 0
            and "RESERVED" not in d["name"].upper()
        ]
        if tx_candidates:
            i, d = min(tx_candidates, key=lambda kv: candidate_score(kv[1], tx_name, host_api_rank_tx))
            self._tx_device_index = i
            self.status.tx_device = (
                f'{d["name"]} ({hostapis[d["hostapi"]]["name"]})'
            )

        if self._rx_device_index is None:
            self.status.rx_capture = f'RX device not found (looked for "{rx_name}"). Is SmartSDR running?'
        if self._tx_device_index is None:
            self.status.tx_playback = f'TX device not found (looked for "{tx_name}"). Is SmartSDR running?'
        else:
            self.status.tx_playback = "ok"

    # ---- internals: STT init ---------------------------------------

    def _init_stt(self) -> None:
        if not self.settings.stt.enabled:
            self.status.stt_engine = "disabled"
            self.stt = MockSTTEngine(lambda _p, _sr: TranscriptionResult(text=""))
            return
        try:
            self.stt = WhisperSTTEngine(self.settings.stt)
            self.status.stt_engine = "whisper"
        except Exception as e:
            self.status.notes.append(f"whisper init deferred: {e}")
            # faster-whisper loads lazily — but if the import itself fails we
            # fall back to mock.
            self.stt = MockSTTEngine(lambda _p, _sr: TranscriptionResult(text=""))
            self.status.stt_engine = "mock"

    async def _init_vad(self) -> None:
        try:
            from .vad import VadConfig, VoiceActivityDetector

            cfg = VadConfig(
                sample_rate=16000,
                frame_ms=self.settings.audio.frame_ms,
                aggressiveness=self.settings.audio.vad_aggressiveness,
                hangover_ms=self.settings.audio.vad_hangover_ms,
            )
            self._vad = VoiceActivityDetector(self.bus, cfg, channel="rx")
        except Exception as e:
            self.status.rx_capture = f"VAD init failed: {e}"
            self._vad = None
            return

        if self.stt is not None:
            from ..stt.prompt_builder import PromptBuilder

            self.transcriber = Transcriber(
                self.bus,
                self.stt,
                PromptBuilder(),
                TranscriberConfig(
                    own_callsign=self.settings.operator.callsign,
                    extra_prompt=self.settings.stt.initial_prompt_extra,
                ),
            )

    # ---- internals: TTS init ---------------------------------------

    def _init_tts(self) -> None:
        if not self.settings.tts.enabled:
            self.status.tts_engine = "disabled"
            return
        # Look in the voice-model directory first (most users drop piper.exe
        # next to the .onnx model), then fall back to PATH.
        piper_bin: str | None = None
        voice_path = Path(self.settings.tts.voice_path)
        local_candidates = [
            voice_path.parent / "piper.exe",
            voice_path.parent / "piper",
        ]
        for cand in local_candidates:
            if cand.exists():
                piper_bin = str(cand)
                break
        if piper_bin is None:
            piper_bin = shutil.which("piper") or shutil.which("piper.exe")
        if piper_bin is None:
            self.status.notes.append(
                "Piper binary not found. Place piper.exe in "
                f"{voice_path.parent} (next to the voice model) or on PATH. "
                "Download from https://github.com/rhasspy/piper/releases."
            )
            self.tts = MockTTSEngine()
            self.status.tts_engine = "mock"
            return
        if not voice_path.exists():
            self.status.notes.append(
                f"Piper voice not found at {voice_path}. Download a voice model "
                "(e.g. en_US-amy-medium.onnx) from "
                "https://huggingface.co/rhasspy/piper-voices and place it there."
            )
            self.tts = MockTTSEngine()
            self.status.tts_engine = "mock"
            return
        self.tts = PiperTTSEngine(self.settings.tts, piper_bin=piper_bin)
        self.status.tts_engine = "piper"
        self.status.piper_bin = piper_bin

    # ---- internals: capture ----------------------------------------

    async def _maybe_start_capture(self) -> None:
        if self._sd is None or self._np is None or self._rx_device_index is None or self._vad is None:
            return
        try:
            sd = self._sd
            np = self._np
            sample_rate_in = self.settings.audio.sample_rate
            sample_rate_out = 16000
            frame_ms = self.settings.audio.frame_ms
            frame_samples_in = int(sample_rate_in * frame_ms / 1000)
            ratio = sample_rate_in // sample_rate_out
            frame_bytes_out = int(sample_rate_out * 2 * frame_ms / 1000)

            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)

            def callback(indata, _frames, _time_info, status) -> None:  # noqa: ANN001
                if status:
                    log.warning("audio_pipeline.capture_status", status=str(status))
                mono = indata[:, 0] if indata.ndim > 1 else indata
                if ratio > 1:
                    n = (mono.shape[0] // ratio) * ratio
                    mono = mono[:n].reshape(-1, ratio).mean(axis=1)
                clipped = np.clip(mono, -1.0, 1.0)
                pcm = (clipped * 32767.0).astype(np.int16).tobytes()
                # Drop oldest on overflow
                loop.call_soon_threadsafe(self._enqueue_frame, queue, pcm, frame_bytes_out)

            self._capture_stream = sd.InputStream(
                samplerate=sample_rate_in,
                blocksize=frame_samples_in,
                device=self._rx_device_index,
                channels=1,
                dtype="float32",
                callback=callback,
            )
            self._capture_stream.start()
            self.status.rx_capture = "ok"
            self._capture_loop_task = asyncio.create_task(
                self._capture_loop(queue, frame_bytes_out), name="audio_pipeline.capture"
            )
        except Exception as e:
            log.exception("audio_pipeline.capture_start_failed")
            self.status.rx_capture = f"capture start failed: {e}"

    def _enqueue_frame(self, queue: asyncio.Queue, pcm: bytes, frame_bytes_out: int) -> None:
        # The audio callback may produce frames sized to its block; we accumulate
        # exact ``frame_bytes_out``-sized chunks in `_carry` so VAD always gets
        # webrtcvad-legal sizes.
        carry = getattr(self, "_carry", b"") + pcm
        while len(carry) >= frame_bytes_out:
            chunk = carry[:frame_bytes_out]
            carry = carry[frame_bytes_out:]
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(chunk)
        self._carry = carry

    async def _capture_loop(self, queue: asyncio.Queue, frame_bytes_out: int) -> None:
        try:
            while True:
                frame = await queue.get()
                if self._vad is None:
                    continue
                try:
                    utt = await self._vad.push_frame(frame)
                except Exception:
                    log.exception("audio_pipeline.vad_error")
                    continue
                if utt is not None and self.transcriber is not None:
                    self.transcriber.submit(utt)
        except asyncio.CancelledError:
            return

    # ---- internals: playback ---------------------------------------

    async def _send_via_radio(self, result: TTSResult) -> float:
        """Push PCM through the radio's native TX audio stream (FlexLib DAX)."""
        if self.radio is None:
            return 0.0
        sr = result.sample_rate
        n_samples = len(result.pcm) // 2  # int16 → 2 bytes/sample
        if n_samples == 0:
            return 0.0
        duration = n_samples / float(sr)

        # Operator monitor on the PC default device, non-blocking. The radio
        # call is the source of truth for duration.
        if self._sd is not None and self._np is not None and bool(self.settings.tts.pc_monitor_enabled):
            try:
                arr = self._np.frombuffer(result.pcm, dtype=self._np.int16)
                self._sd.play(arr, samplerate=sr, blocking=False)
            except Exception:
                log.exception("audio_pipeline.monitor_play_failed")

        try:
            await self.radio.add_tx_audio(result.pcm, sr)
        except Exception:
            log.exception("audio_pipeline.tx_native_failed")
            return 0.0
        return duration

    async def _play_pcm(self, result: TTSResult) -> float:
        if self._sd is None or self._np is None or self._tx_device_index is None:
            return 0.0
        np = self._np
        sd = self._sd
        sr = result.sample_rate
        arr = np.frombuffer(result.pcm, dtype=np.int16)
        if arr.size == 0:
            return 0.0

        # FlexRadio DAX TX is native 48 kHz. Resample Piper's 22050 Hz output
        # before handing it to MME/WASAPI — letting the host API resample on
        # its own drops frames silently on this driver. Linear interp is fine
        # for speech bandwidth.
        DAX_RATE = 48000
        if sr != DAX_RATE:
            ratio = sr / DAX_RATE
            out_len = int(arr.size / ratio)
            src_idx_f = np.arange(out_len) * ratio
            idx = src_idx_f.astype(np.int64)
            frac = src_idx_f - idx
            idx_next = np.minimum(idx + 1, arr.size - 1)
            resampled = (
                arr[idx].astype(np.float32) * (1.0 - frac)
                + arr[idx_next].astype(np.float32) * frac
            )
            play_arr = resampled.astype(np.int16)
            play_sr = DAX_RATE
        else:
            play_arr = arr
            play_sr = sr

        loop = asyncio.get_running_loop()
        duration = arr.size / float(sr)
        monitor = bool(self.settings.tts.pc_monitor_enabled)

        def _do() -> None:
            # Start the monitor playback first (non-blocking, default device)
            # so the operator hears what's being sent at the same time as the
            # radio's TX. The DAX TX playback is blocking — it's the source of
            # truth for the duration we report.
            if monitor:
                try:
                    sd.play(arr, samplerate=sr, blocking=False)
                except Exception:
                    log.exception("audio_pipeline.monitor_play_failed")
            try:
                sd.play(play_arr, samplerate=play_sr, device=self._tx_device_index, blocking=True)
            except Exception:
                log.exception("audio_pipeline.tx_play_failed")

        await loop.run_in_executor(None, _do)
        return duration

    # ---- introspection ---------------------------------------------

    def status_dict(self) -> dict[str, Any]:
        return self.status.to_dict()
