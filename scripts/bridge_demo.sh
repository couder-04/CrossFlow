#!/usr/bin/env bash
# End-to-end proof of the CrossSight -> XtraFlow hand-off, on a scratch copy so the
# published XtraFlow results/ are never overwritten.
#
#   1. crossflow.bridge turns sample CrossSight flow windows into observed_counts.csv
#   2. XtraFlow calibrate_demand scales SUMO demand to those counts and reports GEH
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${CROSSFLOW_PY:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || { echo "environment missing: run 'make setup'" >&2; exit 1; }

SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

rsync -a --exclude=.venv --exclude=results/demo --exclude=results/rl "$ROOT/xtraflow/" "$SCRATCH/xtraflow/"
export SUMO_HOME="$("$PY" -c 'import sumo,os; print(os.path.dirname(sumo.__file__))')"

cd "$SCRATCH/xtraflow"
[ -f results/networks/intersection.net.xml ] || "$PY" -m sim.build_network

"$PY" -m crossflow.bridge \
  --flows "$ROOT/crossflow/examples/flows.sample.jsonl" \
  --camera-map "$ROOT/crossflow/examples/camera_map.json" \
  --out data/observed_counts.csv \
  --mix-out results/observed_mix.json

"$PY" -m experiments.calibrate_demand --smoke

"$PY" - <<'EOF'
import json
d = json.load(open("results/demand_calibration.json"))
assert d["mode"] == "scaled_to_observed_counts", d["mode"]
print("mode      :", d["mode"])
print("scales    :", {k: round(v, 3) for k, v in d["scales"].items()})
print("GEH       :", {k: round(v, 2) for k, v in d["geh"].items()}, "(target < %s)" % d["geh_target"])
assert all(g < d["geh_target"] for g in d["geh"].values()), "GEH above target"
print("bridge demo OK")
EOF
