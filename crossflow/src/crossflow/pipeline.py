"""The CrossFlow pipeline: city cameras -> measured demand -> signal-control study.

1. ``city``     simulate a day of camera counts with CrossSight's simulator
2. ``bridge``   take the busiest four-arm junction-hour, convert counts to veh/h per arm
3. ``xtraflow`` run SUMO at that demand, with the camera-observed vehicle mix, under
                several signal controllers on the same random seeds
4. report      paired comparison with a confidence interval

XtraFlow's frozen configuration, tuned parameters and published results are never
modified. Runs are tagged with a run id and the report goes to ``runs/<id>/``.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .bridge import approach_rates, vehicle_mix, write_observed_counts
from .city import CityFlow, busiest_junction, junction_records, simulate_city
from .paths import runs_dir, xtraflow_dir

BASELINES = ("fixed_tuned", "actuated", "queue_pressure")
OURS = "XtraFlow"
SCENARIO = "balanced"  # XtraFlow's demand table is replaced per arm by the measured scales
# Student t, two-sided 95 %, by degrees of freedom (n - 1). Beyond 30 use the normal value.
_T95 = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    12: 2.179,
    15: 2.131,
    20: 2.086,
    25: 2.060,
    30: 2.042,
}


def _t95(df: int) -> float:
    if df <= 0:
        return float("nan")
    for k in sorted(_T95):
        if df <= k:
            return _T95[k]
    return 1.96


# ---------------------------------------------------------------- XtraFlow plumbing


def ensure_networks(xdir: Path) -> None:
    """Build XtraFlow's SUMO networks when absent (they are generated, not committed)."""
    net = xdir / "results" / "networks" / "intersection.net.xml"
    if net.exists():
        return
    for mod in ("sim.build_network", "sim.build_grid"):
        proc = subprocess.run(
            [sys.executable, "-m", mod], cwd=xdir, capture_output=True, text=True, check=False
        )
        if proc.returncode != 0:
            raise RuntimeError(f"{mod} failed:\n{proc.stdout[-800:]}\n{proc.stderr[-800:]}")


def _use_xtraflow(xdir: str) -> None:
    if xdir not in sys.path:
        sys.path.insert(0, xdir)


def generate_routes(
    xdir: Path, seeds: list[int], scales: dict[str, float], mix: dict[str, float] | None
) -> dict[int, str]:
    """Write each seed's routes file once, before any worker starts.

    The controllers of one seed share identical demand; letting every worker regenerate it
    would have them rewrite the same file while others read it.
    """
    _use_xtraflow(str(xdir))
    from sim.gen_demand import generate
    from sim.util import load_config

    cfg = load_config()
    return {
        s: str(generate(SCENARIO, s, cfg, demand_mult=1.0, mix_override=mix, demand_scale=scales))
        for s in seeds
    }


def _init_worker(xdir: str) -> None:
    import os

    _use_xtraflow(xdir)
    if not os.environ.get("SUMO_HOME"):
        try:
            import sumo

            os.environ["SUMO_HOME"] = os.path.dirname(sumo.__file__)
        except ImportError:
            pass


def _run_job(job: dict[str, Any]) -> dict[str, Any]:
    """One SUMO run. Top-level so it can run in a worker process."""
    from sim.run_sim import run_one  # XtraFlow; xtraflow dir is on sys.path

    try:
        row = run_one(
            SCENARIO,
            job["controller"],
            job["seed"],
            demand_scale=job["scales"],
            mix_override=job["mix"],
            run_id=job["run_id"],
            horizon_s=job["horizon_s"],
            routes_path=Path(job["routes"]) if job.get("routes") else None,
        )
    except Exception as exc:  # noqa: BLE001 - reported, not hidden
        return {
            "controller": job["controller"],
            "seed": job["seed"],
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }
    keep = (
        "controller",
        "seed",
        "status",
        "n_departed",
        "n_completed",
        "fuel_per_vehicle_L",
        "total_fuel_L",
        "mean_waiting_s",
        "p95_waiting_s",
        "mean_queue_veh",
        "stops_per_vehicle",
        "approach_veh_h",
    )
    return {k: row.get(k) for k in keep}


# ------------------------------------------------------------------- statistics


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _geh(obs: float, sim: float) -> float:
    return math.sqrt(2 * (sim - obs) ** 2 / max(sim + obs, 1e-9))


_FAILURE_KINDS = ("timeout", "gridlock", "error")


def _failure_counts(by: dict[str, dict[int, dict[str, Any]]]) -> dict[str, dict[str, int]]:
    """Per controller, how many runs (over all seeds) ended in each non-clean status."""
    out: dict[str, dict[str, int]] = {}
    for c, runs in by.items():
        counts = dict.fromkeys(_FAILURE_KINDS, 0)
        for r in runs.values():
            status = r.get("status")
            if status != "ok":
                kind = status if status in _FAILURE_KINDS else "error"
                counts[kind] += 1
        out[c] = counts
    return out


