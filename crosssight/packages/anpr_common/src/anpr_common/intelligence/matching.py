"""Cross-camera identity linking.

Plate equality is the strongest signal. Feature matching is only reported when
both embeddings are supplied. This repository does not ship an evaluated ReID
model, so callers must not quote FEATURE_MATCH accuracy.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Protocol

from anpr_common.fuzzy import weighted_edit_distance


class MatchKind(str, Enum):
    HIGH_CONFIDENCE_PLATE_MATCH = "HIGH_CONFIDENCE_PLATE_MATCH"
    FUZZY_PLATE_MATCH = "FUZZY_PLATE_MATCH"
    FEATURE_MATCH = "FEATURE_MATCH"
    TOPOLOGY_TIME_MATCH = "TOPOLOGY_TIME_MATCH"


class ReIDEncoder(Protocol):
    def embed(self, image: bytes) -> list[float]:
        """Return an appearance embedding. Not implemented by a bundled model."""


class UnavailableReIDEncoder:
    """Placeholder encoder. Embedding is not available and has not been evaluated."""

    def embed(self, image: bytes) -> list[float]:
        raise RuntimeError("ReID encoder is not configured; no accuracy claim is valid")


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def classify_link(
    *,
    plate_a: str,
    plate_b: str,
    conf_a: float,
    conf_b: float,
    class_a: str | None = None,
    class_b: str | None = None,
    color_a: str | None = None,
    color_b: str | None = None,
    elapsed_s: float | None = None,
    min_travel_s: float | None = None,
    max_travel_s: float | None = None,
    topology_ok: bool = False,
    embedding_a: list[float] | None = None,
    embedding_b: list[float] | None = None,
    embed_threshold: float = 0.85,
) -> dict | None:
    pa = plate_a.upper().replace(" ", "")
    pb = plate_b.upper().replace(" ", "")
    conf = min(conf_a, conf_b)
    if pa and pa == pb and conf >= 0.8:
        return {"kind": MatchKind.HIGH_CONFIDENCE_PLATE_MATCH.value, "score": conf, "plate": pa}
    if pa and pa == pb and conf >= 0.5:
        return {"kind": MatchKind.HIGH_CONFIDENCE_PLATE_MATCH.value, "score": conf, "plate": pa}
    if pa and pb:
        cost = weighted_edit_distance(pa, pb)
        if 0 < cost <= 1.0:
            return {
                "kind": MatchKind.FUZZY_PLATE_MATCH.value,
                "score": max(0.0, 1.0 - cost),
                "edit_cost": cost,
                "plate_a": pa,
                "plate_b": pb,
            }
    class_ok = bool(class_a and class_b and class_a == class_b)
    color_ok = color_a is None or color_b is None or color_a.lower() == color_b.lower()
    if embedding_a is not None and embedding_b is not None and class_ok:
        sim = cosine(embedding_a, embedding_b)
        if sim >= embed_threshold and color_ok:
            return {
                "kind": MatchKind.FEATURE_MATCH.value,
                "score": sim,
                "evaluated": False,
                "note": "embedding similarity only; ReID accuracy is not claimed",
            }
    in_window = (
        elapsed_s is not None
        and min_travel_s is not None
        and max_travel_s is not None
        and min_travel_s <= elapsed_s <= max_travel_s
    )
    if topology_ok and in_window and class_ok and color_ok:
        assert elapsed_s is not None and min_travel_s is not None and max_travel_s is not None
        span = max(max_travel_s - min_travel_s, 1.0)
        closeness = 1.0 - min(abs(elapsed_s - (min_travel_s + max_travel_s) / 2) / span, 1.0)
        return {
            "kind": MatchKind.TOPOLOGY_TIME_MATCH.value,
            "score": max(0.1, closeness),
            "elapsed_s": elapsed_s,
        }
    return None
