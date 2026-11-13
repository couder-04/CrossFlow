# Space-aware controller scoring: re-run of the TEST benchmark

**Bottom line:** accounting for vehicle road space did **not** improve fuel. `XtraFlow_space` is
about 2.1% worse than the dot-based `XtraFlow` and about 1.6% worse than the best pressure
baseline, both outside the 95% interval (pooled n=20). Weighting by size alone (`ours_footprint`)
is statistically indistinguishable from the dot `XtraFlow`.

## Method

SUMO already simulates real vehicle size. The dot assumption was only in the controller score
`pressure() = sum(w_type * (1 if halted else alpha))`. Footprint = length x width from
`vehicle_params`, normalised to car = 1. No new assumptions.

| type | fuel weight w | footprint s | XtraFlow_space weight w/s | ours_footprint weight s |
|---|---|---|---|---|
| two_wheeler | 0.535 | 0.20 | 2.71 | 0.20 |
| auto | 1.000 | 0.47 | 2.14 | 0.47 |
| car | 1.000 | 1.00 | 1.00 | 1.00 |
| bus | 1.996 | 3.70 | 0.54 | 3.70 |
| truck | 2.773 | 2.67 | 1.04 | 2.67 |

`*_space3` and `*_footprint3` force the truck footprint to 3.0 (sensitivity).

Protocol: locked TEST, scenarios balanced / peak_unbalanced / dynamic / low_demand, seeds 1-5,
`emission_model=primary`, same demand files. `config.lock` hashes were asserted by the driver.
Published `raw_runs.csv` is untouched. All adaptive controllers were re-run at current HEAD
(published adaptive numbers are stale, see `STATE.md`). `fixed_tuned` and `actuated` were
re-run too and match the published `total_fuel_L` bit-for-bit (40 runs, max diff 0.0).
Every run finished: no errors, no unfinished vehicles, no gridlock; `n_departed` is identical
across controllers for each scenario+seed. Statistics are paired by scenario+seed with 95%
t-intervals. "Best baseline" is the lowest-mean-fuel member of {fixed_tuned, actuated,
queue_pressure, maxpressure} per scenario (maxpressure in 3 scenarios, queue_pressure in balanced).

Files: `space_aware_raw_runs.csv` (200 runs), `space_aware_paired.csv` (all paired results),
`space_aware_fuel_by_type.csv`.

## Fuel per vehicle, % change (negative = less fuel), mean ± 95% CI

| variant | vs | pooled (n=20) | balanced | dynamic | low_demand | peak_unbalanced |
|---|---|---|---|---|---|---|
| XtraFlow (dot, before) | best baseline | -0.46 ±0.70 | -0.30 ±1.12 | +0.51 ±0.58 | -1.36 ±2.72 | -0.69 ±1.93 |
| XtraFlow (dot, before) | ours_count | -0.84 ±0.66 | -0.35 ±1.60 | -0.13 ±0.79 | -2.03 ±2.18 | -0.85 ±1.59 |
| XtraFlow (dot, before) | queue_pressure | -1.00 ±0.60 | -0.30 ±1.12 | -1.09 ±0.84 | -1.91 ±2.04 | -0.69 ±1.72 |
| **XtraFlow_space** | XtraFlow (dot) | **+2.11 ±0.81** | +1.72 ±1.70 | +1.33 ±0.47 | +3.67 ±2.98 | +1.71 ±1.79 |
| **XtraFlow_space** | best baseline | **+1.62 ±0.55** | +1.41 ±0.94 | +1.85 ±0.59 | +2.22 ±2.29 | +1.00 ±1.39 |
| **XtraFlow_space** | ours_count | +1.23 ±0.40 | +1.35 ±1.26 | +1.20 ±0.76 | +1.53 ±1.19 | +0.84 ±1.15 |
| **XtraFlow_space** | queue_pressure | +1.07 ±0.47 | +1.41 ±0.94 | +0.23 ±1.25 | +1.65 ±1.24 | +1.00 ±1.08 |
| ours_footprint | XtraFlow (dot) | -0.28 ±0.54 | -0.03 ±1.29 | -0.44 ±1.49 | -0.43 ±1.72 | -0.22 ±1.64 |
| ours_footprint | best baseline | -0.74 ±0.69 | -0.32 ±1.64 | +0.06 ±1.26 | -1.80 ±2.62 | -0.92 ±0.90 |
| ours_footprint | ours_count | -1.13 ±0.70 | -0.38 ±1.93 | -0.58 ±1.53 | -2.46 ±2.20 | -1.08 ±0.75 |
| ours_footprint | queue_pressure | -1.28 ±0.73 | -0.32 ±1.64 | -1.53 ±2.26 | -2.34 ±2.17 | -0.92 ±0.89 |

