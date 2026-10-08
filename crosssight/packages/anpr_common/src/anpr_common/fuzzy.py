"""Weighted fuzzy plate matching with confusion-pair-aware costs."""

from __future__ import annotations

from anpr_common.grammar import DIGIT_TO_LETTER, LETTER_TO_DIGIT, STATE_CODES, normalize_plate

CONFUSION_PAIRS: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"0", "O"}),
        frozenset({"1", "I"}),
        frozenset({"2", "Z"}),
        frozenset({"5", "S"}),
        frozenset({"8", "B"}),
        frozenset({"6", "G"}),
    }
)

# Also accept reverse letter↔digit already covered above.


def _is_confusion(a: str, b: str) -> bool:
    if a == b:
        return False
    pair = frozenset({a.upper(), b.upper()})
    if pair in CONFUSION_PAIRS:
        return True
    # Cross-map via DIGIT_TO_LETTER tables
    au, bu = a.upper(), b.upper()
    if DIGIT_TO_LETTER.get(au) == bu or LETTER_TO_DIGIT.get(au) == bu:
        return True
    return DIGIT_TO_LETTER.get(bu) == au or LETTER_TO_DIGIT.get(bu) == au


def weighted_edit_distance(a: str, b: str) -> float:
    """Edit distance where confusion-pair substitutions cost 0.3; others cost 1.0."""
    a = a.upper()
    b = b.upper()
    n, m = len(a), len(b)
    if n == 0:
        return float(m)
    if m == 0:
        return float(n)

    prev = [float(j) for j in range(m + 1)]
    for i in range(1, n + 1):
        cur = [float(i)] + [0.0] * m
        for j in range(1, m + 1):
            if a[i - 1] == b[j - 1]:
                sub = prev[j - 1]
            else:
                cost = 0.3 if _is_confusion(a[i - 1], b[j - 1]) else 1.0
                sub = prev[j - 1] + cost
            cur[j] = min(prev[j] + 1.0, cur[j - 1] + 1.0, sub)
        prev = cur
    return prev[m]


def candidates(
    plate: str,
    pool: list[str] | None = None,
    max_cost: float = 1.0,
) -> list[tuple[str, float]]:
    """Return watchlist/trajectory candidates within max_cost of ``plate``.

    If ``pool`` is None, generates a small neighborhood by applying single
    confusion substitutions (useful for tests). Callers should pass the
    watchlist or known plate set in production.
    """
    norm = normalize_plate(plate).norm or plate.upper().replace(" ", "")
    if pool is None:
        pool = _neighbor_pool(norm)
    out: list[tuple[str, float]] = []
    for other in pool:
        other_norm = normalize_plate(other).norm or other.upper().replace(" ", "")
        cost = weighted_edit_distance(norm, other_norm)
        if cost <= max_cost:
            out.append((other_norm, cost))
    out.sort(key=lambda x: (x[1], x[0]))
    return out


def _neighbor_pool(norm: str) -> list[str]:
    """Generate confusion-substitution neighbors for offline candidate search."""
    pool = {norm}
    chars = list(norm)
    for i, ch in enumerate(chars):
        swaps: list[str] = []
        if ch in DIGIT_TO_LETTER:
            swaps.append(DIGIT_TO_LETTER[ch])
        if ch in LETTER_TO_DIGIT:
            swaps.append(LETTER_TO_DIGIT[ch])
        for s in swaps:
            alt = chars.copy()
            alt[i] = s
            pool.add("".join(alt))
    # Also include same-state plates differing by one serial digit for tests
    if len(norm) >= 2 and norm[:2] in STATE_CODES:
        pool.add(norm)
    return sorted(pool)
