# XtraFlow — Research Report

**Label:** Simulation-based estimate; not a real-world deployment result. Assumed mixed-traffic scenario unless observed counts provided.

## How to read these numbers (2026-10-07)

- **Adaptive pressure vs fixed / Webster / actuated:** about **8–17%** lower fuel per vehicle (scenario means from `raw_runs.csv`; actuated is closer to 5–11%). That is the main effect.
- **Fuel weighting vs count-only (`ours_count`):** about **0–2%**; detectable mainly on `low_demand`. Against the best pressure baseline the headline is **0.3–1.9%**, with CIs that include 0 on balanced and peak_unbalanced.
- **TEST n=5.** Holm-adjusted Wilcoxon p-values are all **1.0** (minimum raw Wilcoxon p at n=5 is 0.0625).
- **`maxpressure` is not an independent baseline in this file.** In `raw_runs.csv` it is bit-identical to `queue_pressure` on every metric, because the published controller used a near-zero downstream halting penalty. Treat “vs maxpressure” and “vs queue_pressure” as the same comparison. Controller code was patched to use out-edge occupancy / vehicle count; **these tables are stale until a re-sweep.**
- **`results/sensitivity.json` waiting times are stale / invalid** (many `mean_waiting_s: 0.0` beside normal fuel; mix rows omitted the key). Do not quote wait sensitivity until re-run.

## Abstract

We evaluate an adaptive, fuel-weighted pressure traffic signal controller (XtraFlow) against legacy fixed-time, a validation-tuned fixed plan, Webster, SUMO-actuated, queue-pressure, max-pressure, and a count-only ablation. PPO was trained to the configured step budget and is not in this TEST comparison. Controllers use an oracle detector (SUMO speed, class, and route turn) unless info_mode is camera. Emission classes are proxies. Simulation-based estimate; assumed traffic mix. 
Sublane actually used: True. 
 On `balanced`, mean fuel change versus `maxpressure` was 0.30% (95% CI [-0.38, 1.01]; flag=OK).
 On `dynamic`, mean fuel change versus `maxpressure` was 1.09% (95% CI [0.65, 1.70]; flag=OK).
 On `low_demand`, mean fuel change versus `maxpressure` was 1.91% (95% CI [0.51, 3.04]; flag=OK).
 On `peak_unbalanced`, mean fuel change versus `maxpressure` was 0.69% (95% CI [-0.35, 1.77]; flag=OK).


## Method

- Single 4-arm intersection; left-hand traffic; yellow 3 s; all-red 2 s. Grid numbers are reported only from results/grid_results.json.

- Seed protocol: TRAIN 1000–1049, VALIDATION 2000–2019, TEST 1–5 (5 seeds in raw_runs.csv) after config.lock.

- Metrics from tripinfo with device.emissions.probability=1; fuel mg→L via densities.

- Sublane model used: True.

- Emission class map (primary): `{"two_wheeler": "HBEFA3/LDV_G_EU4", "auto_rickshaw": "HBEFA3/PC_G_EU4", "car": "HBEFA3/PC_G_EU4", "bus": "HBEFA3/Bus", "truck": "HBEFA3/HDV"}`.


## Assumptions

| Parameter | Value | Source |
|---|---|---|

| Vehicle mix | 2W 40 / car 30 / auto 10 / bus 5 / truck 15 | assumed |

| Occupancy | 1.3 / 2.0 / 2.0 / 40 / 1.2 | assumed |

| Fuel densities | petrol 0.74, diesel 0.84 kg/L | assumed |

| Geometry | 300 m approaches, 3 in / 2 out lanes | assumed |

| Demand mode | measured_on_validation | Approach veh/h are counts of vehicles seen on each incoming edge, scaled to an hour from the simulated duration. Degree of saturation uses an assumed 1800 veh/h/lane and the green fraction served. The config demand table is not copied into measured_approach_veh_h. |


## Weights (measured)

```

{
  "idle_fuel_mg_per_s": {
    "two_wheeler": 448.05555555555554,
    "auto_rickshaw": 837.2222222222223,
    "car": 837.2222222222223,
    "bus": 1671.1111111111109,
    "truck": 2321.6666666666665
  },
  "w_type": {
    "two_wheeler": 0.5351692103516921,
    "auto_rickshaw": 1.0,
    "car": 1.0,
    "bus": 1.9960185799601853,
    "truck": 2.77305905773059
  },
  "reference": "car",
  "duration_s": 60.0,
  "note": "Measured via TraCI getFuelConsumption while stopped. auto-rickshaw uses a passenger-car emission class and two-wheeler uses LDV_G_EU4 when that class loads; these idle-fuel weights inherit those proxies. Simulation-based estimate; assumed traffic mix."
}

```


## Tuned parameters (VALIDATION only)

```

{
  "detection_distance_m": 150,
  "alpha_moving": 0.5,
  "hysteresis": 0.25,
  "selected_on": "VALIDATION seeds only",
  "scenarios": [
    "balanced",
    "peak_unbalanced",
    "dynamic",
    "low_demand"
  ],
  "screen_seeds": [
    2000,
    2001,
    2002,
    2003,
    2004
  ],
  "confirm_seeds": [
    2000,
    2001,
    2002,
    2003,
    2004,
    2005,
    2006,
    2007,
    2008,
    2009,
    2010,
    2011,
    2012,
    2013,
    2014,
    2015,
    2016,
    2017,
    2018,
    2019
  ],
  "mean_fuel_per_vehicle_L": 0.12327643280361333,
  "screen_relative_span": 0.013761764847601068,
  "n_screen_combos": 27,
  "smoke": false
}

```


## Per-scenario results (mean±std)


