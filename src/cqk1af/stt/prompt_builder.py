"""Whisper ``initial_prompt`` builder.

Recent callsigns, ham-radio vocabulary, operator context, and the current
session stage are spliced into the prompt to bias the decoder toward the
right tokens. Length is bounded — Whisper truncates long prompts and only
the last ~224 tokens are actually used, so the most stage-specific tokens
go at the end.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal

# Listing the NATO words explicitly biases Whisper toward transcribing
# phonetics literally instead of guessing common words ("kilo" → "kill",
# "foxtrot" → "fox trot").
DEFAULT_VOCAB = (
    "Amateur radio QSO on single side band SSB. Callsigns are spelled with "
    "the NATO phonetic alphabet: alpha, bravo, charlie, delta, echo, foxtrot, "
    "golf, hotel, india, juliet, kilo, lima, mike, november, oscar, papa, "
    "quebec, romeo, sierra, tango, uniform, victor, whiskey, x-ray, yankee, "
    "zulu. Digits: zero, one, two, three, four, five, six, seven, eight, "
    "nine. Example callsigns: W1AW, K1AF, KC1ABC, VE3XYZ. Signal report "
    "five nine, five by nine. Common phrases: QSL, QSB, QRM, QRN, 73, 88, "
    "CQ, QRZ, over, go ahead, please copy, my name is, my QTH is."
)

Stage = Literal["scanning", "listening", "calling_cq", "in_qso", "pileup"]


@dataclass
class OperatorContext:
    callsign: str = ""
    name: str = ""
    qth: str = ""
    grid: str = ""


class PromptBuilder:
    def __init__(self, base_vocab: str = DEFAULT_VOCAB, recent_limit: int = 12) -> None:
        self.base_vocab = base_vocab.strip()
        self._recent: deque[str] = deque(maxlen=recent_limit)
        self.operator = OperatorContext()

    def set_operator(
        self,
        *,
        callsign: str = "",
        name: str = "",
        qth: str = "",
        grid: str = "",
    ) -> None:
        self.operator = OperatorContext(
            callsign=callsign.strip().upper(),
            name=name.strip(),
            qth=qth.strip(),
            grid=grid.strip().upper(),
        )

    def remember_callsign(self, callsign: str) -> None:
        c = callsign.strip().upper()
        if not c:
            return
        if c in self._recent:
            self._recent.remove(c)
        self._recent.append(c)

    def build(
        self,
        *,
        extra: str = "",
        own_callsign: str | None = None,
        stage: Stage | None = None,
    ) -> str:
        """Order matters: Whisper effectively keeps the tail ~224 tokens, so the
        most situationally-relevant context (stage hint, recent callsigns) goes
        last.
        """
        parts: list[str] = [self.base_vocab]

        op_call = (own_callsign or self.operator.callsign or "").strip().upper()
        op_bits: list[str] = []
        if op_call:
            op_bits.append(f"Operator callsign: {op_call}.")
        if self.operator.name:
            op_bits.append(f"Operator name: {self.operator.name}.")
        if self.operator.qth:
            op_bits.append(f"Operator QTH: {self.operator.qth}.")
        if self.operator.grid:
            op_bits.append(f"Operator grid square: {self.operator.grid}.")
        if op_bits:
            parts.append(" ".join(op_bits))

        if extra:
            parts.append(extra.strip())

        stage_text = _stage_prompt(stage, own_callsign=op_call)
        if stage_text:
            parts.append(stage_text)

        if self._recent:
            parts.append("Recently heard callsigns: " + ", ".join(self._recent) + ".")

        prompt = " ".join(parts).strip()
        return prompt[:800]


def _stage_prompt(stage: Stage | None, *, own_callsign: str = "") -> str:
    if stage is None:
        return ""
    if stage == "calling_cq":
        return (
            "Listening for stations replying to a CQ. A caller will speak "
            "their callsign phonetically, then say over or go ahead."
        )
    if stage == "in_qso":
        target = f" with {own_callsign}" if own_callsign else ""
        return (
            f"In QSO{target}. Exchanging signal report (five nine), operator "
            "name, and QTH."
        )
    if stage == "pileup":
        return (
            "Handling a pileup. Multiple callers will speak callsigns "
            "simultaneously or in rapid succession."
        )
    if stage in ("listening", "scanning"):
        return "Listening for activity on frequency."
    return ""
