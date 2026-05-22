"""Unit tests for FlexLibRadioClient.

These tests don't import pythonnet or load FlexLib — they manually wire up a
FlexLibRadioClient instance with fake .NET objects (mock_stream, mock_radio)
and exercise the Python-side logic: stream activation handshake, add_tx_audio
resample / interleave / chunking, and disconnect tear-down.

The real .NET interop is only validated at runtime via scripts/probe_flexlib_tx.py.
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from cqk1af.events import EventBus
from cqk1af.radio.flexlib_client import (
    DAX_TX_SAMPLE_RATE,
    DAX_TX_SAMPLES_PER_PACKET,
    DAX_TX_STEREO_FLOATS_PER_PACKET,
    FlexLibRadioClient,
)


# ---- helpers -------------------------------------------------------


def _make_client_with_stream(stream: Any) -> FlexLibRadioClient:
    """Build a client with the .NET pieces faked out, ready for add_tx_audio."""
    client = FlexLibRadioClient(EventBus(), Path("runtime/flexlib"))
    client._np = np
    client._tx_stream = stream
    client._radio = MagicMock()
    client._state.connected = True
    return client


class _RecordingStream:
    """Mimics enough of DAXTXAudioStream for add_tx_audio's purposes."""

    def __init__(self) -> None:
        self.TXStreamID = 0xDEADBEEF
        self.Transmit = False
        self.calls: list[tuple[np.ndarray, bool]] = []
        self.request_tx_calls: list[bool] = []
        self.closed = False

    def AddTXData(self, samples: Any, sendReducedBW: bool) -> None:
        # Capture a copy so callers can mutate their buffers without affecting us.
        arr = np.array(samples, dtype=np.float32, copy=True)
        self.calls.append((arr, sendReducedBW))

    def RequestTX(self, on: bool) -> None:
        self.request_tx_calls.append(on)

    def Close(self) -> None:
        self.closed = True


# ---- add_tx_audio: resample + chunk shape --------------------------


@pytest.mark.asyncio
async def test_add_tx_audio_emits_correct_packet_count_at_22050() -> None:
    """22050 Hz mono → resampled to 24000 → 128-sample stereo packets."""
    stream = _RecordingStream()
    client = _make_client_with_stream(stream)

    # 1 second of Piper-rate audio = 22050 samples
    pcm = (np.zeros(22050, dtype=np.int16)).tobytes()
    await client.add_tx_audio(pcm, 22050)

    # After resample to 24 kHz: 24000 samples. Stereo = 48000 floats.
    # Chunks of 256 floats → 48000 / 256 = 187.5 → 188 with one padded chunk.
    expected_min = 24000 // DAX_TX_SAMPLES_PER_PACKET  # 187 complete frames
    assert len(stream.calls) in (expected_min, expected_min + 1)
    for arr, sendReducedBW in stream.calls:
        assert arr.size == DAX_TX_STEREO_FLOATS_PER_PACKET
        assert arr.dtype == np.float32
        assert sendReducedBW is False


@pytest.mark.asyncio
async def test_add_tx_audio_passthrough_at_24000_skips_resample() -> None:
    stream = _RecordingStream()
    client = _make_client_with_stream(stream)

    # Exactly 5 packets worth at native rate.
    n = DAX_TX_SAMPLES_PER_PACKET * 5
    pcm = (np.full(n, 16000, dtype=np.int16)).tobytes()
    await client.add_tx_audio(pcm, DAX_TX_SAMPLE_RATE)

    assert len(stream.calls) == 5
    # Each packet should carry our int16 value (16000) scaled to ~0.488 in both L and R.
    expected = 16000.0 / 32768.0
    for arr, _ in stream.calls:
        assert np.allclose(arr, expected, atol=1e-4)


@pytest.mark.asyncio
async def test_add_tx_audio_stereo_interleave_pattern() -> None:
    """Verify L,R,L,R,... layout: L and R should match for mono source."""
    stream = _RecordingStream()
    client = _make_client_with_stream(stream)

    # Use a distinct sample value per index so we can verify interleave order.
    n = DAX_TX_SAMPLES_PER_PACKET  # exactly 1 packet at native rate
    src = np.arange(n, dtype=np.int16) * 100  # 0, 100, 200, ...
    pcm = src.tobytes()
    await client.add_tx_audio(pcm, DAX_TX_SAMPLE_RATE)

    assert len(stream.calls) == 1
    arr, _ = stream.calls[0]
    # Left channel = even indices, Right = odd. Mono → L==R.
    left = arr[0::2]
    right = arr[1::2]
    assert np.allclose(left, right)
    # First sample should be near 0, last near n*100/32768.
    assert abs(left[0]) < 1e-6
    assert abs(left[-1] - ((n - 1) * 100 / 32768.0)) < 1e-4