| scenario        | controller     |   n |   fuel_per_vehicle_L_mean |   fuel_per_vehicle_L_std |   total_CO2_kg_mean |   total_CO2_kg_std |   mean_waiting_s_mean |   mean_waiting_s_std |   p95_waiting_s_mean |   p95_waiting_s_std |   mean_queue_veh_mean |   mean_queue_veh_std |   n_completed_mean |   n_completed_std |   n_unfinished_mean |   n_unfinished_std |   gridlock_flag_mean |   gridlock_flag_std |   ssm_conflicts_mean |   ssm_conflicts_std |
|:----------------|:---------------|----:|--------------------------:|-------------------------:|--------------------:|-------------------:|----------------------:|---------------------:|---------------------:|--------------------:|----------------------:|---------------------:|-------------------:|------------------:|--------------------:|-------------------:|---------------------:|--------------------:|---------------------:|--------------------:|
| balanced        | fixed          |   5 |                  0.141919 |               0.00371389 |             685.837 |           31.1984  |               30.0277 |             1.26806  |                 73.8 |            4.43847  |              15.333   |             0.925331 |             1932.2 |           37.3323 |                   0 |                  0 |                    0 |                   0 |               2992   |            126.493  |
| balanced        | fixed_tuned    |   5 |                  0.141359 |               0.00283377 |             683.303 |           26.287   |               28.7274 |             0.559475 |                 67.2 |            0.447214 |              14.9333  |             0.492688 |             1932.2 |           37.3323 |                   0 |                  0 |                    0 |                   0 |               3069.2 |            151.759  |
| balanced        | actuated       |   5 |                  0.13723  |               0.00177556 |             663.123 |           21.1561  |               26.1047 |             0.445988 |                 62.2 |            0.83666  |              13.6325  |             0.354022 |             1932.2 |           37.3323 |                   0 |                  0 |                    0 |                   0 |               2928.8 |            133.618  |
| balanced        | webster        |   5 |                  0.143443 |               0.00281791 |             693.587 |           27.1856  |               28.8671 |             0.448629 |                 61   |            0.707107 |              15.0257  |             0.457958 |             1932.2 |           37.3323 |                   0 |                  0 |                    0 |                   0 |               3266.8 |            116.076  |
| balanced        | queue_pressure |   5 |                  0.131003 |               0.00179025 |             633.489 |           20.7878  |               21.3651 |             0.448953 |                 77.2 |            3.34664  |              11.1353  |             0.245167 |             1932.2 |           37.3323 |                   0 |                  0 |                    0 |                   0 |               2538.8 |            101.08   |
| balanced        | maxpressure    |   5 |                  0.131003 |               0.00179025 |             633.489 |           20.7878  |               21.3651 |             0.448953 |                 77.2 |            3.34664  |              11.1353  |             0.245167 |             1932.2 |           37.3323 |                   0 |                  0 |                    0 |                   0 |               2538.8 |            101.08   |
| balanced        | ours_count     |   5 |                  0.131089 |               0.00216344 |             633.928 |           21.5611  |               21.3428 |             0.436245 |                 78   |            2.54951  |              11.1094  |             0.306849 |             1932.2 |           37.3323 |                   0 |                  0 |                    0 |                   0 |               2546.8 |             94.7059 |
| balanced        | XtraFlow       |   5 |                  0.130612 |               0.0017711  |             631.069 |           21.5792  |               21.7883 |             0.424012 |                 73.4 |            1.34164  |              11.3502  |             0.348273 |             1932.2 |           37.3323 |                   0 |                  0 |                    0 |                   0 |               2591.6 |             82.0658 |
| peak_unbalanced | fixed          |   5 |                  0.151162 |               0.00591313 |             679.431 |           26.9478  |               36.9526 |             3.39836  |                110.2 |           27.3624   |              17.4575  |             1.46347  |             1801.4 |           21.2438 |                   0 |                  0 |                    0 |                   0 |               3125.8 |             96.9211 |
| peak_unbalanced | fixed_tuned    |   5 |                  0.137858 |               0.00366985 |             619.991 |           12.0939  |               28.141  |             1.22182  |                 77.6 |            2.07364  |              13.6022  |             0.559678 |             1801.4 |           21.2438 |                   0 |                  0 |                    0 |                   0 |               2728   |             56.9517 |
| peak_unbalanced | actuated       |   5 |                  0.13611  |               0.00190695 |             612.856 |            9.24416 |               25.3208 |             0.920304 |                 61.4 |            0.547723 |              12.314   |             0.538097 |             1801.4 |           21.2438 |                   0 |                  0 |                    0 |                   0 |               2792.4 |            102.478  |
| peak_unbalanced | webster        |   5 |                  0.138243 |               0.00246212 |             622.258 |            7.33637 |               26.4179 |             0.517538 |                 61.8 |            1.09545  |              12.8414  |             0.263676 |             1801.4 |           21.2438 |                   0 |                  0 |                    0 |                   0 |               2920.2 |            136.023  |
| peak_unbalanced | queue_pressure |   5 |                  0.125404 |               0.00292329 |             564.675 |            7.84695 |               18.1115 |             0.779773 |                 68.8 |            4.08656  |               8.82277 |             0.285753 |             1801.4 |           21.2438 |                   0 |                  0 |                    0 |                   0 |               2208.4 |             29.7456 |
| peak_unbalanced | maxpressure    |   5 |                  0.125404 |               0.00292329 |             564.675 |            7.84695 |               18.1115 |             0.779773 |                 68.8 |            4.08656  |               8.82277 |             0.285753 |             1801.4 |           21.2438 |                   0 |                  0 |                    0 |                   0 |               2208.4 |             29.7456 |
| peak_unbalanced | ours_count     |   5 |                  0.125603 |               0.00271913 |             565.715 |            7.02358 |               18.2265 |             0.507003 |                 70   |            2.54951  |               8.88026 |             0.17991  |             1801.4 |           21.2438 |                   0 |                  0 |                    0 |                   0 |               2168   |             62.8172 |
| peak_unbalanced | XtraFlow       |   5 |                  0.124543 |               0.00351481 |             560.185 |           11.5141  |               18.3722 |             0.774463 |                 70.6 |            1.81659  |               8.95535 |             0.356174 |             1801.4 |           21.2438 |                   0 |                  0 |                    0 |                   0 |               2244.4 |             65.2403 |
| dynamic         | fixed          |   5 |                  0.143904 |               0.00298723 |             631.549 |           22.1963  |               32.1862 |             1.12618  |                 81.4 |            6.10737  |              15.1496  |             0.578419 |             1757.2 |           29.201  |                   0 |                  0 |                    0 |                   0 |               2874.2 |            106.516  |
| dynamic         | fixed_tuned    |   5 |                  0.150542 |               0.0029684  |             660.433 |           22.0436  |               36.9068 |             2.3681   |                 99   |            8        |              17.3349  |             0.994355 |             1757.2 |           29.201  |                   0 |                  0 |                    0 |                   0 |               2977.2 |             78.6174 |
| dynamic         | actuated       |   5 |                  0.13643  |               0.00348899 |             599.261 |           26.2932  |               25.8703 |             0.632416 |                 61.2 |            1.09545  |              12.3169  |             0.389739 |             1757.2 |           29.201  |                   0 |                  0 |                    0 |                   0 |               2766.4 |             95.5945 |
| dynamic         | webster        |   5 |                  0.151002 |               0.005122   |             663.016 |           34.1332  |               34.7427 |             2.04246  |                 83.8 |           13.3679   |              16.4352  |             1.16926  |             1757.2 |           29.201  |                   0 |                  0 |                    0 |                   0 |               3244.8 |            189.97   |
| dynamic         | queue_pressure |   5 |                  0.126232 |               0.00317047 |             554.993 |           23.3084  |               18.5589 |             0.310273 |                 68.2 |            3.11448  |               8.82322 |             0.23784  |             1757.2 |           29.201  |                   0 |                  0 |                    0 |                   0 |               2139.6 |             70.5429 |
| dynamic         | maxpressure    |   5 |                  0.126232 |               0.00317047 |             554.993 |           23.3084  |               18.5589 |             0.310273 |                 68.2 |            3.11448  |               8.82322 |             0.23784  |             1757.2 |           29.201  |                   0 |                  0 |                    0 |                   0 |               2139.6 |             70.5429 |
| dynamic         | ours_count     |   5 |                  0.125022 |               0.00333375 |             549.221 |           23.4025  |               18.4312 |             0.592162 |                 68.8 |            4.91935  |               8.74413 |             0.32188  |             1757.2 |           29.201  |                   0 |                  0 |                    0 |                   0 |               2102.8 |             70.6767 |
| dynamic         | XtraFlow       |   5 |                  0.124853 |               0.00325591 |             548.053 |           23.8466  |               18.81   |             0.332752 |                 70.6 |            1.81659  |               8.94317 |             0.175767 |             1757.2 |           29.201  |                   0 |                  0 |                    0 |                   0 |               2139.2 |            108.077  |
| low_demand      | fixed          |   5 |                  0.136329 |               0.00535827 |             205.464 |            9.07916 |               27.2225 |             1.44494  |                 69   |            1.22474  |               4.44159 |             0.230881 |              602.8 |           30.3266 |                   0 |                  0 |                    0 |                   0 |                417.6 |             60.6201 |
| low_demand      | fixed_tuned    |   5 |                  0.133118 |               0.0048656  |             200.781 |           12.2775  |               24.5415 |             1.16478  |                 60   |            1        |               4.02138 |             0.379656 |              602.8 |           30.3266 |                   0 |                  0 |                    0 |                   0 |                382.8 |             59.6423 |
| low_demand      | actuated       |   5 |                  0.131084 |               0.00570023 |             197.746 |           13.7797  |               23.1748 |             1.03395  |                 56.4 |            0.547723 |               3.78633 |             0.317689 |              602.8 |           30.3266 |                   0 |                  0 |                    0 |                   0 |                397   |             54.1756 |
| low_demand      | webster        |   5 |                  0.132538 |               0.00388561 |             199.775 |            9.71825 |               24.0416 |             0.921683 |                 57.6 |            0.547723 |               3.93945 |             0.298008 |              602.8 |           30.3266 |                   0 |                  0 |                    0 |                   0 |                394   |             61.3677 |
| low_demand      | queue_pressure |   5 |                  0.119356 |               0.00581493 |             179.986 |           10.1886  |               15.2287 |             1.05288  |                 57.6 |            7.63544  |               2.50079 |             0.212638 |              602.8 |           30.3266 |                   0 |                  0 |                    0 |                   0 |                238   |             48.3529 |
| low_demand      | maxpressure    |   5 |                  0.119356 |               0.00581493 |             179.986 |           10.1886  |               15.2287 |             1.05288  |                 57.6 |            7.63544  |               2.50079 |             0.212638 |              602.8 |           30.3266 |                   0 |                  0 |                    0 |                   0 |                238   |             48.3529 |
| low_demand      | ours_count     |   5 |                  0.11951  |               0.0059792  |             180.208 |           10.3066  |               15.3518 |             1.05277  |                 58.4 |            7.95613  |               2.52017 |             0.202433 |              602.8 |           30.3266 |                   0 |                  0 |                    0 |                   0 |                237.2 |             49.5702 |
| low_demand      | XtraFlow       |   5 |                  0.11702  |               0.00460451 |             175.992 |           10.0836  |               16.1693 |             0.667017 |                 59.4 |            1.14018  |               2.65471 |             0.23351  |              602.8 |           30.3266 |                   0 |                  0 |                    0 |                   0 |                234   |             38.704  |


