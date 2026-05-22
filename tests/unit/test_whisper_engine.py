from __future__ import annotations

from dataclasses import dataclass

import pytest

from cqk1af.stt.whisper_engine import (
    TranscriptionResult,
    _aggregate_segment_scores,
)


@dataclass
class _Seg:
    start: float
    end: float
    avg_logprob: float
    no_speech_prob: float


def test_aggregate_empty_returns_pessimistic() -> None:
    lp, nsp = _aggregate_segment_scores([])
    assert lp == -1.0
    assert nsp == 1.0


def test_aggregate_single_segment_passes_through() -> None:
    seg = _Seg(start=0.0, end=2.0, avg_logprob=-0.3, no_speech_prob=0.1)
    lp, nsp = _aggregate_segment_scores([seg])
    assert lp == -0.3
    assert nsp == 0.1


def test_aggregate_weights_by_duration() -> None:
    # Short bad segment + long good segment — long should dominate.
    short_bad = _Seg(start=0.0, end=0.5, avg_logprob=-2.0, no_speech_prob=0.9)
    long_good = _Seg(start=0.5, end=5.5, avg_logprob=-0.2, no_speech_prob=0.05)
    lp, nsp = _aggregate_segment_scores([short_bad, long_good])
    # First-segment-only would give -2.0 / 0.9; weighted should be much closer
    # to the long good one.
    assert lp > -0.5, f"expected long-good to dominate, got {lp}"
    assert nsp < 0.2, f"expected low no-speech, got {nsp}"


def test_aggregate_zero_duration_segments_equal_weight() -> None:
    # Whisper sometimes emits segments with start==end; fall back to equal
    # weight rather than dividing by zero.
    a = _Seg(start=1.0, end=1.0, avg_logprob=-0.4, no_speech_prob=0.2)
    b = _Seg(start=2.0, end=2.0, avg_logprob=-0.6, no_speech_prob=0.4)
    lp, nsp = _aggregate_segment_scores([a, b])
    assert lp == pytest.approx(-0.5)
    assert nsp == pytest.approx(0.3)


def test_confidence_uses_aggregated_scores() -> None:
    # Sanity: TranscriptionResult's confidence still maps a tight clean
    # result to higher than a noisy one.
    clean = TranscriptionResult(text="hi", avg_logprob=-0.1, no_speech_prob=0.05)
    noisy = TranscriptionResult(text="hi", avg_logprob=-1.2, no_speech_prob=0.7)
    assert clean.confidence > noisy.confidence
