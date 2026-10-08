# Instruct.md — obsolete split plan

The two-MacBook split is **not** the active procedure.

The study was finished on one Mac as a **time-cut publish** and pushed to GitHub. Current status lives in:

- `STATE.md`
- `results/CHANGES.md`
- `results/headlines.json`
- `results/REPORT.md`
- `docs/DECISIONS.md` (D010d, D010b, D018 notes)

Published cut in short:

- TRAIN / VALIDATION work completed for demand calibration, tune, and fixed_tuned
- PPO trained to ≥200000 steps; not scored into the TEST table
- Locked TEST: seeds 1–5, eight controllers, four scenarios
- 2×2 grid not scored

Do not follow the phase commands below for a new run unless you intentionally revive a multi-machine split.

---

# Archived: Two-MacBook split (not used)

Machine A was this Mac. Machine B was the other M4. The commands that follow were drafted then superseded by the one-Mac time-cut.

Already finished before the split was cancelled:

- Networks and idle-fuel weights
- Demand calibration (`results/demand_calibration.json`, validation, not smoke)
- Controller tuning (`results/tuned_params.json`)
- Fixed-green shards under `results/shards/` merged into `results/fixed_tuned.json`

The remainder of this file is kept only as an archive of that cancelled plan.