## Statistical tests (XtraFlow vs baselines)


| scenario        | baseline       | metric             |   pct_reduction_mean |   pct_reduction_ci_lo |   pct_reduction_ci_hi |   wilcoxon_stat |   p_raw |   cohens_dz |   n_pairs |   p_holm |
|:----------------|:---------------|:-------------------|---------------------:|----------------------:|----------------------:|----------------:|--------:|------------:|----------:|---------:|
| balanced        | fixed          | fuel_per_vehicle_L |             7.93819  |              6.71058  |               9.08539 |               0 |  0.0625 |   -4.65124  |         5 |        1 |
| balanced        | fixed_tuned    | fuel_per_vehicle_L |             7.58857  |              6.65048  |               8.42523 |               0 |  0.0625 |   -5.89147  |         5 |        1 |
| balanced        | webster        | fuel_per_vehicle_L |             8.93394  |              8.43444  |               9.71167 |               0 |  0.0625 |   -8.73082  |         5 |        1 |
| balanced        | actuated       | fuel_per_vehicle_L |             4.82153  |              4.29527  |               5.25047 |               0 |  0.0625 |   -7.80534  |         5 |        1 |
| balanced        | queue_pressure | fuel_per_vehicle_L |             0.295165 |             -0.380924 |               1.0131  |               5 |  0.625  |   -0.332978 |         5 |        1 |
| balanced        | maxpressure    | fuel_per_vehicle_L |             0.295165 |             -0.380924 |               1.0131  |               5 |  0.625  |   -0.332978 |         5 |        1 |
| balanced        | ours_count     | fuel_per_vehicle_L |             0.353543 |             -0.755788 |               1.24958 |               5 |  0.625  |   -0.285252 |         5 |        1 |
| dynamic         | fixed          | fuel_per_vehicle_L |            13.2395   |             12.3303   |              14.308   |               0 |  0.0625 |  -10.3006   |         5 |        1 |
| dynamic         | fixed_tuned    | fuel_per_vehicle_L |            17.0438   |             15.0953   |              18.9426  |               0 |  0.0625 |   -6.35245  |         5 |        1 |
| dynamic         | webster        | fuel_per_vehicle_L |            17.2855   |             15.8235   |              18.7444  |               0 |  0.0625 |   -7.72593  |         5 |        1 |
| dynamic         | actuated       | fuel_per_vehicle_L |             8.48254  |              7.76617  |               9.21374 |               0 |  0.0625 |   -8.28822  |         5 |        1 |
| dynamic         | queue_pressure | fuel_per_vehicle_L |             1.09237  |              0.647048 |               1.70239 |               0 |  0.0625 |   -1.61694  |         5 |        1 |
| dynamic         | maxpressure    | fuel_per_vehicle_L |             1.09237  |              0.647048 |               1.70239 |               0 |  0.0625 |   -1.61694  |         5 |        1 |
| dynamic         | ours_count     | fuel_per_vehicle_L |             0.132786 |             -0.359609 |               0.62518 |               6 |  0.8125 |   -0.21331  |         5 |        1 |
| low_demand      | fixed          | fuel_per_vehicle_L |            14.1478   |             12.715    |              15.4877  |               0 |  0.0625 |   -6.87583  |         5 |        1 |
| low_demand      | fixed_tuned    | fuel_per_vehicle_L |            12.0972   |             11.3836   |              12.8108  |               0 |  0.0625 |  -12.7132   |         5 |        1 |
| low_demand      | webster        | fuel_per_vehicle_L |            11.7264   |             10.9117   |              12.7308  |               0 |  0.0625 |  -11.7439   |         5 |        1 |
| low_demand      | actuated       | fuel_per_vehicle_L |            10.6947   |              8.95637  |              12.433   |               0 |  0.0625 |   -4.21333  |         5 |        1 |
| low_demand      | queue_pressure | fuel_per_vehicle_L |             1.91393  |              0.50983  |               3.03893 |               1 |  0.125  |   -1.14308  |         5 |        1 |
| low_demand      | maxpressure    | fuel_per_vehicle_L |             1.91393  |              0.50983  |               3.03893 |               1 |  0.125  |   -1.14308  |         5 |        1 |
| low_demand      | ours_count     | fuel_per_vehicle_L |             2.03332  |              0.50983  |               3.27372 |               1 |  0.125  |   -1.13313  |         5 |        1 |
| peak_unbalanced | fixed          | fuel_per_vehicle_L |            17.5162   |             14.4282   |              20.3266  |               0 |  0.0625 |   -3.97228  |         5 |        1 |
| peak_unbalanced | fixed_tuned    | fuel_per_vehicle_L |             9.64293  |              8.03889  |              11.247   |               0 |  0.0625 |   -4.37266  |         5 |        1 |
| peak_unbalanced | webster        | fuel_per_vehicle_L |             9.92109  |              8.87416  |              10.5582  |               0 |  0.0625 |   -9.12175  |         5 |        1 |
| peak_unbalanced | actuated       | fuel_per_vehicle_L |             8.50188  |              6.64144  |              10.0216  |               0 |  0.0625 |   -4.11111  |         5 |        1 |
| peak_unbalanced | queue_pressure | fuel_per_vehicle_L |             0.689012 |             -0.354357 |               1.76514 |               3 |  0.3125 |   -0.491294 |         5 |        1 |
| peak_unbalanced | maxpressure    | fuel_per_vehicle_L |             0.689012 |             -0.354357 |               1.76514 |               3 |  0.3125 |   -0.491294 |         5 |        1 |
| peak_unbalanced | ours_count     | fuel_per_vehicle_L |             0.85079  |             -0.13364  |               1.77265 |               3 |  0.3125 |   -0.655749 |         5 |        1 |



## Where XtraFlow loses or ties


- p95_waiting_s vs `fixed_tuned` on `balanced`: -9.24% (CI [-11.04, -7.11])

- p95_waiting_s vs `webster` on `balanced`: -20.35% (CI [-22.78, -17.87])

- p95_waiting_s vs `actuated` on `balanced`: -18.03% (CI [-20.40, -15.66])

- mean_waiting_s vs `queue_pressure` on `balanced`: -2.00% (CI [-3.39, -0.19])

