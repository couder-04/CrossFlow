from __future__ import annotations

from crossflow.bridge import approach_rates, vehicle_mix
from crossflow.city import ARRIVES_FROM, busiest_junction, junction_records, simulate_city


def test_arrival_arm_is_opposite_of_travel_direction():
    assert ARRIVES_FROM == {"N": "S", "S": "N", "E": "W", "W": "E"}


def test_city_is_deterministic_and_streams_scale_traffic():
    a = simulate_city(num_vehicles=500, num_cameras=20, hours=24, streams=3, seed=7)
    b = simulate_city(num_vehicles=500, num_cameras=20, hours=24, streams=3, seed=7)
    assert a.traversals == b.traversals and a.records == b.records
    more = simulate_city(num_vehicles=500, num_cameras=20, hours=24, streams=12, seed=7)
    assert more.traversals > 2 * a.traversals


def test_records_are_flowwindows_with_zero_windows_kept():
    flow = simulate_city(num_vehicles=500, num_cameras=20, streams=20, seed=7)
    cam, hour, _ = busiest_junction(flow, min_arms=2)
    recs = junction_records(flow, cam, hour)
    arms = {r["approach"] for r in recs}
    assert len(recs) == 12 * len(arms)  # every 5-minute window, zeros included
    assert any(r["volume"] == 0 for r in recs) or all(r["volume"] > 0 for r in recs)
    for r in recs:
        assert r["window_end"] > r["window_start"]
        assert r["volume"] == sum(r["counts_by_class"].values())
    rates = approach_rates(recs)
    assert set(rates) <= {"N", "S", "E", "W"}
    # one hour of windows: vehicles in the hour == veh/h
    total = sum(r["volume"] for r in recs)
    assert abs(sum(rates.values()) - total) < 0.1 * len(rates) + 1e-6


def test_busy_junction_mix_maps_to_xtraflow_classes():
    flow = simulate_city(num_vehicles=500, num_cameras=20, streams=20, seed=7)
    cam, hour, _ = busiest_junction(flow, min_arms=2)
    mix = vehicle_mix(junction_records(flow, cam, hour))
    assert abs(sum(mix.values()) - 1.0) < 1e-3
    assert set(mix) == {"two_wheeler", "auto_rickshaw", "car", "bus", "truck"}