def _best_baseline_any(
    by: dict[str, dict[int, dict[str, Any]]], baselines: list[str]
) -> str | None:
    """Lowest mean fuel among each baseline's own clean runs (when no seed is clean for all)."""
    means = {}
    for c in baselines:
        fuels = [
            float(r["fuel_per_vehicle_L"])
            for r in by[c].values()
            if r.get("status") == "ok" and r.get("fuel_per_vehicle_L") is not None
        ]
        if fuels:
            means[c] = _mean(fuels)
    return min(means, key=lambda c: means[c]) if means else None


def summarize(rows: list[dict[str, Any]], controllers: list[str]) -> dict[str, Any]:
    """Paired comparison over seeds that completed cleanly under every controller.

    The pairing drops a seed for everyone when anyone fails it, which would hide XtraFlow's
    failures. So failures are counted per controller, and a seed XtraFlow failed while the best
    baseline succeeded is named in the verdict, which then makes no claim of superiority.
    """
    by = {c: {r["seed"]: r for r in rows if r["controller"] == c} for c in controllers}
    ok = {c: {s for s, r in by[c].items() if r.get("status") == "ok"} for c in controllers}
    seeds = sorted(set.intersection(*ok.values())) if all(ok.values()) else []
    dropped = sorted({r["seed"] for r in rows} - set(seeds))

    per: dict[str, dict[str, float]] = {}
    for c in controllers:
        sel = [by[c][s] for s in seeds]
        if not sel:
            continue
        per[c] = {
            "n_seeds": len(sel),
            "fuel_per_vehicle_L": _mean([float(r["fuel_per_vehicle_L"]) for r in sel]),
            "mean_waiting_s": _mean([float(r["mean_waiting_s"]) for r in sel]),
            "mean_queue_veh": _mean([float(r["mean_queue_veh"]) for r in sel]),
            "stops_per_vehicle": _mean([float(r["stops_per_vehicle"]) for r in sel]),
            "vehicles": _mean([float(r["n_departed"]) for r in sel]),
        }

    comparison: dict[str, Any] = {
        "paired_seeds": seeds,
        "dropped_seeds": dropped,
        "failures": _failure_counts(by),
    }
    baselines = [c for c in controllers if c != OURS and c in per]
    best_for_failures = (
        min(baselines, key=lambda c: per[c]["fuel_per_vehicle_L"])
        if baselines
        else _best_baseline_any(by, [c for c in controllers if c != OURS])
    )
    xf_failed: dict[int, str] = {}
    if OURS in by and best_for_failures is not None:
        for s, r in sorted(by[OURS].items()):
            if r.get("status") != "ok" and by[best_for_failures].get(s, {}).get("status") == "ok":
                xf_failed[s] = str(r.get("status") or "error")
    comparison["xtraflow_failed_seeds"] = sorted(xf_failed)
    failure_note = ""
    if xf_failed:
        detail = ", ".join(f"seed {s} ({kind})" for s, kind in xf_failed.items())
        failure_note = (
            f"XtraFlow FAILED on {detail} where the best baseline ({best_for_failures}) succeeded"
        )

    if OURS not in per or not baselines:
        verdict = "no comparison: too few clean runs"
        comparison["verdict"] = f"{verdict}. {failure_note}" if failure_note else verdict
        return {"per_controller": per, "comparison": comparison}

    best = min(baselines, key=lambda c: per[c]["fuel_per_vehicle_L"])
    vs: dict[str, Any] = {}
    for b in baselines:
        diffs = [
            100.0
            * (float(by[b][s]["fuel_per_vehicle_L"]) - float(by[OURS][s]["fuel_per_vehicle_L"]))
            / float(by[b][s]["fuel_per_vehicle_L"])
            for s in seeds
        ]
        m = _mean(diffs)
        if len(diffs) > 1:
            var = sum((d - m) ** 2 for d in diffs) / (len(diffs) - 1)
            half = _t95(len(diffs) - 1) * math.sqrt(var / len(diffs))
            lo, hi = m - half, m + half
        else:
            lo = hi = None
        vs[b] = {"pct_fuel_reduction": m, "ci_lo": lo, "ci_hi": hi, "n": len(diffs)}
    comparison["vs_baseline"] = vs
    comparison["best_baseline"] = best
    bv = vs[best]
    if failure_note:
        # The paired seeds exclude exactly the runs XtraFlow lost, so any gain there is
        # survivorship-biased: report the failure first and claim nothing in XtraFlow's favour.
        if bv["ci_lo"] is not None and bv["ci_hi"] < 0:
            tail = f"on the clean paired seeds XtraFlow also used MORE fuel than {best}"
        else:
            tail = "the fuel comparison covers only the clean paired seeds, so no superiority is claimed"
        verdict = f"{failure_note}; {tail}"
    elif bv["ci_lo"] is None:
        verdict = "single seed: no confidence interval, treat as anecdote"
    elif bv["ci_lo"] > 0:
        verdict = f"XtraFlow uses less fuel than the best baseline ({best}); interval excludes zero"
    elif bv["ci_hi"] < 0:
        verdict = f"XtraFlow uses MORE fuel than the best baseline ({best}); interval excludes zero"
    else:
        verdict = f"no established difference from the best baseline ({best}); interval spans zero"
    comparison["verdict"] = verdict
    return {"per_controller": per, "comparison": comparison}


