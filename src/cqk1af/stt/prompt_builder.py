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
# "foxtrot" → "fox trot"). The vocab is split into named constants so
# stage-specific prompts can swap chunks and so a sibling DEFAULT_HOTWORDS
# can carry terse jargon through faster-whisper's `hotwords` channel
# without re-tokenising prose.
VOCAB_NATO = (
    "Amateur radio QSO on single side band SSB. Callsigns are spelled with "
    "the NATO phonetic alphabet: alpha, bravo, charlie, delta, echo, foxtrot, "
    "golf, hotel, india, juliet, kilo, lima, mike, november, oscar, papa, "
    "quebec, romeo, sierra, tango, uniform, victor, whiskey, x-ray, yankee, "
    "zulu."
)
VOCAB_DIGITS = (
    "Digits: zero, one, two, three, four, five, six, seven, eight, nine."
)
VOCAB_QCODES = (
    "Q-codes: QSL, QSO, QSY, QSB, QRM, QRN, QRT, QRU, QRV, QRX, QRZ, QTH, QTC."
)
VOCAB_SIGREPS = "Signal report five nine, five by nine, fifty nine. RST 599."
VOCAB_PHRASES = (
    "Common phrases: 73, 88, CQ, QRZ, over, go ahead, please copy, "
    "loud and clear, full quieting, good copy, my name is, my QTH is."
)
VOCAB_CONTEST = (
    "CQ contest, CQ Sweepstakes, CQ Sprint, section, serial number, "
    "zone, exchange, QSO B4."
)
VOCAB_SAMPLE_CALLS = (
    "Example callsigns: W1AW, K1AF, KC1ABC, VE3XYZ, G3ABC, DL1ABC, "
    "JA1ABC, VK2ABC."
)

DEFAULT_VOCAB = " ".join(
    [
        VOCAB_NATO,
        VOCAB_DIGITS,
        VOCAB_QCODES,
        VOCAB_SIGREPS,
        VOCAB_SAMPLE_CALLS,
        VOCAB_PHRASES,
    ]
)

# Terse tokens for faster-whisper's `hotwords` biasing channel. Distinct from
# the prose `initial_prompt`: Whisper biases each independently, so the same
# vocab listed in both does not double-count — it complements.
DEFAULT_HOTWORDS = (
    "QSL QSO QSY QSB QRM QRN QRT QRU QRV QRX QRZ QTH QTC CQ 73 88 "
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet "
    "kilo lima mike november oscar papa quebec romeo sierra tango "
    "uniform victor whiskey x-ray yankee zulu "
    "W1AW K1AF KC1ABC VE3XYZ"
)

Stage = Literal["scanning", "listening", "calling_cq", "in_qso", "pileup", "contest"]

# Terse contest-jargon tokens, swapped into hotwords when stage="contest".
HOTWORDS_CONTEST = "section serial zone exchange contest Sweepstakes Sprint"


@dataclass
class OperatorContext:
    callsign: str = ""
    name: str = ""
    qth: str = ""
    grid: str = ""


class PromptBuilder:
    def __init__(
        self,
        base_vocab: str = DEFAULT_VOCAB,
        recent_limit: int = 12,
        base_hotwords: str = DEFAULT_HOTWORDS,
    ) -> None:
        self.base_vocab = base_vocab.strip()
        self.base_hotwords = base_hotwords.strip()
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
        extra_hotwords: str = "",
        own_callsign: str | None = None,
        stage: Stage | None = None,
    ) -> tuple[str, str]:
        """Returns (initial_prompt, hotwords). faster-whisper biases each
        channel independently, so the same vocab listed in both does not
        double-count — prose context in initial_prompt, terse jargon tokens
        in hotwords.

        Order in the prompt matters: Whisper effectively keeps the tail ~224
        tokens, so the most situationally-relevant context (stage hint,
        recent callsigns) goes last.
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

        prompt = " ".join(parts).strip()[:800]

        hot_tokens: list[str] = self.base_hotwords.split()
        if stage == "contest":
            hot_tokens.extend(HOTWORDS_CONTEST.split())
        hot_tokens.extend(self._recent)
        if extra_hotwords:
            hot_tokens.extend(extra_hotwords.split())
        seen: set[str] = set()
        deduped: list[str] = []
        for tok in hot_tokens:
            key = tok.upper()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(tok)
        hotwords = " ".join(deduped)[:1000]

        return prompt, hotwords


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
    if stage == "contest":
        return (
            "Contest exchange: callsign, signal report, serial number or "
            "section. " + VOCAB_CONTEST
        )
    if stage in ("listening", "scanning"):
        return "Listening for activity on frequency."
    return ""
