from __future__ import annotations

from cqk1af.stt.prompt_builder import PromptBuilder


def test_basic_build_includes_vocab() -> None:
    p = PromptBuilder()
    out = p.build(own_callsign="K1AF")
    assert "K1AF" in out
    assert "QSO" in out or "amateur" in out.lower()


def test_recent_callsigns_included_and_deduped() -> None:
    # Use a vocab string that does not include W1AW so we can count cleanly.
    p = PromptBuilder(base_vocab="placeholder vocab")
    for c in ["W1AW", "K9XYZ", "VE3ABC", "w1aw"]:
        p.remember_callsign(c)
    out = p.build()
    assert out.count("W1AW") == 1
    assert "K9XYZ" in out
    assert "VE3ABC" in out


def test_recent_limit_respected() -> None:
    p = PromptBuilder(recent_limit=3)
    for c in ["AA1A", "AA2A", "AA3A", "AA4A"]:
        p.remember_callsign(c)
    out = p.build()
    # Oldest one is dropped
    assert "AA1A" not in out
    assert "AA4A" in out


def test_prompt_bounded() -> None:
    p = PromptBuilder()
    for i in range(50):
        p.remember_callsign(f"K1A{i:03d}")
    out = p.build(extra="extra notes " * 200)
    assert len(out) <= 800


def test_operator_context_included() -> None:
    p = PromptBuilder(base_vocab="vocab")
    p.set_operator(callsign="K1AF", name="Burns", qth="Boston, MA", grid="FN42")
    out = p.build()
    assert "K1AF" in out
    assert "Burns" in out
    assert "Boston" in out
    assert "FN42" in out


def test_stage_calling_cq_hint() -> None:
    p = PromptBuilder(base_vocab="vocab")
    out = p.build(stage="calling_cq")
    assert "CQ" in out or "reply" in out.lower()


def test_stage_in_qso_includes_own_call() -> None:
    p = PromptBuilder(base_vocab="vocab")
    p.set_operator(callsign="K1AF")
    out = p.build(stage="in_qso")
    assert "QSO" in out
    # Own callsign should be referenced in the stage hint, not only the operator block.
    assert out.count("K1AF") >= 2


def test_recent_callsigns_at_tail() -> None:
    # Whisper effectively uses the last ~224 tokens; recent context should land last.
    p = PromptBuilder(base_vocab="vocab")
    p.remember_callsign("W1AW")
    out = p.build(extra="some notes")
    assert out.rstrip(".").endswith("W1AW")


def test_explicit_nato_words_in_default_vocab() -> None:
    p = PromptBuilder()
    out = p.build()
    # If Whisper sees the NATO words in-context, it's biased to keep them
    # literal instead of guessing common-language homophones.
    for word in ("kilo", "foxtrot", "whiskey", "zulu"):
        assert word in out.lower(), f"missing NATO word {word!r}"
