from __future__ import annotations

import pytest

from cqk1af.nlp.callsign_extractor import extract_callsigns


def callsigns(text: str) -> list[str]:
    return [c.callsign for c in extract_callsigns(text)]


def test_literal_callsign() -> None:
    out = extract_callsigns("This is W1AW calling CQ")
    assert out
    assert out[0].callsign == "W1AW"
    assert out[0].method == "literal"


def test_nato_phonetic() -> None:
    out = extract_callsigns("This is kilo one alpha foxtrot, going to you")
    cs = [c.callsign for c in out]
    assert "K1AF" in cs


def test_phonetic_with_alfa_spelling() -> None:
    out = extract_callsigns("whiskey one alfa whiskey")
    cs = [c.callsign for c in out]
    assert "W1AW" in cs


def test_portable_suffix_in_literal() -> None:
    out = extract_callsigns("W1AW/M is portable mobile")
    cs = [c.callsign for c in out]
    assert "W1AW/M" in cs


def test_lowercase_callsign_normalised() -> None:
    out = extract_callsigns("hello w1aw")
    cs = [c.callsign for c in out]
    assert "W1AW" in cs


def test_invalid_shape_rejected() -> None:
    out = callsigns("the team scored 12 to 7")
    assert "12" not in out
    assert all(len(c) >= 4 for c in out)


def test_phonetic_with_digits_words() -> None:
    out = extract_callsigns("kilo niner x-ray yankee zulu, fife five")
    cs = [c.callsign for c in out]
    assert any(c.startswith("K9XYZ") for c in cs)


def test_multiple_callsigns_in_one_transmission() -> None:
    out = extract_callsigns("W1AW this is K9XYZ over")
    cs = [c.callsign for c in out]
    assert "W1AW" in cs
    assert "K9XYZ" in cs


def test_ranking_literal_above_phonetic() -> None:
    out = extract_callsigns("kilo one alpha foxtrot, this is W1AW")
    # W1AW (literal) should outrank K1AF (phonetic) due to base confidence
    assert out[0].callsign == "W1AW"


def test_unknown_prefix_still_extracted_with_lower_confidence() -> None:
    out = extract_callsigns("ZX5YYY here")
    assert out
    assert out[0].callsign == "ZX5YYY"
    # Unknown prefix => slightly lower base
    assert out[0].confidence < 0.95


def test_pure_noise_returns_empty() -> None:
    assert extract_callsigns("hello, how are you today?") == []
