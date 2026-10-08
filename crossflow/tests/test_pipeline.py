from __future__ import annotations

import json
import math

import pytest
from crossflow.paths import repo_root
from crossflow.signals import base_demand, latest_run, list_runs, published_summary

from crossflow import pipeline


def _row(controller, seed, fuel, status="ok"):
    return {
        "controller": controller,
        "seed": seed,
        "status": status,
        "n_departed": 1000,
        "n_completed": 1000,
        "fuel_per_vehicle_L": fuel,
        "total_fuel_L": fuel * 1000,
        "mean_waiting_s": 20.0,
        "p95_waiting_s": 60.0,
        "mean_queue_veh": 5.0,
        "stops_per_vehicle": 0.7,
        "approach_veh_h": {},
    }


CONTROLLERS = ["fixed_tuned", "actuated", "queue_pressure", "XtraFlow"]


def _rows(fuel, seeds=(1, 2, 3, 4, 5)):
    return [_row(c, s, fuel[c] + 0.0001 * s) for c in CONTROLLERS for s in seeds]


def test_summary_picks_best_baseline_and_flags_real_gain():
    fuel = {"fixed_tuned": 0.12, "actuated": 0.11, "queue_pressure": 0.10, "XtraFlow": 0.09}
    out = pipeline.summarize(_rows(fuel), CONTROLLERS)
    comp = out["comparison"]
    assert comp["best_baseline"] == "queue_pressure"
    assert comp["vs_baseline"]["queue_pressure"]["pct_fuel_reduction"] == pytest.approx(
        10.0, abs=0.2
    )
    assert comp["vs_baseline"]["queue_pressure"]["ci_lo"] > 0
    assert "excludes zero" in comp["verdict"] and "less fuel" in comp["verdict"]


def test_summary_does_not_claim_a_win_when_interval_spans_zero():
    rows = []
    gains = {1: 3.0, 2: -3.0, 3: 2.0, 4: -2.0, 5: 0.5}
    for s, g in gains.items():
        rows += [
            _row("fixed_tuned", s, 0.12),
            _row("actuated", s, 0.11),
            _row("queue_pressure", s, 0.10),
            _row("XtraFlow", s, 0.10 * (1 - g / 100)),
        ]
    out = pipeline.summarize(rows, CONTROLLERS)
    assert "no established difference" in out["comparison"]["verdict"]


def test_summary_reports_loss_honestly():
    fuel = {"fixed_tuned": 0.10, "actuated": 0.10, "queue_pressure": 0.10, "XtraFlow": 0.11}
    verdict = pipeline.summarize(_rows(fuel), CONTROLLERS)["comparison"]["verdict"]
    assert "MORE fuel" in verdict


def test_unclean_seed_is_dropped_for_every_controller():
    fuel = {"fixed_tuned": 0.12, "actuated": 0.11, "queue_pressure": 0.10, "XtraFlow": 0.09}
    rows = _rows(fuel)
    rows = [
        r if not (r["controller"] == "actuated" and r["seed"] == 3) else {**r, "status": "timeout"}
        for r in rows
    ]
    out = pipeline.summarize(rows, CONTROLLERS)
    assert out["comparison"]["paired_seeds"] == [1, 2, 4, 5]
    assert out["comparison"]["dropped_seeds"] == [3]


def test_all_runs_failed_yields_no_verdict_not_a_crash():
    rows = [_row(c, s, 0.1, status="error") for c in CONTROLLERS for s in (1, 2)]
    out = pipeline.summarize(rows, CONTROLLERS)
    assert out["comparison"]["verdict"].startswith("no comparison")


def test_single_seed_has_no_interval_and_is_json_safe():
    fuel = {"fixed_tuned": 0.12, "actuated": 0.11, "queue_pressure": 0.10, "XtraFlow": 0.09}
    out = pipeline.summarize(_rows(fuel, seeds=(1,)), CONTROLLERS)
    v = out["comparison"]["vs_baseline"]["queue_pressure"]
    assert v["ci_lo"] is None and "anecdote" in out["comparison"]["verdict"]
    json.dumps(out, allow_nan=False)


def test_t_quantile_is_sane():
    assert pipeline._t95(4) == pytest.approx(2.776)
    assert pipeline._t95(100) == pytest.approx(1.96)
    assert math.isnan(pipeline._t95(0))


def test_geh_zero_when_equal():
    assert pipeline._geh(400, 400) == 0
    assert pipeline._geh(400, 500) > 4  # large miss


def test_base_demand_reads_the_real_config():
    assert base_demand() == {"N": 480.0, "S": 480.0, "E": 480.0, "W": 480.0}


def test_published_summary_matches_the_shipped_results():
    pub = published_summary()
    assert pub["available"] and pub["config_locked"]
    scenarios = {c["scenario"] for c in pub["controllers"]}
    assert scenarios == {"balanced", "peak_unbalanced", "dynamic", "low_demand"}
    assert {h["scenario"] for h in pub["headlines"]} == scenarios


def test_missing_results_are_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setenv("CROSSFLOW_XTRAFLOW_RESULTS", str(tmp_path / "nothing"))
    monkeypatch.setenv("CROSSFLOW_RUNS_DIR", str(tmp_path / "runs"))
    assert published_summary()["available"] is False
    assert latest_run() is None and list_runs() == []


