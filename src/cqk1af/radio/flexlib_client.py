"""FlexLib radio client (pythonnet wrapper around ``FlexLib.dll``).

SmartSDR v4 dropped the Windows "DAX TX" audio device shim, so audio written
to that device no longer reaches the modulator. This client uses FlexLib's
managed API to create a DAX TX audio stream over the network (the same
VITA-49 transport DAX.exe uses) and writes Piper PCM directly into it.

The FlexLib API contract (cross-checked against ``FlexLib_Core/Radio.cs``):

  1. ``Radio.DAXOn = True``                  # sends ``transmit set dax=1``
  2. ``Radio.RequestDAXTXAudioStream()``     # sends ``stream create type=dax_tx``
  3. Wait on the ``DAXTXAudioStreamAdded`` event; receive ``DAXTXAudioStream``.
  4. ``stream.Transmit = True``              # gates AddTXData
  5. ``stream.AddTXData(float[] stereo)``    # 128 samples × 2 = 256 floats per call, 24 kHz
  6. ``stream.Close()`` on disconnect.

Activation requirements:
  * **DAX.exe TX channel must be DISABLED.** When DAX.exe has its TX channel
    enabled and bound to a slice, the radio routes audio from DAX.exe's
    stream and silently drops packets from any other DAX TX stream — two
    clients cannot share the DAX TX path. This is the most common cause of
    "stream created, packets sent, zero modulation". Disable it in the
    DAX.exe UI before running CQK1AF with the FlexLib backend.
  * DAX.exe doesn't need to be running for this backend — we talk directly
    to the radio over VITA UDP. The Windows "DAX TX" audio device is unused.
  * MicLevel and RFPower must be non-zero on the radio (configurable in
    SmartSDR — same as for physical mic TX).

This is loaded lazily so unit tests can import this module without pythonnet
or the DLLs present. MockRadioClient remains the default for mock_mode.
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

from ..config import RigMode
from ..events import (
    EventBus,
    PttChanged,
    RadioConnected,
    RadioDisconnected,
    SliceUpdated,
    SMeterReading,
)
from ..util.logging import get_logger
from .base import GateToken, KILL_TOKEN, RadioClient, TxNotPermitted
from .slice_model import RadioState, SliceState

log = get_logger(__name__)


# Stream config — all hardcoded by FlexLib's DAX TX wire format.
DAX_TX_SAMPLE_RATE = 24000
DAX_TX_SAMPLES_PER_PACKET = 128  # one stereo frame per AddTXData call
DAX_TX_STEREO_FLOATS_PER_PACKET = DAX_TX_SAMPLES_PER_PACKET * 2  # 256


class FlexLibRadioClient(RadioClient):
    """Real FlexRadio control via FlexLib.dll, including DAX TX audio."""

    def __init__(
        self,
        bus: EventBus,
        flexlib_dir: Path,
        *,
        target_serial: str | None = None,
    ) -> None:
        self.bus = bus
        self.flexlib_dir = flexlib_dir
        self.target_serial = target_serial
        self._state = RadioState()
        self._radio: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._interlock = None  # type: ignore[assignment]
        self._connect_lock = asyncio.Lock()
        self._tx_lock = asyncio.Lock()
        # The TX stream is created asynchronously by FlexLib via an event
        # handler that fires on FlexLib's UDP worker thread. We block the
        # connect() flow on this until it arrives.
        self._tx_stream: Any = None
        self._tx_stream_ready = threading.Event()
        # Cached numpy for the resample / float-conversion hot path.
        self._np: Any = None

    def bind_interlock(self, interlock) -> None:  # type: ignore[no-untyped-def]
        self._interlock = interlock

    @property
    def state(self) -> RadioState:
        return self._state

    @property
    def supports_native_tx_audio(self) -> bool:
        return self._tx_stream is not None

    # ---- lifecycle --------------------------------------------------

    async def connect(self) -> None:
        async with self._connect_lock:
            if self._state.connected:
                return
            self._loop = asyncio.get_running_loop()
            await self._loop.run_in_executor(None, self._load_flexlib_blocking)
            await self.bus.publish(
                RadioConnected(
                    model=self._state.model,
                    version=self._state.version,
                    serial=self._state.serial,
                )
            )

    async def disconnect(self) -> None:
        async with self._connect_lock:
            if not self._state.connected:
                return
            try:
                self.force_ptt_off()
                # Tear down the TX stream before disconnecting so the radio
                # frees the stream id and doesn't leak a daxtx slot.
                if self._tx_stream is not None:
                    try:
                        self._tx_stream.RequestTX(False)  # yield TX ownership
                    except Exception:
                        log.exception("flexlib.tx_stream_yield_failed")
                    try:
                        self._tx_stream.Close()
                    except Exception:
                        log.exception("flexlib.tx_stream_close_failed")
                    self._tx_stream = None
                    self._state.tx_stream_id = None
                if self._radio is not None:
                    try:
                        self._radio.Disconnect()  # type: ignore[attr-defined]
                    except Exception:
                        log.exception("flexlib.disconnect_failed")
            finally:
                self._state.connected = False
                await self.bus.publish(RadioDisconnected(reason="requested"))

    # ---- pythonnet bootstrap ---------------------------------------

    def _load_flexlib_blocking(self) -> None:
        """Initialise the CLR, load FlexLib.dll, connect, and open the DAX TX stream."""
        # FlexLib v4 targets net8.0-windows, which requires the coreclr
        # runtime. pythonnet's default on Windows is .NET Framework — switch
        # explicitly before the first ``import clr``. Safe to call repeatedly:
        # pythonnet.load no-ops if a runtime is already loaded.
        from pythonnet import load as _pn_load  # type: ignore[import-not-found]
        try:
            _pn_load("coreclr")
        except Exception:
            pass  # already loaded
        import clr  # type: ignore[import-not-found]
        import numpy as np  # noqa: F401  (cached for add_tx_audio)

        self._np = np

        # pythonnet's AddReference needs absolute paths.
        flex_dir_abs = self.flexlib_dir.resolve()
        for dll in ("FlexLib", "Util", "Vita", "Flex.UiWpfFramework"):
            dll_path = flex_dir_abs / f"{dll}.dll"
            if dll_path.exists():
                clr.AddReference(str(dll_path))
                log.info("flexlib.dll_loaded", name=dll)

        from Flex.Smoothlake.FlexLib import API  # type: ignore[import-not-found]

        # ProgramName must be set before Init() / Connect() in v4 — Radio.Connect
        # calls StartKeepAlive() which throws "ProgramName not set" otherwise.
        # IsGUI=False means we're a non-GUI client so SmartSDR.exe can keep its
        # session; multiple non-GUI clients can coexist on one radio.
        if not getattr(API, "ProgramName", None):
            API.ProgramName = "cqk1af"
        # IsGUI=True registers our app as a GUI client so it can own a DAX TX
        # stream. With IsGUI=False the stream gets reassigned and our packets
        # are silently dropped.
        API.IsGUI = True
        API.Init()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if list(API.RadioList):
                break
            time.sleep(0.1)
        radios = list(API.RadioList)
        if not radios:
            raise RuntimeError("No FlexRadio discovered on the network")
        chosen = radios[0]
        if self.target_serial:
            for r in radios:
                if str(r.Serial) == self.target_serial:
                    chosen = r
                    break

        chosen.Connect()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not list(chosen.SliceList):
            time.sleep(0.1)

        self._radio = chosen
        self._state.connected = True
        self._state.model = str(chosen.Model)
        self._state.version = str(chosen.Version)
        self._state.serial = str(chosen.Serial)
        for s in list(chosen.SliceList):
            self._state.slices[int(s.Index)] = SliceState(
                slice_id=int(s.Index),
                freq_hz=int(s.Freq * 1_000_000),
                mode=str(s.DemodMode),
                filter_low_hz=int(s.FilterLow),
                filter_high_hz=int(s.FilterHigh),
            )

        # Stand up the DAX TX audio stream. Order matters: DAXOn must be set
        # before the stream is requested so the radio's TX path is ready to
        # consume audio from it.
        self._open_dax_tx_stream_blocking(chosen)

    def _open_dax_tx_stream_blocking(self, radio: Any) -> None:
        """Subscribe to DAXTXAudioStreamAdded, request the stream, wait for it.

        FlexLib creates the stream asynchronously: it sends ``stream create
        type=dax_tx``, the radio answers with a status update, and FlexLib
        fires ``DAXTXAudioStreamAdded`` on its UDP receive thread with the
        constructed ``DAXTXAudioStream`` object. We block the calling thread
        on a ``threading.Event`` until that fires (or time out).
        """
        self._tx_stream_ready.clear()
        self._tx_stream = None

        def _on_stream_added(stream: Any) -> None:
            # Runs on FlexLib's UDP worker thread.
            self._tx_stream = stream
            try:
                # Local gate — purely client-side state, doesn't reach the radio.
                stream.Transmit = True
                # Actually claim TX ownership of this stream over the wire.
                # In v4 multiFLEX, multiple clients can hold a DAX TX stream
                # but only one can be the active transmitter at a time. The
                # radio drops audio packets from streams that haven't called
                # ``stream set 0x<id> tx=1`` — which is what RequestTX does.
                # Without this, AddTXData succeeds locally and packets go on
                # the wire, but the radio ignores them: PTT keys with silence.
                stream.RequestTX(True)
            except Exception:
                log.exception("flexlib.tx_stream_enable_failed")
            self._tx_stream_ready.set()

        radio.DAXTXAudioStreamAdded += _on_stream_added
        try:
            radio.DAXOn = True
            radio.RequestDAXTXAudioStream()
            if not self._tx_stream_ready.wait(timeout=5.0):
                raise RuntimeError(
                    "Timed out waiting for DAXTXAudioStreamAdded. Is DAX.exe "
                    "running and connected to the radio?"
                )
        finally:
            # Detach so we don't double-bind on reconnect.
            try:
                radio.DAXTXAudioStreamAdded -= _on_stream_added
            except Exception:
                pass

        if self._tx_stream is None:
            raise RuntimeError("DAX TX stream creation reported success but stream is None")
        self._state.tx_stream_id = int(self._tx_stream.TXStreamID)
        log.info("flexlib.tx_stream_ready", stream_id=self._state.tx_stream_id)

    # ---- slice control ---------------------------------------------

    def _require_radio(self) -> Any:
        if self._radio is None:
            raise RuntimeError("Radio not connected")
        return self._radio

    async def list_slices(self) -> list[SliceState]:
        return list(self._state.slices.values())

    async def tune(self, slice_id: int, freq_hz: int) -> SliceState:
        r = self._require_radio()
        loop = asyncio.get_running_loop()

        def _do() -> None:
            r.SliceList[slice_id].Freq = freq_hz / 1_000_000.0

        await loop.run_in_executor(None, _do)
        s = self._state.slices[slice_id]
        s.freq_hz = freq_hz
        await self._publish_slice(s)
        return s

    async def set_mode(self, slice_id: int, mode: RigMode) -> SliceState:
        r = self._require_radio()
        loop = asyncio.get_running_loop()

        def _do() -> None:
            r.SliceList[slice_id].DemodMode = mode

        await loop.run_in_executor(None, _do)
        s = self._state.slices[slice_id]
        s.mode = mode
        await self._publish_slice(s)
        return s

    async def set_filter(self, slice_id: int, low_hz: int, high_hz: int) -> SliceState:
        r = self._require_radio()
        loop = asyncio.get_running_loop()

        def _do() -> None:
            r.SliceList[slice_id].FilterLow = low_hz
            r.SliceList[slice_id].FilterHigh = high_hz

        await loop.run_in_executor(None, _do)
        s = self._state.slices[slice_id]
        s.filter_low_hz = low_hz
        s.filter_high_hz = high_hz
        await self._publish_slice(s)
        return s

    async def read_s_meter(self, slice_id: int) -> float:
        r = self._require_radio()
        loop = asyncio.get_running_loop()

        def _do() -> float:
            return float(r.SliceList[slice_id].SMeter)

        dbm = await loop.run_in_executor(None, _do)
        self._state.slices[slice_id].s_meter_dbm = dbm
        await self.bus.publish(SMeterReading(slice_id=slice_id, dbm=dbm))
        return dbm

    # ---- TX --------------------------------------------------------

    async def set_ptt(self, on: bool, *, gate_token: GateToken | None = None) -> None:
        if on:
            if gate_token is None or gate_token is KILL_TOKEN:
                raise TxNotPermitted("set_ptt(True) requires a GateToken from the interlock")
            if gate_token.is_expired():
                raise TxNotPermitted("GateToken expired")
            if self._interlock is not None and not self._interlock.is_token_valid(gate_token):
                raise TxNotPermitted("GateToken not issued or already consumed")
            if self._interlock is not None:
                self._interlock.consume_token(gate_token)
        r = self._require_radio()
        loop = asyncio.get_running_loop()

        def _do() -> None:
            r.Mox = bool(on)

        await loop.run_in_executor(None, _do)
        self._state.ptt_on = on
        await self.bus.publish(PttChanged(on=on))

    def force_ptt_off(self) -> None:
        """Synchronous .NET call. Safe from any context. The kill path uses this."""
        if self._radio is None:
            return
        try:
            self._radio.Mox = False
        except Exception:
            log.exception("flexlib.force_ptt_off_failed")
        self._state.ptt_on = False
        self.bus.publish_nowait(PttChanged(on=False))

    async def add_tx_audio(self, pcm: bytes, sample_rate: int) -> None:
        """Push int16 mono PCM into the DAX TX stream.

        Converts to 24 kHz mono float32 → stereo-interleaved (L=R=mono) →
        chunks of 128 stereo frames (256 floats) → one ``AddTXData`` call
        per chunk, paced so we don't outrun the radio's UDP buffer.
        """
        if self._tx_stream is None:
            raise RuntimeError("DAX TX stream not open; FlexLib client not fully connected")
        np = self._np
        if np is None:
            import numpy as np  # type: ignore[import-not-found]
            self._np = np

        # int16 LE → float32 in [-1, 1]
        mono_i16 = np.frombuffer(pcm, dtype=np.int16)
        if mono_i16.size == 0:
            return
        mono_f = mono_i16.astype(np.float32) / 32768.0

        # Linear resample to 24 kHz (matches LinearResampler.cs in smartsdr-mcp).
        if sample_rate != DAX_TX_SAMPLE_RATE:
            ratio = sample_rate / DAX_TX_SAMPLE_RATE
            out_len = int(mono_f.size / ratio)
            src_idx_f = np.arange(out_len) * ratio
            idx = src_idx_f.astype(np.int64)
            frac = (src_idx_f - idx).astype(np.float32)
            idx_next = np.minimum(idx + 1, mono_f.size - 1)
            mono_f = mono_f[idx] * (1.0 - frac) + mono_f[idx_next] * frac

        # Mono → stereo interleaved: [L0, R0, L1, R1, ...]. FlexLib's AddTXData
        # wants stereo even though DAX TX is mono on the radio side; it
        # deinterleaves L and uses that channel.
        stereo = np.empty(mono_f.size * 2, dtype=np.float32)
        stereo[0::2] = mono_f
        stereo[1::2] = mono_f

        # Pad final chunk with silence so we always emit complete frames.
        n_complete = (stereo.size // DAX_TX_STEREO_FLOATS_PER_PACKET) * DAX_TX_STEREO_FLOATS_PER_PACKET
        remainder = stereo.size - n_complete
        if remainder > 0:
            pad = np.zeros(DAX_TX_STEREO_FLOATS_PER_PACKET - remainder, dtype=np.float32)
            stereo = np.concatenate([stereo, pad])

        n_packets = stereo.size // DAX_TX_STEREO_FLOATS_PER_PACKET
        packet_seconds = DAX_TX_SAMPLES_PER_PACKET / DAX_TX_SAMPLE_RATE  # 5.333 ms

        loop = asyncio.get_running_loop()
        async with self._tx_lock:
            # We send all packets back-to-back to the radio's UDP buffer, but
            # pace ourselves to roughly real time so a 5-second utterance
            # doesn't dump 940 packets in one tick. FlexLib doesn't backpressure.
            sent = 0
            for i in range(n_packets):
                chunk = stereo[
                    i * DAX_TX_STEREO_FLOATS_PER_PACKET : (i + 1) * DAX_TX_STEREO_FLOATS_PER_PACKET
                ]
                await loop.run_in_executor(None, self._send_chunk_blocking, chunk)
                sent += 1
                # Pace every ~10 packets (~53 ms of audio). Lets the loop
                # process other tasks (status updates, S-meter, kill switch)
                # while we stream.
                if sent % 10 == 0:
                    await asyncio.sleep(packet_seconds * 10 * 0.9)
            log.info(
                "flexlib.tx_audio_sent",
                packets=n_packets,
                seconds=n_packets * packet_seconds,
            )

    def _send_chunk_blocking(self, chunk: Any) -> None:
        """Marshal a numpy float32 array to System.Single[] and call AddTXData.

        Sends in reduced-BW mode because FlexLib's Connect sends
        ``client set send_reduced_bw_dax=1`` and the radio drops full-BW
        packets after that. tolist() avoids pythonnet 3.0.5's inability to
        marshal numpy float32 ndarray → System.Single[] directly.
        """
        self._tx_stream.AddTXData(chunk.tolist(), True)

    # ---- internals -------------------------------------------------

    async def _publish_slice(self, s: SliceState) -> None:
        await self.bus.publish(
            SliceUpdated(
                slice_id=s.slice_id,
                freq_hz=s.freq_hz,
                mode=s.mode,
                filter_low_hz=s.filter_low_hz,
                filter_high_hz=s.filter_high_hz,
                rxant=s.rxant,
            )
        )
