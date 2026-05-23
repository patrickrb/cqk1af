from __future__ import annotations

import pytest

from cqk1af.nlp.lingo_corrector import correct_lingo
from tests.fixtures.golden_transcripts import GOLDEN_PAIRS


@pytest.mark.parametrize("raw, expected", GOLDEN_PAIRS)
def test_golden_pairs(raw: str, expected: str) -> None:
    assert correct_lingo(raw) == expected


def test_empty_string_unchanged() -> None:
    assert correct_lingo("") == ""


def test_nato_words_untouched() -> None:
    # The callsign extractor's phonetic pass depends on the NATO words
    # arriving verbatim — over-correction would break callsign extraction.
    text = "alpha bravo charlie delta echo foxtrot golf hotel"
    assert correct_lingo(text) == text


def test_unknown_q_letters_left_alone() -> None:
    # "Q. A. B." is not a real Q-code; corrector must not invent one.
    assert correct_lingo("Q. A. B. random") == "Q. A. B. random"


@pytest.mark.parametrize("raw, expected", GOLDEN_PAIRS)
def test_idempotent(raw: str, expected: str) -> None:
    once = correct_lingo(raw)
    twice = correct_lingo(once)
    assert once == twice == expected


def test_letter_prefix_collapse_feeds_callsign_extractor() -> None:
    # End-to-end: the rewrite must produce text the literal callsign
    # extractor accepts. This is the whole point of the rule — Whisper
    # mishears "N4WF" as "In 4WF", and downstream we want N4WF to
    # appear in `pileup` candidates as if Whisper had transcribed it
    # correctly.
    from cqk1af.nlp.callsign_extractor import extract_callsigns

    corrected = correct_lingo("calling In 4WF, In 4WF over")
    assert "N4WF" in corrected
    cands = [c.callsign for c in extract_callsigns(corrected)]
    assert "N4WF" in cands
