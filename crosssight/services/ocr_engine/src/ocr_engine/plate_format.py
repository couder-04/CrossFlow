"""Indian plate-format constrained decoding for CCT OCR slot probabilities.

Ported from vendor/PlateOCR/plate_format.py (Ajitesh-07/PlateOCR).
Adds TG alongside TS to match anpr_common.grammar state whitelist.

Inference extras (no retraining):
  - lookalike probability remix before template search (0↔Q/O, 1↔I, …)
  - optional 3-digit trailing serial templates (older plates)
"""

from __future__ import annotations

import numpy as np

INDIA_STATE_CODES = [
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
]

DIGITS = "0123456789"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Transfer mass from OCR lookalikes into the class the Indian grammar expects.
# Helps when the model puts mass on Q/O for a digit-0 slot (or I for 1, etc.).
LETTER_TO_DIGIT_LOOKALIKE = {
    "O": "0",
    "Q": "0",
    "D": "0",
    "I": "1",
    "L": "1",
    "Z": "2",
    "S": "5",
    "G": "6",
    "B": "8",
}
DIGIT_TO_LETTER_LOOKALIKE = {v: k for k, v in LETTER_TO_DIGIT_LOOKALIKE.items() if v in DIGITS}
# Prefer canonical letter for each digit when both map (0→O not Q).
DIGIT_TO_LETTER_LOOKALIKE = {"0": "O", "1": "I", "2": "Z", "5": "S", "6": "G", "8": "B"}


def remix_lookalike_probs(
    probs: np.ndarray,
    alphabet: str,
    strength: float = 0.4,
) -> np.ndarray:
    """Blend lookalike character mass so format decode sees stronger digit/letter signals.

    For each slot, add ``strength * P(lookalike)`` into the canonical digit/letter, then
    renormalize. Does not change ranking among unrelated characters much; helps templates
    that require digits where the model spilled mass onto Q/O/I/….
    """
    if strength <= 0:
        return probs
    out = probs.astype(np.float64, copy=True)
    idx = {c: i for i, c in enumerate(alphabet)}
    for src, dst in LETTER_TO_DIGIT_LOOKALIKE.items():
        if src in idx and dst in idx:
            out[:, idx[dst]] += strength * probs[:, idx[src]]
    for src, dst in DIGIT_TO_LETTER_LOOKALIKE.items():
        if src in idx and dst in idx:
            out[:, idx[dst]] += strength * probs[:, idx[src]]
    out = np.clip(out, 1e-9, None)
    out /= out.sum(axis=1, keepdims=True)
    return out


def _india_templates(max_slots: int) -> list[list[str]]:
    """Each template is a list of allowed-character sets, one per slot ("S" = state pair)."""
    out: list[list[str]] = []
    for d in (1, 2):
        for n_letters in range(4):
            for n_serial in (4, 3):  # 4-digit standard; 3-digit older serials
                t = ["S", "S"] + [DIGITS] * d + [LETTERS] * n_letters + [DIGITS] * n_serial
                if len(t) <= max_slots:
                    out.append(t)
    for n_letters in (1, 2):  # Bharat series
        t = [DIGITS, DIGITS, "B", "H"] + [DIGITS] * 4 + [LETTERS] * n_letters
        if len(t) <= max_slots:
            out.append(t)
    return out


def decode_india(
    probs: np.ndarray,
    alphabet: str,
    pad_char: str,
    *,
    remix_strength: float = 0.4,
) -> tuple[str, list[float]]:
    """
    probs: (max_slots, len(alphabet)) softmax output for one plate.
    Returns the most probable valid Indian plate and its per-character probabilities.
    """
    remixed = remix_lookalike_probs(probs, alphabet, strength=remix_strength)
    max_slots = remixed.shape[0]
    logp = np.log(np.clip(remixed, 1e-9, 1.0))
    idx = {c: i for i, c in enumerate(alphabet)}
    pad = idx[pad_char]
    state_pairs = [(idx[s[0]], idx[s[1]]) for s in INDIA_STATE_CODES if s[0] in idx and s[1] in idx]

    best_score, best_ids = -np.inf, None
    for tmpl in _india_templates(max_slots):
        ids: list[int] = []
        score = 0.0
        if tmpl[0] == "S":
            a, b = max(state_pairs, key=lambda p: logp[0, p[0]] + logp[1, p[1]])
            ids += [a, b]
            score += logp[0, a] + logp[1, b]
            rest = tmpl[2:]
        else:
            rest = tmpl
        for slot, allowed in enumerate(rest, start=len(ids)):
            cand = [idx[c] for c in allowed if c in idx]
            j = max(cand, key=lambda k: logp[slot, k])
            ids.append(j)
            score += logp[slot, j]
        # Prefer fuller plates slightly when scores are close (avoid chopping serial digit).
        score += 0.02 * len(ids)
        score += float(logp[len(ids) :, pad].sum())
        if score > best_score:
            best_score, best_ids = score, ids

    assert best_ids is not None
    # Report confidence from original (unremixed) probs for calibrated mean conf.
    text = "".join(alphabet[i] for i in best_ids)
    confs = [float(probs[s, i]) for s, i in enumerate(best_ids)]
    return text, confs
