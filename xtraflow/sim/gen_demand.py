"""Seeded demand generation independent of controller (same seed => identical demand)."""
from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any, Dict, List, Tuple

from sim.util import ROOT, SCENARIOS, ensure_dirs, load_config, save_json

APPROACHES = ["N", "S", "E", "W"]
DEST_FROM_TURN = {
    # approach -> turn -> dest approach (LHT)
    "N": {"left": "E", "through": "S", "right": "W"},
    "S": {"left": "W", "through": "N", "right": "E"},
    "E": {"left": "S", "through": "W", "right": "N"},
    "W": {"left": "N", "through": "E", "right": "S"},
}


def _flows_for_scenario(cfg: Dict[str, Any], scenario: str) -> List[Tuple[float, float, Dict[str, float]]]:
    """Return list of (t0, t1, {approach: veh_per_hour})."""
    dem = cfg["demand"][scenario]
    horizon = float(cfg["simulation"]["demand_horizon_s"])
    if scenario == "dynamic":
        blocks = dem["blocks"]
        t = 0.0
        out = []
        for b in blocks:
            dur = float(b["duration"])
            rates = {a: float(b[a]) for a in APPROACHES}
            out.append((t, t + dur, rates))
            t += dur
        return out
    rates = {a: float(dem[a]) for a in APPROACHES}
    return [(0.0, horizon, rates)]


def _sample_vtype(rng: random.Random, mix: Dict[str, float]) -> str:
    keys = list(mix.keys())
    weights = [mix[k] for k in keys]
    return rng.choices(keys, weights=weights, k=1)[0]


def _sample_turn(rng: random.Random, ratios: Dict[str, float]) -> str:
    keys = ["left", "through", "right"]
    weights = [ratios[k] for k in keys]
    return rng.choices(keys, weights=weights, k=1)[0]


def generate(scenario: str, seed: int, cfg: Dict[str, Any] | None = None,
             demand_mult: float = 1.0, mix_override: Dict[str, float] | None = None,
             demand_scale: Dict[str, float] | None = None) -> Path:
    """Write routes file for scenario+seed. Controller-independent."""
    cfg = cfg or load_config()
    ensure_dirs()
    if scenario not in SCENARIOS and scenario != "grid_balanced":
        raise ValueError(f"Unknown scenario {scenario}")

    out_dir = ROOT / "results" / "demand"
    out_path = out_dir / f"{scenario}_seed{seed}.rou.xml"
    # Include mult/mix hash in filename if non-default
    if demand_mult != 1.0 or mix_override is not None or demand_scale:
        tag = f"_m{demand_mult:.2f}"
        if demand_scale:
            tag += "_s" + "_".join(f"{k}{demand_scale[k]:.2f}" for k in sorted(demand_scale))
        out_path = out_dir / f"{scenario}_seed{seed}{tag}.rou.xml"

    rng = random.Random(int(seed))  # deterministic
    mix = dict(mix_override or cfg["vehicle_mix"])
    # renormalize
    s = sum(mix.values())
    mix = {k: v / s for k, v in mix.items()}
    ratios = cfg["turn_ratios"]
    horizon = float(cfg["simulation"]["demand_horizon_s"])

    vehicles: List[str] = []
    vid = 0
    blocks = _flows_for_scenario(cfg, scenario if scenario != "grid_balanced" else "balanced")

    for t0, t1, rates in blocks:
        for approach in APPROACHES:
            scale = 1.0 if not demand_scale else float(demand_scale.get(approach, 1.0))
            rate = rates[approach] * demand_mult * scale  # veh/h
            # Poisson-like via exponential interarrivals
            t = t0
            mean_headway = 3600.0 / max(rate, 1e-6)
            while t < t1:
                headway = rng.expovariate(1.0 / mean_headway)
                t += headway
                if t >= t1 or t >= horizon:
                    break
                vtype = _sample_vtype(rng, mix)
                turn = _sample_turn(rng, ratios)
                dest = DEST_FROM_TURN[approach][turn]
                # lane preference by turn (0 leftish, 1 through, 2 rightish) — SUMO may remap
                depart_lane = {"left": "0", "through": "1", "right": "2"}[turn]
                edge_from = f"{approach}_in"
                edge_to = f"{dest}_out"
                vehicles.append(
                    f'  <vehicle id="v{vid}" type="{vtype}" depart="{t:.2f}" '
                    f'departLane="{depart_lane}" departSpeed="max">\n'
                    f'    <route edges="{edge_from} {edge_to}"/>\n'
                    f'  </vehicle>'
                )
                vid += 1

    # Sort by depart
    vehicles_sorted = sorted(vehicles, key=lambda line: float(line.split('depart="')[1].split('"')[0]))
    xml = ['<routes>'] + vehicles_sorted + ['</routes>']
    out_path.write_text("\n".join(xml) + "\n", encoding="utf-8")

    meta = {
        "scenario": scenario,
        "seed": seed,
        "n_vehicles": vid,
        "demand_mult": demand_mult,
        "mix": mix,
        "path": str(out_path.relative_to(ROOT)),
        "label": "assumed mixed-traffic scenario",
    }
    save_json(out_path.with_suffix(".meta.json"), meta)
    return out_path


