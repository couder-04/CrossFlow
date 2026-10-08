# Verification record

Run on macOS (Apple Silicon), 2026-10-08, from the tree as committed. Everything below ran in the
**single root environment** (Python 3.12, `uv sync` from the root `pyproject.toml` and `uv.lock`)
unless marked otherwise.

## Results

| Check | Command | Result |
| --- | --- | --- |
| Environment resolves and installs | `uv sync --python 3.12` | 177 packages; `numpy 1.26.4`, `pandas 2.2.3`, `torch 2.5.1` as XtraFlow pins |
| CrossFlow tests (bridge, city, pipeline statistics, results reader, CLI) | `make test-crossflow` | 37 passed |
| XtraFlow tests (60 s SUMO smoke runs for 6 controllers) | `make test-xtraflow` | 41 passed |
| CrossSight tests, including `/signals` API tests | `make test-crosssight` | 225 passed, 8 skipped (the 8 are the live-stack integration tests) |
| Camera counts to SUMO calibration | `make bridge-demo` | `scaled_to_observed_counts` mode; GEH N 1.46, S 2.42, E 1.85, W 1.14 (target < 5) |
| End-to-end pipeline, quick | `crossflow run` | 8/8 SUMO runs clean, about 1.5 min |
| End-to-end pipeline, full | `crossflow run --full` | 20/20 SUMO runs clean, about 3 min; GEH 0.16 to 0.99 per arm; see [`SAMPLE_RUN.md`](SAMPLE_RUN.md) |
| Lint, format, types | `make lint` | ruff and ruff format clean on `crossflow` and `crosssight`; mypy clean (137 files) |
| Dashboard | `pnpm install --frozen-lockfile && typecheck && build` | Passed; `/signals` route builds |
| API in Docker with the mounts | `docker compose --profile app up api dashboard` | `/signals/summary` and `/signals/runs/latest` served the real results from inside the container |
| Live demand from ClickHouse | Inserted 12 five-minute windows for two cameras, queried `/signals/demand` | N 480, E 120, S 0 veh/h; the camera with no rows reported as `silent_cameras`, not dropped |
| Dashboard page | Signed in as `analyst` in the Docker dashboard, opened `/signals` | Headline table, latest-run table and measured demand rendered |

Not re-run after adding `/signals`: the live-stack integration suite (`RUN_INTEGRATION=1`), which
passed 8/8 before the unification. The change adds a read-only router and does not touch ingest,
workers or storage.

## What the pipeline result means

`crossflow run --full` on a synthetic city, 5 paired seeds:

* XtraFlow used **11.8%** less fuel per vehicle than the tuned fixed plan (95% CI 9.8 to 13.7) and
  **9.3%** less than actuated (8.2 to 10.3).
* Against the best baseline, queue pressure, the difference was **0.75%** (95% CI -0.44 to 1.95):
  **not established**. This matches XtraFlow's own published headlines (+0.3% to +1.9% against its
  best baseline, intervals spanning zero in two of four scenarios).

So the honest summary of the product's effect is that fuel-weighted pressure control beats
fixed and actuated control and ties a plain queue-pressure controller on this demand.

## Defects found and fixed

1. **Clone detection raced with ingest (CrossSight, real bug).** Ingest and the alerts worker both
   wrote Redis `lastseen:{plate}`. Ingest is faster, so by the time the clone rule evaluated the
   second sighting the key already held that same sighting and the rule returned no alert. The
   alerts worker now owns `alerts:lastseen:{plate}`. Regression test:
   `crosssight/services/workers/tests/test_lastseen_isolation.py`.
2. **Seven pre-existing mypy errors** in CrossSight; fixed. One more environment-dependent error
   (`lap` fallback assigned `None`) surfaced under the unified environment and is now annotated.
3. **ClickHouse TTL integration test** matched only `INTERVAL n DAY`; ClickHouse 24.8 reports
   `toIntervalDay(n)`. The test accepts both.
4. **Hard-coded names depended on the checkout directory:** `sih-redpanda-1` in the Makefile and two scripts, and
   an absolute path in `datasets/plates_synth/data.yaml`. The compose project name is pinned
   (`name: crosssight`).
5. **Portability:** `MINIO_PORT` override and `platform: linux/amd64` on the PostGIS service.
6. **Executable bit** on every file in the source archives (143 ruff `EXE002` errors); cleared except
   for scripts with a shebang.
7. **Bridge design flaws fixed while merging.** `flow_5min` has no row for an empty window, so a
   naive rate over present windows over-reports quiet arms; the API path now divides by the queried
   period and the city path emits explicit zero windows. Shortened SUMO horizons end as `timeout`
   and bias results, so the pipeline always uses XtraFlow's full horizon.

## Known issues (inherited, not changed)

* **`xtraflow` audit fails.** `python -m tools.audit` rejects percentages in `xtraflow/README.md`.
  It is not part of `make test` or CI.
* **`maxpressure` equals `queue_pressure` in the published XtraFlow results; TEST needs a re-run**
  after the occupancy fix, and sensitivity wait times are stale (`xtraflow/STATE.md`). The published
  numbers were not regenerated.
* **CrossSight OCR claims** (91.7% multi-region, 73.0% India crops) were not re-measured; they need
  datasets and weights that are not in this repository.
* **Not exercised:** GPU OCR profile, RTSP/fleet cameras, `make benchmark-ocr`, `make city-load`,
  the XtraFlow YOLO demo (input clips are not redistributed), PPO scoring.
* **Demo city scale.** The offline city needs `--streams` (default 2000) to reach junction-scale
  flow; this is disclosed in every report.

## What is not in the repository

Regenerable or machine-local, so excluded: virtual environments, `node_modules`, `.next`,
`.pnpm-store`, caches, `.env`, CrossSight `data/` (about 2.9 GB of benchmark datasets and logs) and
its training `runs/`, XtraFlow `results/demand`, `results/raw`, `results/timeseries`,
`results/networks` (rebuilt by `make setup`), intermediate PPO checkpoints, and the input video
clips. Included: the final PPO model, all published results, figures, decks, demo videos and reports.
