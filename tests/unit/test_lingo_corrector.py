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
