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