def generate_grid(scenario: str, seed: int, cfg: Dict[str, Any] | None = None) -> Path:
    """Simple OD demand for 2x2 grid using external stubs."""
    cfg = cfg or load_config()
    ensure_dirs()
    out_path = ROOT / "results" / "demand" / f"grid_{scenario}_seed{seed}.rou.xml"
    rng = random.Random(int(seed) + 99991)
    mix = cfg["vehicle_mix"]
    s = sum(mix.values())
    mix = {k: v / s for k, v in mix.items()}
    horizon = float(cfg["simulation"]["demand_horizon_s"])
    # Entries: N0,N1,S0,S1,E0,E1,W0,W1 — route to opposite side
    entries = {
        "N0_in": "S0_out",
        "N1_in": "S1_out",
        "S0_in": "N0_out",
        "S1_in": "N1_out",
        "E0_in": "W0_out",
        "E1_in": "W1_out",
        "W0_in": "E0_out",
        "W1_in": "E1_out",
    }
    rate = 400.0  # veh/h per entry assumed
    vehicles = []
    vid = 0
    for edge_from, edge_to in entries.items():
        mean_headway = 3600.0 / rate
        t = 0.0
        while t < horizon:
            t += rng.expovariate(1.0 / mean_headway)
            if t >= horizon:
                break
            vtype = _sample_vtype(rng, mix)
            vehicles.append(
                f'  <vehicle id="g{vid}" type="{vtype}" depart="{t:.2f}" departSpeed="max">\n'
                f'    <route edges="{edge_from} {edge_to}"/>\n'
                f'  </vehicle>'
            )
            vid += 1
    vehicles = sorted(vehicles, key=lambda line: float(line.split('depart="')[1].split('"')[0]))
    # Note: single-edge pairs may need intermediate edges; use trips + duarouter if needed.
    # Write trips and run duarouter for connectivity.
    trip_path = out_path.with_suffix(".trips.xml")
    trips = ['<routes>']
    # rewrite as trips
    rng2 = random.Random(int(seed) + 99991)
    vid = 0
    for edge_from, edge_to in entries.items():
        mean_headway = 3600.0 / rate
        t = 0.0
        while t < horizon:
            t += rng2.expovariate(1.0 / mean_headway)
            if t >= horizon:
                break
            vtype = _sample_vtype(rng2, mix)
            trips.append(
                f'  <trip id="g{vid}" type="{vtype}" depart="{t:.2f}" '
                f'from="{edge_from}" to="{edge_to}"/>'
            )
            vid += 1
    trips.append("</routes>")
    trip_path.write_text("\n".join(trips) + "\n", encoding="utf-8")

    from sim.util import run_cmd, tool_cmd
    net = ROOT / "results" / "networks" / "grid2x2.net.xml"
    vtypes = ROOT / "results" / "networks" / "vtypes.add.xml"
    cmd = [
        *tool_cmd("duarouter"),
        "-n", str(net),
        "-r", str(trip_path),
        "-o", str(out_path),
        "--ignore-errors", "true",
        "--additional-files", str(vtypes),
    ]
    run_cmd(cmd, check=False)
    if not out_path.exists():
        # fallback write empty
        out_path.write_text("<routes/>\n", encoding="utf-8")
    return out_path


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--scenario", default="balanced")
    p.add_argument("--seed", type=int, default=1)
    args = p.parse_args()
    path = generate(args.scenario, args.seed)
    print(path)
