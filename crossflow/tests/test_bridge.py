from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from crossflow.bridge import (
    FlowError,
    approach_rates,
    load_camera_map,
    load_flow_windows,
    vehicle_mix,
    write_observed_counts,
)
from crossflow.bridge.cli import main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
ROOT = Path(__file__).resolve().parents[2]


def _rec(cam, start, end, volume=None, counts=None, lane=None):
    return {
        "camera_id": cam,
        "window_start": start,
        "window_end": end,
        "counts_by_class": counts or {},
        "volume": volume or 0,
        "lane": lane,
    }


def test_sample_rates_match_hand_calculation():
    cmap = load_camera_map(EXAMPLES / "camera_map.json")
    rates = approach_rates(load_flow_windows(EXAMPLES / "flows.sample.jsonl"), cmap)
    # 264 vehicles in 30 minutes -> 528 veh/h
    assert rates == {"N": 528.0, "S": 528.0, "E": 184.0, "W": 180.0}


def test_two_cameras_on_one_approach_add_and_lanes_sum():
    cmap = {"a": "N", "b": "N"}
    recs = [
        _rec("a", "2026-01-01T00:00:00+00:00", "2026-01-01T00:30:00+00:00", 50, lane=0),
        _rec("a", "2026-01-01T00:00:00+00:00", "2026-01-01T00:30:00+00:00", 50, lane=1),
        _rec("b", "2026-01-01T00:00:00+00:00", "2026-01-01T00:30:00+00:00", 100),
    ]
    # camera a: 100 veh / 0.5 h = 200; camera b: 200; the lanes share one window
    assert approach_rates(recs, cmap) == {"N": 400.0}


def test_duplicate_deliveries_are_not_double_counted():
    cmap = {"a": "E"}
    r = _rec("a", "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z", 300)
    assert approach_rates([r, dict(r)], cmap) == {"E": 300.0}


def test_unmapped_cameras_ignored_and_empty_approaches_omitted():
    cmap = {"a": "N"}
    recs = [
        _rec("a", "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z", 120),
        _rec("zzz", "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z", 9999),
    ]
    assert approach_rates(recs, cmap) == {"N": 120.0}


def test_volume_falls_back_to_class_counts():
    r = _rec("a", "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z", 0, {"car": 30, "bus": 10})
    assert approach_rates([r], {"a": "S"}) == {"S": 40.0}


@pytest.mark.parametrize(
    "start,end",
    [("2026-01-01T01:00:00Z", "2026-01-01T00:00:00Z"), ("nonsense", "2026-01-01T00:00:00Z")],
)
def test_bad_windows_rejected(start, end):
    with pytest.raises(FlowError):
        approach_rates([_rec("a", start, end, 5)], {"a": "N"})


def test_negative_counts_rejected():
    r = _rec("a", "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z", 0, {"car": -3})
    with pytest.raises(FlowError):
        approach_rates([r], {"a": "N"})


def test_camera_map_validates_approach(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"cam": "UP"}))
    with pytest.raises(FlowError):
        load_camera_map(p)


def test_mix_maps_classes_and_normalises():
    recs = [
        _rec(
            "a",
            "2026-01-01T00:00:00Z",
            "2026-01-01T01:00:00Z",
            counts={"motorcycle": 40, "car": 30, "auto": 10, "bus": 5, "truck": 15, "other": 99},
        )
    ]
    mix = vehicle_mix(recs)
    assert mix == {
        "two_wheeler": 0.4,
        "auto_rickshaw": 0.1,
        "car": 0.3,
        "bus": 0.05,
        "truck": 0.15,
    }
    assert sum(mix.values()) == pytest.approx(1.0)


def test_mix_without_classified_vehicles_fails():
    with pytest.raises(FlowError):
        vehicle_mix([_rec("a", "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z", 5)])


def test_csv_matches_xtraflow_calibrate_demand_contract(tmp_path):
    """calibrate_demand reads columns ``approach`` and ``count_veh_h``."""
    out = write_observed_counts({"N": 528.0, "W": 180.0}, tmp_path / "observed_counts.csv")
    rows = list(csv.DictReader(out.open()))
    assert rows == [
        {"approach": "N", "count_veh_h": "528.0"},
        {"approach": "W", "count_veh_h": "180.0"},
    ]


def test_cli_end_to_end(tmp_path, capsys):
    out = tmp_path / "observed_counts.csv"
    mix = tmp_path / "mix.json"
    code = main(
        [
            "--flows", str(EXAMPLES / "flows.sample.jsonl"),
            "--camera-map", str(EXAMPLES / "camera_map.json"),
            "--out", str(out),
            "--mix-out", str(mix),
        ]
    )  # fmt: skip
    assert code == 0
    assert out.read_text().splitlines()[0] == "approach,count_veh_h"
    assert json.loads(mix.read_text())["vehicle_mix"]["two_wheeler"] > 0.3
    assert "N=528" in capsys.readouterr().out


def test_cli_reports_errors_without_traceback(tmp_path, capsys):
    bad = tmp_path / "flows.jsonl"
    bad.write_text("")
    code = main(["--flows", str(bad), "--camera-map", str(EXAMPLES / "camera_map.json")])
    assert code == 2
    assert "error:" in capsys.readouterr().err


def test_output_feeds_xtraflow_calibration_scaling(tmp_path):
    """The CSV must be consumable by XtraFlow's own scaling logic."""
    pd = pytest.importorskip("pandas")
    out = write_observed_counts(
        {"N": 528.0, "S": 528.0, "E": 184.0, "W": 180.0}, tmp_path / "o.csv"
    )
    obs = pd.read_csv(out)
    base = {"N": 480, "S": 480, "E": 480, "W": 480}  # xtraflow config demand.balanced
    scales = {
        str(r["approach"]): float(r["count_veh_h"]) / max(float(base[str(r["approach"])]), 1.0)
        for _, r in obs.iterrows()
    }
    assert scales["N"] == pytest.approx(1.1)
    assert scales["E"] == pytest.approx(184 / 480)


def test_sample_flows_validate_against_crosssight_schema():
    """If CrossSight's schema package is importable, the sample must satisfy it."""
    schemas = pytest.importorskip("anpr_common.schemas")
    for rec in load_flow_windows(EXAMPLES / "flows.sample.jsonl"):
        schemas.FlowWindow.model_validate(rec)


def test_sample_mix_keys_are_valid_xtraflow_vehicle_types():
    yaml = pytest.importorskip("yaml")
    cfg = yaml.safe_load((ROOT / "xtraflow" / "config.yaml").read_text())
    mix = vehicle_mix(load_flow_windows(EXAMPLES / "flows.sample.jsonl"))
    assert set(mix) == set(cfg["vehicle_mix"])
