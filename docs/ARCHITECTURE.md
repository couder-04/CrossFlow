# CrossFlow architecture

## Shape

```text
                          crossflow  (Python package, root environment)
        ┌──────────────────────────────────────────────────────────────────┐
cameras │ city.py ──► bridge ──► pipeline.py ──► report.json / report.md  │
(sim or │   │            │            │                  │                  │
 live)  │ CrossSight   FlowWindow   XtraFlow          signals.py (reader)  │
        │ simulator    -> veh/h,    run_one()                │             │
        │              class mix    via SUMO               │             │
        └──────────────────────────────────────────────────┼─────────────┘
                                                           ▼
        crosssight/services/api  routes/signals.py  ──►  dashboard /signals
```

| Module | Responsibility |
| --- | --- |
| `crossflow.bridge` | Pure functions: FlowWindow records to veh/h per arm and an XtraFlow vehicle mix. Stdlib only. |
| `crossflow.city` | Drives CrossSight's simulator offline and aggregates camera hits to 5-minute windows (zeros kept). |
| `crossflow.pipeline` | Picks the busiest four-arm junction-hour, scales XtraFlow demand per arm, runs controllers on paired seeds in parallel SUMO processes, computes the paired comparison. |
| `crossflow.signals` | Reads XtraFlow results and pipeline reports. Stdlib only, so the slim API image can import it. |
| `crossflow.cli` | `run`, `city`, `bridge`, `status`. |
| `crosssight/.../routes/signals.py` | `/signals/summary`, `/signals/runs/latest`, `/signals/demand`. |
| `crosssight/apps/dashboard/.../signals` | The Signals page. |

## One environment

The root `pyproject.toml` is the union of CrossSight's and XtraFlow's dependencies and resolves on
Python 3.12 (`uv.lock`, 177 packages). The one conflict, `pandas`, is pinned to XtraFlow's 2.2.3.
`uv sync` creates `.venv`; the CrossSight, XtraFlow and CrossFlow suites all run in it.

CrossSight's Docker images keep building from `crosssight/uv.lock` on Python 3.11. That lock is
untouched; `crosssight/pyproject.toml` only gains `../crossflow/src` on the pytest path.

## Contracts

| Contract | Owner | Consumer | Enforced by |
| --- | --- | --- | --- |
| `FlowWindow` (`camera_id`, `window_start`, `window_end`, `counts_by_class`, `volume`, `lane`) | `crosssight/packages/anpr_common` | `crossflow.bridge`, `crossflow.city` | `crossflow/tests/test_bridge.py` validates samples against the real model |
| `observed_counts.csv` (`approach,count_veh_h`) | `xtraflow/experiments/calibrate_demand.py` | `crossflow.bridge` | `test_bridge.py`, `scripts/bridge_demo.sh` (runs the real calibration) |
| `demand_scale` per arm in `run_one` | `xtraflow/sim` | `crossflow.pipeline` | `crossflow run` in CI |
| `vehicle_mix` keys | `xtraflow/config.yaml` | `crossflow.bridge` | `test_bridge.py` |
| `/signals/*` JSON | API | dashboard `types/Signals.ts` | `services/api/tests/test_signals_routes.py`, `tsc` |

## Rules the code follows

* **Arm = side traffic arrives from**, the opposite of its travel direction. Diagonal directions fit
  no arm and are counted as dropped, not guessed.
* **Idle windows are data.** `flow_5min` has no row for an empty window, so live demand divides by
  the requested period (`period_hours`), and the offline city emits explicit zero windows.
  Otherwise quiet arms would be over-reported.
* **No silent success.** A run whose SUMO status is not `ok` is dropped for every controller (paired
  design) and listed in the report. With no clean runs the verdict is "no comparison".
* **Gains are measured against the best baseline**, with a Student-t 95% interval over paired
  seeds. An interval spanning zero is reported as "no established difference".
* **Frozen results stay frozen.** `xtraflow/config.yaml`, tuned parameters and `results/` are read,
  never written, by the pipeline. Runs carry a `run_id` and write to `runs/` (git-ignored).
* **Live data never enters SUMO per request.** XtraFlow is an offline, seeded study; the API reads its
  outputs and measures demand, which an operator can then run through `crossflow`.

## Docker

The compose `api` service mounts `crossflow/src`, `xtraflow/` and `runs/` read-only and sets
`PYTHONPATH`, `CROSSFLOW_XTRAFLOW_DIR`, `CROSSFLOW_RUNS_DIR`. Without them the API still starts and
the endpoints answer `available: false`. The compose project name is pinned (`name: crosssight`) so
container names do not depend on the checkout directory.