@pytest.mark.asyncio
async def test_add_tx_audio_pads_final_partial_chunk_with_silence() -> None:
    stream = _RecordingStream()
    client = _make_client_with_stream(stream)

    # 1.5 packets of samples — should produce 2 packets (last half-silence).
    n = DAX_TX_SAMPLES_PER_PACKET + DAX_TX_SAMPLES_PER_PACKET // 2
    pcm = (np.full(n, 32767, dtype=np.int16)).tobytes()
    await client.add_tx_audio(pcm, DAX_TX_SAMPLE_RATE)

    assert len(stream.calls) == 2
    second = stream.calls[1][0]
    # First half of second packet = ~1.0 (max int16 scaled), second half = 0.
    half = DAX_TX_STEREO_FLOATS_PER_PACKET // 2
    assert np.all(np.abs(second[:half]) > 0.9)
    assert np.all(second[half:] == 0.0)


@pytest.mark.asyncio
async def test_add_tx_audio_empty_pcm_is_noop() -> None:
    stream = _RecordingStream()
    client = _make_client_with_stream(stream)
    await client.add_tx_audio(b"", DAX_TX_SAMPLE_RATE)
    assert stream.calls == []


@pytest.mark.asyncio
async def test_add_tx_audio_raises_when_stream_not_open() -> None:
    client = FlexLibRadioClient(EventBus(), Path("runtime/flexlib"))
    with pytest.raises(RuntimeError, match="DAX TX stream not open"):
        await client.add_tx_audio(b"\x00\x00", DAX_TX_SAMPLE_RATE)


# ---- supports_native_tx_audio gate ---------------------------------


def test_supports_native_tx_audio_false_before_connect() -> None:
    client = FlexLibRadioClient(EventBus(), Path("runtime/flexlib"))
    assert client.supports_native_tx_audio is False


def test_supports_native_tx_audio_true_when_stream_open() -> None:
    client = FlexLibRadioClient(EventBus(), Path("runtime/flexlib"))
    client._tx_stream = _RecordingStream()
    assert client.supports_native_tx_audio is True


# ---- _open_dax_tx_stream_blocking: activation handshake ------------


class _FakeRadioWithStreamEvent:
    """Fake FlexLib Radio that fires DAXTXAudioStreamAdded inline on Request."""

    def __init__(self, *, fire_event: bool = True) -> None:
        self.DAXOn = False
        self._handlers: list = []
        self._fire_event = fire_event
        self.requested = False
        self.stream = _RecordingStream()

    # pythonnet event subscription uses += / -=. Emulate that via __iadd__ on
    # an event-shaped wrapper. Easiest: expose attributes as plain lists and
    # let the client treat them like real events (the test interface matches).
    @property
    def DAXTXAudioStreamAdded(self):
        return _FakeEvent(self._handlers)

    @DAXTXAudioStreamAdded.setter
    def DAXTXAudioStreamAdded(self, value):
        # pythonnet's `+=` returns the event itself; assignment is the result.
        pass

    def RequestDAXTXAudioStream(self) -> None:
        self.requested = True
        if self._fire_event:
            # Fire on a worker thread to simulate FlexLib's UDP receive thread.
            def _fire():
                for h in list(self._handlers):
                    h(self.stream)
            t = threading.Thread(target=_fire)
            t.start()
            t.join()


class _FakeEvent:
    def __init__(self, handlers: list) -> None:
        self._handlers = handlers

    def __iadd__(self, handler):
        self._handlers.append(handler)
        return self

    def __isub__(self, handler):
        if handler in self._handlers:
            self._handlers.remove(handler)
        return self


def test_open_dax_tx_stream_blocking_succeeds_on_event() -> None:
    client = FlexLibRadioClient(EventBus(), Path("runtime/flexlib"))
    radio = _FakeRadioWithStreamEvent(fire_event=True)
    client._open_dax_tx_stream_blocking(radio)
    assert radio.DAXOn is True
    assert radio.requested is True
    assert client._tx_stream is radio.stream
    assert client._tx_stream.Transmit is True
    assert client._state.tx_stream_id == 0xDEADBEEF


def test_open_dax_tx_stream_blocking_raises_on_timeout() -> None:
    # Patch the wait timeout so the test runs fast.
    client = FlexLibRadioClient(EventBus(), Path("runtime/flexlib"))
    radio = _FakeRadioWithStreamEvent(fire_event=False)

    # Shrink the wait by monkey-patching threading.Event.wait via a subclass.
    class _FastReadyEvent:
        def __init__(self):
            self._inner = threading.Event()
        def clear(self): self._inner.clear()
        def set(self): self._inner.set()
        def wait(self, timeout=None): return self._inner.wait(timeout=0.1)

    client._tx_stream_ready = _FastReadyEvent()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="Timed out"):
        client._open_dax_tx_stream_blocking(radio)


# ---- disconnect tears down the stream ------------------------------


@pytest.mark.asyncio
async def test_disconnect_closes_stream_and_clears_state() -> None:
    stream = _RecordingStream()
    client = _make_client_with_stream(stream)
    client._state.tx_stream_id = int(stream.TXStreamID)
    # Patch radio.Disconnect so it doesn't blow up on MagicMock.
    client._radio.Disconnect = MagicMock()

    await client.disconnect()

    assert stream.closed is True
    assert client._tx_stream is None
    assert client._state.tx_stream_id is None
    assert client._state.connected is False