- mean_waiting_s vs `maxpressure` on `balanced`: -2.00% (CI [-3.39, -0.19])

- mean_waiting_s vs `ours_count` on `balanced`: -2.14% (CI [-4.88, 0.39])

- p95_waiting_s vs `actuated` on `dynamic`: -15.41% (CI [-18.50, -12.31])

- mean_waiting_s vs `queue_pressure` on `dynamic`: -1.36% (CI [-2.50, 0.16])

- p95_waiting_s vs `queue_pressure` on `dynamic`: -3.74% (CI [-8.43, 1.49])

- mean_waiting_s vs `maxpressure` on `dynamic`: -1.36% (CI [-2.50, 0.16])

- p95_waiting_s vs `maxpressure` on `dynamic`: -3.74% (CI [-8.43, 1.49])

- mean_waiting_s vs `ours_count` on `dynamic`: -2.12% (CI [-4.32, 0.36])

- p95_waiting_s vs `ours_count` on `dynamic`: -3.06% (CI [-9.39, 3.27])

- p95_waiting_s vs `webster` on `low_demand`: -3.14% (CI [-5.25, -1.38])

- p95_waiting_s vs `actuated` on `low_demand`: -5.31% (CI [-6.33, -4.29])

- mean_waiting_s vs `queue_pressure` on `low_demand`: -6.42% (CI [-10.48, -2.16])

- p95_waiting_s vs `queue_pressure` on `low_demand`: -4.40% (CI [-13.69, 5.28])

- mean_waiting_s vs `maxpressure` on `low_demand`: -6.42% (CI [-10.48, -2.16])

- p95_waiting_s vs `maxpressure` on `low_demand`: -4.40% (CI [-13.69, 5.28])

- mean_waiting_s vs `ours_count` on `low_demand`: -5.58% (CI [-9.96, -1.20])

- p95_waiting_s vs `ours_count` on `low_demand`: -3.08% (CI [-13.23, 6.45])

- p95_waiting_s vs `webster` on `peak_unbalanced`: -14.24% (CI [-15.86, -12.60])

- p95_waiting_s vs `actuated` on `peak_unbalanced`: -14.98% (CI [-16.60, -13.11])

- mean_waiting_s vs `queue_pressure` on `peak_unbalanced`: -1.52% (CI [-4.91, 1.87])

- p95_waiting_s vs `queue_pressure` on `peak_unbalanced`: -3.02% (CI [-10.10, 3.68])

- mean_waiting_s vs `maxpressure` on `peak_unbalanced`: -1.52% (CI [-4.91, 1.87])

- p95_waiting_s vs `maxpressure` on `peak_unbalanced`: -3.02% (CI [-10.10, 3.68])

- mean_waiting_s vs `ours_count` on `peak_unbalanced`: -0.81% (CI [-3.54, 2.10])

- p95_waiting_s vs `ours_count` on `peak_unbalanced`: -1.01% (CI [-5.81, 2.52])


## Ablations


| scenario        |   pct_reduction_fuel_vs_count_mean |     ci_lo |   ci_hi | statement                |
|:----------------|-----------------------------------:|----------:|--------:|:-------------------------|
| balanced        |                           0.353543 | -0.755788 | 1.24958 | no_detectable_difference |
| dynamic         |                           0.132786 | -0.359609 | 0.62518 | no_detectable_difference |
| low_demand      |                           2.03332  |  0.50983  | 3.27372 | fuel_weights_lower_fuel  |
| peak_unbalanced |                           0.85079  | -0.13364  | 1.77265 | no_detectable_difference |



## RL comparison


RL selected on VALIDATION: `{"selected_on": "Final checkpoint at 200704 steps. VALIDATION scoring was stopped before it finished.", "train_seeds": [1000, 1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008, 1009, 1010, 1011, 1012, 1013, 1014, 1015, 1016, 1017, 1018, 1019, 1020, 1021, 1022, 1023, 1024, 1025, 1026, 1027, 1028, 1029, 1030, 1031, 1032, 1033, 1034, 1035, 1036, 1037, 1038, 1039, 1040, 1041, 1042, 1043, 1044, 1045, 1046, 1047, 1048, 1049], "confirm_seeds": [], "scenarios": ["balanced", "peak_unbalanced", "dynamic", "low_demand"], "best_path": "results/rl/ppo_best.zip", "best_val_fuel_per_vehicle_L": null, "smoke": false, "timesteps_budget": 200704, "config_total_timesteps": 200000, "sha256": "277f555ade8cefea0e3ef9519b209ce495907cf9823d1f0e114a9dd1132c4280", "scored_checkpoints": [], "note": "Time cut. This zip is the 200000-step model. It was not scored on VALIDATION or TEST, so rl_ppo is not in raw_runs.csv."}`.




## Sensitivity

**Stale wait metric:** many rows in `results/sensitivity.json` show `mean_waiting_s: 0.0` next to normal fuel, and mix-LHS rows omitted wait entirely. Parallel workers clobbered shared tripinfo paths; the mix task also dropped the key. Code is fixed; **do not quote waiting-time sensitivity until re-run.** Fuel columns may still be informative.

