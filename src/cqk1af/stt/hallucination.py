"""Filter out common Whisper hallucinations.

Whisper (and distilled variants) reliably produce a small set of phrases when
fed silence, static, or low-information audio — typically YouTube subtitle
boilerplate baked in during training, plus a few sound-effect markups. SSB
voice has long silences and continuous band noise between utterances, so the
filter sees these regularly even with VAD gating.

Two layers:
  1. Phrase match against a normalized set of known hallucinations.
  2. Heuristics combining text shape with the segment's
     ``no_speech_prob`` and audio duration.

The check returns ``(verdict, reason)`` so callers can log the reason.
"""
from __future__ import annotations

import re
import unicodedata

_RAW_HALLUCINATIONS: tuple[str, ...] = (
    "thank you",
    "thanks for watching",
    "thank you for watching",
    "thanks for watching the video",
    "thank you for watching the video",
    "please subscribe",
    "please subscribe to my channel",
    "subscribe to my channel",
    "like and subscribe",
    "like comment and subscribe",
    "don't forget to subscribe",
    "see you in the next video",
    "i'll see you in the next video",
    "see you next time",
    "thanks for listening",
    "thank you for listening",
    "bye bye",
    "goodbye",
    "bye",
    "you",
    "music",
    "applause",
    "silence",
    "blank audio",
    "inaudible",
    # Foreign-language artifacts Whisper falls into on silence.
    "sous-titres réalisés par la communauté d'amara.org",
    "sous-titres",
    "merci",
    "amara.org",
    # Bracketed sound-effect labels emitted as plain text after stripping.
    "music",
    "applause",
)

# Bracketed sound-effect markup: "[Music]", "(applause)", "♪ lyrics ♪".
_BRACKETED_MARKUP = re.compile(r"^\s*[\[\(♪].*[\]\)♪]\s*$")
_MUSIC_NOTES_ONLY = re.compile(r"^[\s♩♪♫♬♭♮♯]+$")


def _normalize(text: str) -> str:
    s = unicodedata.normalize("NFKC", text).lower().strip()
    # Drop punctuation and music-note glyphs; keep letters, digits, spaces.
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_KNOWN_HALLUCINATIONS: frozenset[str] = frozenset(
    _normalize(p) for p in _RAW_HALLUCINATIONS
)


def is_likely_hallucination(
    text: str,
    *,
    no_speech_prob: float = 0.0,
    audio_duration_s: float = 0.0,
) -> tuple[bool, str]:
    """Return (True, reason) if ``text`` should be dropped.

    ``audio_duration_s`` is the source utterance length. A two-word transcript
    for a 6-second utterance is suspicious in a way the same transcript over
    0.5 s is not.
    """
    raw = (text or "").strip()
    if not raw:
        return True, "empty"

    if _BRACKETED_MARKUP.match(raw) or _MUSIC_NOTES_ONLY.match(raw):
        return True, "bracketed_markup"

    norm = _normalize(raw)
    if not norm:
        return True, "empty_after_normalize"

    if norm in _KNOWN_HALLUCINATIONS:
        return True, f"known_phrase:{norm}"

    # "thank you" plus trailing variants ("thank you so much", "thank you all")
    # are still subtitle boilerplate; catch the prefix.
    if norm.startswith("thank you ") and len(norm.split()) <= 4:
        return True, "thank_you_prefix"

    # Long audio + extremely short transcript + high no-speech prob is the
    # classic silence-hallucination signature.
    if (
        audio_duration_s >= 2.0
        and len(norm) <= 3
        and no_speech_prob >= 0.6
    ):
        return True, "short_text_no_speech"

    return False, ""
