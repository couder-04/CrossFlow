"""Role matrix for new operational actions. Existing route deps stay in place."""

from __future__ import annotations

_ACTIONS: dict[str, frozenset[str]] = {
    "view_analytics": frozenset({"admin", "operator", "analyst"}),
    "view_health": frozenset({"admin", "operator", "analyst"}),
    "review_alert": frozenset({"admin", "operator"}),
    "upload_video": frozenset({"admin", "operator"}),
    "upload_image": frozenset({"admin", "operator"}),
    "import_watchlist": frozenset({"admin", "operator"}),
    "import_registry": frozenset({"admin"}),
    "import_cameras": frozenset({"admin"}),
    "import_calibration": frozenset({"admin"}),
    "import_zones": frozenset({"admin"}),
    "export_aggregates": frozenset({"admin", "operator", "analyst"}),
    "export_plates": frozenset({"admin", "operator"}),
    "download_evidence": frozenset({"admin", "operator"}),
    "investigate": frozenset({"admin", "operator"}),
}

PLATE_EXPORTS = frozenset(
    {
        "incidents",
        "enforcement",
        "sightings",
        "investigation",
        "watchlist",
        "registry",
    }
)


def role_can(role: str, action: str) -> bool:
    return role in _ACTIONS.get(action, frozenset())


def export_action(kind: str) -> str:
    if kind in PLATE_EXPORTS:
        return "export_plates"
    return "export_aggregates"