```
{
  "rows": [
    {
      "kind": "demand_mult",
      "mult": 0.5,
      "controller": "fixed",
      "seed": 2,
      "fuel_per_vehicle_L": 0.1332378094558267,
      "mean_waiting_s": 28.19148936170213
    },
    {
      "kind": "demand_mult",
      "mult": 0.5,
      "controller": "fixed",
      "seed": 1,
      "fuel_per_vehicle_L": 0.1431390767456774,
      "mean_waiting_s": 27.654753395282345
    },
    {
      "kind": "demand_mult",
      "mult": 0.75,
      "controller": "fixed",
      "seed": 1,
      "fuel_per_vehicle_L": 0.14325535981717244,
      "mean_waiting_s": 0.0
    },
    {
      "kind": "demand_mult",
      "mult": 0.5,
      "controller": "XtraFlow",
      "seed": 2,
      "fuel_per_vehicle_L": 0.11677548038184646,
      "mean_waiting_s": 17.98936170212766
    },
    {
      "kind": "demand_mult",
      "mult": 0.5,
      "controller": "XtraFlow",
      "seed": 1,
      "fuel_per_vehicle_L": 0.1309645950811718,
      "mean_waiting_s": 0.0
    },
    {
      "kind": "demand_mult",
      "mult": 0.75,
      "controller": "XtraFlow",
      "seed": 1,
      "fuel_per_vehicle_L": 0.1309645950811718,
      "mean_waiting_s": 0.0
    },
    {
      "kind": "demand_mult",
      "mult": 0.75,
      "controller": "fixed",
      "seed": 2,
      "fuel_per_vehicle_L": 0.1379938681527001,
      "mean_waiting_s": 0.0
    },
    {
      "kind": "demand_mult",
      "mult": 1.0,
      "controller": "fixed",
      "seed": 1,
      "fuel_per_vehicle_L": 0.13962373390567034,
      "mean_waiting_s": 0.0
    },
    {
      "kind": "demand_mult",
      "mult": 0.75,
      "controller": "XtraFlow",
      "seed": 2,
      "fuel_per_vehicle_L": 0.12582827008045933,
      "mean_waiting_s": 0.0
    },
    {
      "kind": "demand_mult",
      "mult": 1.0,
      "controller": "fixed",
      "seed": 2,
      "fuel_per_vehicle_L": 0.14132551193279075,
      "mean_waiting_s": 0.0
    },
    {
      "kind": "demand_mult",
      "mult": 1.0,
      "controller": "XtraFlow",
      "seed": 1,
      "fuel_per_vehicle_L": 0.12828411566549222,
      "mean_waiting_s": 0.0
    },
    {
      "kind": "demand_mult",
      "mult": 1.25,
      "controller": "fixed",
      "seed": 1,
      "fuel_per_vehicle_L": 0.14922284309989636,
      "mean_waiting_s": 0.0
    },
    {
      "kind": "demand_mult",
      "mult": 1.0,
      "controller": "XtraFlow",
      "seed": 2,
      "fuel_per_vehicle_L": 0.1308740779743712,
      "mean_waiting_s": 0.0
    },
    {
      "kind": "demand_mult",
      "mult": 1.25,
      "controller": "fixed",
      "seed": 2,
      "fuel_per_vehicle_L": 0.1460236656739335,
      "mean_waiting_s": 0.0
    },
    {
      "kind": "demand_mult",
      "mult": 1.25,
      "controller": "XtraFlow",
      "seed": 1,
      "fuel_per_vehicle_L": 0.13494780542831156,
      "mean_waiting_s": 0.0
    },
    {
      "kind": "demand_mult",
      "mult": 1.5,
      "controller": "fixed",
      "seed": 1,
      "fuel_per_vehicle_L": 0.1543060154507785,
      "mean_waiting_s": 0.0
    },
    {
      "kind": "demand_mult",
      "mult": 1.25,
      "controller": "XtraFlow",
      "seed": 2,
      "fuel_per_vehicle_L": 0.13437074071863928,
      "mean_waiting_s": 0.0
    },
    {
      "kind": "demand_mult",
      "mult": 1.5,
      "controller": "fixed",
      "seed": 2,
      "fuel_per_vehicle_L": 0.1494578002127547,
      "mean_waiting_s": 0.0
    },
    {
      "kind": "mix_lhs",
      "mix_id": 0,
      "mix": {
        "two_wheeler": 0.4781368498658292,
        "car": 0.2852831813082284,
        "auto_rickshaw": 0.05343037734179477,
        "bus": 0.0891543651239004,
        "truck": 0.09399522636024729
      },
      "controller": "fixed",
      "fuel_per_vehicle_L": 0.13219709330255897
    },
    {
      "kind": "mix_lhs",
      "mix_id": 1,
      "mix": {
        "two_wheeler": 0.43001426825990885,
        "car": 0.22058092322959474,
        "auto_rickshaw": 0.10117243746534813,
        "bus": 0.05344342889914786,
        "truck": 0.19478894214600034
      },
      "controller": "fixed",
      "fuel_per_vehicle_L": 0.1536607414343959
    },
    {
      "kind": "demand_mult",
      "mult": 1.5,
      "controller": "XtraFlow",
      "seed": 1,
      "fuel_per_vehicle_L": 0.14011297511064535,
      "mean_waiting_s": 0.0
    },
    {
      "kind": "mix_lhs",
      "mix_id": 0,
      "mix": {
        "two_wheeler": 0.4781368498658292,
        "car": 0.2852831813082284,
        "auto_rickshaw": 0.05343037734179477,
        "bus": 0.0891543651239004,
        "truck": 0.09399522636024729
      },
      "controller": "XtraFlow",
      "fuel_per_vehicle_L": 0.1222391635078359
    },
    {
      "kind": "demand_mult",
      "mult": 1.5,
      "controller": "XtraFlow",
      "seed": 2,
      "fuel_per_vehicle_L": 0.13641815618119082,
      "mean_waiting_s": 0.0
    },
    {
      "kind": "mix_lhs",
      "mix_id": 2,
      "mix": {
        "two_wheeler": 0.34523308444266276,
        "car": 0.3434712053465223,
        "auto_rickshaw": 0.13789141737919453,
        "bus": 0.04463736783379652,
        "truck": 0.1287669249978238
      },
      "controller": "fixed",
      "fuel_per_vehicle_L": 0.13657207013078718
    },
    {
      "kind": "mix_lhs",
      "mix_id": 1,
      "mix": {
        "two_wheeler": 0.43001426825990885,
        "car": 0.22058092322959474,
        "auto_rickshaw": 0.10117243746534813,
        "bus": 0.05344342889914786,
        "truck": 0.19478894214600034
      },
      "controller": "XtraFlow",
      "fuel_per_vehicle_L": 0.1453824802350091
    },
    {
      "kind": "mix_lhs",
      "mix_id": 3,
      "mix": {
        "two_wheeler": 0.34857648035722594,
        "car": 0.1953307523216949,
        "auto_rickshaw": 0.213994012927545,
        "bus": 0.023799247396646423,
        "truck": 0.21829950699688783
      },
      "controller": "fixed",
      "fuel_per_vehicle_L": 0.15950607406851708
    },
    {
      "kind": "mix_lhs",
      "mix_id": 2,
      "mix": {
        "two_wheeler": 0.34523308444266276,
        "car": 0.3434712053465223,
        "auto_rickshaw": 0.13789141737919453,
        "bus": 0.04463736783379652,
        "truck": 0.1287669249978238
      },
      "controller": "XtraFlow",
      "fuel_per_vehicle_L": 0.1234405664113021
    },
    {
      "kind": "mix_lhs",
      "mix_id": 3,
      "mix": {
        "two_wheeler": 0.34857648035722594,
        "car": 0.1953307523216949,
        "auto_rickshaw": 0.213994012927545,
        "bus": 0.023799247396646423,
        "truck": 0.21829950699688783
      },
      "controller": "XtraFlow",
      "fuel_per_vehicle_L": 0.14570883097862194
    }
  ],
  "gains": [
    {
      "demand_mult": 0.5,
      "dos_proxy": 0.5,
      "pct_fuel_reduction": 10.361507118799725
    },
    {
      "demand_mult": 0.75,
      "dos_proxy": 0.75,
      "pct_fuel_reduction": 8.695619534593405
    },
    {
      "demand_mult": 1.0,
      "dos_proxy": 1.0,
      "pct_fuel_reduction": 7.756223774000444
    },
    {
      "demand_mult": 1.25,
      "dos_proxy": 1.25,
      "pct_fuel_reduction": 8.781801598453729
    },
    {
      "demand_mult": 1.5,
      "dos_proxy": 1.5,
      "pct_fuel_reduction": 8.965085032333658
    }
  ],
  "mix_ranges_assumed": {
    "two_wheeler": [
      0.25,
      0.55
    ],
    "car": [
      0.15,
      0.4
    ],
    "auto_rickshaw": [
      0.05,
      0.2
    ],
    "bus": [
      0.02,
      0.1
    ],
    "truck": [
      0.05,
      0.25
    ]
  },
  "label": "assumed"
}
```


## Emission cross-check

