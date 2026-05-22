"""Phonetic and ITU-prefix tables used by the callsign extractor."""
from __future__ import annotations

# NATO phonetic (with common variants/misrecognitions Whisper might emit)
NATO_TABLE: dict[str, str] = {
    "alpha": "A", "alfa": "A",
    "bravo": "B",
    "charlie": "C", "charly": "C",
    "delta": "D",
    "echo": "E",
    "foxtrot": "F", "fox": "F",
    "golf": "G",
    "hotel": "H",
    "india": "I", "indigo": "I",
    "juliet": "J", "juliett": "J",
    "kilo": "K",
    "lima": "L",
    "mike": "M",
    "november": "N",
    "oscar": "O",
    "papa": "P",
    "quebec": "Q",
    "romeo": "R",
    "sierra": "S",
    "tango": "T",
    "uniform": "U",
    "victor": "V",
    "whiskey": "W", "whisky": "W",
    "x-ray": "X", "xray": "X", "x ray": "X",
    "yankee": "Y",
    "zulu": "Z",
}

DIGIT_TABLE: dict[str, str] = {
    "zero": "0", "oh": "0", "naught": "0",
    "one": "1", "wun": "1",
    "two": "2", "too": "2",
    "three": "3", "tree": "3",
    "four": "4", "fower": "4", "for": "4",
    "five": "5", "fife": "5",
    "six": "6",
    "seven": "7",
    "eight": "8", "ate": "8",
    "nine": "9", "niner": "9",
}

# Common single-letter pronunciations Whisper sometimes emits when the speaker
# says individual letters (e.g. "A F" instead of "alpha foxtrot").
LETTER_HOMOPHONES: dict[str, str] = {
    "ay": "A", "bee": "B", "see": "C", "sea": "C", "dee": "D", "ee": "E",
    "ef": "F", "gee": "G", "jee": "G", "aitch": "H", "haitch": "H", "eye": "I",
    "jay": "J", "kay": "K", "el": "L", "em": "M", "en": "N", "oh": "O",
    "pee": "P", "cue": "Q", "queue": "Q", "ar": "R", "are": "R", "ess": "S",
    "tee": "T", "you": "U", "vee": "V", "double-u": "W", "double u": "W",
    "ex": "X", "why": "Y", "wye": "Y", "zee": "Z", "zed": "Z",
}

# ITU callsign prefix patterns — minimal subset used for validation.
# Source: ITU table of allocated prefixes. We're permissive on numeric parts.
US_PREFIXES = frozenset(
    list("AKNW") + ["AA", "AB", "AC", "AD", "AE", "AF", "AG", "AH", "AI", "AJ", "AK",
                    "KA", "KB", "KC", "KD", "KE", "KF", "KG", "KH", "KI", "KJ", "KK", "KL", "KM",
                    "KN", "KO", "KP", "KQ", "KR", "KS", "KT", "KU", "KV", "KW", "KX", "KY", "KZ",
                    "NA", "NB", "NC", "ND", "NE", "NF", "NG", "NH", "NI", "NJ", "NK", "NL", "NM",
                    "NN", "NO", "NP", "NQ", "NR", "NS", "NT", "NU", "NV", "NW", "NX", "NY", "NZ",
                    "WA", "WB", "WC", "WD", "WE", "WF", "WG", "WH", "WI", "WJ", "WK", "WL", "WM",
                    "WN", "WO", "WP", "WQ", "WR", "WS", "WT", "WU", "WV", "WW", "WX", "WY", "WZ"]
)

# Coarse one- or two-letter prefix list (rough — sufficient for SSB voice QSO use).
COMMON_PREFIXES = US_PREFIXES | frozenset([
    "VE", "VA", "VO", "VY",  # Canada
    "G", "GB", "GD", "GI", "GJ", "GM", "GU", "GW", "M", "MM", "MW",  # UK
    "F",  # France
    "DL", "DK", "DG", "DH", "DJ", "DD", "DB",  # Germany
    "EA", "EB", "EC",  # Spain
    "I",  # Italy
    "OH",  # Finland
    "JA", "JE", "JF", "JG", "JH", "JI", "JJ", "JK", "JL", "JM", "JN", "JO", "JP",  # Japan
    "VK", "VL",  # Australia
    "ZL", "ZM",  # New Zealand
    "PY", "PP", "PT", "PR", "PS", "PU", "PV", "PW",  # Brazil
    "LU",  # Argentina
    "ZS", "ZR",  # South Africa
])