# ------------------------------------------------------------------------ driver


def choose_demand(
    flow: CityFlow, *, hour: int | None, min_arms: int = 4
) -> tuple[str, int, dict[str, float], list[dict[str, Any]]]:
    cam, hr, _ = busiest_junction(flow, min_arms=min_arms, hour=hour)
    recs = junction_records(flow, cam, hr)
    rates = approach_rates(recs)
    return cam, hr, rates, recs


def run_pipeline(
    *,
    quick: bool = True,
    seeds: int | None = None,
    streams: int = 2000,
    num_vehicles: int = 4000,
    hour: int | None = None,
    observed_mix: bool = True,
    workers: int = 4,
    out_dir: Path | None = None,
    log=print,
) -> Path:
    """Run the whole pipeline and return the report directory."""
    from .signals import base_demand

    xdir = xtraflow_dir()
    base = base_demand()
    if not base:
        raise RuntimeError(f"could not read demand.balanced from {xdir / 'config.yaml'}")
    n_seeds = seeds if seeds is not None else (2 if quick else 5)
    horizon_s = (
        None  # XtraFlow's own horizon and drain; truncated runs end 'timeout' and bias results
    )

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = (out_dir or runs_dir()) / run_id
    out.mkdir(parents=True, exist_ok=True)

    log(f"[1/4] simulating a city day ({streams} demand streams)...")
    flow = simulate_city(num_vehicles=num_vehicles, streams=streams)
    cam, hr, rates, recs = choose_demand(flow, hour=hour)
    log(f"      {flow.traversals} camera hits; busiest 4-arm junction: {cam} at {hr:02d}:00")

    log("[2/4] bridge: camera counts -> approach demand")
    missing = [a for a in ("N", "S", "E", "W") if a not in rates]
    if missing:
        raise RuntimeError(f"junction {cam} has no data for arms {missing}")
    write_observed_counts(rates, out / "observed_counts.csv")
    scales = {a: rates[a] / base[a] for a in rates}
    mix = vehicle_mix(recs) if observed_mix else None
    log(
        f"      veh/h {rates}; scale vs XtraFlow balanced { ({a: round(s, 2) for a, s in scales.items()}) }"
    )

    log("[3/4] xtraflow: SUMO runs")
    ensure_networks(xdir)
    controllers = [*BASELINES, OURS]
    seed_list = list(range(1, n_seeds + 1))
    routes = generate_routes(xdir, seed_list, scales, mix)
    jobs = [
        {
            "controller": c,
            "seed": s,
            "scales": scales,
            "mix": mix,
            "routes": routes[s],
            "run_id": f"cf{run_id}",
            "horizon_s": horizon_s,
        }
        for s in seed_list
        for c in controllers
    ]
    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(
        max_workers=max(1, workers), initializer=_init_worker, initargs=(str(xdir),)
    ) as pool:
        for i, res in enumerate(pool.map(_run_job, jobs), 1):
            rows.append(res)
            log(
                f"      {i}/{len(jobs)} {res['controller']} seed {res['seed']}: {res.get('status')}"
                + (f" ({res['error']})" if res.get("error") else "")
            )

    log("[4/4] report")
    stats = summarize(rows, controllers)

    sim_flows: dict[str, list[float]] = {a: [] for a in rates}
    for r in rows:
        if r.get("status") == "ok" and r.get("approach_veh_h"):
            for a in rates:
                if a in r["approach_veh_h"]:
                    sim_flows[a].append(float(r["approach_veh_h"][a]))
    geh = {a: round(_geh(rates[a], _mean(v)), 2) for a, v in sim_flows.items() if v}

    report = {
        "crossflow_version": __version__,
        "created": datetime.now(UTC).isoformat(),
        "source": {
            "kind": "offline CrossSight city simulation (synthetic grid)",
            "demand_streams": flow.streams,
            "camera_hits_in_day": flow.traversals,
            "dropped_diagonal_hits": flow.dropped_diagonal,
            "camera": cam,
            "hour": hr,
        },
        "demand": {
            "observed_veh_h": rates,
            "scale_vs_xtraflow_balanced": {a: round(s, 3) for a, s in scales.items()},
            "vehicle_mix": mix,
            "vehicle_mix_source": "camera-observed (class 'other' dropped)"
            if mix
            else "XtraFlow assumed mix",
            "geh_observed_vs_simulated": geh,
            "geh_target": 5.0,
        },
        "run": {
            "mode": "quick" if quick else "full",
            "horizon_s": 3600,
            "seeds": list(range(1, n_seeds + 1)),
            "controllers": controllers,
            "scenario_template": SCENARIO,
        },
        **stats,
        "caveats": [
            (
                "The city is CrossSight's synthetic grid; its trips are scaled by superimposing "
                f"{flow.streams} demand streams. The flow is realistic in shape, not measured."
            ),
            (
                "XtraFlow's tuned parameters and fixed plan were tuned on the assumed demand "
                "and mix, not on this measured one."
            ),
            (
                "Results are a SUMO simulation of one four-arm junction; they are not a field "
                "measurement."
            ),
            *(
                ["Quick mode runs only 2 seeds, so the interval is wide; use --full for evidence."]
                if quick
                else []
            ),
        ],
        "raw_runs": rows,
    }
    (out / "report.json").write_text(
        json.dumps(report, indent=2, default=float) + "\n", encoding="utf-8"
    )
    (out / "report.md").write_text(render_markdown(report), encoding="utf-8")
    log(f"      verdict: {stats['comparison']['verdict']}")
    log(f"      wrote {out / 'report.md'}")
    return out


