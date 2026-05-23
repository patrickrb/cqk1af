from __future__ import annotations

from cqk1af.nlp.pileup_probe import build_probe, has_wildcards, with_operator_id


def test_has_wildcards() -> None:
    assert not has_wildcards("K1AF")
    assert has_wildcards("K1A?")
    assert not has_wildcards("")


def test_all_unknown_returns_full_call_again() -> None:
    p = build_probe("????")
    assert p.pattern == "all_unknown"
    assert "full callsign" in p.text.lower()


def test_suffix_entirely_unknown() -> None:
    p = build_probe("K1???")
    assert p.pattern == "suffix_unknown"
    assert "kilo one" in p.text.lower()
    assert "suffix" in p.text.lower()


def test_suffix_unknown_shorter_suffix() -> None:
    p = build_probe("K1??")
    assert p.pattern == "suffix_unknown"
    assert "kilo one" in p.text.lower()
    assert "suffix" in p.text.lower()


def test_prefix_entirely_unknown() -> None:
    p = build_probe("??1AF")
    assert p.pattern == "prefix_unknown"
    assert "alpha foxtrot" in p.text.lower()
    assert "prefix" in p.text.lower()


def test_number_unknown() -> None:
    p = build_probe("K?AF")
    assert p.pattern == "number_unknown"
    assert "kilo" in p.text.lower()
    assert "alpha foxtrot" in p.text.lower()
    assert "number" in p.text.lower()


def test_single_trailing_wildcard() -> None:
    p = build_probe("K1AF?")
    assert p.pattern == "trailing_one"
    assert "what?" in p.text.lower()
    assert "kilo one alpha foxtrot" in p.text.lower()


def test_single_leading_wildcard() -> None:
    # Real world: copied suffix and number but missing first letter of prefix.
    p = build_probe("?A1AF")
    # Could be parsed either as embedded or leading depending on split. Both are
    # acceptable phrasings; assert behavior on the produced text.
    assert "what" in p.text.lower()
    assert "alpha foxtrot" in p.text.lower()


def test_single_embedded_wildcard() -> None:
    p = build_probe("K1?F")
    assert p.pattern in {"embedded_one", "trailing_one"}
    assert "what" in p.text.lower()
    assert "foxtrot" in p.text.lower()


def test_multi_gap_fallback_includes_known_chars() -> None:
    # Two non-adjacent wildcards in distinct regions — fall through to fallback.
    p = build_probe("?1A?")
    assert p.pattern == "multi_gap"
    # Should mention the parts we did copy.
    assert "one" in p.text.lower() or "alpha" in p.text.lower()


def test_no_wildcard_returns_qrz_form() -> None:
    p = build_probe("K1AF")
    assert p.pattern == "no_wildcard"
    assert "k1af".lower() not in p.text.lower()  # rendered phonetically
    assert "kilo one alpha foxtrot" in p.text.lower()


def test_empty_call() -> None:
    p = build_probe("")
    assert p.pattern == "empty"


def test_with_operator_id_appended() -> None:
    out = with_operator_id("kilo one alpha what?", "K1AF")
    assert out.endswith("K1AF.")
    assert "kilo one alpha what?" in out


def test_with_operator_id_no_op_callsign_passthrough() -> None:
    out = with_operator_id("kilo one alpha what?", "")
    assert out == "kilo one alpha what?"


def test_lowercase_input_normalised() -> None:
    p = build_probe("k1??")
    assert p.pattern == "suffix_unknown"
    assert "kilo one" in p.text.lower()
