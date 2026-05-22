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
from ..events import AudioPipelineStatus, EventBus
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
    # Diagnostic counters. Bumped from the capture loop, VAD events, and the
    # transcriber so the dashboard can tell where the chain breaks when no
    # transcripts appear despite a healthy can_rx.
    frames_seen: int = 0
    # Per-frame VAD verdicts — if this stays at 0 while frames_seen climbs,
    # webrtcvad isn't classifying the audio as speech (try lowering
    # vad_aggressiveness in config).
    voice_frames: int = 0
    voice_utterances: int = 0
    stt_attempts: int = 0
    stt_results: int = 0
    stt_dropped: int = 0
    last_stt_error: str = ""
    # Last observed RMS in dBFS over the capture frame window. Operator-visible
    # so "Is audio actually flowing?" has a one-glance answer.
    rx_rms_dbfs: float = -120.0

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
            "frames_seen": self.frames_seen,
            "voice_frames": self.voice_frames,
            "voice_utterances": self.voice_utterances,
            "stt_attempts": self.stt_attempts,
            "stt_results": self.stt_results,
            "stt_dropped": self.stt_dropped,
            "last_stt_error": self.last_stt_error,
            "rx_rms_dbfs": round(self.rx_rms_dbfs, 1),
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
        self._voice_sub: object | None = None
        self._transcript_sub: object | None = None
        self._rx_connect_sub: object | None = None
        self._status_publish_task: asyncio.Task | None = None
        # When did we last broadcast the metrics-bearing status? Coalesced so
        # high-rate counter updates produce at most one WS message per second.
        self._last_status_publish: float = 0.0

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
        await self._wire_metric_subs()
        # Eagerly trigger Whisper's model load on a background task so the user
        # sees "model_loading" / a load error rather than silent first-call
        # delay (distil-large-v3 download is ~600 MB; current dashboards just
        # showed an empty feed for 30+ seconds while it pulled).
        await self._kickoff_model_load()
        self._started = True
        log.info("audio_pipeline.started", status=self.status.to_dict())
        await self._publish_status()

    async def _wire_metric_subs(self) -> None:
        from ..events import TranscriptDropped, TranscriptFinal, VoiceActivityStopped

        async def on_voice_stop(ev) -> None:  # noqa: ANN001
            if isinstance(ev, VoiceActivityStopped):
                self.status.voice_utterances += 1
                await self._publish_status_throttled()

        async def on_transcript(ev) -> None:  # noqa: ANN001
            if isinstance(ev, TranscriptFinal) and ev.direction == "rx":
                self.status.stt_results += 1
                await self._publish_status_throttled()

        async def on_drop(ev) -> None:  # noqa: ANN001
            if isinstance(ev, TranscriptDropped):
                self.status.stt_dropped += 1
                self.status.last_stt_error = f"{ev.reason}: {ev.text[:48]}"
                await self._publish_status_throttled()

        self._voice_sub = await self.bus.subscribe(
            "audio.voice_stopped", on_voice_stop, name="pipeline.metrics.voice"
        )
        self._transcript_sub = await self.bus.subscribe(
            "transcript.final", on_transcript, name="pipeline.metrics.transcript"
        )
        await self.bus.subscribe(
            "transcript.dropped", on_drop, name="pipeline.metrics.dropped"
        )

    async def _kickoff_model_load(self) -> None:
        """Load Whisper eagerly on a background task and stamp the result.

        Lazy load on first transcribe call is fine for tests but for live
        operation a 30-second silent wait while the model downloads looks like
        a broken pipeline. Doing it here lets the dashboard say "loading…".
        """
        if not isinstance(self.stt, WhisperSTTEngine):
            return

        async def _load() -> None:
            try:
                self.status.notes.append("whisper.model_loading")
                await self._publish_status()
                await self.stt._ensure_model()  # type: ignore[attr-defined]  # SLF001
                # Drop the loading note now that it's ready
                self.status.notes = [n for n in self.status.notes if n != "whisper.model_loading"]
                self.status.notes.append("whisper.model_loaded")
                await self._publish_status()
            except Exception as e:
                self.status.notes = [n for n in self.status.notes if n != "whisper.model_loading"]
                self.status.stt_engine = f"whisper_load_failed: {e}"
                self.status.last_stt_error = f"model_load: {e}"
                log.exception("audio_pipeline.whisper_load_failed")
                await self._publish_status()

        asyncio.create_task(_load(), name="audio_pipeline.whisper_load")

    async def _publish_status_throttled(self) -> None:
        """Publish at most ~1 status event/sec while counters tick."""
        loop = asyncio.get_running_loop()
        now = loop.time()
        if now - self._last_status_publish < 1.0:
            return
        self._last_status_publish = now
        await self._publish_status()

    async def _publish_status(self) -> None:
        """Push the current AudioStatus onto the bus so the dashboard mirrors it."""
        try:
            await self.bus.publish(AudioPipelineStatus(status=self.status.to_dict()))
        except Exception:
            log.exception("audio_pipeline.status_publish_failed")

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
        # Tear down the native RX stream if it was open.
        if self.radio is not None and self.radio.supports_native_rx_audio:
            try:
                await self.radio.stop_rx_audio()
            except Exception:
                log.exception("audio_pipeline.native_rx_stop_failed")
        for sub in (self._voice_sub, self._transcript_sub, self._rx_connect_sub):
            if sub is not None:
                try:
                    await self.bus.unsubscribe(sub)  # type: ignore[arg-type]
                except Exception:
                    pass
        self._voice_sub = None
        self._transcript_sub = None
        self._rx_connect_sub = None
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
        # Hot path: if the radio backend will deliver both RX and TX natively
        # (FlexLib), skip sounddevice entirely. Even just calling
        # ``sd.query_devices()`` forces PortAudio to fully enumerate WASAPI,
        # and on this machine that enumeration alone makes SmartSDR's DAX
        # driver flip "PC Audio" off — we lose the operator's speakers without
        # ever opening a stream. The FlexLib path doesn't need any Windows
        # audio device, so don't touch PortAudio at all.
        if (
            self.radio is not None
            and self._radio_can_have_native_rx()
            and self._radio_can_have_native_tx()
        ):
            # rx_capture will flip to "ok" inside _start_native_rx_capture once
            # the radio connects. tx_playback flips to "ok" when we observe
            # supports_native_tx_audio (radio.connect opens that stream).
            self.status.rx_device = "FlexLib DAX RX 1 (UDP stream)"
            self.status.tx_device = "FlexLib DAX TX (UDP stream)"
            self.status.rx_capture = "waiting for radio.connect"
            self.status.tx_playback = "waiting for radio.connect"
            return

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
            # Only flag "device not found" if we're going to use it. When the
            # radio supports native RX audio (FlexLib UDP stream), the Windows
            # DAX device is irrelevant — and not opening it is the whole point.
            if self.radio is None or not self.radio.supports_native_rx_audio:
                self.status.rx_capture = (
                    f'RX device not found (looked for "{rx_name}"). Is SmartSDR running?'
                )
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
                max_utterance_ms=int(self.settings.audio.max_utterance_seconds * 1000),
            )
            self._vad = VoiceActivityDetector(self.bus, cfg, channel="rx")
        except Exception as e:
            self.status.rx_capture = f"VAD init failed: {e}"
            self._vad = None
            return

        if self.stt is not None:
            from ..stt.prompt_builder import PromptBuilder

            prompts = PromptBuilder()
            op = self.settings.operator
            prompts.set_operator(
                callsign=op.callsign,
                name=op.name,
                qth=op.qth,
                grid=op.grid_square,
            )
            self.transcriber = Transcriber(
                self.bus,
                self.stt,
                prompts,
                TranscriberConfig(
                    own_callsign=op.callsign,
                    extra_prompt=self.settings.stt.initial_prompt_extra,
                    extra_hotwords=self.settings.stt.hotwords_extra,
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
        # Preferred path: FlexLib's DAX RX audio stream. It rides FlexLib's
        # UDP channel; no Windows audio device is opened, so SmartSDR doesn't
        # drop PC Audio routing when capture comes online. Falls back to the
        # Windows DAX device only if the backend doesn't support native RX.
        #
        # The radio likely isn't connected yet at pipeline.start() time (the
        # operator calls connect_radio later), so we also defer-start on the
        # RadioConnected event below.
        if self.radio is not None and self.radio.supports_native_rx_audio:
            await self._start_native_rx_capture()
            return
        if self.radio is not None and self._radio_can_have_native_rx():
            # Wait for connect — we'll bring the stream up then.
            await self._subscribe_for_native_rx_on_connect()
            return
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
                # Cheap RMS dBFS — gives the operator a one-glance "is audio
                # flowing?" answer when the transcript feed stays empty.
                try:
                    rms = float(np.sqrt(np.mean(clipped.astype(np.float32) ** 2)) + 1e-12)
                    dbfs = 20.0 * float(np.log10(rms))
                    self.status.rx_rms_dbfs = dbfs
                except Exception:
                    pass
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

    def _radio_can_have_native_rx(self) -> bool:
        """True if the configured radio backend *will* support native RX once
        connected, even if it currently doesn't (radio not connected yet)."""
        # FlexLibRadioClient is the only one that delivers native RX today,
        # and it advertises supports_native_rx_audio once Radio is non-None
        # (post-connect). Identify the class by name to avoid importing
        # FlexLibRadioClient here — that import drags in pythonnet.
        return type(self.radio).__name__ == "FlexLibRadioClient"

    def _radio_can_have_native_tx(self) -> bool:
        """Mirror of ``_radio_can_have_native_rx`` for the TX direction."""
        return type(self.radio).__name__ == "FlexLibRadioClient"

    async def _subscribe_for_native_rx_on_connect(self) -> None:
        """Bring the native RX stream up the moment the radio reports connected.

        Idempotent: once the stream is open we ignore further connect events.
        """
        if self._rx_connect_sub is not None:
            return
        from ..events import RadioConnected

        async def on_connect(ev) -> None:  # noqa: ANN001
            if not isinstance(ev, RadioConnected):
                return
            if self.radio is None or not self.radio.supports_native_rx_audio:
                return
            # Mirror TX availability into the status now that the FlexLib TX
            # stream is also open (radio.connect builds it).
            if self.radio.supports_native_tx_audio:
                self.status.tx_playback = "ok"
            # _start_native_rx_capture sets rx_capture="ok" on success and
            # rx_capture="native rx start failed: ..." on failure. Either way
            # the dashboard learns about it via _publish_status_throttled().
            await self._start_native_rx_capture()
            await self._publish_status()

        self.status.rx_capture = "waiting for radio.connect"
        self._rx_connect_sub = await self.bus.subscribe(
            "radio.connected", on_connect, name="pipeline.native_rx_on_connect"
        )

    async def _start_native_rx_capture(self) -> None:
        """Subscribe to the radio's native DAX RX audio stream (FlexLib).

        FlexLib delivers 24 kHz mono float32 frames on its UDP worker thread
        via the registered handler. We downsample 24 → 16 kHz, convert to
        int16, frame-align to the VAD's expected size, and push onto the same
        async queue the sounddevice path uses.
        """
        if self.radio is None or self._vad is None:
            return

        # numpy is required for the downsample + format conversion. It's part
        # of the audio extras and pythonnet pulls it in via flexlib_client too.
        try:
            import numpy as np  # type: ignore[import-not-found]
        except Exception as e:
            self.status.rx_capture = f"numpy not installed: {e}"
            return

        sample_rate_in = 24000  # FlexLib DAX RX wire format
        sample_rate_out = 16000  # webrtcvad + faster-whisper input
        frame_ms = self.settings.audio.frame_ms
        frame_bytes_out = int(sample_rate_out * 2 * frame_ms / 1000)

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)

        # Diagnostic: count handler invocations from inside the closure so we
        # can tell whether DataReady fires our handler but the enqueue path
        # silently drops frames, vs. DataReady never reaching us at all.
        rx_call_count = [0]
        first_call_noted = [False]

        def rx_handler(samples, _sr) -> None:  # noqa: ANN001
            # Runs on FlexLib's UDP worker thread. Keep work minimal.
            rx_call_count[0] += 1
            if not first_call_noted[0]:
                first_call_noted[0] = True
                self.status.notes.append(
                    f"native_rx_handler_first_call (samples={samples.size})"
                )
                log.info("audio_pipeline.native_rx_handler_first_call", samples=samples.size)
            if samples.size == 0:
                return
            clipped = np.clip(samples, -1.0, 1.0)
            try:
                rms = float(np.sqrt(np.mean(clipped.astype(np.float32) ** 2)) + 1e-12)
                self.status.rx_rms_dbfs = 20.0 * float(np.log10(rms))
            except Exception:
                pass
            # Linear-decimate 24 → 16 kHz (ratio 1.5). Take every 3rd sample
            # of an upsample-by-2 view: equivalent to picking samples at
            # indices [0, 1.5, 3.0, 4.5, ...] linearly interpolated. Cheap
            # and adequate for speech-bandwidth audio.
            ratio = sample_rate_in / sample_rate_out
            out_len = int(clipped.size / ratio)
            if out_len == 0:
                return
            idx_f = np.arange(out_len, dtype=np.float32) * ratio
            idx = idx_f.astype(np.int64)
            frac = (idx_f - idx).astype(np.float32)
            idx_next = np.minimum(idx + 1, clipped.size - 1)
            decimated = (
                clipped[idx].astype(np.float32) * (1.0 - frac)
                + clipped[idx_next].astype(np.float32) * frac
            )
            pcm = (decimated * 32767.0).astype(np.int16).tobytes()
            loop.call_soon_threadsafe(self._enqueue_frame, queue, pcm, frame_bytes_out)

        self.radio.set_rx_audio_handler(rx_handler)
        try:
            await self.radio.start_rx_audio(channel=1)
        except Exception as e:
            log.exception("audio_pipeline.native_rx_start_failed")
            self.status.rx_capture = f"native rx start failed: {e}"
            return

        self.status.rx_capture = "ok"
        self.status.rx_device = "FlexLib DAX RX 1 (UDP stream)"
        self._capture_loop_task = asyncio.create_task(
            self._capture_loop(queue, frame_bytes_out), name="audio_pipeline.capture"
        )

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
                self.status.frames_seen += 1
                # First frame: publish immediately (bypass throttle) so the
                # dashboard sees "capture is alive" within a sample-block of
                # SmartSDR's DAX delivering audio. Then heartbeat every ~1 s
                # for a live RMS gauge. (1 frame = 20 ms → 50 frames/s.)
                if self.status.frames_seen == 1:
                    await self._publish_status()
                    self._last_status_publish = asyncio.get_running_loop().time()
                elif self.status.frames_seen % 50 == 0:
                    await self._publish_status_throttled()
                if self._vad is None:
                    continue
                try:
                    utt = await self._vad.push_frame(frame)
                except Exception:
                    log.exception("audio_pipeline.vad_error")
                    continue
                # Mirror the VAD's running voice-frame count into the status
                # snapshot. Cheap (a single attribute read) and lets the
                # dashboard show "voice_frames=0" when webrtcvad is rejecting
                # all of the audio.
                self.status.voice_frames = self._vad.voice_frame_count
                if utt is not None and self.transcriber is not None:
                    self.status.stt_attempts += 1
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
