"""Trajectory logic unit tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from api.trajectory_logic import (
    CameraPairInfo,
    Sighting,
    build_trajectory_legs,
    flag_impossible_hops,
    merge_fuzzy_reads,
    required_speed_kmh,
)


def _sighting(
    camera_id: str,
    offset_seconds: int,
    plate: str = "BR01AB1234",
    vehicle_class: str = "car",
    color: str | None = "white",
) -> Sighting:
    base = datetime(2025, 1, 1, 8, 0, tzinfo=UTC)
    return Sighting(
        camera_id=camera_id,
        ts=base + timedelta(seconds=offset_seconds),
        plate_norm=plate,
        confidence=0.9,
        vehicle_class=vehicle_class,
        color=color,
    )


def _pairs() -> dict[tuple[str, str], CameraPairInfo]:
    return {
        ("cam-a", "cam-b"): CameraPairInfo("cam-a", "cam-b", 1000.0, True),
        ("cam-b", "cam-c"): CameraPairInfo("cam-b", "cam-c", 1000.0, True),
        ("cam-a", "cam-c"): CameraPairInfo("cam-a", "cam-c", 2500.0, False),
    }


def test_required_speed_kmh():
    assert required_speed_kmh(1000.0, 3600.0) == pytest.approx(1.0)
    assert required_speed_kmh(1000.0, 60.0) == pytest.approx(60.0)


def test_flag_impossible_hops_detects_too_fast():
    sightings = [_sighting("cam-a", 0), _sighting("cam-b", 15)]
    flags = flag_impossible_hops(sightings, _pairs(), max_speed_kmh=120.0)
    assert flags == [True]


def test_flag_impossible_hops_allows_feasible_travel():
    sightings = [_sighting("cam-a", 0), _sighting("cam-b", 120)]
    flags = flag_impossible_hops(sightings, _pairs(), max_speed_kmh=120.0)
    assert flags == [False]


def test_merge_fuzzy_reads_requires_matching_attributes():
    main = [_sighting("cam-a", 0, vehicle_class="car", color="white")]
    compatible = [_sighting("cam-b", 300, plate="BR01AB1235", vehicle_class="car", color="white")]
    incompatible = [_sighting("cam-c", 600, plate="BR01AB1236", vehicle_class="truck", color="red")]
    merged = merge_fuzzy_reads(
        main,
        {
            "BR01AB1235": compatible,
            "BR01AB1236": incompatible,
        },
    )
    plates = {s.plate_norm for s in merged}
    assert "BR01AB1235" in plates
    assert "BR01AB1236" not in plates


def test_build_trajectory_legs_gap_fills_non_adjacent_path():
    sightings = [_sighting("cam-a", 0), _sighting("cam-c", 1200)]
    coords = {
        "cam-a": (18.5, 73.8),
        "cam-b": (18.51, 73.81),
        "cam-c": (18.52, 73.82),
    }
    legs = build_trajectory_legs(
        sightings,
        _pairs(),
        coords,
        max_speed_kmh=120.0,
        free_flow_speed_kmh=40.0,
    )
    assert len(legs) >= 2
    assert any(not leg.observed for leg in legs)
    assert all(leg.feasible for leg in legs if not leg.impossible_hop)
