# What changed

Simulation-based estimate; assumed traffic mix.

## Docs / code honesty pass (2026-10-07, no re-run)

- Reframed claims: adaptive pressure vs fixed/Webster/actuated ≈ 8–17%; fuel weights ≈ 0–2% (mainly low_demand); n=5; Holm p=1.0.
- Documented that published `maxpressure` ≡ `queue_pressure` (not an independent baseline).
- Patched `maxpressure` downstream to occupancy / vehicle count; unit test added. **`raw_runs.csv` / headlines remain the 2026-10-06 numbers (stale for maxpressure).**
- Fixed sensitivity `mean_waiting_s` drop + tripinfo tag collisions. **`sensitivity.json` wait values remain stale until re-run.**

## Published time-cut (2026-10-06)

The locked TEST comparison in `results/raw_runs.csv` and the headline file `results/headlines.json` are from this publish. Do not quote older pre-fix headlines.

### What was re-run for real

- Demand calibration on VALIDATION (`smoke: false`).
- Controller tune on VALIDATION (`smoke: false`).
- Fixed-green search for all four scenarios (`results/fixed_tuned.json`, `smoke: false`). Coarse screen used 900 s for peak/dynamic/low; balanced was scored on the full hour. Finalists confirmed on the full demand horizon.
- PPO trained to the config budget (≥200000 steps). `results/rl/ppo_best.zip` is that final checkpoint. VALIDATION scoring of checkpoints was stopped for wall-clock; `rl_ppo` is not in the TEST CSV.
- Config lock after the real fixed plan and the 200k zip.
- TEST sweep: 8 controllers (no `rl_ppo`), 4 scenarios, seeds **1–5**, full demand horizon, all `status=ok`.
- Analyze, report, figures, robustness (seeds 1–3), sensitivity (trimmed), emission cross-check (seeds 1–2), safety, extrapolate, demo, deck, audit, pytest.

### Explicit cuts

- TEST seeds 1–5 instead of 1–30.
- No PPO in the TEST comparison.
- 2×2 grid not scored (`results/grid_results.json` records why).

## Claims removed or constrained

- Lateral resolution is not claimed unless `results/sublane_fallback.json` says the probe ran.
- Safety is not described as all zeros unless `results/safety.json` says so.
- The grid is not described as a numeric result until a successful grid sweep exists.
- RL is not described as a fixed percent better than PPO. It is a baseline trained with the budget in `results/rl/rl_selection.json` and, in this publish, is not in the TEST table.
- Detector-miss curves are not described as flat unless the loaded robustness rows say that.
- Degree of saturation is the measured field in `results/demand_calibration.json`, or it is absent.
- HBEFA4 is not the primary model. Config and `results/emission_class_map.json` name HBEFA3.
- INR figures stay a placeholder.
- Large % fuel cuts are not attributed to class fuel weighting; those come from adaptive pressure vs fixed-style baselines.
- `maxpressure` is not described as a distinct baseline for the published CSV.

## Code corrections that change the numbers

- Perception misses are a per-vehicle Markov burst, seeded from the run, and count jitter is applied.
- SSM counts `<conflict>` elements whose `minTTC` value is below the threshold, per 1000 departed vehicles.
- Fuel per vehicle divides by departed vehicles and includes unfinished trips.
- Crashes are `status=error`. Unfinished runs stay out of the paired headline.
- `fixed_tuned` is selected on VALIDATION. Queue-pressure and max-pressure are separate controllers.
- Webster reads measured VALIDATION flows.
- The config lock is created by `make freeze_config` and the sweep only checks it.
- TraCI starts without a fixed port so SUMO can retry on a free port.
- Short CLI flags (`--max-seeds`, `--val-seeds`, `--mix-samples`) support time-cut evaluation without rewriting the seed protocol in config.
