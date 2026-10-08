"""Indian license plate grammar normalization and validation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

PlateFormat = Literal["standard", "bh", "nonstandard"]

STATE_CODES: frozenset[str] = frozenset(
    {
        "AN",
        "AP",
        "AR",
        "AS",
        "BR",
        "CG",
        "CH",
        "DD",
        "DL",
        "DN",
        "GA",
        "GJ",
        "HP",
        "HR",
        "JH",
        "JK",
        "KA",
        "KL",
        "LA",
        "LD",
        "MH",
        "ML",
        "MN",
        "MP",
        "MZ",
        "NL",
        "OD",
        "OR",
        "PB",
        "PY",
        "RJ",
        "SK",
        "TG",
        "TN",
        "TR",
        "TS",
        "UA",
        "UK",
        "UP",
        "WB",
    }
)

STANDARD_RE = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$")
BH_RE = re.compile(r"^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$")

DIGIT_TO_LETTER = {"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G"}
LETTER_TO_DIGIT = {v: k for k, v in DIGIT_TO_LETTER.items()}


@dataclass(frozen=True)
class GrammarResult:
    norm: str
    valid: bool
    format: PlateFormat
    corrections: list[tuple[int, str, str]] = field(default_factory=list)


def _strip(raw: str) -> str:
    return "".join(ch for ch in raw.upper() if ch.isalnum())


def _validate_standard(norm: str) -> bool:
    return bool(STANDARD_RE.match(norm)) and norm[:2] in STATE_CODES


def _validate_bh(norm: str) -> bool:
    return bool(BH_RE.match(norm))


def _apply_at_positions(norm: str, letter_pos: set[int]) -> tuple[str, list[tuple[int, str, str]]]:
    chars = list(norm)
    corrections: list[tuple[int, str, str]] = []
    for i, ch in enumerate(chars):
        if i in letter_pos:
            if ch in DIGIT_TO_LETTER:
                to = DIGIT_TO_LETTER[ch]
                corrections.append((i, ch, to))
                chars[i] = to
        else:
            if ch in LETTER_TO_DIGIT:
                to = LETTER_TO_DIGIT[ch]
                corrections.append((i, ch, to))
                chars[i] = to
    return "".join(chars), corrections


def _standard_parses(length: int) -> list[set[int]]:
    """Return letter-position sets for every structurally valid standard layout."""
    parses: list[set[int]] = []
    for rto_len in (1, 2):
        for letter_len in (0, 1, 2, 3):
            if 2 + rto_len + letter_len + 4 != length:
                continue
            letter_pos = {0, 1}
            start = 2 + rto_len
            letter_pos |= set(range(start, start + letter_len))
            parses.append(letter_pos)
    return parses


def _bh_parses(length: int) -> list[set[int]]:
    """BH: ## BH #### X{1,2} — letters at BH and trailing series."""
    parses: list[set[int]] = []
    for trail in (1, 2):
        if 2 + 2 + 4 + trail != length:
            continue
        letter_pos = {2, 3} | set(range(8, 8 + trail))
        parses.append(letter_pos)
    return parses


def normalize_plate(raw: str) -> GrammarResult:
    """Normalize and validate an Indian plate string.

    Only applies confusion corrections when the corrected string becomes valid.
    Non-matching strings are returned as nonstandard without force-correction.
    """
    norm = _strip(raw)
    if not norm:
        return GrammarResult(norm="", valid=False, format="nonstandard", corrections=[])

    if _validate_bh(norm):
        return GrammarResult(norm=norm, valid=True, format="bh", corrections=[])
    if _validate_standard(norm):
        return GrammarResult(norm=norm, valid=True, format="standard", corrections=[])

    candidates: list[tuple[str, list[tuple[int, str, str]], PlateFormat]] = []

    for letter_pos in _bh_parses(len(norm)):
        corrected, corrs = _apply_at_positions(norm, letter_pos)
        if _validate_bh(corrected):
            candidates.append((corrected, corrs, "bh"))

    for letter_pos in _standard_parses(len(norm)):
        corrected, corrs = _apply_at_positions(norm, letter_pos)
        if _validate_standard(corrected):
            candidates.append((corrected, corrs, "standard"))

    if not candidates:
        return GrammarResult(norm=norm, valid=False, format="nonstandard", corrections=[])

    # Prefer fewest corrections; then prefer already-"natural" format order (standard, bh).
    candidates.sort(key=lambda c: (len(c[1]), 0 if c[2] == "standard" else 1, c[0]))
    corrected, corrs, fmt = candidates[0]
    return GrammarResult(norm=corrected, valid=True, format=fmt, corrections=corrs)
