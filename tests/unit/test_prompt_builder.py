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