def test_latest_run_is_newest_report(tmp_path, monkeypatch):
    monkeypatch.setenv("CROSSFLOW_RUNS_DIR", str(tmp_path))
    for rid, verdict in (("20260101T000000Z", "old"), ("20260102T000000Z", "new")):
        d = tmp_path / rid
        d.mkdir()
        (d / "report.json").write_text(
            json.dumps(
                {"created": rid, "run": {"mode": "quick"}, "comparison": {"verdict": verdict}}
            )
        )
    assert latest_run()["comparison"]["verdict"] == "new"
    assert [r["run_id"] for r in list_runs()] == ["20260102T000000Z", "20260101T000000Z"]


def test_repo_root_finds_checkout():
    assert (repo_root() / "xtraflow" / "config.yaml").exists()


def _report(stats):
    """Smallest report dict render_markdown accepts."""
    return {
        "created": "2026-01-01T00:00:00+00:00",
        "run": {"mode": "full", "horizon_s": 3600, "seeds": [1, 2, 3, 4, 5]},
        "source": {"kind": "test", "camera": "cam", "hour": 8, "demand_streams": 1},
        "demand": {
            "observed_veh_h": {"N": 480.0},
            "scale_vs_xtraflow_balanced": {"N": 1.0},
            "geh_observed_vs_simulated": {},
            "vehicle_mix_source": "test",
            "geh_target": 5.0,
        },
        "caveats": [],
        **stats,
    }


_GOOD = {"fixed_tuned": 0.12, "actuated": 0.11, "queue_pressure": 0.10, "XtraFlow": 0.09}


def _fail(rows, controller, seed, status):
    return [
        {**r, "status": status} if (r["controller"], r["seed"]) == (controller, seed) else r
        for r in rows
    ]


def test_xtraflow_timeout_is_named_in_the_verdict_and_never_called_a_win():
    rows = _fail(_rows(_GOOD), "XtraFlow", 3, "timeout")
    out = pipeline.summarize(rows, CONTROLLERS)
    comp = out["comparison"]
    # The clean paired seeds alone would show a clear gain; the verdict must not claim it.
    assert comp["vs_baseline"]["queue_pressure"]["ci_lo"] > 0
    assert "seed 3 (timeout)" in comp["verdict"]
    assert "FAILED" in comp["verdict"] and "queue_pressure" in comp["verdict"]
    assert "less fuel" not in comp["verdict"] and "excludes zero" not in comp["verdict"]
    assert comp["xtraflow_failed_seeds"] == [3]
    assert comp["paired_seeds"] == [1, 2, 4, 5]
    assert comp["failures"]["XtraFlow"] == {"timeout": 1, "gridlock": 0, "error": 0}
    for baseline in ("fixed_tuned", "actuated", "queue_pressure"):
        assert comp["failures"][baseline] == {"timeout": 0, "gridlock": 0, "error": 0}


def test_failure_counts_by_kind_and_controller():
    rows = _rows(_GOOD)
    rows = _fail(rows, "XtraFlow", 1, "gridlock")
    rows = _fail(rows, "XtraFlow", 2, "error")
    rows = _fail(rows, "XtraFlow", 3, "error")
    rows = _fail(rows, "actuated", 4, "timeout")
    f = pipeline.summarize(rows, CONTROLLERS)["comparison"]["failures"]
    assert f["XtraFlow"] == {"timeout": 0, "gridlock": 1, "error": 2}
    assert f["actuated"] == {"timeout": 1, "gridlock": 0, "error": 0}
    assert f["fixed_tuned"] == {"timeout": 0, "gridlock": 0, "error": 0}


def test_baseline_only_failure_keeps_the_normal_verdict():
    rows = _fail(_rows(_GOOD), "actuated", 3, "timeout")
    comp = pipeline.summarize(rows, CONTROLLERS)["comparison"]
    assert comp["xtraflow_failed_seeds"] == []
    assert "less fuel" in comp["verdict"]
    assert comp["failures"]["actuated"]["timeout"] == 1


def test_xtraflow_failing_every_seed_is_reported_not_hidden():
    rows = [
        _row(c, s, _GOOD[c], "timeout" if c == "XtraFlow" else "ok")
        for c in CONTROLLERS
        for s in (1, 2)
    ]
    comp = pipeline.summarize(rows, CONTROLLERS)["comparison"]
    assert comp["verdict"].startswith("no comparison")
    assert "seed 1 (timeout)" in comp["verdict"] and "seed 2 (timeout)" in comp["verdict"]
    assert comp["failures"]["XtraFlow"]["timeout"] == 2


def test_failure_that_xtraflow_shares_with_the_best_baseline_is_not_its_fault():
    rows = _fail(_rows(_GOOD), "XtraFlow", 3, "timeout")
    rows = _fail(rows, "queue_pressure", 3, "timeout")
    comp = pipeline.summarize(rows, CONTROLLERS)["comparison"]
    assert comp["xtraflow_failed_seeds"] == []  # the best baseline did not succeed there
    assert comp["failures"]["XtraFlow"]["timeout"] == 1


def test_markdown_report_has_a_failures_table_and_the_verdict():
    rows = _fail(_rows(_GOOD), "XtraFlow", 3, "timeout")
    stats = pipeline.summarize(rows, CONTROLLERS)
    md = pipeline.render_markdown(_report(stats))
    assert "## Failed runs" in md
    assert "| Controller | timeout | gridlock | error |" in md
    assert "| XtraFlow | 1 | 0 | 0 |" in md
    assert "| fixed_tuned | 0 | 0 | 0 |" in md
    assert "seed 3 (timeout)" in md
