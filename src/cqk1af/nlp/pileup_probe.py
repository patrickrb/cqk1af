"""Generate the on-air phrasing used to tease a partial callsign into a full one.

A pileup entry may have `?` placeholders in any position when the operator only
copied part of the call (e.g. `K1??` for a known prefix and unknown suffix, or
`?A1AF` for a confidently-heard suffix with an unknown first letter). Given
that string, this module picks a phrasing pattern that matches the standard
DX-SSB practice for asking the caller to fill in the missing characters.

Patterns drawn from on-air practice (W8RIT "Working DX HF Pileups", ON4WW
operating-practice notes, and standard ARRL-style pileup management):

  * Full unknown            → "Station calling K1AF, please your full call again."
  * Suffix entirely unknown → "The kilo one station, what's your suffix?"
  * Prefix entirely unknown → "Station ending alpha foxtrot, what's your prefix?"
  * Number unknown          → "Kilo alpha foxtrot, your number?"
  * Single trailing ?       → "Kilo one alpha what?"  (also: "what's after alpha?")
  * Single embedded ?       → "Kilo one what alpha foxtrot?"
  * Single leading ?        → "What one alpha foxtrot?"
  * Multi-gap fallback      → "{known phonetic}, again your callsign please."

The output is plain English with NATO words; the TX path will phoneticize any
literal callsigns later (the operator's own callsign is appended for ID).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..tts.phonetic import DIGIT_WORDS, NATO_FROM_LETTER

# Same shape as the real callsign regex, but `?` is allowed in any position.
# Anchored: the whole string must be the partial. 1-2 letters or ?, 1-4
# digits or ?, 1-3 letters or ?. Portable suffix not supported in probes.
_PARTIAL_RE = re.compile(r"^[A-Z\?]{1,2}\d?[\dA-Z\?]{0,3}[A-Z\?]{1,3}$")


@dataclass(frozen=True)
class Probe:
    text: str
    pattern: str  # name of the pattern that fired — for tests + UI hinting


def has_wildcards(callsign: str) -> bool:
    return "?" in (callsign or "")


def _phonetic_token(ch: str) -> str:
    """Spoken form for a single character. `?` itself never appears in output."""
    if ch in NATO_FROM_LETTER:
        return NATO_FROM_LETTER[ch]
    if ch.isdigit():
        return DIGIT_WORDS[ch]
    return ch.lower()


def _phon_join(chars: str) -> str:
    return " ".join(_phonetic_token(c) for c in chars if c != "?")


def _split_call(callsign: str) -> tuple[str, str, str] | None:
    """Split a partial callsign into (prefix_letters, number, suffix_letters).

    The shape is the standard amateur template ``[A-Z]{1,2}\\d{1,4}[A-Z]{1,3}``
    with ``?`` allowed in any position. Because a `?` could play the role of
    either a letter or a digit, the function enumerates every length split that
    obeys the per-region type constraints (no real digits in letter regions and
    no real letters in the number region) and picks the one that explains the
    most known characters. Ties favour the most common US layout: 1-char
    prefix, 1-char number, 2-char suffix.

    Returns None only if the input is empty or every split is inconsistent.
    """
    s = callsign.upper().strip()
    n = len(s)
    if n < 3 or n > 7:
        return None

    def region_ok(chars: str, kind: str) -> tuple[bool, int]:
        """(valid, hard_match_count) for a region given expected kind."""
        hard = 0
        for c in chars:
            if c == "?":
                continue
            if kind == "L":
                if not c.isalpha():
                    return False, 0
                hard += 1
            else:  # 'D'
                if not c.isdigit():
                    return False, 0
                hard += 1
        return True, hard

    best: tuple[int, int, int, tuple[str, str, str]] | None = None
    # Tuple ranks: (-hard_total, abs(prefix-1), abs(suffix-2)) — lower is better.
    for pre_len in (1, 2):
        for num_len in range(1, 5):
            suf_len = n - pre_len - num_len
            if suf_len < 1 or suf_len > 3:
                continue
            prefix = s[:pre_len]
            number = s[pre_len : pre_len + num_len]
            suffix = s[pre_len + num_len :]
            p_ok, p_hard = region_ok(prefix, "L")
            n_ok, n_hard = region_ok(number, "D")
            s_ok, s_hard = region_ok(suffix, "L")
            if not (p_ok and n_ok and s_ok):
                continue
            # Tiebreakers (lower wins): explain the most known chars; then
            # prefer a single-digit number (rare to have multi-digit numbers,
            # so cluster wildcards into the suffix region instead); then prefer
            # the most common US shape (1-char prefix, 2-char suffix).
            score = (-(p_hard + n_hard + s_hard), num_len, abs(pre_len - 1), abs(suf_len - 2))
            if best is None or score < best[:4]:
                best = (*score, (prefix, number, suffix))  # type: ignore[assignment]
    if best is None:
        return None
    return best[4]


def build_probe(callsign: str, *, operator_callsign: str = "") -> Probe:
    """Choose the right probing phrasing for a `?`-containing partial."""
    raw = (callsign or "").upper().strip()
    if not raw:
        return Probe("Station calling, please your callsign again.", "empty")
    if "?" not in raw:
        # No wildcards — the caller shouldn't really invoke this, but return a
        # neutral "QRZ the X" so we never produce broken text.
        return Probe(f"QRZ the {_phon_join(raw)}?", "no_wildcard")

    parts = _split_call(raw)
    if parts is None:
        return Probe("Station calling, please your full call again.", "unparseable")
    prefix, number, suffix = parts
    prefix_known = "?" not in prefix
    number_known = "?" not in number
    suffix_known = "?" not in suffix
    all_unknown_prefix = set(prefix) == {"?"}
    all_unknown_suffix = set(suffix) == {"?"}
    all_unknown_number = set(number) == {"?"}
    wildcards_total = raw.count("?")

    # 1. Entire call unknown
    if all_unknown_prefix and all_unknown_number and all_unknown_suffix:
        return Probe(
            "Station calling, please your full callsign again.",
            "all_unknown",
        )

    # 2. Suffix entirely unknown, prefix+number known → "the K-one station, your suffix?"
    if prefix_known and number_known and all_unknown_suffix:
        head = _phon_join(prefix + number)
        return Probe(f"The {head} station, what's your suffix?", "suffix_unknown")

    # 3. Prefix entirely unknown, number+suffix known → "station ending AF, your prefix?"
    if all_unknown_prefix and number_known and suffix_known:
        tail = _phon_join(suffix)
        return Probe(
            f"Station ending in {tail}, what's your prefix?",
            "prefix_unknown",
        )

    # 4. Only the number is unknown → "kilo alpha foxtrot, your number?"
    if prefix_known and suffix_known and all_unknown_number:
        return Probe(
            f"{_phon_join(prefix)} {_phon_join(suffix)}, what's your number?",
            "number_unknown",
        )

    # 5. Exactly one wildcard somewhere — use "what?" in position.
    if wildcards_total == 1:
        idx = raw.index("?")
        before = raw[:idx]
        after = raw[idx + 1 :]
        before_phon = _phon_join(before)
        after_phon = _phon_join(after)
        if not after:  # trailing ?  — "kilo one alpha what?"
            return Probe(f"{before_phon} what?", "trailing_one")
        if not before:  # leading ?  — "what one alpha foxtrot?"
            return Probe(f"What {after_phon}?", "leading_one")
        # Embedded — both "what's after X" and "X what Y" are valid; pick the
        # latter, which preserves the position information for the caller.
        return Probe(
            f"{before_phon} what {after_phon}?",
            "embedded_one",
        )

    # 6. Multi-gap fallback — read what we have, ask again.
    head = _phon_join(raw)
    if head:
        return Probe(f"{head}, again your full callsign please.", "multi_gap")
    return Probe("Station calling, please your full callsign again.", "multi_gap")


def with_operator_id(probe_text: str, operator_callsign: str) -> str:
    """Append the operator's call so the probe TX also satisfies §97.119 ID.

    The TX path phoneticizes callsign-shaped tokens in the text, so we can
    just append the literal call and let the renderer do the work.
    """
    op = (operator_callsign or "").upper().strip()
    if not op:
        return probe_text
    sep = " " if probe_text.endswith("?") or probe_text.endswith(".") else ". "
    return f"{probe_text}{sep}This is {op}."
