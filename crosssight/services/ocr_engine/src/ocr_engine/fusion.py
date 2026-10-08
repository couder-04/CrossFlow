"""Track-level plate read fusion across frames."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass
class FrameRead:
    """Single-frame recognition candidate attached to a track."""

    text: str
    char_probs: list[float]
    quality: float


@dataclass
class FusionResult:
    text: str
    char_conf: list[float]
    confidence: float
    alternates: list[str] = field(default_factory=list)


def _levenshtein_matrix(a: str, b: str) -> list[list[int]]:
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    return dp


def _align_to_reference(ref: str, other: str) -> list[tuple[str | None, str | None]]:
    """Align ``other`` to ``ref`` using Levenshtein backtrace; gaps are None."""
    dp = _levenshtein_matrix(ref, other)
    i, j = len(ref), len(other)
    pairs_rev: list[tuple[str | None, str | None]] = []
    while i > 0 or j > 0:
        if (
            i > 0
            and j > 0
            and dp[i][j] == dp[i - 1][j - 1] + (0 if ref[i - 1] == other[j - 1] else 1)
        ):
            pairs_rev.append((ref[i - 1], other[j - 1]))
            i -= 1
            j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            pairs_rev.append((ref[i - 1], None))
            i -= 1
        else:
            pairs_rev.append((None, other[j - 1]))
            j -= 1
    pairs_rev.reverse()
    return pairs_rev


def _read_weight(read: FrameRead) -> float:
    """Mean char confidence x quality. A mean, not a sum, so longer strings don't win by length."""
    if not read.text:
        return 0.0
    probs = read.char_probs or [0.5]
    return (sum(probs) / len(probs)) * read.quality


def _pick_reference(reads: Sequence[FrameRead]) -> str:
    """Pick the plate string with the most support across frames.

    Identical reads are grouped and their weights summed, so the reference is the text the track
    agrees on most, not the single read with the highest total confidence.
    """
    support: dict[str, float] = {}
    for read in reads:
        if read.text:
            support[read.text] = support.get(read.text, 0.0) + _read_weight(read)
    if not support:
        return ""
    return max(support.items(), key=lambda kv: kv[1])[0]


def fuse_track_reads(reads: Sequence[FrameRead], top_k: int = 3) -> FusionResult:
    """Fuse per-frame reads into one plate string with char confidences and alternates."""
    if not reads:
        return FusionResult(text="", char_conf=[], confidence=0.0, alternates=[])

    ref = _pick_reference(reads)
    if not ref:
        return FusionResult(text="", char_conf=[], confidence=0.0, alternates=[])

    # Collect votes per reference position by aligning each read to ref.
    position_votes: dict[int, dict[str, float]] = {}
    max_len = len(ref)

    for read in reads:
        pairs = _align_to_reference(ref, read.text)
        ref_pos = 0
        prob_idx = 0
        for ref_ch, other_ch in pairs:
            if ref_ch is not None:
                if other_ch is not None:
                    prob = read.char_probs[prob_idx] if prob_idx < len(read.char_probs) else 0.5
                    weight = prob * read.quality
                    ch = other_ch
                    prob_idx += 1
                else:
                    weight = 0.0
                    ch = ref_ch
                votes = position_votes.setdefault(ref_pos, {})
                votes[ch] = votes.get(ch, 0.0) + weight
                ref_pos += 1
            elif other_ch is not None:
                # Extra character with no reference position: skip it. Voting it into
                # ``ref_pos`` and advancing shifted every later vote of this read by one.
                prob_idx += 1

    chars: list[str] = []
    char_conf: list[float] = []
    for pos in range(max_len):
        votes = position_votes.get(pos, {ref[pos]: 0.0})
        if not votes:
            chars.append(ref[pos])
            char_conf.append(0.0)
            continue
        best_ch = max(votes.items(), key=lambda kv: kv[1])[0]
        total = sum(votes.values())
        conf = votes[best_ch] / total if total > 0 else 0.0
        chars.append(best_ch)
        char_conf.append(min(1.0, conf))

    fused = "".join(chars)
    confidence = sum(char_conf) / len(char_conf) if char_conf else 0.0

    # Top-k alternates: perturb low-confidence positions with runner-up chars.
    alternates: list[str] = []
    for _ in range(top_k):
        alt_chars = list(fused)
        weakest = sorted(range(len(char_conf)), key=lambda i: char_conf[i])
        changed = False
        for pos in weakest:
            votes = position_votes.get(pos, {})
            if len(votes) < 2:
                continue
            ranked = sorted(votes.items(), key=lambda kv: kv[1], reverse=True)
            if ranked[1][0] != alt_chars[pos]:
                alt_chars[pos] = ranked[1][0]
                changed = True
                break
        candidate = "".join(alt_chars)
        if changed and candidate not in alternates and candidate != fused:
            alternates.append(candidate)

    return FusionResult(
        text=fused,
        char_conf=char_conf,
        confidence=confidence,
        alternates=alternates[:top_k],
    )
