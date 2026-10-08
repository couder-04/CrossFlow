"""Statistical analysis, figures, tables, REPORT.md — all numbers from result files."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from scipy import stats

from sim.util import (
    CONTROLLER_NAMES,
    HEADLINE_BASELINES,
    ROOT,
    SCENARIOS,
    assert_config_locked,
    ensure_dirs,
    load_config,
    load_json,
    save_json,
)

sns.set_palette("colorblind")
plt.rcParams["figure.dpi"] = 200


def bootstrap_ci(deltas: np.ndarray, n_resamples: int = 10000, alpha: float = 0.05, seed: int = 0) -> Tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    deltas = np.asarray(deltas, dtype=float)
    deltas = deltas[np.isfinite(deltas)]
    if len(deltas) == 0:
        return float("nan"), float("nan"), float("nan")
    means = []
    n = len(deltas)
    for _ in range(n_resamples):
        sample = rng.choice(deltas, size=n, replace=True)
        means.append(sample.mean())
    lo = float(np.percentile(means, 100 * alpha / 2))
    hi = float(np.percentile(means, 100 * (1 - alpha / 2)))
    return float(deltas.mean()), lo, hi


def cohens_dz(deltas: np.ndarray) -> float:
    deltas = np.asarray(deltas, dtype=float)
    deltas = deltas[np.isfinite(deltas)]
    if len(deltas) < 2:
        return float("nan")
    sd = deltas.std(ddof=1)
    return float(deltas.mean() / sd) if sd > 0 else float("nan")


def holm(pvals: List[float]) -> List[float]:
    m = len(pvals)
    order = np.argsort(pvals)
    adj = [0.0] * m
    prev = 0.0
    for i, idx in enumerate(order):
        rank = m - i
        val = min(1.0, pvals[idx] * rank)
        val = max(val, prev)
        adj[idx] = val
        prev = val
    return adj


def load_runs(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    return df


def headline_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Paired headline stats exclude errors and runs that left vehicles unfinished."""
    out = df
    if "status" in out.columns:
        out = out[out["status"] == "ok"]
    if "n_unfinished" in out.columns:
        out = out[out["n_unfinished"].fillna(0).astype(float) == 0]
    return out


def best_baseline(df: pd.DataFrame, scenario: str) -> str | None:
    means = []
    for name in HEADLINE_BASELINES:
        sub = df[(df.scenario == scenario) & (df.controller == name)]
        if len(sub) == 0 or not np.isfinite(sub["fuel_per_vehicle_L"].mean()):
            continue
        means.append((float(sub["fuel_per_vehicle_L"].mean()), name))
    if not means:
        return None
    return min(means)[1]


def paired_pct(df: pd.DataFrame, scenario: str, a: str, b: str, metric: str = "fuel_per_vehicle_L"):
    da = df[(df.scenario == scenario) & (df.controller == a)][["seed", metric]].rename(columns={metric: "a"})
    db = df[(df.scenario == scenario) & (df.controller == b)][["seed", metric]].rename(columns={metric: "b"})
    m = da.merge(db, on="seed")
    # percent change of a relative to b: (b-a)/b * 100 = reduction of a vs b
    pct = (m["b"] - m["a"]) / m["b"].replace(0, np.nan) * 100.0
    return pct.to_numpy(), m


