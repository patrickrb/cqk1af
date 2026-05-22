from __future__ import annotations

import pytest

from cqk1af.audio.ring_buffer import RingBuffer


def test_basic_read_write() -> None:
    b = RingBuffer(10)
    assert b.write(b"hello") == 0
    assert b.size == 5
    assert b.read(3) == b"hel"
    assert b.read(10) == b"lo"
    assert b.size == 0


def test_wraparound() -> None:
    b = RingBuffer(8)
    b.write(b"12345")  # fill 5/8
    assert b.read(3) == b"123"
    # Now buffer has "45" with write pointer past midpoint; add 6 more -> wrap
    b.write(b"abcdef")  # capacity is 8, but we still have 2 left = 8 in queue
    assert b.size == 8
    assert b.read(8) == b"45abcdef"


def test_overflow_drops_oldest() -> None:
    b = RingBuffer(4)
    b.write(b"AB")
    overflow = b.write(b"CDEF")  # 6 bytes into cap-4 = drops 2
    assert overflow == 2
    assert b.read(4) == b"CDEF"


def test_oversized_single_write_keeps_tail() -> None:
    b = RingBuffer(4)
    b.write(b"123456789")
    # Only last 4 should survive
    assert b.read(4) == b"6789"


def test_peek_does_not_consume() -> None:
    b = RingBuffer(8)
    b.write(b"abcdef")
    assert b.peek(3) == b"abc"
    assert b.size == 6
    assert b.read(3) == b"abc"


def test_clear() -> None:
    b = RingBuffer(8)
    b.write(b"xxxx")
    b.clear()
    assert b.size == 0
    assert b.read(8) == b""


def test_invalid_capacity() -> None:
    with pytest.raises(ValueError):
        RingBuffer(0)


def test_empty_write_is_noop() -> None:
    b = RingBuffer(4)
    assert b.write(b"") == 0
    assert b.size == 0
