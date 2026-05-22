"""Whisper ``initial_prompt`` builder.

Recent callsigns and ham-radio vocabulary are spliced into the prompt to bias
the decoder toward the right tokens. Length is bounded — Whisper truncates long
prompts and only the last ~224 tokens are actually used.
"""
from __future__ import annotations

from collections import deque

DEFAULT_VOCAB = (
    "Amateur radio QSO on SSB. Callsigns like W1AW, K1AF, KC1ABC, VE3XYZ. "
    "Signal report five nine, five by nine, seven by seven. "
    "QSL, QSB, QRM, QRN, 73, 88, CQ, QRZ, over, go ahead, please copy."
)


class PromptBuilder:
    def __init__(self, base_vocab: str = DEFAULT_VOCAB, recent_limit: int = 12) -> None:
        self.base_vocab = base_vocab.strip()
        self._recent: deque[str] = deque(maxlen=recent_limit)

    def remember_callsign(self, callsign: str) -> None:
        c = callsign.strip().upper()
        if not c:
            return
        if c in self._recent:
            # Move to most-recent end
            self._recent.remove(c)
        self._recent.append(c)

    def build(self, *, extra: str = "", own_callsign: str | None = None) -> str:
        parts: list[str] = []
        if own_callsign:
            parts.append(f"Operator callsign: {own_callsign.upper()}.")
        if self._recent:
            parts.append("Recently heard callsigns: " + ", ".join(self._recent) + ".")
        parts.append(self.base_vocab)
        if extra:
            parts.append(extra.strip())
        prompt = " ".join(parts).strip()
        # Bound length: Whisper effectively uses ~224 tokens of prompt.
        return prompt[:800]
