# Design Decisions Log

All non-sourced parameters are labelled **assumed**. Choices below record engineering trade-offs.

## D001 — Python version

**Choice:** Python 3.12 (venv).  
**Rationale:** System default is 3.14; torch/SB3/ultralytics more mature on 3.12. Homebrew `python@3.12` available.

## D001b — SUMO / dependency pins

**Choice:** `eclipse-sumo==1.21.0` (1.22.0 not on PyPI); `numpy==1.26.4` (ultralytics Darwin pin `<2`); `Pillow==10.4.0` (streamlit `<11`).  
**Rationale:** Resolved from pip conflict errors at install time; locked in `requirements.lock`.

## D002 — Lane geometry

**Choice:** 3 inbound lanes per approach: (1) left-turn/through shared, (2) through-only, (3) right-turn/through shared; 2 outbound lanes.  
**Rationale:** Matches typical Indian arterial approaches with mixed turns while keeping netconvert/TraCI tractable. Documented as assumed geometry.

## D003 — Phase plan

**Choice:** 4-phase protected plan: NS through+left → NS right → EW through+left → EW right; yellow 3 s, all-red 2 s.  
**Rationale:** Separates right-turn conflicts under left-hand traffic; simplifies foe validation vs a 2-group plan with permissive rights.

## D004 — Sublane model

**Choice:** Do not pass `--lateral-resolution` to netconvert (SUMO 1.21 has no such netconvert option). Set `<lateral-resolution value="0.4"/>` in the sumocfg. A 60 s probe must succeed or the build fails. The probe has been run: `results/sublane_fallback.json` has `used_sublane` true at 0.4. That probe is one vehicle for 60 s. A full-demand stability check has not been re-run.  
**Rationale:** A silent fallback left the report describing a lateral model the simulation never ran.

## D005 — Vehicle mix

**Choice:** two_wheeler 40%, car 30%, auto_rickshaw 10%, bus 5%, truck 15% (assumed mixed-traffic scenario).  
**Occupancy assumed:** 2W 1.3, car 2.0, auto 2.0, bus 40, truck 1.2 persons/vehicle.

## D006 — Fuel densities

**Choice:** petrol 0.74 kg/L, diesel 0.84 kg/L (assumed, typical liquid hydrocarbon densities).  
**CO2 factors for extrapolation:** petrol 2.31 kg/L, diesel 2.68 kg/L (widely cited tank-to-wheel factors; labelled simulation-based estimate when used).

## D007 — Controllers share hard constraints

**Choice:** MIN_GREEN 15 s, MAX_GREEN 90 s, yellow 3 s, all-red 2 s, starvation 120 s for adaptive controllers.  
**Rationale:** Spec hard rules; prevents unsafe short greens and unbounded phases.

## D008 — Demand horizon

**Choice:** 3600 s demand + drain until completion or 2× timeout (7200 s wall).  
**Scenarios:** balanced, peak_unbalanced (NS≈4×EW), dynamic (1200 s blocks), low_demand.

## D009 — Emission primary model

**Choice:** `config.yaml` `emission.primary` is HBEFA3, matching the classes this SUMO 1.21 build accepts. HBEFA4 is not in the wheel. The alternate map keeps bus and truck on heavy-duty or bus classes. auto-rickshaw uses a passenger-car class. two-wheeler uses `LDV_G_EU4` when that class loads. Idle-fuel weights inherit those proxies. The cross-check verdict compares confidence intervals, not only sign.  
**Rationale:** The config used to name HBEFA4 while the run used HBEFA3, and the alternate map sent buses and trucks to a passenger car.

## D010 — RL reward

**Choice:** `r = -fuel_weighted_waiting - λ_switch * switch`; λ_switch tuned lightly on validation.  
**Rationale:** Aligns RL with energy-aware objective while discouraging chatter.

## D010b — RL training budget

**Choice:** `config.yaml` `rl.total_timesteps` is the training budget (200000). Code does not cap that budget and does not stop early below it. Episodes use the demand horizon. The intended selection is VALIDATION, all four scenarios, full horizon. Report the run from `results/rl/rl_selection.json`.  
**Time-cut publish (2026-10-06):** training reached the budget; VALIDATION scoring was stopped. The final zip is kept as `results/rl/ppo_best.zip` and `rl_ppo` is omitted from the TEST sweep.  
**Rationale:** Config, this note, and the trainer have to name the same budget. A short smoke run must not be described as the config budget.

## D010c — Hyperparameter search

**Choice:** Screen tune grid on first 5 VALIDATION seeds; confirm top-3 on all 20 VALIDATION seeds.  
**Rationale:** Same seed split; reduces redundant full-grid × 20 cost while keeping selection VAL-only.

