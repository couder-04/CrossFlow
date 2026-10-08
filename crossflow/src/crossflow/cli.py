"""``crossflow``: one command line for the whole product."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .bridge import cli as bridge_cli


def _cmd_run(a: argparse.Namespace) -> int:
    from .pipeline import run_pipeline

    out = run_pipeline(
        quick=not a.full,
        seeds=a.seeds,
        streams=a.streams,
        hour=a.hour,
        observed_mix=not a.assumed_mix,
        workers=a.workers,
    )
    print(f"\nReport: {out / 'report.md'}")
    return 0


def _cmd_city(a: argparse.Namespace) -> int:
    from .bridge import approach_rates
    from .city import busiest_junction, junction_records, simulate_city

    flow = simulate_city(streams=a.streams)
    cam, hr, arms = busiest_junction(flow, min_arms=a.min_arms)
    rates = approach_rates(junction_records(flow, cam, hr))
    print(
        f"camera hits in the day: {flow.traversals} ({flow.streams} demand streams, "
        f"{flow.dropped_diagonal} diagonal hits dropped)"
    )
    print(f"busiest junction-hour: {cam} at {hr:02d}:00, vehicles per arm {arms}")
    print(f"veh/h per arm: {rates}")
    return 0


def _cmd_status(_a: argparse.Namespace) -> int:
    from .signals import latest_run, published_summary

    pub = published_summary()
    print(f"XtraFlow published results: {'available' if pub['available'] else 'missing'}")
    if pub["available"]:
        print(f"  {pub['n_runs']} clean runs, config locked: {pub['config_locked']}")
        for h in pub["headlines"]:
            lo, hi = h.get("ci_lo"), h.get("ci_hi")
            print(
                f"  {h['scenario']:16s} fuel vs best baseline "
                f"{h['pct_fuel_reduction_vs_best']:+.2f}% (95% CI {lo:+.2f} to {hi:+.2f})"
            )
    run = latest_run()
    if run:
        print(f"latest pipeline run: {run['created']} - {run['comparison']['verdict']}")
    else:
        print("no pipeline run yet: `crossflow run`")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="crossflow", description=__doc__)
    p.add_argument("--version", action="version", version=f"crossflow {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="city cameras -> measured demand -> signal-control study")
    r.add_argument(
        "--full",
        action="store_true",
        help="5 seeds instead of 2 (about 3 minutes); default is a quick check",
    )
    r.add_argument("--seeds", type=int, help="number of paired seeds")
    r.add_argument(
        "--streams",
        type=int,
        default=2000,
        help="superimposed demand streams (scales the simulator's sparse trip rate)",
    )
    r.add_argument("--hour", type=int, help="restrict to one hour of day (default: busiest)")
    r.add_argument(
        "--assumed-mix",
        action="store_true",
        help="use XtraFlow's assumed vehicle mix instead of the camera-observed one",
    )
    r.add_argument("--workers", type=int, default=4)
    r.set_defaults(fn=_cmd_run)

    c = sub.add_parser("city", help="show what the offline city produces, without running SUMO")
    c.add_argument("--streams", type=int, default=2000)
    c.add_argument("--min-arms", type=int, default=4)
    c.set_defaults(fn=_cmd_city)

    b = sub.add_parser("bridge", help="convert a FlowWindow file to XtraFlow calibration input")
    bridge_cli.add_arguments(b)
    b.set_defaults(fn=bridge_cli.run)

    s = sub.add_parser("status", help="published XtraFlow results and the latest pipeline run")
    s.set_defaults(fn=_cmd_status)

    args = p.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
