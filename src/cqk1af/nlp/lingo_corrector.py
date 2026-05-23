"""Deterministic post-Whisper text normalisation for ham-radio jargon.

Whisper transcribes phonetic alphabet and ham idioms verbatim ("seventy
three", "Q. R. Z."), which is awkward for downstream extractors and ugly
in the transcript. This module rewrites a small allowlist of patterns to
the canonical forms ham operators actually read: ``73``, ``QRZ``, ``CQ``,
etc.

Rules are intentionally narrow — applied on word boundaries, restricted
to a known Q-code allowlist, and leave NATO words untouched (the callsign
extractor's phonetic pass depends on them).
"""
from __future__ import annotations

import re

_QCODES = frozenset(
    {
        "QSL", "QSB", "QSO", "QSY",
        "QRM", "QRN", "QRT", "QRU", "QRV", "QRX", "QRZ",
        "QTH", "QTC",
    }
)


def _qcode_repl(m: re.Match[str]) -> str:
    code = "Q" + m.group(1).upper() + m.group(2).upper()
    return code if code in _QCODES else m.group(0)


# Whisper sometimes types a word that sounds like a single callsign-leading
# letter ("In 4WF" instead of "N4WF"). Limited to 1-letter prefixes that
# actually appear in US (N, K, W, A) and common intl (G, F, I, M) call areas;
# omits ambiguous single-character English words like "I" / "a" which would
# match constantly. We only collapse when the next token starts with a digit
# (callsign shape: letter + digit + letter), which makes the rule
# self-anchoring against most prose.
_LETTER_PREFIX_HOMOPHONES: dict[str, str] = {
    "in": "N", "and": "N", "en": "N",
    "kay": "K",
    "double-u": "W", "double u": "W", "double-you": "W", "double you": "W",
    "ay": "A", "eh": "A",
    "gee": "G",
    "ef": "F", "eff": "F",
    "eye": "I", "aye": "I",
    "em": "M",
}

_LETTER_PREFIX_PATTERN = re.compile(
    r"\b(" + "|".join(
        re.escape(k) for k in sorted(_LETTER_PREFIX_HOMOPHONES, key=len, reverse=True)
    ) + r")\s+(\d{1,4}[A-Za-z]{1,3}(?:/[A-Za-z0-9]{1,4})?)\b",
    re.IGNORECASE,
)


def _letter_prefix_repl(m: re.Match[str]) -> str:
    tail = m.group(2)
    # Require the tail's letters to be uppercase. Whisper writes callsigns in
    # caps; lowercase tails ("an 8-hour", "in 4wd") are almost always prose.
    if not tail.isupper():
        return m.group(0)
    letter = _LETTER_PREFIX_HOMOPHONES.get(m.group(1).lower())
    if letter is None:
        return m.group(0)
    return letter + tail


# Compiled once at import. Order matters: spelled-out numbers and dotted
# Q-codes are normalised before any other rule sees them.
_RULES: tuple[tuple[re.Pattern[str], object], ...] = (
    (re.compile(r"\bseven(?:ty)?[\s\-]+three\b", re.IGNORECASE), "73"),
    (re.compile(r"\beight(?:y)?[\s\-]+eight\b", re.IGNORECASE), "88"),
    (
        re.compile(r"\bfive\s+by\s+(nine|five)\b", re.IGNORECASE),
        lambda m: "five " + m.group(1).lower(),
    ),
    (
        # \b at end fails between "." and " " (both non-word), so use a
        # lookahead to consume an optional trailing dot before a boundary.
        re.compile(r"\bQ[\.\s]+([A-Za-z])[\.\s]*([A-Za-z])\.?(?=\s|$|[,;:!?])"),
        _qcode_repl,
    ),
    (re.compile(r"\bC\.\s*Q\.?(?=[\s,.;!?]|$)", re.IGNORECASE), "CQ"),
    (_LETTER_PREFIX_PATTERN, _letter_prefix_repl),
)


def correct_lingo(text: str) -> str:
    if not text:
        return text
    out = text
    for pattern, repl in _RULES:
        if callable(repl):
            out = pattern.sub(repl, out)
        else:
            out = pattern.sub(repl, out)
    return out
