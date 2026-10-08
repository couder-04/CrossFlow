"""``crossflow bridge``: convert a flow file into XtraFlow calibration inputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .flows import (
    FlowError,
    approach_rates,
    load_camera_map,
    load_flow_windows,
    vehicle_mix,
    write_observed_counts,
)


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--flows", required=True, help="FlowWindow JSONL/JSON (analytics.flow.v1)")
    p.add_argument(
        "--camera-map",
        help='JSON {"cam-001": "N", ...}; optional when records carry an "approach" field',
    )
    p.add_argument(
        "--out",
        default="xtraflow/data/observed_counts.csv",
        help="observed_counts.csv for XtraFlow calibrate_demand",
    )
    p.add_argument("--mix-out", help="optional JSON path for the observed vehicle mix")
    p.add_argument(
        "--period-hours",
        type=float,
        help="length of the observed period; use when idle windows are absent from the data",
    )


def run(args: argparse.Namespace) -> int:
    try:
        cmap = load_camera_map(args.camera_map) if args.camera_map else None
        records = load_flow_windows(args.flows)
        rates = approach_rates(records, cmap, period_hours=args.period_hours)
        path = write_observed_counts(rates, args.out)
        print(f"wrote {path}: " + ", ".join(f"{a}={v:g} veh/h" for a, v in rates.items()))
        if args.mix_out:
            mix = vehicle_mix(records, cmap)
            Path(args.mix_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.mix_out).write_text(
                json.dumps({"vehicle_mix": mix}, indent=2) + "\n", encoding="utf-8"
            )
            print(f"wrote {args.mix_out}: {mix}")
    except (FlowError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="crossflow bridge",
        description="Convert CrossSight flow windows into XtraFlow calibration inputs.",
    )
    add_arguments(p)
    return run(p.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
