from __future__ import annotations

from cqk1af.stt.prompt_builder import DEFAULT_HOTWORDS, DEFAULT_VOCAB, PromptBuilder


def test_basic_build_includes_vocab() -> None:
    p = PromptBuilder()
    prompt, _ = p.build(own_callsign="K1AF")
    assert "K1AF" in prompt
    assert "QSO" in prompt or "amateur" in prompt.lower()


def test_recent_callsigns_included_and_deduped() -> None:
    # Use a vocab string that does not include W1AW so we can count cleanly.
    p = PromptBuilder(base_vocab="placeholder vocab", base_hotwords="placeholder")
    for c in ["W1AW", "K9XYZ", "VE3ABC", "w1aw"]:
        p.remember_callsign(c)
    prompt, _ = p.build()
    assert prompt.count("W1AW") == 1
    assert "K9XYZ" in prompt
    assert "VE3ABC" in prompt


def test_recent_limit_respected() -> None:
    p = PromptBuilder(recent_limit=3, base_hotwords="placeholder")
    for c in ["AA1A", "AA2A", "AA3A", "AA4A"]:
        p.remember_callsign(c)
    prompt, _ = p.build()
    # Oldest one is dropped
    assert "AA1A" not in prompt
    assert "AA4A" in prompt


def test_prompt_bounded() -> None:
    p = PromptBuilder()
    for i in range(50):
        p.remember_callsign(f"K1A{i:03d}")
    prompt, hotwords = p.build(extra="extra notes " * 200)
    assert len(prompt) <= 800
    assert len(hotwords) <= 1000


def test_operator_context_included() -> None:
    p = PromptBuilder(base_vocab="vocab")
    p.set_operator(callsign="K1AF", name="Burns", qth="Boston, MA", grid="FN42")
    prompt, _ = p.build()
    assert "K1AF" in prompt
    assert "Burns" in prompt
    assert "Boston" in prompt
    assert "FN42" in prompt


def test_stage_calling_cq_hint() -> None:
    p = PromptBuilder(base_vocab="vocab")
    prompt, _ = p.build(stage="calling_cq")
    assert "CQ" in prompt or "reply" in prompt.lower()


def test_stage_in_qso_includes_own_call() -> None:
    p = PromptBuilder(base_vocab="vocab")
    p.set_operator(callsign="K1AF")
    prompt, _ = p.build(stage="in_qso")
    assert "QSO" in prompt
    # Own callsign should be referenced in the stage hint, not only the operator block.
    assert prompt.count("K1AF") >= 2


def test_recent_callsigns_at_tail() -> None:
    # Whisper effectively uses the last ~224 tokens; recent context should land last.
    p = PromptBuilder(base_vocab="vocab")
    p.remember_callsign("W1AW")
    prompt, _ = p.build(extra="some notes")
    assert prompt.rstrip(".").endswith("W1AW")


def test_explicit_nato_words_in_default_vocab() -> None:
    p = PromptBuilder()
    prompt, _ = p.build()
    # If Whisper sees the NATO words in-context, it's biased to keep them
    # literal instead of guessing common-language homophones.
    for word in ("kilo", "foxtrot", "whiskey", "zulu"):
        assert word in prompt.lower(), f"missing NATO word {word!r}"


def test_extended_qcodes_in_default_vocab() -> None:
    # QSY/QTH/QRX were missing from the original DEFAULT_VOCAB; the extended
    # set now biases Whisper toward the full canonical Q-code list.
    for code in ("QSY", "QTH", "QRX", "QRV", "QTC"):
        assert code in DEFAULT_VOCAB, f"missing Q-code {code!r}"


def test_default_hotwords_contains_terse_jargon() -> None:
    assert isinstance(DEFAULT_HOTWORDS, str)
    assert DEFAULT_HOTWORDS
    for token in ("QSL", "kilo", "CQ", "73", "W1AW"):
        assert token in DEFAULT_HOTWORDS, f"missing hotword token {token!r}"


def test_build_returns_prompt_and_hotwords_tuple() -> None:
    p = PromptBuilder()
    result = p.build()
    assert isinstance(result, tuple)
    assert len(result) == 2
    prompt, hotwords = result
    assert isinstance(prompt, str)
    assert isinstance(hotwords, str)
    assert hotwords  # should be non-empty by default


def test_contest_stage_adds_contest_hotwords_and_prompt() -> None:
    p = PromptBuilder()
    prompt, hotwords = p.build(stage="contest")
    assert "section" in hotwords
    assert "serial" in hotwords
    assert "Contest exchange" in prompt


def test_hotwords_include_recent_callsigns() -> None:
    p = PromptBuilder()
    p.remember_callsign("W1AW")
    p.remember_callsign("K9XYZ")
    _, hotwords = p.build()
    assert "W1AW" in hotwords
    assert "K9XYZ" in hotwords


def test_extra_hotwords_appended() -> None:
    p = PromptBuilder()
    _, hotwords = p.build(extra_hotwords="DXpedition KH7Z")
    assert "DXpedition" in hotwords
    assert "KH7Z" in hotwords


def test_hotwords_deduped_case_insensitive() -> None:
    # DEFAULT_HOTWORDS already includes "kilo"; user adding "KILO" via
    # extra_hotwords or recent_callsign shouldn't duplicate the token.
    p = PromptBuilder()
    _, hotwords = p.build(extra_hotwords="kilo KILO Kilo")
    tokens = hotwords.split()
    # Count case-insensitive occurrences of "kilo"
    assert sum(1 for t in tokens if t.upper() == "KILO") == 1