def render_markdown(rep: dict[str, Any]) -> str:
    d, c = rep["demand"], rep["comparison"]
    lines = [
        f"# CrossFlow run {rep['created']}",
        "",
        (
            f"**Mode:** {rep['run']['mode']} ({rep['run']['horizon_s']} s horizon, "
            f"seeds {rep['run']['seeds']})"
        ),
        (
            f"**Source:** {rep['source']['kind']}, camera `{rep['source']['camera']}` "
            f"at {rep['source']['hour']:02d}:00, {rep['source']['demand_streams']} demand streams"
        ),
        "",
        "## Measured demand (veh/h per arm)",
        "",
        "| Arm | Observed | Scale vs XtraFlow balanced | GEH (sim vs obs) |",
        "|---|---|---|---|",
    ]
    for a, v in d["observed_veh_h"].items():
        lines.append(
            f"| {a} | {v:g} | {d['scale_vs_xtraflow_balanced'][a]} | "
            f"{d['geh_observed_vs_simulated'].get(a, 'n/a')} |"
        )
    lines += [
        "",
        f"Vehicle mix: {d['vehicle_mix_source']}. GEH target < {d['geh_target']:g}.",
        "",
        "## Controllers (mean over paired clean seeds)",
        "",
        "| Controller | Fuel / vehicle (L) | Mean wait (s) | Mean queue | Stops / veh |",
        "|---|---|---|---|---|",
    ]
    for name, m in rep["per_controller"].items():
        lines.append(
            f"| {name} | {m['fuel_per_vehicle_L']:.4f} | {m['mean_waiting_s']:.1f} | "
            f"{m['mean_queue_veh']:.1f} | {m['stops_per_vehicle']:.2f} |"
        )
    lines += [
        "",
        "## XtraFlow fuel reduction vs each baseline",
        "",
        "| Baseline | Reduction % | 95% CI | n |",
        "|---|---|---|---|",
    ]
    for b, v in (c.get("vs_baseline") or {}).items():
        ci = "n/a" if v["ci_lo"] is None else f"{v['ci_lo']:.2f} to {v['ci_hi']:.2f}"
        lines.append(f"| {b} | {v['pct_fuel_reduction']:.2f} | {ci} | {v['n']} |")
    lines += [
        "",
        "## Failed runs (all seeds, including those dropped from the paired comparison)",
        "",
        "| Controller | timeout | gridlock | error |",
        "|---|---|---|---|",
    ]
    for name, f in (c.get("failures") or {}).items():
        lines.append(f"| {name} | {f['timeout']} | {f['gridlock']} | {f['error']} |")
    lines += ["", f"**Verdict:** {c['verdict']}", "", "## Caveats", ""]
    lines += [f"- {x}" for x in rep["caveats"]]
    if c.get("dropped_seeds"):
        lines += ["", f"Seeds dropped for a non-clean run: {c['dropped_seeds']}"]
    return "\n".join(lines) + "\n"
