from __future__ import annotations

from cqk1af.tts.phonetic import phonetic_callsign, render_cq_template, render_freq_check


def test_callsign_basic() -> None:
    assert phonetic_callsign("K1AF") == "kilo one alpha foxtrot"


def test_callsign_lowercase_normalised() -> None:
    assert phonetic_callsign("k1af") == "kilo one alpha foxtrot"


def test_callsign_with_portable_suffix() -> None:
    assert phonetic_callsign("W1AW/M") == "whiskey one alpha whiskey stroke mike"


def test_cq_template_substitution() -> None:
    out = render_cq_template(
        "CQ CQ CQ, this is {callsign_phonetic}, {callsign_phonetic}, calling CQ.",
        callsign="K1AF",
    )
    assert "kilo one alpha foxtrot" in out
    assert out.count("kilo one alpha foxtrot") == 2


def test_cq_template_with_literal_callsign() -> None:
    out = render_cq_template("Hi this is {callsign}, K1AF over.", callsign="K1AF")
    # {callsign} replaced; literal "K1AF" later in template stays
    assert out.count("K1AF") == 2


def test_freq_check_attempt_one_no_call() -> None:
    out = render_freq_check("K1AF", attempt=1)
    assert "K1AF" not in out
    assert "in use" in out


def test_freq_check_attempt_two_includes_phonetic() -> None:
    out = render_freq_check("K1AF", attempt=2)
    assert "kilo one alpha foxtrot" in out
