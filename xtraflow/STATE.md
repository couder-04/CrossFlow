# STATE.md — XtraFlow

Last updated: 2026-10-07

## Status

Published time-cut study is on `main`. Simulation-based estimate; assumed traffic mix. Controllers use an oracle detector unless `info_mode` is camera. Emission classes are proxies.

**Interpretation (no re-run):** adaptive pressure vs fixed / Webster / actuated is about 8–17% fuel; fuel weighting vs `ours_count` is about 0–2% (clearest on low_demand). TEST n=5; all Holm p = 1.0.

## What was published

| Artifact | State |
|---|---|
| `results/demand_calibration.json` | Real VALIDATION, `smoke: false` |
| `results/tuned_params.json` | Real VALIDATION tune, `smoke: false` |
| `results/fixed_tuned.json` | All four scenarios, `smoke: false` |
| `results/rl/ppo_best.zip` | Trained to ≥200000 steps (`smoke: false` in selection meta) |
| `results/rl/rl_selection.json` | Final zip kept; VALIDATION scoring stopped for time |
| `results/config.lock` | Frozen after the real fixed plan and 200k zip |
| `results/raw_runs.csv` | Locked TEST: 8 controllers × 4 scenarios × seeds 1–5 (160 ok rows). No `rl_ppo` |
| `results/headlines.json` | From that TEST sweep |
| `results/robustness.json` | Seeds 1–3 |
| `results/sensitivity.json` | Demand mult + 4 mix samples, seeds 1–2 — **wait metric stale** (see caveats) |
| `results/emission_xcheck.json` | Seeds 1–2 |
| `results/safety.json` | From `raw_runs.csv` |
| `results/grid_results.json` | Not scored; note explains TLS mismatch |
| `results/REPORT.md`, figures, deck | Regenerated from the files above |

## Seed protocol (config)

- TRAIN 1000–1049
- VALIDATION 2000–2019 (tuning and fixed-plan search)
- TEST in config is still 1–30; **this publish used TEST seeds 1–5** after the lock

## Caveats

- **`maxpressure` ≡ `queue_pressure` in published results.** Every metric in `raw_runs.csv` is bit-identical across scenarios and seeds. The old downstream term used `getLastStepHaltingNumber` on out-edges (almost never queued), so the penalty was ~0. `maxpressure` is **not** an independent baseline in this package. Code in `sim/controllers.py` now uses out-edge occupancy (falling back to vehicle count); **TEST numbers are stale until someone re-runs the sweep.**
- PPO was trained to the config budget and is on disk. It was not scored on VALIDATION or TEST, so `rl_ppo` is absent from `raw_runs.csv` and the headline tables. Do not claim PPO performance.
- The 2×2 grid was not scored in this publish. See the note in `results/grid_results.json`.
- Sublane: `results/sublane_fallback.json` records a 60 s one-vehicle probe with `used_sublane` true. Full-demand stability under that setting has not been re-run.
- Safety is SSM minTTC conflicts per 1000 departed vehicles; quote the verdict in `results/safety.json`.
- Fuel per vehicle includes departed vehicles that did not finish. Unfinished runs stay out of the paired headline.
- The headline baseline is the best of fixed_tuned, actuated, queue_pressure, and maxpressure. Because maxpressure matched queue_pressure, that “best” is effectively one controller. Legacy fixed is secondary.
- **`sensitivity.json` waiting times are invalid** for many rows (zeros next to normal fuel; mix rows missing `mean_waiting_s`). Code fixed in `experiments/sensitivity.py` / unique tripinfo tags in `sim/run_sim.py`; **re-run required** before quoting wait sensitivity.
- INR figures in the extrapolation are a placeholder.

- **Whole `raw_runs.csv` predates controller edits.** The gap-out change in `_adaptive_step` applies to every adaptive controller (XtraFlow, ours_count, queue_pressure, maxpressure), not only maxpressure. Until TEST is re-run, published numbers do not strictly reproduce from HEAD.
- **Safety:** decreases are vs fixed / fixed_tuned / actuated / webster. Vs maxpressure and queue_pressure the CI includes 0; vs `ours_count`, XtraFlow shows *more* conflicts (CI +6 to +35 per 1000). Do not summarize safety as "fewer conflicts" without the baseline.
- The max-pressure downstream penalty sums the same four out-edges for NS_TL and EW_TL, so it cannot separate those two phases; a per-movement formulation is still to do.
- `sensitivity.json` fuel values may also be affected by the tripinfo clobbering; treat the whole file as suspect until re-run. The `vi_gain_vs_saturation` figure derives from it.

## Not done in this publish

- Re-run TEST after the max-pressure occupancy fix (so `maxpressure` diverges from `queue_pressure`)
- Full TEST seeds 1–30
- Re-run sensitivity after the wait-metric fix
- VALIDATION scoring of PPO checkpoints and a TEST column for `rl_ppo`
- A working 2×2 grid sweep with per-junction TLS programs