```
{
  "rows": [
    {
      "scenario": "balanced",
      "emission_model": "alternate",
      "controller": "fixed",
      "seed": 1,
      "fuel_per_vehicle_L": 0.14285854636736603,
      "total_CO2_kg": 672.904091283447,
      "status": "ok"
    },
    {
      "scenario": "balanced",
      "emission_model": "primary",
      "controller": "fixed",
      "seed": 2,
      "fuel_per_vehicle_L": 0.1412961302612632,
      "total_CO2_kg": 681.2954789553905,
      "status": "ok"
    },
    {
      "scenario": "balanced",
      "emission_model": "primary",
      "controller": "fixed",
      "seed": 1,
      "fuel_per_vehicle_L": 0.13956360729597436,
      "total_CO2_kg": 657.787402040721,
      "status": "ok"
    },
    {
      "scenario": "balanced",
      "emission_model": "alternate",
      "controller": "fixed",
      "seed": 2,
      "fuel_per_vehicle_L": 0.1443690427723397,
      "total_CO2_kg": 695.6346970741059,
      "status": "ok"
    },
    {
      "scenario": "balanced",
      "emission_model": "primary",
      "controller": "XtraFlow",
      "seed": 1,
      "fuel_per_vehicle_L": 0.12817029389871035,
      "total_CO2_kg": 603.9951153626772,
      "status": "ok"
    },
    {
      "scenario": "balanced",
      "emission_model": "primary",
      "controller": "XtraFlow",
      "seed": 2,
      "fuel_per_vehicle_L": 0.13084388105021946,
      "total_CO2_kg": 630.772101553724,
      "status": "ok"
    },
    {
      "scenario": "peak_unbalanced",
      "emission_model": "primary",
      "controller": "fixed",
      "seed": 2,
      "fuel_per_vehicle_L": 0.14752776671506304,
      "total_CO2_kg": 664.1844070064259,
      "status": "ok"
    },
    {
      "scenario": "peak_unbalanced",
      "emission_model": "primary",
      "controller": "fixed",
      "seed": 1,
      "fuel_per_vehicle_L": 0.1613647366357803,
      "total_CO2_kg": 721.6444982931495,
      "status": "ok"
    },
    {
      "scenario": "balanced",
      "emission_model": "alternate",
      "controller": "XtraFlow",
      "seed": 1,
      "fuel_per_vehicle_L": 0.13128276377077155,
      "total_CO2_kg": 618.3233944547327,
      "status": "ok"
    },
    {
      "scenario": "balanced",
      "emission_model": "alternate",
      "controller": "XtraFlow",
      "seed": 2,
      "fuel_per_vehicle_L": 0.13375113550995427,
      "total_CO2_kg": 644.3715173275693,
      "status": "ok"
    },
    {
      "scenario": "peak_unbalanced",
      "emission_model": "primary",
      "controller": "XtraFlow",
      "seed": 1,
      "fuel_per_vehicle_L": 0.12584093505021268,
      "total_CO2_kg": 562.7553761596265,
      "status": "ok"
    },
    {
      "scenario": "peak_unbalanced",
      "emission_model": "primary",
      "controller": "XtraFlow",
      "seed": 2,
      "fuel_per_vehicle_L": 0.12127596294934477,
      "total_CO2_kg": 546.0608768903339,
      "status": "ok"
    },
    {
      "scenario": "peak_unbalanced",
      "emission_model": "alternate",
      "controller": "fixed",
      "seed": 2,
      "fuel_per_vehicle_L": 0.15093799841188532,
      "total_CO2_kg": 679.032090464978,
      "status": "ok"
    },
    {
      "scenario": "peak_unbalanced",
      "emission_model": "alternate",
      "controller": "fixed",
      "seed": 1,
      "fuel_per_vehicle_L": 0.16444562053425987,
      "total_CO2_kg": 734.7288158036108,
      "status": "ok"
    },
    {
      "scenario": "dynamic",
      "emission_model": "primary",
      "controller": "fixed",
      "seed": 1,
      "fuel_per_vehicle_L": 0.14487523776751607,
      "total_CO2_kg": 625.6118520572074,
      "status": "ok"
    },
    {
      "scenario": "dynamic",
      "emission_model": "primary",
      "controller": "fixed",
      "seed": 2,
      "fuel_per_vehicle_L": 0.14223147038743683,
      "total_CO2_kg": 613.3882026784562,
      "status": "ok"
    },
    {
      "scenario": "peak_unbalanced",
      "emission_model": "alternate",
      "controller": "XtraFlow",
      "seed": 1,
      "fuel_per_vehicle_L": 0.12864752812794067,
      "total_CO2_kg": 574.9482677040259,
      "status": "ok"
    },
    {
      "scenario": "peak_unbalanced",
      "emission_model": "alternate",
      "controller": "XtraFlow",
      "seed": 2,
      "fuel_per_vehicle_L": 0.12419478541147852,
      "total_CO2_kg": 558.8618647191222,
      "status": "ok"
    },
    {
      "scenario": "dynamic",
      "emission_model": "primary",
      "controller": "XtraFlow",
      "seed": 2,
      "fuel_per_vehicle_L": 0.12060960777355809,
      "total_CO2_kg": 520.3504090474487,
      "status": "ok"
    },
    {
      "scenario": "dynamic",
      "emission_model": "primary",
      "controller": "XtraFlow",
      "seed": 1,
      "fuel_per_vehicle_L": 0.12500979688259428,
      "total_CO2_kg": 539.1356642732264,
      "status": "ok"
    },
    {
      "scenario": "dynamic",
      "emission_model": "alternate",
      "controller": "fixed",
      "seed": 1,
      "fuel_per_vehicle_L": 0.1479104894900931,
      "total_CO2_kg": 638.2028785546838,
      "status": "ok"
    },
    {
      "scenario": "low_demand",
      "emission_model": "primary",
      "controller": "fixed",
      "seed": 1,
      "fuel_per_vehicle_L": 0.13770269779552455,
      "total_CO2_kg": 211.39531932633693,
      "status": "ok"
    },
    {
      "scenario": "low_demand",
      "emission_model": "primary",
      "controller": "fixed",
      "seed": 2,
      "fuel_per_vehicle_L": 0.13851882189307213,
      "total_CO2_kg": 205.19804007872017,
      "status": "ok"
    },
    {
      "scenario": "dynamic",
      "emission_model": "alternate",
      "controller": "fixed",
      "seed": 2,
      "fuel_per_vehicle_L": 0.1456268316970946,
      "total_CO2_kg": 627.5914470662732,
      "status": "ok"
    },
    {
      "scenario": "low_demand",
      "emission_model": "alternate",
      "controller": "fixed",
      "seed": 1,
      "fuel_per_vehicle_L": 0.14046954333047293,
      "total_CO2_kg": 215.51185578511095,
      "status": "ok"
    },
    {
      "scenario": "low_demand",
      "emission_model": "alternate",
      "controller": "fixed",
      "seed": 2,
      "fuel_per_vehicle_L": 0.14181316340646913,
      "total_CO2_kg": 209.98555363954992,
      "status": "ok"
    },
    {
      "scenario": "dynamic",
      "emission_model": "alternate",
      "controller": "XtraFlow",
      "seed": 2,
      "fuel_per_vehicle_L": 0.12376801951954183,
      "total_CO2_kg": 533.7455479517067,
      "status": "ok"
    },
    {
      "scenario": "dynamic",
      "emission_model": "alternate",
      "controller": "XtraFlow",
      "seed": 1,
      "fuel_per_vehicle_L": 0.1280125066885558,
      "total_CO2_kg": 551.7802950145065,
      "status": "ok"
    },
    {
      "scenario": "low_demand",
      "emission_model": "primary",
      "controller": "XtraFlow",
      "seed": 2,
      "fuel_per_vehicle_L": 0.1170654291224278,
      "total_CO2_kg": 172.90379455638896,
      "status": "ok"
    },
    {
      "scenario": "low_demand",
      "emission_model": "primary",
      "controller": "XtraFlow",
      "seed": 1,
      "fuel_per_vehicle_L": 0.12200204175410849,
      "total_CO2_kg": 186.76354854535012,
      "status": "ok"
    },
    {
      "scenario": "low_demand",
      "emission_model": "alternate",
      "controller": "XtraFlow",
      "seed": 1,
      "fuel_per_vehicle_L": 0.12478991440779068,
      "total_CO2_kg": 190.93799092112926,
      "status": "ok"
    },
    {
      "scenario": "low_demand",
      "emission_model": "alternate",
      "controller": "XtraFlow",
      "seed": 2,
      "fuel_per_vehicle_L": 0.11998366594673722,
      "total_CO2_kg": 177.14195094098878,
      "status": "ok"
    }
  ],
  "by_model": {
    "primary": {
      "mean": 13.89669546016646,
      "ci_lo": 10.805155785139204,
      "ci_hi": 17.176727707998204,
      "per_scenario": {
        "balanced": {
          "mean": 7.780467007684246,
          "ci_lo": 7.39740656146565,
          "ci_hi": 8.16352745390284,
          "n": 2
        },
        "peak_unbalanced": {
          "mean": 19.90454171960809,
          "ci_lo": 17.794483269323347,
          "ci_hi": 22.014600169892837,
          "n": 2
        },
        "dynamic": {
          "mean": 14.456992990960547,
          "ci_lo": 13.712102351680155,
          "ci_hi": 15.201883630240937,
          "n": 2
        },
        "low_demand": {
          "mean": 13.444780122412963,
          "ci_lo": 11.401850720985905,
          "ci_hi": 15.48770952384002,
          "n": 2
        }
      }
    },
    "alternate": {
      "mean": 13.745367950712748,
      "ci_lo": 10.700351210396775,
      "ci_hi": 16.99973910463684,
      "per_scenario": {
        "balanced": {
          "mean": 7.72883316046862,
          "ci_lo": 7.354698111512145,
          "ci_hi": 8.102968209425093,
          "n": 2
        },
        "peak_unbalanced": {
          "mean": 19.743483387937932,
          "ci_lo": 17.718012218122112,
          "ci_hi": 21.76895455775375,
          "n": 2
        },
        "dynamic": {
          "mean": 14.231436757199297,
          "ci_lo": 13.452719188567125,
          "ci_hi": 15.010154325831468,
          "n": 2
        },
        "low_demand": {
          "mean": 13.277718497245143,
          "ci_lo": 11.162297926600273,
          "ci_hi": 15.393139067890015,
          "n": 2
        }
      }
    }
  },
  "pct_reduction_primary": 13.89669546016646,
  "pct_reduction_alternate": 13.745367950712748,
  "verdict": "ci_overlap",
  "emission_map": {
    "primary": {
      "two_wheeler": "HBEFA3/LDV_G_EU4",
      "auto_rickshaw": "HBEFA3/PC_G_EU4",
      "car": "HBEFA3/PC_G_EU4",
      "bus": "HBEFA3/Bus",
      "truck": "HBEFA3/HDV"
    },
    "alternate": {
      "two_wheeler": "HBEFA3/LDV_G_EU3",
      "auto_rickshaw": "HBEFA3/PC_G_EU3",
      "car": "PHEMlight/PC_G_EU4",
      "bus": "HBEFA3/HDV",
      "truck": "HBEFA3/HDV_D_EU4"
    },
    "tested": {
      "HBEFA3/LDV_G_EU4": true,
      "HBEFA3/PC_G_EU4": true,
      "HBEFA3/Bus": true,
      "HBEFA3/HDV": true,
      "HBEFA3/LDV_G_EU3": true,
      "HBEFA3/PC_G_EU3": true,
      "PHEMlight/PC_G_EU4": true,
      "HBEFA3/HDV_D_EU4": true
    },
    "proxies": {
      "auto_rickshaw": "passenger-car class (no auto-rickshaw emission class in this SUMO build)",
      "two_wheeler": "LDV_G_EU4 when available, else the first accepted light/passenger class",
      "weights": "results/weights.json idle-fuel weights inherit these emission classes"
    },
    "note": "Installed SUMO 1.21: HBEFA3/PHEMlight available; HBEFA4 not present in this build. Alternate bus/truck classes stay heavy-duty or bus, not passenger car. Simulation-based estimate; assumed traffic mix."
  },
  "note": "Alternate bus and truck classes are heavy-duty or bus, not a passenger car. auto-rickshaw uses a passenger-car proxy. two-wheeler uses LDV_G_EU4 when that class loads. Idle-fuel weights inherit the primary classes. Simulation-based estimate; assumed traffic mix.",
  "label": "Simulation-based estimate; assumed traffic mix"
}
```


