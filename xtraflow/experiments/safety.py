"""Paired conflict rates versus each baseline. The verdict comes from the CI."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from experiments.analyze import bootstrap_ci
from sim.metrics import conflicts_per_1000
from sim.util import ROOT, save_json


def _rate(df: pd.DataFrame) -> pd.Series:
    if "conflicts_per_1000_veh" in df.columns and df["conflicts_per_1000_veh"].notna().any():
        return df["conflicts_per_1000_veh"].astype(float)
    departed = df["n_departed"].astype(float) if "n_departed" in df.columns else 0
    return [
        conflicts_per_1000(int(c), int(n))
        for c, n in zip(df["ssm_conflicts"].fillna(0), departed)
    ]


def _verdict(lo: float, hi: float) -> str:
    if not (np.isfinite(lo) and np.isfinite(hi)):
        return "insufficient_data"
    if lo > 0:
        return "increase"
    if hi < 0:
        return "decrease"
    return "no detectable change"


def main(smoke: bool = False) -> None:
    path = ROOT / "results" / ("raw_runs_smoke.csv" if smoke else "raw_runs.csv")
    if not path.exists():
        raise SystemExit(f"Missing {path}")
    df = pd.read_csv(path)
    if "status" in df.columns:
        df = df[df["status"].isin(["ok", "gridlock", "timeout"])]
    df = df.copy()
    df["conflicts_per_1000_veh"] = _rate(df)

    comparisons = []
    if "XtraFlow" not in set(df.controller):
        out = {"verdict": "insufficient_data", "comparisons": []}
        save_json(ROOT / "results" / "safety.json", out)
        print(out)
        return

    for other in sorted(set(df.controller) - {"XtraFlow"}):
        a = df[df.controller == "XtraFlow"][["seed", "scenario", "conflicts_per_1000_veh"]].rename(
            columns={"conflicts_per_1000_veh": "ours"}
        )
        b = df[df.controller == other][["seed", "scenario", "conflicts_per_1000_veh"]].rename(
            columns={"conflicts_per_1000_veh": "base"}
        )
        m = a.merge(b, on=["seed", "scenario"])
        if m.empty:
            continue
        delta = (m["ours"] - m["base"]).to_numpy()
        mean, lo, hi = bootstrap_ci(delta, n_resamples=2000 if smoke else 10000)
        try:
            stat, p = stats.wilcoxon(m["ours"], m["base"], zero_method="wilcox", alternative="two-sided")
        except ValueError:
            stat, p = float("nan"), 1.0
        comparisons.append({
            "baseline": other,
            "mean_delta_conflicts_per_1000": mean,
            "ci_lo": lo,
            "ci_hi": hi,
            "wilcoxon_p": float(p),
            "wilcoxon_stat": float(stat) if np.isfinite(stat) else None,
            "n_pairs": int(len(m)),
            "verdict": _verdict(lo, hi),
        })

    # Headline verdict is versus legacy fixed when that comparison exists, else the first baseline.
    primary = next((c for c in comparisons if c["baseline"] == "fixed"), comparisons[0] if comparisons else None)
    out = {
        "metric": "conflicts_per_1000_veh",
        "comparisons": comparisons,
        "verdict": primary["verdict"] if primary else "insufficient_data",
        "verdict_baseline": primary["baseline"] if primary else None,
        "note": "SSM minTTC below 1.5 s, per 1000 departed vehicles. Verdict is the sign of the bootstrap CI.",
        "label": "Simulation-based estimate; assumed traffic mix",
    }
    save_json(ROOT / "results" / "safety.json", out)
    print(out["verdict"])


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--smoke", action="store_true")
    args = p.parse_args()
    main(smoke=args.smoke)
