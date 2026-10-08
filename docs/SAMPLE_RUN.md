# Sample run

Output of `make demo-full` on 2026-10-08 (Apple Silicon, 4 workers, about 3 minutes). Reproduce with
`make demo-full`; seeds are fixed, so the numbers should repeat on the same SUMO version.
The raw per-run rows are in the JSON report, which is not committed (`runs/` is ignored).

Read the verdict, not the headline: against the best baseline the difference is not established.


**Mode:** full (3600 s horizon, seeds [1, 2, 3, 4, 5])
**Source:** offline CrossSight city simulation (synthetic grid), camera `cam-010` at 08:00, 2000 demand streams

## Measured demand (veh/h per arm)

| Arm | Observed | Scale vs XtraFlow balanced | GEH (sim vs obs) |
|---|---|---|---|
| N | 296 | 0.617 | 0.9 |
| S | 366 | 0.762 | 0.16 |
| E | 39 | 0.081 | 0.22 |
| W | 413 | 0.86 | 0.99 |

Vehicle mix: camera-observed (class 'other' dropped). GEH target < 5.

## Controllers (mean over paired clean seeds)

| Controller | Fuel / vehicle (L) | Mean wait (s) | Mean queue | Stops / veh |
|---|---|---|---|---|
| fixed_tuned | 0.1074 | 27.8 | 8.3 | 0.79 |
| actuated | 0.1044 | 24.6 | 7.3 | 0.79 |
| queue_pressure | 0.0955 | 18.6 | 5.5 | 0.71 |
| XtraFlow | 0.0947 | 18.7 | 5.6 | 0.72 |

## XtraFlow fuel reduction vs each baseline

| Baseline | Reduction % | 95% CI | n |
|---|---|---|---|
| fixed_tuned | 11.76 | 9.82 to 13.69 | 5 |
| actuated | 9.26 | 8.24 to 10.28 | 5 |
| queue_pressure | 0.75 | -0.44 to 1.95 | 5 |

**Verdict:** no established difference from the best baseline (queue_pressure); interval spans zero

## Caveats

- The city is CrossSight's synthetic grid; its trips are scaled by superimposing 2000 demand streams. The flow is realistic in shape, not measured.
- XtraFlow's tuned parameters and fixed plan were tuned on the assumed demand and mix, not on this measured one.
- Results are a SUMO simulation of one four-arm junction; they are not a field measurement.