## Safety

```
{
  "metric": "conflicts_per_1000_veh",
  "comparisons": [
    {
      "baseline": "actuated",
      "mean_delta_conflicts_per_1000": -276.3454937876085,
      "ci_lo": -311.2154512890763,
      "ci_hi": -241.56314905513582,
      "wilcoxon_p": 1.9073486328125e-06,
      "wilcoxon_stat": 0.0,
      "n_pairs": 20,
      "verdict": "decrease"
    },
    {
      "baseline": "fixed",
      "mean_delta_conflicts_per_1000": -354.6822739366572,
      "ci_lo": -405.8298036084927,
      "ci_hi": -302.8453684961318,
      "wilcoxon_p": 1.9073486328125e-06,
      "wilcoxon_stat": 0.0,
      "n_pairs": 20,
      "verdict": "decrease"
    },
    {
      "baseline": "fixed_tuned",
      "mean_delta_conflicts_per_1000": -309.48003411508546,
      "ci_lo": -358.9087527428811,
      "ci_hi": -265.626653157827,
      "wilcoxon_p": 1.9073486328125e-06,
      "wilcoxon_stat": 0.0,
      "n_pairs": 20,
      "verdict": "decrease"
    },
    {
      "baseline": "maxpressure",
      "mean_delta_conflicts_per_1000": 10.194160796315904,
      "ci_lo": -5.257563985837055,
      "ci_hi": 25.880232323095335,
      "wilcoxon_p": 0.34881019592285156,
      "wilcoxon_stat": 79.0,
      "n_pairs": 20,
      "verdict": "no detectable change"
    },
    {
      "baseline": "ours_count",
      "mean_delta_conflicts_per_1000": 20.388518635555897,
      "ci_lo": 5.567280815793398,
      "ci_hi": 34.92507528994849,
      "wilcoxon_p": 0.01531219482421875,
      "wilcoxon_stat": 41.0,
      "n_pairs": 20,
      "verdict": "increase"
    },
    {
      "baseline": "queue_pressure",
      "mean_delta_conflicts_per_1000": 10.194160796315904,
      "ci_lo": -5.257563985837055,
      "ci_hi": 25.880232323095335,
      "wilcoxon_p": 0.34881019592285156,
      "wilcoxon_stat": 79.0,
      "n_pairs": 20,
      "verdict": "no detectable change"
    },
    {
      "baseline": "webster",
      "mean_delta_conflicts_per_1000": -404.24428103951504,
      "ci_lo": -469.9075070935509,
      "ci_hi": -344.31420989737916,
      "wilcoxon_p": 1.9073486328125e-06,
      "wilcoxon_stat": 0.0,
      "n_pairs": 20,
      "verdict": "decrease"
    }
  ],
  "verdict": "decrease",
  "verdict_baseline": "fixed",
  "note": "SSM minTTC below 1.5 s, per 1000 departed vehicles. Verdict is the sign of the bootstrap CI.",
  "label": "Simulation-based estimate; assumed traffic mix"
}
```


## Robustness

