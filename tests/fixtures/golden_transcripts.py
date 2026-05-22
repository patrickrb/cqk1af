"""Golden (raw_whisper, corrected) text pairs for lingo_corrector tests.

Each pair represents a typical Whisper output on real ham audio and the
canonicalised form an operator expects to read in the dashboard transcript.
"""
from __future__ import annotations

GOLDEN_PAIRS: list[tuple[str, str]] = [
    # 73 / 88 sign-offs
    ("seventy three and seventy three", "73 and 73"),
    ("seven three, seven three, seven three", "73, 73, 73"),
    ("eighty-eight from Maria", "88 from Maria"),
    ("eight eight to the YL", "88 to the YL"),
    # Dotted Q-codes
    ("CQ CQ CQ this is Q.R.Z. five nine", "CQ CQ CQ this is QRZ five nine"),
    ("Q. S. L. on the fifty-nine report", "QSL on the fifty-nine report"),
    ("Q. R. M. on frequency, please QSY", "QRM on frequency, please QSY"),
    ("Q. T. H. is Boston", "QTH is Boston"),
    ("Q. R. Z. the frequency", "QRZ the frequency"),
    # CQ with dots
    ("C. Q. contest, C. Q. contest", "CQ contest, CQ contest"),
    # Signal-report shorthand
    ("you are five by nine into Boston", "you are five nine into Boston"),
    ("five by five with QSB", "five five with QSB"),
    # Mixed (NATO survives untouched)
    (
        "kilo one alpha foxtrot seventy three",
        "kilo one alpha foxtrot 73",
    ),
]