def main(smoke: bool = False) -> None:
    ensure_dirs()
    cfg = load_config()
    raw_path = ROOT / "results" / ("raw_runs_smoke.csv" if smoke else "raw_runs.csv")
    if not smoke:
        assert_config_locked()
    if not raw_path.exists():
        raise SystemExit(f"Missing {raw_path}")

    df_all = load_runs(raw_path)
    df = headline_frame(df_all)
    fig_dir = ROOT / "results" / "figures"
    tab_dir = ROOT / "results" / "tables"

    # Mean ± std per cell
    summary_rows = []
    metrics = [
        "fuel_per_vehicle_L", "total_CO2_kg", "mean_waiting_s", "p95_waiting_s", "mean_queue_veh",
        "n_completed", "n_unfinished", "gridlock_flag", "ssm_conflicts",
    ]
    metrics = [m for m in metrics if m in df_all.columns]
    for sc in df_all.scenario.unique():
        for ctrl in df_all.controller.unique():
            sub = df_all[(df_all.scenario == sc) & (df_all.controller == ctrl)]
            row = {"scenario": sc, "controller": ctrl, "n": len(sub)}
            for m in metrics:
                row[f"{m}_mean"] = float(sub[m].mean())
                row[f"{m}_std"] = float(sub[m].std(ddof=1)) if len(sub) > 1 else 0.0
            summary_rows.append(row)
    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(tab_dir / "summary_mean_std.csv", index=False)

    # Paired comparisons XtraFlow vs others
    comparisons = []
    pvals = []
    for sc in sorted(df.scenario.unique()):
        for other in CONTROLLER_NAMES:
            if other == "XtraFlow":
                continue
            if other not in set(df.controller):
                continue
            pct, merged = paired_pct(df, sc, "XtraFlow", other)
            mean, lo, hi = bootstrap_ci(pct, n_resamples=10000 if not smoke else 200)
            # Wilcoxon on paired fuel
            try:
                stat, p = stats.wilcoxon(merged["a"], merged["b"], zero_method="wilcox", alternative="two-sided")
            except ValueError:
                stat, p = float("nan"), 1.0
            dz = cohens_dz(merged["a"].to_numpy() - merged["b"].to_numpy())
            comparisons.append({
                "scenario": sc,
                "baseline": other,
                "metric": "fuel_per_vehicle_L",
                "pct_reduction_mean": mean,
                "pct_reduction_ci_lo": lo,
                "pct_reduction_ci_hi": hi,
                "wilcoxon_stat": float(stat) if np.isfinite(stat) else None,
                "p_raw": float(p),
                "cohens_dz": dz,
                "n_pairs": int(len(merged)),
            })
            pvals.append(float(p))

    if pvals:
        adj = holm(pvals)
        for i, c in enumerate(comparisons):
            c["p_holm"] = adj[i]
    comp_df = pd.DataFrame(comparisons)
    comp_df.to_csv(tab_dir / "paired_comparisons.csv", index=False)

    wait_rows = []
    for sc in sorted(df.scenario.unique()):
        for other in CONTROLLER_NAMES:
            if other == "XtraFlow" or other not in set(df.controller):
                continue
            for metric in ("mean_waiting_s", "p95_waiting_s"):
                if metric not in df.columns:
                    continue
                pct, merged = paired_pct(df, sc, "XtraFlow", other, metric=metric)
                mean, lo, hi = bootstrap_ci(pct, n_resamples=2000 if smoke else 4000)
                wait_rows.append({
                    "scenario": sc,
                    "baseline": other,
                    "metric": metric,
                    "pct_reduction_mean": mean,
                    "pct_reduction_ci_lo": lo,
                    "pct_reduction_ci_hi": hi,
                    "n_pairs": int(len(merged)),
                })
    pd.DataFrame(wait_rows).to_csv(tab_dir / "waiting_comparisons.csv", index=False)

    # Ablation: XtraFlow (fuel weights) versus ours_count (counts only).
    abl_rows = []
    for sc in sorted(df.scenario.unique()):
        if "ours_count" not in set(df.controller):
            continue
        pct, merged = paired_pct(df, sc, "XtraFlow", "ours_count")
        mean, lo, hi = bootstrap_ci(pct, n_resamples=10000 if not smoke else 200)
        if not np.isfinite(lo) or not np.isfinite(hi):
            statement = "insufficient_data"
        elif lo > 0:
            statement = "fuel_weights_lower_fuel"
        elif hi < 0:
            statement = "fuel_weights_higher_fuel"
        else:
            statement = "no_detectable_difference"
        abl_rows.append({
            "scenario": sc,
            "pct_reduction_fuel_vs_count_mean": mean,
            "ci_lo": lo,
            "ci_hi": hi,
            "statement": statement,
        })
    pd.DataFrame(abl_rows).to_csv(tab_dir / "ablation_fuel_vs_count.csv", index=False)

    # n_completed comparability
    comp_flags = []
    for sc in df.scenario.unique():
        sub = df[df.scenario == sc]
        means = sub.groupby("controller")["n_completed"].mean()
        spread = float(means.max() - means.min()) if len(means) else 0
        comp_flags.append({"scenario": sc, "n_completed_mean_spread": spread, "means": means.to_dict()})
    save_json(ROOT / "results" / "n_completed_check.json", comp_flags)

    # Headline: XtraFlow vs the best non-oracle baseline. Legacy fixed is secondary.
    # The >30% flag applies to the tuned baseline, not the legacy plan.
    headlines = []
    for sc in sorted(df.scenario.unique()):
        base = best_baseline(df, sc)
        entry = {"scenario": sc, "best_baseline": base}
        if base is not None:
            pct, _ = paired_pct(df, sc, "XtraFlow", base)
            mean, lo, hi = bootstrap_ci(pct, n_resamples=10000 if not smoke else 200)
            flag = "OK"
            if np.isfinite(mean) and mean > 30:
                flag = "INVESTIGATE_GT_30PCT"
            entry.update({
                "pct_fuel_reduction_vs_best": mean,
                "ci_lo": lo,
                "ci_hi": hi,
                "sanity_flag": flag,
            })
        if "fixed" in set(df.controller):
            pct_f, _ = paired_pct(df, sc, "XtraFlow", "fixed")
            mean_f, lo_f, hi_f = bootstrap_ci(pct_f, n_resamples=2000 if smoke else 4000)
            entry["pct_fuel_reduction_vs_fixed_legacy"] = mean_f
            entry["legacy_ci_lo"] = lo_f
            entry["legacy_ci_hi"] = hi_f
        # Keep the old key only as an alias of the headline comparison so figures
        # that still read it show the tuned comparison when that is the headline.
        entry["pct_fuel_reduction_vs_fixed"] = entry.get("pct_fuel_reduction_vs_best", entry.get("pct_fuel_reduction_vs_fixed_legacy"))
        headlines.append(entry)
    save_json(ROOT / "results" / "headlines.json", headlines)

    # summary.json
    summary = {
        "n_rows": int(len(df)),
        "controllers": sorted(df.controller.unique().tolist()),
        "scenarios": sorted(df.scenario.unique().tolist()),
        "headlines": headlines,
        "comparisons": comparisons,
        "ablation": abl_rows,
        "waiting": wait_rows,
        "excluded_unfinished_or_not_ok": int(len(df_all) - len(df)),
        "gridlock_rate": float(df_all["gridlock_flag"].mean()) if "gridlock_flag" in df_all else None,
        "error_rows": int((df_all["status"] == "error").sum()) if "status" in df_all.columns else 0,
        "label": "Simulation-based estimate; assumed traffic mix",
    }
    save_json(ROOT / "results" / "summary.json", summary)

    # ---- Figures ----
    _fig_grouped_bars(df, fig_dir / "i_grouped_bars.png")
    _fig_pct_reduction(comp_df, fig_dir / "ii_pct_reduction.png")
    _fig_timeseries(fig_dir / "iii_dynamic_queue_ts.png")
    _fig_green_alloc(fig_dir / "iv_green_allocation.png")
    _fig_fuel_by_type(df, fig_dir / "v_fuel_by_type.png")
    # vi-xi may be filled by other experiments; create placeholders from available data
    _fig_placeholder_from_headlines(headlines, fig_dir / "vi_gain_vs_saturation.png", "Gain vs saturation (filled by sensitivity.py)")
    for name in [
        "vii_robustness.png", "viii_rl_training.png", "ix_emission_xcheck.png",
        "x_grid_results.png", "xi_extrapolation.png",
    ]:
        p = fig_dir / name
        if not p.exists():
            _fig_placeholder_from_headlines(headlines, p, name)

    # Try RL curve
    curve_path = ROOT / "results" / "rl" / "training_curve.json"
    if curve_path.exists():
        _fig_rl_curve(curve_path, fig_dir / "viii_rl_training.png")

    write_report(df, summary_df, comp_df, headlines, abl_rows, cfg, wait_rows)
    # markdown tables
    summary_df.to_markdown(tab_dir / "summary_mean_std.md", index=False)
    comp_df.to_markdown(tab_dir / "paired_comparisons.md", index=False)
    print("Analysis complete.")