```
{
  "scenario": "peak_unbalanced",
  "seeds": [
    1,
    2,
    3
  ],
  "rows": [
    {
      "kind": "reference",
      "controller": "actuated",
      "miss": 0.0,
      "seed": 2,
      "fuel_per_vehicle_L": 0.13307671070499769,
      "mean_waiting_s": 24.200996677740864,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "reference",
      "controller": "actuated",
      "miss": 0.0,
      "seed": 1,
      "fuel_per_vehicle_L": 0.13817529548316046,
      "mean_waiting_s": 25.703579418344518,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "reference",
      "controller": "fixed_tuned",
      "miss": 0.0,
      "seed": 2,
      "fuel_per_vehicle_L": 0.13707631081281635,
      "mean_waiting_s": 28.5,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "reference",
      "controller": "actuated",
      "miss": 0.0,
      "seed": 3,
      "fuel_per_vehicle_L": 0.1365786711201391,
      "mean_waiting_s": 26.546149645002732,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "reference",
      "controller": "fixed_tuned",
      "miss": 0.0,
      "seed": 3,
      "fuel_per_vehicle_L": 0.13403971220575547,
      "mean_waiting_s": 27.620972146368103,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "reference",
      "controller": "fixed_tuned",
      "miss": 0.0,
      "seed": 1,
      "fuel_per_vehicle_L": 0.14303281470490586,
      "mean_waiting_s": 30.05145413870246,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "XtraFlow",
      "controller": "XtraFlow",
      "miss": 0.0,
      "seed": 1,
      "fuel_per_vehicle_L": 0.12584093505021268,
      "mean_waiting_s": 17.76510067114094,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "XtraFlow",
      "controller": "XtraFlow",
      "miss": 0.0,
      "seed": 2,
      "fuel_per_vehicle_L": 0.12127596294934477,
      "mean_waiting_s": 17.775193798449614,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "reference",
      "controller": "maxpressure",
      "miss": 0.0,
      "seed": 1,
      "fuel_per_vehicle_L": 0.12832810601454975,
      "mean_waiting_s": 18.430648769574944,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "reference",
      "controller": "maxpressure",
      "miss": 0.0,
      "seed": 2,
      "fuel_per_vehicle_L": 0.12411701485796048,
      "mean_waiting_s": 18.21262458471761,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "XtraFlow",
      "controller": "XtraFlow",
      "miss": 0.0,
      "seed": 3,
      "fuel_per_vehicle_L": 0.1217852352253264,
      "mean_waiting_s": 18.508465319497542,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "reference",
      "controller": "maxpressure",
      "miss": 0.0,
      "seed": 3,
      "fuel_per_vehicle_L": 0.12203860165217548,
      "mean_waiting_s": 17.747132714363737,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "XtraFlow",
      "controller": "XtraFlow",
      "miss": 0.1,
      "seed": 1,
      "fuel_per_vehicle_L": 0.12656920008520828,
      "mean_waiting_s": 18.187360178970916,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "XtraFlow",
      "controller": "XtraFlow",
      "miss": 0.2,
      "seed": 1,
      "fuel_per_vehicle_L": 0.1254381812054682,
      "mean_waiting_s": 18.05089485458613,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "XtraFlow",
      "controller": "XtraFlow",
      "miss": 0.1,
      "seed": 2,
      "fuel_per_vehicle_L": 0.1217944166410601,
      "mean_waiting_s": 18.26799557032115,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "XtraFlow",
      "controller": "XtraFlow",
      "miss": 0.2,
      "seed": 2,
      "fuel_per_vehicle_L": 0.12204059021363967,
      "mean_waiting_s": 18.341638981173865,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "XtraFlow",
      "controller": "XtraFlow",
      "miss": 0.1,
      "seed": 3,
      "fuel_per_vehicle_L": 0.1214493813878731,
      "mean_waiting_s": 18.178590933915892,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "XtraFlow",
      "controller": "XtraFlow",
      "miss": 0.2,
      "seed": 3,
      "fuel_per_vehicle_L": 0.12336124466172643,
      "mean_waiting_s": 18.653194975423265,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "XtraFlow",
      "controller": "XtraFlow",
      "miss": 0.3,
      "seed": 1,
      "fuel_per_vehicle_L": 0.1253948474371648,
      "mean_waiting_s": 18.159395973154364,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "XtraFlow",
      "controller": "XtraFlow",
      "miss": 0.3,
      "seed": 2,
      "fuel_per_vehicle_L": 0.12229606221484901,
      "mean_waiting_s": 18.08250276854928,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "empirical_noise",
      "controller": "XtraFlow",
      "miss": 0.15,
      "seed": 1,
      "fuel_per_vehicle_L": 0.12710238737294577,
      "mean_waiting_s": 19.090044742729308,
      "status": "ok",
      "n_unfinished": 0,
      "source": "assumed"
    },
    {
      "kind": "empirical_noise",
      "controller": "XtraFlow",
      "miss": 0.15,
      "seed": 3,
      "fuel_per_vehicle_L": 0.12248929888771971,
      "mean_waiting_s": 18.66411796832332,
      "status": "ok",
      "n_unfinished": 0,
      "source": "assumed"
    },
    {
      "kind": "XtraFlow",
      "controller": "XtraFlow",
      "miss": 0.3,
      "seed": 3,
      "fuel_per_vehicle_L": 0.12423576977283632,
      "mean_waiting_s": 19.60240305843801,
      "status": "ok",
      "n_unfinished": 0
    },
    {
      "kind": "empirical_noise",
      "controller": "XtraFlow",
      "miss": 0.15,
      "seed": 2,
      "fuel_per_vehicle_L": 0.12501813543235263,
      "mean_waiting_s": 19.366555924695458,
      "status": "ok",
      "n_unfinished": 0,
      "source": "assumed"
    }
  ],
  "label": "Simulation-based estimate; assumed traffic mix"
}
```


## Grid study

```
{
  "rows": [],
  "summary_means": {},
  "coordination_reduces_fuel": false,
  "n_error_rows": 10,
  "smoke": false,
  "label": "Simulation-based estimate; assumed traffic mix",
  "note": "2x2 grid not scored in this time-cut run. duarouter path was fixed, but the single-intersection tls.add.xml does not provide programs for the grid TLS ids, so SUMO aborts."
}
```


## Extrapolation

```
{
  "label": "Simulation-based estimate; assumed traffic mix",
  "scenario_weights_assumption": {
    "balanced": 0.25,
    "peak_unbalanced": 0.25,
    "dynamic": 0.25,
    "low_demand": 0.25
  },
  "baseline_per_scenario": {
    "balanced": "maxpressure",
    "peak_unbalanced": "maxpressure",
    "dynamic": "maxpressure",
    "low_demand": "maxpressure"
  },
  "measured_saving_L_per_veh": {
    "mean": 0.0012414041332105704,
    "p05": 0.0005689789336791061,
    "p95": 0.001903998664543465
  },
  "vehicles_per_peak_hour_assumption": {
    "low": 800,
    "base": 1400,
    "high": 2200
  },
  "co2_kg_per_L_from_fuel_split": 2.501871473827247,
  "annual_litres": {
    "median": 917831.6670066761,
    "p05": 251338.50959340282,
    "p95": 2654039.690850678
  },
  "annual_INR_PLACEHOLDER_price": {
    "median": 91434539.62161177,
    "p05": 24929251.680866595,
    "p95": 268704801.39816594
  },
  "annual_tonnes_CO2": {
    "median": 2296.296865459312,
    "p05": 628.8166474259905,
    "p95": 6640.066192944597
  },
  "fuel_price_note": "INR/L is a user-editable PLACEHOLDER in config.yaml"
}
```


## Limitations


- Simulation-to-reality gap; emission model not calibrated to Indian vehicles.

- Assumed vehicle mix unless observed counts provided.

- No pedestrians; simplified turning/phase plan; single-city context.

- Perception noise without user video is assumed.


## Threats to validity


- Internal: seed leakage prevented by config.lock; demand independent of controller.

- Construct: HBEFA proxies idle/fuel; SSM conflicts are model-based.

- External: Indian arterial heterogeneity not fully represented.


## Tuning landscape


Relative span of screened hyperparameter means, (max-min)/min, was 0.0138. Selection used VALIDATION seeds across the scenarios listed in tuned_params.json.


## Q&A


**How is fuel computed?** SUMO emission devices using the classes in results/emission_class_map.json; milligrams converted with the densities in config. Fuel per vehicle uses departed vehicles, including unfinished trips.


**What do the controllers see?** Default info_mode is oracle: SUMO speed, class, and route turn. Camera mode applies perception/noise_model.json. The noise source field says whether that model is empirical or assumed.


**What is the headline comparison?** XtraFlow versus the lowest-fuel controller among fixed_tuned, actuated, queue_pressure, and maxpressure, with a paired bootstrap interval. Legacy fixed is a secondary row. Numbers are in results/headlines.json.


**Did you tune on test data?** Tuning, fixed-plan search, and RL checkpoint selection use VALIDATION seeds only. TEST seeds run once after the config lock.


**What should not be claimed from this file?** Any sentence whose number is not in a results JSON or CSV loaded above. Grid, safety, and robustness verdicts are the fields in those JSON files.

