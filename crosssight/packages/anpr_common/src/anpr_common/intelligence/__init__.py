"""Deterministic traffic-intelligence helpers shared by workers and the API."""

from anpr_common.intelligence.access import role_can
from anpr_common.intelligence.calibration import apply_homography, validate_calibration
from anpr_common.intelligence.dwell import (
    DwellClass,
    DwellState,
    DwellThresholds,
    advance_dwell,
    classify_dwell,
    dwell_anomaly,
    thresholds_for_camera,
)
from anpr_common.intelligence.exporters import (
    export_filename,
    rows_to_csv,
    rows_to_json,
    rows_to_pdf,
)
from anpr_common.intelligence.health import (
    CameraHealthThresholds,
    HealthState,
    classify_camera_health,
)
from anpr_common.intelligence.journeys import (
    aggregate_od,
    aggregate_vehicle_class_counts,
    aggregate_vehicle_classes,
    sessionize_dwell,
    travel_statistics,
    valid_travel,
)
from anpr_common.intelligence.matching import MatchKind, classify_link
from anpr_common.intelligence.review import ReviewError, transition_status

__all__ = [
    "CameraHealthThresholds",
    "DwellClass",
    "DwellState",
    "DwellThresholds",
    "HealthState",
    "MatchKind",
    "ReviewError",
    "advance_dwell",
    "aggregate_od",
    "aggregate_vehicle_class_counts",
    "aggregate_vehicle_classes",
    "apply_homography",
    "classify_camera_health",
    "classify_dwell",
    "classify_link",
    "dwell_anomaly",
    "export_filename",
    "role_can",
    "rows_to_csv",
    "rows_to_json",
    "rows_to_pdf",
    "sessionize_dwell",
    "thresholds_for_camera",
    "transition_status",
    "travel_statistics",
    "valid_travel",
    "validate_calibration",
]
