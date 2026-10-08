"""Annual figures from the paired saving versus the best tuned baseline.

Scenario weights are an explicit assumption in config.yaml. The saving draw
resamples paired seeds. INR figures are a placeholder. The CO2 factor is the
fuel-type mix in the runs, not a fixed petrol/diesel split.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from sim.util import HEADLINE_BASELINES, ROOT, load_config, load_json, save_json


def _best_baseline(df: pd.DataFrame, scenario: str) -> str | None:
    means = []
    for name in HEADLINE_BASELINES:
        sub = df[(df.scenario == scenario) & (df.controller == name)]
        if "status" in sub.columns:
            sub = sub[sub.status == "ok"]
        if "n_unfinished" in sub.columns:
            sub = sub[sub.n_unfinished.fillna(0).astype(float) == 0]
        if len(sub) == 0:
            continue
        means.append((float(sub.fuel_per_vehicle_L.mean()), name))
    if not means:
        return None
    return min(means)[1]


def _co2_factor(df: pd.DataFrame, cfg: dict) -> float:
    """kg CO2 per litre from the fuel split actually recorded, else config factors."""
    petrol = 0.0
    diesel = 0.0
    if "fuel_L_by_type" in df.columns:
        types = cfg["fuel"]["type"]
        for raw in df["fuel_L_by_type"].dropna():
            try:
                blob = json.loads(raw) if isinstance(raw, str) else {}
            except json.JSONDecodeError:
                continue
            for vtype, litres in blob.items():
                kind = types.get(vtype, "petrol")
                if kind == "diesel":
                    diesel += float(litres)
                else:
                    petrol += float(litres)
    total = petrol + diesel
    factors = cfg["fuel"]["co2_kg_per_L"]
    if total <= 0:
        return float("nan")
    return (petrol * float(factors["petrol"]) + diesel * float(factors["diesel"])) / total


def triangular(rng, lo, base, hi, n):
    return rng.triangular(lo, base, hi, size=n)


def main() -> None:
    cfg = load_config()
    raw = ROOT / "results" / "raw_runs.csv"
    if not raw.exists():
        raw = ROOT / "results" / "raw_runs_smoke.csv"
    if not raw.exists():
        raise SystemExit("No raw runs to extrapolate")
    df = pd.read_csv(raw)
    weights = cfg["extrapolation"].get("scenario_weights") or {}
    scenarios = [s for s in weights if s in set(df.scenario)]
    if not scenarios:
        scenarios = list(df.scenario.unique())
        weights = {s: 1.0 / len(scenarios) for s in scenarios}

    per_scenario = {}
    # Paired seeds that exist for every included scenario, so one resample is aligned.
    seed_sets = []
    for scenario in scenarios:
        base = _best_baseline(df, scenario)
        if base is None:
            continue
        ours = df[(df.scenario == scenario) & (df.controller == "XtraFlow")][["seed", "fuel_per_vehicle_L"]]
        other = df[(df.scenario == scenario) & (df.controller == base)][["seed", "fuel_per_vehicle_L"]]
        merged = ours.merge(other, on="seed", suffixes=("_ours", "_base"))
        saving = (merged["fuel_per_vehicle_L_base"] - merged["fuel_per_vehicle_L_ours"]).to_numpy()
        per_scenario[scenario] = {"baseline": base, "seeds": merged["seed"].tolist(), "saving": saving}
        seed_sets.append(set(merged["seed"].tolist()))
    if not per_scenario:
        raise SystemExit("No paired baseline rows")

    common = set.intersection(*[set(v["seeds"]) for v in per_scenario.values()]) if per_scenario else set()
    rng = np.random.default_rng(0)
    n_boot = 4000
    boots = []
    names = list(per_scenario)
    if common:
        for _ in range(n_boot):
            draw = rng.choice(list(common), size=len(common), replace=True)
            acc = 0.0
            wsum = 0.0
            for scenario in names:
                info = per_scenario[scenario]
                by_seed = dict(zip(info["seeds"], info["saving"]))
                sample = np.array([by_seed[int(s) if int(s) in by_seed else s] for s in draw])
                acc += float(weights.get(scenario, 0.0)) * float(sample.mean())
                wsum += float(weights.get(scenario, 0.0))
            boots.append(acc / wsum if wsum else acc)
    else:
        for _ in range(n_boot):
            acc = 0.0
            wsum = 0.0
            for scenario in names:
                saving = per_scenario[scenario]["saving"]
                sample = rng.choice(saving, size=len(saving), replace=True)
                acc += float(weights.get(scenario, 0.0)) * float(sample.mean())
                wsum += float(weights.get(scenario, 0.0))
            boots.append(acc / wsum if wsum else acc)
    boots = np.asarray(boots, dtype=float)
    mean_save = float(np.mean(boots))

    n = int(cfg["extrapolation"]["n_monte_carlo"])
    ex = cfg["extrapolation"]
    price = cfg["fuel"]["price_inr_per_L"]
    co2_f = _co2_factor(df, cfg)
    save_draw = rng.choice(boots, size=n, replace=True)
    vph = triangular(rng, ex["vehicles_per_peak_hour"]["low"], ex["vehicles_per_peak_hour"]["base"],
                     ex["vehicles_per_peak_hour"]["high"], n)
    phd = triangular(rng, ex["peak_hours_per_day"]["low"], ex["peak_hours_per_day"]["base"],
                     ex["peak_hours_per_day"]["high"], n)
    dpy = triangular(rng, ex["days_per_year"]["low"], ex["days_per_year"]["base"],
                     ex["days_per_year"]["high"], n)
    nint = triangular(rng, ex["n_intersections"]["low"], ex["n_intersections"]["base"],
                      ex["n_intersections"]["high"], n)
    price_d = triangular(rng, price["low"], price["base"], price["high"], n)
    annual_L = save_draw * vph * phd * dpy * nint
    annual_INR = annual_L * price_d
    annual_tCO2 = annual_L * co2_f / 1000.0 if np.isfinite(co2_f) else np.full(n, np.nan)

    def summ(arr):
        arr = np.asarray(arr, dtype=float)
        arr = arr[np.isfinite(arr)]
        if len(arr) == 0:
            return {"median": None, "p05": None, "p95": None}
        return {
            "median": float(np.median(arr)),
            "p05": float(np.percentile(arr, 5)),
            "p95": float(np.percentile(arr, 95)),
        }

    out = {
        "label": "Simulation-based estimate; assumed traffic mix",
        "scenario_weights_assumption": weights,
        "baseline_per_scenario": {s: per_scenario[s]["baseline"] for s in per_scenario},
        "measured_saving_L_per_veh": {
            "mean": mean_save,
            "p05": float(np.percentile(boots, 5)),
            "p95": float(np.percentile(boots, 95)),
        },
        "vehicles_per_peak_hour_assumption": ex["vehicles_per_peak_hour"],
        "co2_kg_per_L_from_fuel_split": co2_f,
        "annual_litres": summ(annual_L),
        "annual_INR_PLACEHOLDER_price": summ(annual_INR),
        "annual_tonnes_CO2": summ(annual_tCO2),
        "fuel_price_note": "INR/L is a user-editable PLACEHOLDER in config.yaml",
    }
    save_json(ROOT / "results" / "extrapolation.json", out)

    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(annual_L[np.isfinite(annual_L)], bins=50, color="C0", alpha=0.8)
    ax.axvline(out["annual_litres"]["median"], color="k", label="median")
    ax.set_xlabel("Annual litres (simulation-based estimate)")
    ax.set_title("Extrapolation")
    ax.legend()
    fig.tight_layout()
    fig.savefig(ROOT / "results" / "figures" / "xi_extrapolation.png", dpi=200)
    plt.close(fig)
    print(json.dumps(out["measured_saving_L_per_veh"]))


if __name__ == "__main__":
    main()
