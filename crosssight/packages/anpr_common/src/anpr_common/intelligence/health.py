"""Camera health from read timestamps and rates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class HealthState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    OFFLINE = "OFFLINE"


@dataclass(frozen=True)
class CameraHealthThresholds:
    stale_after_s: float = 300.0
    offline_after_s: float = 900.0
    expected_reads_per_min: float = 1.0
    rate_low_ratio: float = 0.25
    rate_high_ratio: float = 4.0
    window_s: float = 300.0

    def __post_init__(self) -> None:
        if self.stale_after_s <= 0 or self.offline_after_s < self.stale_after_s:
            raise ValueError("offline threshold must be >= stale threshold > 0")


def classify_camera_health(
    *,
    status: str,
    now: datetime,
    last_read: datetime | None,
    reads_in_window: int,
    thresholds: CameraHealthThresholds,
    event_count: int | None = None,
    expected_events: float | None = None,
) -> dict:
    age_s = None if last_read is None else max(0.0, (now - last_read).total_seconds())
    window_min = thresholds.window_s / 60.0 if thresholds.window_s > 0 else 1.0
    read_rate = reads_in_window / window_min
    reasons: list[str] = []

    normalized = (status or "active").lower()
    if normalized in {"offline", "disabled", "inactive"}:
        state = HealthState.OFFLINE
        reasons.append("camera_status")
    elif last_read is None or (age_s is not None and age_s >= thresholds.offline_after_s):
        state = HealthState.OFFLINE
        reasons.append("no_reads" if last_read is None else "read_age")
    elif age_s is not None and age_s >= thresholds.stale_after_s:
        state = HealthState.STALE
        reasons.append("stale_reads")
    else:
        state = HealthState.HEALTHY
        expected = thresholds.expected_reads_per_min
        if expected > 0 and reads_in_window >= 0:
            low = expected * thresholds.rate_low_ratio
            high = expected * thresholds.rate_high_ratio
            if read_rate < low or read_rate > high:
                state = HealthState.DEGRADED
                reasons.append("abnormal_read_rate")
        if (
            state is HealthState.HEALTHY
            and event_count is not None
            and expected_events is not None
            and expected_events > 0
        ):
            event_rate = event_count / window_min
            if event_rate < expected_events * thresholds.rate_low_ratio or (
                event_rate > expected_events * thresholds.rate_high_ratio
            ):
                state = HealthState.DEGRADED
                reasons.append("abnormal_event_frequency")

    return {
        "state": state.value,
        "last_read": last_read,
        "age_s": age_s,
        "read_rate_per_min": read_rate,
        "reads_in_window": reads_in_window,
        "reasons": reasons,
    }
