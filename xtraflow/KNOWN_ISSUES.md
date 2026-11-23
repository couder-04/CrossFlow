# Known issues

Open problems with the space-aware work (PR #1) and its results. See
[`results/SPACE_AWARE.md`](results/SPACE_AWARE.md) for the benchmark itself.

* **`XtraFlow_space_tuned` cannot run.** `make_controller` in `sim/controllers.py` loads
  `results/space_tuned_params.json` for this controller, and that file is not in the repository.
* **`maxpressure` differs between the two raw-run files.** In `results/space_aware_raw_runs.csv`, all
  20 `maxpressure` runs differ in `total_fuel_L` from the same scenario and seed in
  `results/raw_runs.csv` (mean -0.53 L, max absolute difference 7.0 L). `fixed_tuned`, `actuated`
  and `queue_pressure` match exactly. The cause has not been determined.
* **No unit tests for the space-aware functions yet.** `space_footprints`, `space_aware_weights`,
  `OursSpaceFuelController` and `OursFootprintController` are not covered in `tests/`.
