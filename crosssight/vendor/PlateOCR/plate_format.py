"""
Format-constrained decoding of fast-plate-ocr outputs.

The OCR model outputs, for every character slot, a probability over the alphabet. Plain decoding
takes the argmax per slot, which can produce impossible plates (e.g. state code "KH"). Here we
instead pick the most probable string that matches a known plate grammar.

India (Motor Vehicles Act formats):
    SS D{1,2} L{0,3} D{4}      e.g. MH12AB1234, DL3CAB1234, KL07BX7197
    SS D{1,2} L{0,3} D{3}      older 3-digit serials
    DD BH D{4} L{1,2}          Bharat series, e.g. 22BH1234AA
SS must be a valid state / union-territory code.

Inference extras (no retraining): lookalike probability remix (0↔Q/O, 1↔I, …).
"""

from __future__ import annotations

import numpy as np

INDIA_STATE_CODES = (
    "AN AP AR AS BR CG CH DD DL DN GA GJ HP HR JH JK KA KL LA LD MH ML MN MP MZ NL OD OR PB PY "
    "RJ SK TG TN TR TS UA UK UP WB"
).split()

DIGITS = "0123456789"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

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
DIGIT_TO_LETTER_LOOKALIKE = {"0": "O", "1": "I", "2": "Z", "5": "S", "6": "G", "8": "B"}


def remix_lookalike_probs(
    probs: np.ndarray,
    alphabet: str,
    strength: float = 0.4,
) -> np.ndarray:
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
    """Each template is a list of allowed-character sets, one per slot ("S" marks the state pair)."""
    out = []
    for d in (1, 2):
        for n_letters in range(4):
            for n_serial in (4, 3):
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
        ids, score = [], 0.0
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
        score += 0.02 * len(ids)
        score += float(logp[len(ids) :, pad].sum())
        if score > best_score:
            best_score, best_ids = score, ids

    text = "".join(alphabet[i] for i in best_ids)
    confs = [float(probs[s, i]) for s, i in enumerate(best_ids)]
    return text, confs