def _fig_grouped_bars(df, path):
    metrics = [("fuel_per_vehicle_L", "Fuel per vehicle (L)"), ("total_CO2_kg", "Total CO2 (kg)"),
               ("mean_waiting_s", "Mean waiting (s)"), ("mean_queue_veh", "Mean queue (veh)")]
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    axes = axes.ravel()
    for ax, (m, label) in zip(axes, metrics):
        g = df.groupby(["scenario", "controller"])[m].agg(["mean", "std"]).reset_index()
        scenarios = list(g.scenario.unique())
        controllers = list(g.controller.unique())
        x = np.arange(len(scenarios))
        width = 0.8 / max(len(controllers), 1)
        for i, ctrl in enumerate(controllers):
            sub = g[g.controller == ctrl]
            means = [sub[sub.scenario == s]["mean"].values[0] if len(sub[sub.scenario == s]) else np.nan for s in scenarios]
            stds = [sub[sub.scenario == s]["std"].values[0] if len(sub[sub.scenario == s]) else 0 for s in scenarios]
            ax.bar(x + i * width, means, width, yerr=stds, label=ctrl, capsize=2)
        ax.set_xticks(x + width * len(controllers) / 2)
        ax.set_xticklabels(scenarios, rotation=30, ha="right")
        ax.set_ylabel(label)
        ax.legend(fontsize=6, ncol=2)
    fig.suptitle("Metrics by scenario and controller (mean±std)")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _fig_pct_reduction(comp_df, path):
    if comp_df.empty:
        return
    show = [b for b in ("fixed_tuned", "actuated", "maxpressure", "queue_pressure") if b in set(comp_df.baseline)]
    sub = comp_df[comp_df.baseline.isin(show)] if show else comp_df
    fig, ax = plt.subplots(figsize=(10, 6))
    scenarios = list(sub.scenario.unique())
    baselines = list(sub.baseline.unique())
    x = np.arange(len(scenarios))
    width = 0.8 / max(len(baselines), 1)
    for i, base in enumerate(baselines):
        means, yerr_lo, yerr_hi = [], [], []
        for s in scenarios:
            row = sub[(sub.scenario == s) & (sub.baseline == base)]
            if len(row) == 0:
                means.append(np.nan)
                yerr_lo.append(0)
                yerr_hi.append(0)
                continue
            m = float(row.pct_reduction_mean.iloc[0])
            lo = float(row.pct_reduction_ci_lo.iloc[0])
            hi = float(row.pct_reduction_ci_hi.iloc[0])
            means.append(m)
            yerr_lo.append(m - lo if np.isfinite(m) and np.isfinite(lo) else 0)
            yerr_hi.append(hi - m if np.isfinite(m) and np.isfinite(hi) else 0)
        ax.bar(x + i * width, means, width, yerr=[yerr_lo, yerr_hi], capsize=3, label=base)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xticks(x + width * max(len(baselines) - 1, 0) / 2)
    ax.set_xticklabels(scenarios, rotation=20, ha="right")
    ax.set_ylabel("% fuel reduction of XtraFlow vs baseline")
    ax.set_title("Reduction versus tuned baselines")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _fig_timeseries(path):
    ts_dir = ROOT / "results" / "timeseries"
    files = list(ts_dir.glob("*dynamic*seed1*.json")) if ts_dir.exists() else []
    fig, ax = plt.subplots(figsize=(10, 4))
    plotted = False
    for f in files:
        data = json.loads(f.read_text())
        rows = data.get("rows", [])
        if not rows:
            continue
        t = [r["t"] for r in rows]
        q = [r["queue"] for r in rows]
        label = f.stem.split("_")[1] if "_" in f.stem else f.stem
        # parse controller from name
        parts = f.stem.split("_")
        ax.plot(t, q, label=f.stem[:40])
        plotted = True
    if not plotted:
        ax.text(0.5, 0.5, "No timeseries yet", ha="center")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Queue (halting veh)")
    ax.set_title("Dynamic scenario queue time series (TEST seed 1)")
    ax.legend(fontsize=6)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _fig_green_alloc(path):
    ts_dir = ROOT / "results" / "timeseries"
    fig, ax = plt.subplots(figsize=(8, 4))
    found = False
    for name in ["fixed", "XtraFlow"]:
        files = list(ts_dir.glob(f"*_{name}_seed1*.json")) if ts_dir.exists() else []
        if not files:
            continue
        data = json.loads(files[0].read_text())
        ga = data.get("green_alloc", {})
        ax.bar([f"{name}:{k}" for k in ga.keys()], list(ga.values()), alpha=0.7)
        found = True
    if not found:
        ax.text(0.5, 0.5, "No green allocation timeseries", ha="center")
    ax.set_ylabel("Green seconds")
    ax.set_title("Green-time allocation XtraFlow vs fixed")
    fig.autofmt_xdate(rotation=30)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _fig_fuel_by_type(df, path):
    # aggregate fuel_L_by_type JSON
    rows = []
    for _, r in df.iterrows():
        try:
            d = json.loads(r["fuel_L_by_type"]) if isinstance(r["fuel_L_by_type"], str) else {}
        except Exception:
            d = {}
        for vt, val in d.items():
            rows.append({"controller": r["controller"], "scenario": r["scenario"], "vtype": vt, "fuel_L": val})
    fig, ax = plt.subplots(figsize=(10, 5))
    if rows:
        tdf = pd.DataFrame(rows)
        g = tdf.groupby(["vtype", "controller"])["fuel_L"].mean().reset_index()
        sns.barplot(data=g, x="vtype", y="fuel_L", hue="controller", ax=ax)
    else:
        ax.text(0.5, 0.5, "No fuel-by-type data", ha="center")
    ax.set_title("Fuel by vehicle type (mean across runs)")
    ax.set_ylabel("Fuel (L)")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _fig_placeholder_from_headlines(headlines, path, title):
    fig, ax = plt.subplots(figsize=(8, 4))
    if headlines:
        xs = [h["scenario"] for h in headlines]
        ys = [h["pct_fuel_reduction_vs_fixed"] for h in headlines]
        ax.bar(xs, ys)
    ax.set_title(title)
    ax.set_ylabel("% fuel reduction vs fixed")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _fig_rl_curve(curve_path, path):
    data = json.loads(curve_path.read_text())
    curve = data.get("curve", [])
    scored = {c.get("checkpoint"): c for c in data.get("scored", []) if isinstance(c, dict)}
    fig, ax = plt.subplots(figsize=(8, 4))
    xs, ys = [], []
    for c in curve:
        if "val_fuel_per_vehicle_L" in c and np.isfinite(c["val_fuel_per_vehicle_L"]):
            xs.append(c["timesteps"])
            ys.append(c["val_fuel_per_vehicle_L"])
            continue
        sc = scored.get(c.get("checkpoint"), {})
        v = sc.get("val_fuel_per_vehicle_L")
        if v is not None and np.isfinite(v):
            xs.append(c["timesteps"])
            ys.append(v)
    if xs:
        ax.plot(xs, ys, marker="o")
        ax.set_ylabel("Val fuel/veh (L)")
        ax.set_title("RL training curve (VALIDATION fuel)")
    else:
        ax.plot([c["timesteps"] for c in curve], marker="o")
        ax.set_ylabel("Checkpoint index")
        ax.set_title("RL training checkpoints (no VALIDATION scores recorded)")
    ax.set_xlabel("Timesteps")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def write_report(df, summary_df, comp_df, headlines, abl_rows, cfg, wait_rows=None):
    # Load optional experiment artifacts
    def _load(p):
        return json.loads(Path(p).read_text()) if Path(p).exists() else None

    demand_cal = _load(ROOT / "results" / "demand_calibration.json")
    weights = _load(ROOT / "results" / "weights.json")
    tuned = _load(ROOT / "results" / "tuned_params.json")
    emis = _load(ROOT / "results" / "emission_class_map.json")
    robust = _load(ROOT / "results" / "robustness.json")
    sens = _load(ROOT / "results" / "sensitivity.json")
    xcheck = _load(ROOT / "results" / "emission_xcheck.json")
    safety = _load(ROOT / "results" / "safety.json")
    grid = _load(ROOT / "results" / "grid_results.json")
    extrap = _load(ROOT / "results" / "extrapolation.json")
    rlmeta = _load(ROOT / "results" / "rl" / "rl_selection.json")
    sublane = _load(ROOT / "results" / "sublane_fallback.json")

    lines = []
    lines.append("# XtraFlow — Research Report\n")
    lines.append("**Label:** Simulation-based estimate; not a real-world deployment result. "
                 "Assumed mixed-traffic scenario unless observed counts provided.\n")

    # Abstract with actual headlines
    lines.append("## Abstract\n")
    has_ppo = "controller" in df.columns and "rl_ppo" in set(df["controller"])
    ppo_clause = (
        "PPO is included in the TEST comparison. "
        if has_ppo
        else "PPO was trained to the configured step budget and is not in this TEST comparison. "
    )
    lines.append(
        "We evaluate an adaptive, fuel-weighted pressure traffic signal controller "
        "(XtraFlow) against legacy fixed-time, a validation-tuned fixed plan, Webster, "
        "SUMO-actuated, queue-pressure, max-pressure, and a count-only ablation. "
        + ppo_clause +
        "Controllers use an oracle detector (SUMO speed, class, and route turn) unless "
        "info_mode is camera. Emission classes are proxies. "
        "Simulation-based estimate; assumed traffic mix. "
    )
    if sublane:
        lines.append(f"Sublane actually used: {sublane.get('used_sublane')}. ")
    for h in headlines:
        base = h.get("best_baseline")
        mean = h.get("pct_fuel_reduction_vs_best")
        if base is None or mean is None or not np.isfinite(mean):
            lines.append(f" On `{h['scenario']}`, no tuned baseline comparison was available.")
            continue
        lines.append(
            f" On `{h['scenario']}`, mean fuel change versus `{base}` was "
            f"{mean:.2f}% (95% CI [{h['ci_lo']:.2f}, {h['ci_hi']:.2f}]; flag={h['sanity_flag']})."
        )
    lines.append("\n")

    lines.append("## Method\n")
    lines.append("- Single 4-arm intersection; left-hand traffic; yellow 3 s; all-red 2 s. "
                 "Grid numbers are reported only from results/grid_results.json.\n")
    if "seed" in df.columns and len(df):
        seeds = sorted(int(s) for s in df["seed"].dropna().unique())
        seed_txt = f"{seeds[0]}–{seeds[-1]} ({len(seeds)} seeds in raw_runs.csv)"
    else:
        seed_txt = "see raw_runs.csv"
    lines.append(
        f"- Seed protocol: TRAIN 1000–1049, VALIDATION 2000–2019, TEST {seed_txt} after config.lock.\n"
    )
    lines.append("- Metrics from tripinfo with device.emissions.probability=1; fuel mg→L via densities.\n")
    if sublane:
        lines.append(f"- Sublane model used: {sublane.get('used_sublane')}.\n")
    if emis:
        lines.append(f"- Emission class map (primary): `{json.dumps(emis.get('primary', {}))}`.\n")

    lines.append("\n## Assumptions\n\n| Parameter | Value | Source |\n|---|---|---|\n")
    lines.append("| Vehicle mix | 2W 40 / car 30 / auto 10 / bus 5 / truck 15 | assumed |\n")
    lines.append("| Occupancy | 1.3 / 2.0 / 2.0 / 40 / 1.2 | assumed |\n")
    lines.append("| Fuel densities | petrol 0.74, diesel 0.84 kg/L | assumed |\n")
    lines.append("| Geometry | 300 m approaches, 3 in / 2 out lanes | assumed |\n")
    if demand_cal:
        lines.append(f"| Demand mode | {demand_cal.get('mode')} | {demand_cal.get('note','')} |\n")

    lines.append("\n## Weights (measured)\n\n```\n")
    lines.append(json.dumps(weights, indent=2) if weights else "missing")
    lines.append("\n```\n")

    lines.append("\n## Tuned parameters (VALIDATION only)\n\n```\n")
    lines.append(json.dumps(tuned, indent=2) if tuned else "missing")
    lines.append("\n```\n")

    lines.append("\n## Per-scenario results (mean±std)\n\n")
    lines.append(summary_df.to_markdown(index=False))
    lines.append("\n\n## Statistical tests (XtraFlow vs baselines)\n\n")
    lines.append(comp_df.to_markdown(index=False))
    lines.append("\n")

    # Honest losses
    lines.append("\n## Where XtraFlow loses or ties\n\n")
    losses = [
        c for c in comp_df.to_dict("records")
        if np.isfinite(c.get("pct_reduction_mean", np.nan)) and c["pct_reduction_mean"] <= 0
    ]
    wait_losses = []
    if wait_rows:
        wait_losses = [
            c for c in wait_rows
            if np.isfinite(c.get("pct_reduction_mean", np.nan)) and c["pct_reduction_mean"] <= 0
        ]
    if not losses and not wait_losses:
        lines.append("No non-positive mean fuel or waiting reductions against listed baselines in the loaded runs.\n")
    for c in losses:
        lines.append(
            f"- fuel vs `{c['baseline']}` on `{c['scenario']}`: "
            f"{c['pct_reduction_mean']:.2f}% (CI [{c['pct_reduction_ci_lo']:.2f}, {c['pct_reduction_ci_hi']:.2f}])\n"
        )
    for c in wait_losses:
        lines.append(
            f"- {c['metric']} vs `{c['baseline']}` on `{c['scenario']}`: "
            f"{c['pct_reduction_mean']:.2f}% (CI [{c['pct_reduction_ci_lo']:.2f}, {c['pct_reduction_ci_hi']:.2f}])\n"
        )

    lines.append("\n## Ablations\n\n")
    lines.append(pd.DataFrame(abl_rows).to_markdown(index=False) if abl_rows else "n/a")
    lines.append("\n")

    lines.append("\n## RL comparison\n\n")
    if rlmeta:
        lines.append(f"RL selected on VALIDATION: `{json.dumps(rlmeta)}`.\n")
    rl_comp = comp_df[comp_df.baseline == "rl_ppo"] if "baseline" in comp_df else pd.DataFrame()
    if len(rl_comp):
        lines.append(rl_comp.to_markdown(index=False))
    lines.append("\n")

    for title, obj in [
        ("Sensitivity", sens), ("Emission cross-check", xcheck), ("Safety", safety),
        ("Robustness", robust), ("Grid study", grid), ("Extrapolation", extrap),
    ]:
        lines.append(f"\n## {title}\n\n```\n{json.dumps(obj, indent=2) if obj else 'Not yet run.'}\n```\n")

    lines.append("\n## Limitations\n\n")
    lines.append("- Simulation-to-reality gap; emission model not calibrated to Indian vehicles.\n")
    lines.append("- Assumed vehicle mix unless observed counts provided.\n")
    lines.append("- No pedestrians; simplified turning/phase plan; single-city context.\n")
    lines.append("- Perception noise without user video is assumed.\n")

    lines.append("\n## Threats to validity\n\n")
    lines.append("- Internal: seed leakage prevented by config.lock; demand independent of controller.\n")
    lines.append("- Construct: HBEFA proxies idle/fuel; SSM conflicts are model-based.\n")
    lines.append("- External: Indian arterial heterogeneity not fully represented.\n")

    tune_path = ROOT / "results" / "tune_grid_results.json"
    if tune_path.exists():
        tune = load_json(tune_path)
        span = tune.get("screen_relative_span")
        lines.append("\n## Tuning landscape\n\n")
        if span is None:
            lines.append("Screen span was not computed.\n")
        else:
            lines.append(
                f"Relative span of screened hyperparameter means, (max-min)/min, was {span:.4f}. "
                "Selection used VALIDATION seeds across the scenarios listed in tuned_params.json.\n"
            )

    lines.append("\n## Q&A\n\n")
    lines.append("**How is fuel computed?** SUMO emission devices using the classes in "
                 "results/emission_class_map.json; milligrams converted with the densities in config. "
                 "Fuel per vehicle uses departed vehicles, including unfinished trips.\n\n")
    lines.append("**What do the controllers see?** Default info_mode is oracle: SUMO speed, class, and route turn. "
                 "Camera mode applies perception/noise_model.json. The noise source field says whether that "
                 "model is empirical or assumed.\n\n")
    lines.append("**What is the headline comparison?** XtraFlow versus the lowest-fuel controller among "
                 "fixed_tuned, actuated, queue_pressure, and maxpressure, with a paired bootstrap interval. "
                 "Legacy fixed is a secondary row. Numbers are in results/headlines.json.\n\n")
    lines.append("**Did you tune on test data?** Tuning, fixed-plan search, and RL checkpoint selection "
                 "use VALIDATION seeds only. TEST seeds run once after the config lock.\n\n")
    lines.append("**What should not be claimed from this file?** Any sentence whose number is not in a "
                 "results JSON or CSV loaded above. Grid, safety, and robustness verdicts are the fields "
                 "in those JSON files.\n\n")

    (ROOT / "results" / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--smoke", action="store_true")
    args = p.parse_args()
    main(smoke=args.smoke)
