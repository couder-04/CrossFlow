"""Optional per-camera homography. Calibration is never required to run the simulator."""

from __future__ import annotations


def _det3(m: list[list[float]]) -> float:
    a, b, c = m
    return (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )


def validate_homography(matrix: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(matrix, list) or len(matrix) != 3:
        return ["homography must be a 3x3 matrix"]
    rows: list[list[float]] = []
    for i, row in enumerate(matrix):
        if not isinstance(row, list) or len(row) != 3:
            return ["homography must be a 3x3 matrix"]
        try:
            rows.append([float(v) for v in row])
        except (TypeError, ValueError):
            errors.append(f"homography row {i} is not numeric")
    if errors:
        return errors
    if abs(_det3(rows)) < 1e-9:
        errors.append("homography is singular")
    return errors


def apply_homography(matrix: list[list[float]], x: float, y: float) -> tuple[float, float]:
    errors = validate_homography(matrix)
    if errors:
        raise ValueError("; ".join(errors))
    a, b, c = matrix
    w = c[0] * x + c[1] * y + c[2]
    if abs(w) < 1e-12:
        raise ValueError("homography mapped the point to infinity")
    world_x = (a[0] * x + a[1] * y + a[2]) / w
    world_y = (b[0] * x + b[1] * y + b[2]) / w
    return world_x, world_y


def speed_kmh(p1: tuple[float, float], p2: tuple[float, float], elapsed_s: float) -> float | None:
    """Speed from map-metre coordinates. Returns None for a non-positive interval."""
    if elapsed_s <= 0:
        return None
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    metres = (dx * dx + dy * dy) ** 0.5
    return (metres / elapsed_s) * 3.6


def validate_calibration(doc: object) -> list[str]:
    if not isinstance(doc, dict):
        return ["calibration must be a JSON object"]
    errors: list[str] = []
    camera_id = doc.get("camera_id")
    if not isinstance(camera_id, str) or not camera_id.strip():
        errors.append("camera_id is required")
    if "homography" in doc and doc["homography"] is not None:
        errors.extend(validate_homography(doc["homography"]))
    ref = doc.get("coordinate_reference")
    if ref is not None and not isinstance(ref, str):
        errors.append("coordinate_reference must be a string")
    lanes = doc.get("lanes")
    if lanes is not None:
        if not isinstance(lanes, list):
            errors.append("lanes must be a list")
        else:
            for i, lane in enumerate(lanes):
                if not isinstance(lane, dict) or "id" not in lane:
                    errors.append(f"lanes[{i}] needs an id")
    speed = doc.get("speed_calibration")
    if speed is not None:
        if not isinstance(speed, dict):
            errors.append("speed_calibration must be an object")
        else:
            for key in ("metres_per_pixel", "fps"):
                if key in speed:
                    try:
                        if float(speed[key]) <= 0:
                            errors.append(f"speed_calibration.{key} must be positive")
                    except (TypeError, ValueError):
                        errors.append(f"speed_calibration.{key} must be numeric")
    return errors
