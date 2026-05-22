"""Parse RST (signal report) phrases.

Accepts forms commonly spoken on SSB voice:
  - "five nine", "five by nine", "5 by 9", "5 9", "59"
  - "five and nine"
  - "fifty nine" (combined)
  - "599" (RST with tone, three digits)

Returns an RST string in canonical form (e.g. ``"59"`` for two-digit or
``"599"`` for three-digit).
"""
from __future__ import annotations

import re

from .vocab import DIGIT_TABLE

_TWO_DIGIT = re.compile(r"\b([0-9])\s*(?:by|and|x|/)?\s*([0-9])\b")
_THREE_DIGIT = re.compile(r"\b([0-9])\s*(?:by|and|x|/)?\s*([0-9])\s*(?:by|and|x|/)?\s*([0-9])\b")
_FIFTYNINE = re.compile(r"\bfifty[-\s]?nine\b", re.IGNORECASE)


def _expand_words(text: str) -> str:
    out_words: list[str] = []
    for tok in re.split(r"(\s+|[,.;:?!])", text.lower()):
        t = tok.strip()
        if not t:
            out_words.append(tok)
            continue
        if t in DIGIT_TABLE:
            out_words.append(DIGIT_TABLE[t])
        else:
            out_words.append(tok)
    return "".join(out_words)


def parse_signal_report(text: str) -> str | None:
    """Return canonical RST or None."""
    if not text:
        return None
    if _FIFTYNINE.search(text):
        return "59"
    expanded = _expand_words(text)
    m3 = _THREE_DIGIT.search(expanded)
    if m3:
        d = m3.group(1) + m3.group(2) + m3.group(3)
        if _valid_rst(d):
            return d
    m2 = _TWO_DIGIT.search(expanded)
    if m2:
        d = m2.group(1) + m2.group(2)
        if _valid_rst(d):
            return d
    return None


def _valid_rst(d: str) -> bool:
    # R: 1-5, S: 1-9, T: 1-9 (CW only). On SSB only R+S are used.
    if len(d) == 2:
        return d[0] in "12345" and d[1] in "123456789"
    if len(d) == 3:
        return d[0] in "12345" and d[1] in "123456789" and d[2] in "123456789"
    return False
