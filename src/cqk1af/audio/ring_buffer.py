"""Lock-free-enough circular byte buffer for audio pre-roll.

A producer (the audio capture callback, running on a sounddevice thread) writes
frames; a consumer (the VAD/utterance assembler, running on the asyncio loop)
reads them. Overrun drops the oldest data, never blocks.

Sizes are in bytes. Convenience methods convert PCM 16-bit mono samples.
"""
from __future__ import annotations

import threading


class RingBuffer:
    def __init__(self, capacity_bytes: int) -> None:
        if capacity_bytes <= 0:
            raise ValueError("capacity must be positive")
        self._cap = capacity_bytes
        self._buf = bytearray(capacity_bytes)
        self._read = 0
        self._write = 0
        self._fill = 0
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._cap

    @property
    def size(self) -> int:
        with self._lock:
            return self._fill

    def clear(self) -> None:
        with self._lock:
            self._read = 0
            self._write = 0
            self._fill = 0

    def write(self, data: bytes) -> int:
        """Write data. If it would overflow, drop the oldest. Returns bytes overwritten."""
        n = len(data)
        if n == 0:
            return 0
        if n > self._cap:
            # Only the last `cap` bytes survive.
            data = data[-self._cap :]
            n = self._cap

        with self._lock:
            overflow = max(0, (self._fill + n) - self._cap)
            # Write data into buffer (handle wraparound)
            end = self._write + n
            if end <= self._cap:
                self._buf[self._write : end] = data
            else:
                first = self._cap - self._write
                self._buf[self._write :] = data[:first]
                self._buf[: n - first] = data[first:]
            self._write = (self._write + n) % self._cap
            if overflow:
                self._read = (self._read + overflow) % self._cap
                self._fill = self._cap
            else:
                self._fill += n
            return overflow

    def read(self, max_bytes: int) -> bytes:
        with self._lock:
            n = min(max_bytes, self._fill)
            if n == 0:
                return b""
            end = self._read + n
            if end <= self._cap:
                out = bytes(self._buf[self._read : end])
            else:
                first = self._cap - self._read
                out = bytes(self._buf[self._read :]) + bytes(self._buf[: n - first])
            self._read = (self._read + n) % self._cap
            self._fill -= n
            return out

    def peek(self, max_bytes: int) -> bytes:
        with self._lock:
            n = min(max_bytes, self._fill)
            if n == 0:
                return b""
            end = self._read + n
            if end <= self._cap:
                return bytes(self._buf[self._read : end])
            first = self._cap - self._read
            return bytes(self._buf[self._read :]) + bytes(self._buf[: n - first])

    def drain(self) -> bytes:
        return self.read(self.size)
