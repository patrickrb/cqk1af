from __future__ import annotations

import pytest

from cqk1af.stt.hallucination import is_likely_hallucination


@pytest.mark.parametrize(
    "text",
    [
        "Thanks for watching!",
        "thank you",
        "Thanks for watching the video",
        "Please subscribe",
        "Please subscribe to my channel",
        "Like and subscribe",
        "Bye bye",
        "Goodbye",
        "you",
        "Music",
        "Sous-titres réalisés par la communauté d'Amara.org",
        "♪ ♪ ♪",
        "[Music]",
        "(applause)",
        "[BLANK_AUDIO]",
        "[Silence]",
    ],
)
def test_known_hallucinations_dropped(text: str) -> None:
    drop, reason = is_likely_hallucination(text)
    assert drop, f"expected drop for {text!r}, reason={reason}"
    assert reason


def test_thank_you_with_short_tail_dropped() -> None:
    # Common subtitle variants past the exact dictionary match.
    drop, reason = is_likely_hallucination("Thank you so much")
    assert drop
    assert "thank_you" in reason


@pytest.mark.parametrize(
    "text",
    [
        "K1AF this is W1AW you are five nine over",
        "QRZ this is kilo one alpha foxtrot",
        "CQ CQ CQ this is K1AF",
        "Five nine into Boston Massachusetts",
        # Long-form "thank you" inside a real exchange should not match the prefix
        "Thank you for the call I copy you five nine name here is Burns",
    ],
)
def test_real_qso_text_kept(text: str) -> None:
    drop, reason = is_likely_hallucination(text)
    assert not drop, f"unexpected drop {reason!r} for {text!r}"


def test_silence_signature_dropped() -> None:
    # Short text over a long utterance with high no-speech prob = classic
    # silence hallucination.
    drop, reason = is_likely_hallucination(
        "uh", no_speech_prob=0.85, audio_duration_s=5.0
    )
    assert drop
    assert reason == "short_text_no_speech"


def test_short_text_with_low_no_speech_kept() -> None:
    # Same short text but the model is confident it's speech — keep it.
    drop, _ = is_likely_hallucination(
        "uh", no_speech_prob=0.1, audio_duration_s=5.0
    )
    assert not drop


def test_empty_input_dropped() -> None:
    drop, reason = is_likely_hallucination("")
    assert drop
    assert reason == "empty"
