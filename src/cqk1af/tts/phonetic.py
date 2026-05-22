"""Phonetic rendering helpers for TTS.

Converts callsigns and arbitrary letter sequences to NATO phonetic spellings,
which Piper renders clearly. Numbers are spoken as digits with a brief pause
after each character.
"""
from __future__ import annotations

NATO_FROM_LETTER: dict[str, str] = {
    "A": "alpha",
    "B": "bravo",
    "C": "charlie",
    "D": "delta",
    "E": "echo",
    "F": "foxtrot",
    "G": "golf",
    "H": "hotel",
    "I": "india",
    "J": "juliet",
    "K": "kilo",
    "L": "lima",
    "M": "mike",
    "N": "november",
    "O": "oscar",
    "P": "papa",
    "Q": "quebec",
    "R": "romeo",
    "S": "sierra",
    "T": "tango",
    "U": "uniform",
    "V": "victor",
    "W": "whiskey",
    "X": "x-ray",
    "Y": "yankee",
    "Z": "zulu",
}

DIGIT_WORDS: dict[str, str] = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "niner",
}


def phonetic_callsign(callsign: str, *, joiner: str = " ") -> str:
    """Render ``K1AF`` -> "kilo one alpha foxtrot".

    ``/`` (portable/mobile) is rendered as "stroke" between segments.
    """
    out: list[str] = []
    for ch in callsign.upper():
        if ch in NATO_FROM_LETTER:
            out.append(NATO_FROM_LETTER[ch])
        elif ch.isdigit():
            out.append(DIGIT_WORDS[ch])
        elif ch == "/":
            out.append("stroke")
        elif ch.isspace():
            continue
        else:
            out.append(ch)
    return joiner.join(out)


def render_cq_template(template: str, *, callsign: str) -> str:
    """Replace ``{callsign_phonetic}`` and ``{callsign}`` placeholders."""
    phon = phonetic_callsign(callsign)
    return (
        template
        .replace("{callsign_phonetic}", phon)
        .replace("{callsign}", callsign.upper())
    )


def render_freq_check(callsign: str, *, attempt: int) -> str:
    """The phrasing for each of the three courtesy-check attempts."""
    if attempt <= 1:
        return "Is this frequency in use?"
    phon = phonetic_callsign(callsign)
    return f"Is this frequency in use? {phon}."
