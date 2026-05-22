"""Callsign extractor.

Two-pass:
  1. Literal regex scan over the input text (uppercased letters and digits) —
     catches Whisper outputs that already contain a clean callsign like
     ``W1AW`` or ``K1AF/M``.
  2. Phonetic decoder: walk the tokenised lowercase input, mapping NATO words
     and digit words to letters/digits. Continuous runs of those tokens form
     candidate callsigns.

Each candidate is validated against the ITU prefix table (with a permissive
fallback that accepts the shape ``[A-Z]{1,2}\\d[A-Z]{1,3}`` for unknown
prefixes — better to surface "did you mean…" than to drop a valid callsign).

Returns ranked candidates with a heuristic confidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .vocab import COMMON_PREFIXES, DIGIT_TABLE, LETTER_HOMOPHONES, NATO_TABLE


_CALLSIGN_RE = re.compile(r"\b[A-Z]{1,2}\d{1,4}[A-Z]{1,3}(?:/[A-Z0-9]{1,4})?\b")
# Sliding-window variant for phonetic runs like "K9XYZ55" where there are no
# word boundaries to anchor against.
_CALLSIGN_RUN_RE = re.compile(r"[A-Z]{1,2}\d{1,4}[A-Z]{1,3}(?:/[A-Z0-9]{1,4})?")
_TOKEN_SPLIT = re.compile(r"[\s,.;:?!]+")


@dataclass(frozen=True)
class CallsignCandidate:
    callsign: str
    confidence: float
    method: str  # "literal" or "phonetic"
    source_span: tuple[int, int]  # start/end token index


def _shape_valid(call: str) -> bool:
    # Allow optional /SUFFIX (portable, mobile)
    main = call.split("/")[0]
    return bool(re.fullmatch(r"[A-Z]{1,2}\d{1,4}[A-Z]{1,3}", main))


def _prefix_known(call: str) -> bool:
    main = call.split("/")[0]
    m = re.match(r"([A-Z]{1,2})", main)
    if not m:
        return False
    return m.group(1) in COMMON_PREFIXES


def _confidence_for(call: str, method: str) -> float:
    if not _shape_valid(call):
        return 0.1
    base = 0.85 if method == "literal" else 0.7
    if _prefix_known(call):
        base = min(1.0, base + 0.1)
    return base


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_SPLIT.split(text.strip()) if t]


def _phonetic_token(tok: str) -> str | None:
    """Return uppercase letter / digit for a phonetic or homophone token, else None."""
    t = tok.strip("-_'")
    if not t:
        return None
    if t in NATO_TABLE:
        return NATO_TABLE[t]
    if t in DIGIT_TABLE:
        return DIGIT_TABLE[t]
    if t in LETTER_HOMOPHONES:
        return LETTER_HOMOPHONES[t]
    # Single letter / digit emitted as a token (e.g. "k", "1", "a")
    if len(t) == 1 and (t.isalpha() or t.isdigit()):
        return t.upper()
    return None


def _phonetic_runs(tokens: list[str]) -> list[tuple[str, int, int]]:
    """Find maximal runs of phonetic tokens; return (concatenated_string, start, end_exclusive)."""
    runs: list[tuple[str, int, int]] = []
    i = 0
    n = len(tokens)
    while i < n:
        if _phonetic_token(tokens[i]) is None:
            i += 1
            continue
        buf: list[str] = []
        start = i
        while i < n:
            p = _phonetic_token(tokens[i])
            if p is None:
                break
            buf.append(p)
            i += 1
        if len(buf) >= 3:
            runs.append(("".join(buf), start, i))
    return runs


def _slice_run_for_callsigns(run: str) -> list[str]:
    """A run like "K9XYZ55" — slide a regex window to find all callsign-shaped
    substrings. Allows multiple callsigns concatenated.
    """
    return _CALLSIGN_RUN_RE.findall(run)


def extract_callsigns(text: str) -> list[CallsignCandidate]:
    """Return a list of plausible callsigns ranked by confidence (desc)."""
    out: list[CallsignCandidate] = []
    seen: set[str] = set()

    # Pass 1: literal regex
    upper = text.upper()
    for m in _CALLSIGN_RE.finditer(upper):
        call = m.group(0)
        if call in seen:
            continue
        seen.add(call)
        out.append(
            CallsignCandidate(
                callsign=call,
                confidence=_confidence_for(call, "literal"),
                method="literal",
                source_span=(m.start(), m.end()),
            )
        )

    # Pass 2: phonetic
    tokens = _tokenize(text)
    for run, t_start, t_end in _phonetic_runs(tokens):
        for call in _slice_run_for_callsigns(run):
            if call in seen:
                continue
            seen.add(call)
            out.append(
                CallsignCandidate(
                    callsign=call,
                    confidence=_confidence_for(call, "phonetic"),
                    method="phonetic",
                    source_span=(t_start, t_end),
                )
            )

    out.sort(key=lambda c: c.confidence, reverse=True)
    return out