## D010d — Time-cut TEST publish

**Choice:** For the 2026-10-06 publish, the locked TEST used seeds 1–5, eight controllers excluding `rl_ppo`, and left the 2×2 grid unscored. Config still defines TEST as 1–30.  
**Rationale:** Wall-clock limit. Documents and `results/CHANGES.md` must describe the cut; do not present it as the full 30-seed protocol.

## D011 — Perception without user video

**Choice:** Synthetic smoke + assumed noise model (miss≈0.15, mild class confusion); clearly labelled assumed.  
**Rationale:** Spec allows this when `data/video/` absent.

## D012 — Fuel-price placeholder

**Choice:** INR 100/L base in config (user-editable PLACEHOLDER).  
**Rationale:** Price varies; extrapolation must not pretend a fixed market price.

## D013 — Headline fuel reduction sanity

**Choice:** If the paired mean fuel reduction versus the best tuned baseline (fixed_tuned, actuated, queue_pressure, or maxpressure) is above 30%, flag the cell for investigation. Legacy fixed is a secondary comparison and is not the sanity baseline.  
**Rationale:** The old equal-green plan is a weak baseline under unbalanced demand, so a large gap against it is not evidence by itself.

## D014 — Independent demand generation

**Choice:** Demand files keyed only by scenario+seed; regenerated identically for every controller.  
**Rationale:** Spec rule 4 — fair comparison.

## D015 — Demand flow levels

**Choice:** Approach flows stay in config.yaml and are labelled assumed until `data/observed_counts.csv` is supplied. Degree of saturation is whatever `experiments/calibrate_demand.py` writes to `results/demand_calibration.json` from a VALIDATION run. That file is not allowed to repeat an unmeasured saturation target.  
**Rationale:** A 70–90% saturation sentence was never computed, and an older note cited a flow that is not in the config.

## D016 — Pressure turn filtering

**Choice:** Phase pressure counts only vehicles whose route turn matches the phase (through+left vs right), not all vehicles on the approach.  
**Rationale:** Without this, NS_TL and NS_R pressures were nearly identical, starving EW and causing gridlock (observed on VAL seed 2000).

## D017 — Headline baseline

**Choice:** The headline is XtraFlow versus the best of fixed_tuned, actuated, queue_pressure, and maxpressure on that scenario, with a paired bootstrap interval. Legacy fixed may be shown as a secondary row. The number lives in `results/headlines.json` after the locked TEST sweep.  
**Rationale:** Calling a gap versus the legacy equal-green plan the upper end of a plausible range was not a justification. The legacy plan gives NS and EW the same green under NS-heavy demand.

## D018 — Grid

**Choice:** Each junction uses phase groups inferred from that junction's incoming edges, not the single-intersection edge names. Coordination subtracts `neighbor_pressure_weight` times downstream vehicle count (same helper as max-pressure). Quote a grid result only from `results/grid_results.json` after a run that records `n_unfinished`.  
**Time-cut publish (2026-10-06):** the grid was not scored. The single-intersection `tls.add.xml` does not provide programs for the grid TLS ids, so SUMO aborts. The JSON note records that.  
**Rationale:** The previous controller queried `N_in` on a net that has no such edge, so it saw no vehicles, and the coordination term did not read a neighbor.

## D019 — Max-pressure downstream term

**Published TEST (2026-10-06):** `maxpressure` and `queue_pressure` are **bit-identical** in `raw_runs.csv` on every metric. The controller used `getLastStepHaltingNumber` on out-edges, which are almost never queued, so the downstream penalty was ~0. **Do not treat max-pressure as an independent baseline in those tables.**

**Code fix (2026-10-07, results not re-run):** `maxpressure` pressure is

`P(phase) = Σ_upstream w·(1 if halt else α) − Σ_out occupancy·scale`

with units:

| Term | Source | Unit |
|---|---|---|
| Upstream | detector vehicles | vehicle-count (fuel-weighted if enabled) |
| Occupancy | TraCI `getLastStepOccupancy` | fraction in **[0, 1]** (clipped) |
| Scale | `downstream_occupancy_scale` (default **10**) | vehicle-equivalent per full edge |
| Fallback | `getLastStepVehicleNumber` | vehicle-count (no scale) |

Out-edges come from `phase_groups.json` / `DEFAULT_DOWNSTREAM`. Gap-out may switch to the least-negative phase when every pressure is non-positive. **Stale until a TEST re-sweep.**  
**Rationale:** Classic max-pressure needs a real downstream queue / occupancy term relative to the movements a phase serves. Occupancy must be scaled because it is dimensionless while upstream pressure is not.