Truck footprint 3.0 sensitivity (pooled): `XtraFlow_space3` +2.40 ±0.91 vs XtraFlow, +1.91 ±0.57
vs best baseline; `ours_footprint3` -0.26 ±0.57 vs XtraFlow, -0.72 ±0.61 vs best baseline.
Same conclusion.

## Other metrics, pooled % change

| variant (vs XtraFlow dot) | mean wait | p95 wait | mean_queue_veh | mean_queue_area_ceq | stops/veh | conflicts/1000 |
|---|---|---|---|---|---|---|
| XtraFlow_space | -1.9 | +0.5 | -1.9 | **+20.7** | -1.3 | -3.2 |
| ours_footprint | +2.4 | +3.8 | +2.5 | -5.1 | -0.5 | +3.5 |

The same table versus the other baselines is in `space_aware_paired.csv` (all metrics, all
scenarios). The pooled `XtraFlow_space` rows versus best baseline: wait +3.2%, p95 +4.9%,
queue area +13.5%.

## Who gains and loses (mean fuel L per run, `fuel_L_by_type`)

| type | XtraFlow | XtraFlow_space | change |
|---|---|---|---|
| two_wheeler | 45.23 | 43.91 | -2.9% |
| auto | 11.96 | 11.78 | -1.5% |
| car | 35.96 | 36.46 | +1.4% |
| bus | 22.56 | 23.25 | +3.0% |
| truck | 75.96 | 79.69 | **+4.9%** |

The new weights do what was predicted: small vehicles gain priority and trucks and buses lose it.
Trucks are the biggest fuel consumers, so the loss outweighs the small-vehicle gain and total
fuel rises. Per-type waiting time was not recorded by the harness, so truck/bus waiting is
inferred from fuel only.

## What this means

- Total fuel does not improve with the space correction; it gets worse, and the interval excludes 0.
- `ours_footprint` (space only) is within noise of the dot `XtraFlow` (interval includes 0), so
  there is no evidence that fuel/space adds anything over size weighting. Neither can the dot
  `XtraFlow` be said to beat the best baseline (-0.46 ±0.70 includes 0), matching the original
  study's caveat. Both `ours_footprint` and `XtraFlow` beat `ours_count` and `queue_pressure`
  pooled, but not the best-baseline comparison with confidence.
- The car-equivalent queue metric (`mean_queue_area_ceq`) gets longer under `XtraFlow_space`
  because fewer large vehicles are served early. The vehicle-count queue looks slightly better,
  so the count metric hides this.
- A plausible reading: the idle-fuel weights already encode truck cost per vehicle, and dividing
  by footprint shifts green toward vehicles that burn less fuel. This is not tested here.

## Caveats

- `tuned_params.json` (detection distance, alpha, hysteresis) was tuned for the dot version and
  was deliberately not re-tuned. This slightly favours the dot `XtraFlow`. A VALIDATION-seed
  (2000-2019) re-tune of the space-aware controller was not done, so a re-tuned
  `XtraFlow_space` could close some of the gap.
- n=5 per scenario; per-scenario intervals are wide and mostly include 0. Pooled intervals treat
  the 20 scenario-seed pairs as independent.
- Footprint uses length x width as a proxy for green time; actual discharge depends on
  acceleration and the sublane model, which could weight differently.
- Results are from one machine (macOS arm64, SUMO 1.21) and reproduce published fixed-plan numbers exactly.
