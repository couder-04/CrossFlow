"""Evaluation metrics for external footage. These functions do not train a model."""

from __future__ import annotations

from anpr_common.fuzzy import weighted_edit_distance


def exact_match(pred: str, gt: str) -> bool:
    return pred.upper().replace(" ", "") == gt.upper().replace(" ", "")


def character_accuracy(pred: str, gt: str) -> float:
    pred_n = pred.upper().replace(" ", "")
    gt_n = gt.upper().replace(" ", "")
    if not gt_n:
        return 0.0
    dist = weighted_edit_distance(pred_n, gt_n)
    # Confusion-pair edits are fractional; clamp so the score stays in 0..1.
    return max(0.0, 1.0 - (dist / max(len(gt_n), len(pred_n), 1)))


def tracking_consistency(observations: list[tuple[str, str]]) -> float | None:
    """Share of track ids that keep a single plate. None when there are no tracks."""
    plates: dict[str, set[str]] = {}
    for track_id, plate in observations:
        plates.setdefault(track_id, set()).add(plate.upper())
    if not plates:
        return None
    stable = sum(1 for values in plates.values() if len(values) == 1)
    return stable / len(plates)


def summarize_ocr(pairs: list[tuple[str, str]]) -> dict:
    if not pairs:
        return {
            "n": 0,
            "exact": None,
            "character_accuracy": None,
            "failures": [],
            "claim": "no ground truth; accuracy is not claimed",
        }
    exact = sum(1 for pred, gt in pairs if exact_match(pred, gt))
    char = sum(character_accuracy(pred, gt) for pred, gt in pairs) / len(pairs)
    failures = [
        {"prediction": pred, "ground_truth": gt} for pred, gt in pairs if not exact_match(pred, gt)
    ][:25]
    return {
        "n": len(pairs),
        "exact": exact / len(pairs),
        "character_accuracy": char,
        "failures": failures,
        "claim": "measured on the supplied ground truth only",
    }


def matching_summary(links: list[dict], ground_truth: list[tuple[str, str]] | None = None) -> dict:
    by_kind: dict[str, int] = {}
    for link in links:
        kind = str(link.get("kind"))
        by_kind[kind] = by_kind.get(kind, 0) + 1
    out: dict = {"counts": by_kind, "n": len(links), "accuracy": None}
    if not ground_truth:
        out["claim"] = "no linkage ground truth; matching accuracy is not claimed"
        return out
    predicted = {(str(item.get("a")), str(item.get("b"))) for item in links}
    truth = set(ground_truth)
    tp = len(predicted & truth)
    precision = tp / len(predicted) if predicted else None
    recall = tp / len(truth) if truth else None
    out["precision"] = precision
    out["recall"] = recall
    out["claim"] = "measured against the supplied linkage ground truth only"
    return out


def latency_summary(samples_ms: list[float]) -> dict | None:
    if not samples_ms:
        return None
    ordered = sorted(samples_ms)
    return {
        "n": len(ordered),
        "mean_ms": sum(ordered) / len(ordered),
        "p50_ms": ordered[len(ordered) // 2],
        "max_ms": ordered[-1],
    }
