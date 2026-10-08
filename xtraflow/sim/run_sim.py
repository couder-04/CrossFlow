"""Run a single SUMO simulation with a given controller and collect metrics."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from sim.controllers import PerceptionNoise, make_controller
from sim.gen_demand import generate
from sim.metrics import conflicts_per_1000, fuel_per_departed, parse_ssm_conflicts, parse_tripinfo
from sim.util import ROOT, ensure_dirs, load_config, load_json, locate_sumo, mg_to_litres, save_json, tool_cmd


def _free_port() -> int:
    """A free localhost port so parallel SUMO processes do not share 8813."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_sumocfg(net: Path, routes: Path, additional: List[Path], out_cfg: Path,
                   tripinfo: Path, ssm: Optional[Path] = None, emission_prob: float = 1.0,
                   begin: int = 0, end: Optional[int] = None,
                   lateral_resolution: Optional[float] = None,
                   tls_program: Optional[str] = None) -> None:
    adds = ",".join(str(p) for p in additional)
    end_line = f'    <end value="{end}"/>\n' if end else ""
    lat_line = ""
    if lateral_resolution and lateral_resolution > 0:
        lat_line = f'    <lateral-resolution value="{lateral_resolution}"/>\n'
    ssm_lines = ""
    if ssm is not None:
        ssm_lines = (
            f'    <device.ssm.probability value="1"/>\n'
            f'    <device.ssm.file value="{ssm}"/>\n'
            f'    <device.ssm.measures value="TTC"/>\n'
            f'    <device.ssm.thresholds value="1.5"/>\n'
        )
    xml = f"""<configuration>
  <input>
    <net-file value="{net}"/>
    <route-files value="{routes}"/>
    <additional-files value="{adds}"/>
  </input>
  <output>
    <tripinfo-output value="{tripinfo}"/>
    <tripinfo-output.write-unfinished value="true"/>
  </output>
  <time>
    <begin value="{begin}"/>
{end_line}  </time>
  <processing>
    <time-to-teleport value="-1"/>
    <device.emissions.probability value="{emission_prob}"/>
{lat_line}{ssm_lines}  </processing>
  <report>
    <no-warnings value="true"/>
    <no-step-log value="true"/>
  </report>
</configuration>
"""
    out_cfg.write_text(xml, encoding="utf-8")


RAW_FIELDS = [
    "scenario", "controller", "seed", "emission_model", "n_departed", "n_completed",
    "n_unfinished", "status", "error",
    "total_fuel_L", "fuel_per_vehicle_L", "fuel_per_person_L", "total_CO2_kg",
    "mean_waiting_s", "p95_waiting_s", "mean_timeLoss_s", "person_delay_s", "mean_travel_time_s",
    "stops_per_vehicle", "idle_vehicle_seconds", "mean_queue_veh", "max_queue_veh",
    "fuel_L_by_type", "ssm_conflicts", "conflicts_per_1000_veh", "gridlock_flag", "wall_time_s",
]


def _sublane_resolution(cfg: dict) -> Optional[float]:
    path = ROOT / "results" / "sublane_fallback.json"
    if not path.exists() or not cfg["simulation"].get("use_sublane"):
        return None
    sub = load_json(path)
    if not sub.get("used_sublane"):
        return None
    return float(cfg["simulation"].get("lateral_resolution", 0.4))


def run_one(
    scenario: str,
    controller: str,
    seed: int,
    cfg: Optional[dict] = None,
    smoke: bool = False,
    emission_model: str = "primary",
    noise: Optional[PerceptionNoise] = None,
    demand_mult: float = 1.0,
    mix_override: Optional[Dict[str, float]] = None,
    save_timeseries: bool = False,
    model=None,
    net_path: Optional[Path] = None,
    routes_path: Optional[Path] = None,
    tls_ids: Optional[List[str]] = None,
    params: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
    horizon_s: Optional[int] = None,
    junction_specs: Optional[List[Dict[str, Any]]] = None,
    demand_scale: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    cfg = cfg or load_config()
    ensure_dirs()
    locate_sumo()

    if horizon_s is not None:
        horizon = int(horizon_s)
        timeout = horizon + 30
    else:
        horizon = 120 if smoke else int(cfg["simulation"]["demand_horizon_s"])
        timeout = 240 if smoke else int(horizon * cfg["simulation"]["drain_timeout_multiplier"])

    net = net_path or (ROOT / "results" / "networks" / "intersection.net.xml")
    vtypes = ROOT / "results" / "networks" / "vtypes.add.xml"
    tls_add = ROOT / "results" / "networks" / "tls.add.xml"

    if routes_path is None:
        routes = generate(
            scenario, seed, cfg, demand_mult=demand_mult,
            mix_override=mix_override, demand_scale=demand_scale,
        )
    else:
        routes = routes_path

    tag = f"{scenario}_{controller}_seed{seed}_{emission_model}"
    if demand_mult is not None and abs(float(demand_mult) - 1.0) > 1e-12:
        tag = f"{tag}_dm{float(demand_mult):g}"
    if mix_override is not None:
        # Stable short fingerprint so parallel mix samples do not clobber tripinfo.
        # Do not use builtin hash(): PYTHONHASHSEED makes it process-unstable.
        mix_key = ",".join(f"{k}={float(mix_override[k]):.4f}" for k in sorted(mix_override))
        digest = hashlib.md5(mix_key.encode("utf-8")).hexdigest()[:8]
        tag = f"{tag}_mix{digest}"
    if run_id:
        tag = f"{tag}_{run_id}"
    if smoke:
        tag = "smoke_" + tag
    raw_dir = ROOT / "results" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    tripinfo = raw_dir / f"{tag}.tripinfo.xml"
    ssm_out = raw_dir / f"{tag}.ssm.xml"
    cfg_path = raw_dir / f"{tag}.sumocfg"

    add_files = [vtypes, tls_add]
    if emission_model == "alternate":
        alt_vtypes = raw_dir / f"{tag}_vtypes_alt.add.xml"
        emap = load_json(ROOT / "results" / "emission_class_map.json")
        alt = emap.get("alternate", {})
        text = vtypes.read_text(encoding="utf-8")
        primary = emap.get("primary", {})
        for vt, clas in primary.items():
            if vt in alt and clas != alt[vt]:
                text = text.replace(clas, alt[vt])
        alt_vtypes.write_text(text, encoding="utf-8")
        add_files = [alt_vtypes, tls_add]

    _write_sumocfg(
        net, routes, add_files, cfg_path, tripinfo, ssm=ssm_out,
        emission_prob=1.0, end=timeout if smoke or horizon_s else None,
        lateral_resolution=_sublane_resolution(cfg),
    )

    import traci

    sumo_cmd = tool_cmd("sumo") + ["-c", str(cfg_path), "--duration-log.disable", "true",
                                   "--tripinfo-output.write-unfinished", "true"]

    t0 = time.time()
    label = f"{tag}_{os.getpid()}"
    # Omit the port so a bind collision retries on a new port. A fixed port disables that.
    traci.start(sumo_cmd, label=label, numRetries=10)
    try:
        traci.switch(label)
    except Exception:
        pass

    specs = junction_specs
    if specs:
        ctrls = []
        for spec in specs:
            cname = spec.get("controller", controller)
            ctrl = make_controller(
                cname, cfg=cfg, scenario=scenario, noise=noise, model=model, params=params,
                tls_id=spec["tls_id"],
                approach_edges=spec.get("approach_edges"),
                downstream_edges=spec.get("downstream_edges"),
                turn_lookup=spec.get("turn_lookup"),
                groups=spec.get("groups"),
                n_links=int(spec.get("n_links") or 0),
                neighbor_ids=spec.get("neighbor_ids"),
            )
            ctrls.append(ctrl)
    else:
        ctrls = [make_controller(
            controller, cfg=cfg, scenario=scenario, noise=noise, model=model, params=params,
        )]

    noise_key = f"{scenario}-{controller}-{seed}-noise"
    for ctrl in ctrls:
        ctrl.set_noise_rng(random.Random(noise_key))

    program_ids = tls_ids or [c.tls_id for c in ctrls]
    for tid in program_ids:
        try:
            if controller == "actuated":
                traci.trafficlight.setProgram(tid, "actuated")
            else:
                traci.trafficlight.setProgram(tid, "fixed")
        except Exception:
            pass

    idle_vehicle_seconds = 0.0
    queue_samples: List[float] = []
    max_queue = 0.0
    departed_ids: set = set()
    ts_rows = []
    green_alloc = {p: 0 for p in ["NS_TL", "NS_R", "EW_TL", "EW_R"]}
    gridlock_flag = 0
    stuck_steps = 0
    fuel_mg: Dict[str, float] = {}
    fuel_vtype: Dict[str, str] = {}
    seen_approach: Dict[str, set] = {a: set() for a in ["N", "S", "E", "W"]}

    step = 0
    while step < timeout:
        for ctrl in ctrls:
            ctrl.step(traci, float(step))
        traci.simulationStep()
        step += 1

        try:
            veh_ids = list(traci.vehicle.getIDList())
            departed_ids.update(traci.simulation.getDepartedIDList())
        except Exception:
            veh_ids = []

        halted = 0
        for vid in veh_ids:
            try:
                if traci.vehicle.getSpeed(vid) < 0.1:
                    idle_vehicle_seconds += 1.0
                    halted += 1
                fuel_mg[vid] = fuel_mg.get(vid, 0.0) + float(traci.vehicle.getFuelConsumption(vid))
                if vid not in fuel_vtype:
                    fuel_vtype[vid] = traci.vehicle.getTypeID(vid)
            except Exception:
                pass
            # Demand only: a vehicle counts if it first shows up on an approach during a step
            # that starts before the demand horizon. Vehicles first seen while draining are not
            # demand, and how many there are depends on the controller.
            if step - 1 < horizon:
                try:
                    edge = traci.vehicle.getRoadID(vid)
                    for ap, edges in ctrls[0].approach_edges.items():
                        if edge in edges:
                            seen_approach[ap].add(vid)
                except Exception:
                    pass
        queue_samples.append(float(halted))
        max_queue = max(max_queue, float(halted))

        lead = ctrls[0]
        if not lead.state.in_yellow and not lead.state.in_all_red:
            green_alloc[lead.current_phase_name()] = green_alloc.get(lead.current_phase_name(), 0) + 1

        if save_timeseries and step % 5 == 0:
            ts_rows.append({
                "t": step,
                "queue": halted,
                "phase": lead.current_phase_name(),
                "green_alloc": dict(green_alloc),
            })

        if step >= horizon:
            try:
                if traci.simulation.getMinExpectedNumber() == 0:
                    break
            except Exception:
                break
            if halted > 50:
                stuck_steps += 1
            else:
                stuck_steps = 0
            if stuck_steps > 600:
                gridlock_flag = 1
                break

    try:
        traci.close()
    except Exception:
        pass
    wall = time.time() - t0

    metrics = parse_tripinfo(tripinfo, cfg)
    ssm_conflicts = parse_ssm_conflicts(ssm_out)
    n_departed = len(departed_ids)
    n_unfinished = int(metrics["n_unfinished"])
    if n_departed > int(metrics["n_accounted"]):
        n_unfinished = max(n_unfinished, n_departed - int(metrics["n_completed"]))

    densities = cfg["fuel"]["density_kg_per_L"]
    fuel_type = cfg["fuel"]["type"]
    traci_fuel_L = 0.0
    for vid, mg in fuel_mg.items():
        vtype = fuel_vtype.get(vid, "car")
        ftype = fuel_type.get(vtype, "petrol")
        traci_fuel_L += mg_to_litres(mg, float(densities[ftype]))

    total_fuel = float(metrics["total_fuel_L"])
    if int(metrics["n_accounted"]) < n_departed and traci_fuel_L > total_fuel:
        total_fuel = traci_fuel_L
    denom = n_departed if n_departed else int(metrics["n_accounted"])
    fuel_per = fuel_per_departed(total_fuel, denom)

    remaining = n_unfinished > 0 or (n_departed > int(metrics["n_completed"]))
    if gridlock_flag:
        status = "gridlock"
    elif step >= timeout and remaining:
        status = "timeout"
    else:
        status = "ok"

    approach_counts = {a: len(seen_approach[a]) for a in seen_approach}
    # Per hour of demand, not of simulated time: the drain period after the horizon varies by
    # controller and would otherwise deflate the rate of whichever controller drains slower.
    hours = max(horizon, 1) / 3600.0
    approach_veh_h = {a: (approach_counts[a] / hours) for a in approach_counts}

    row = {
        "scenario": scenario,
        "controller": controller,
        "seed": seed,
        "emission_model": emission_model,
        "n_departed": n_departed,
        "n_completed": metrics["n_completed"],
        "n_unfinished": n_unfinished,
        "status": status,
        "error": "",
        "total_fuel_L": total_fuel,
        "fuel_per_vehicle_L": fuel_per,
        "fuel_per_person_L": metrics["fuel_per_person_L"],
        "total_CO2_kg": metrics["total_CO2_kg"],
        "mean_waiting_s": metrics["mean_waiting_s"],
        "p95_waiting_s": metrics["p95_waiting_s"],
        "mean_timeLoss_s": metrics["mean_timeLoss_s"],
        "person_delay_s": metrics["person_delay_s"],
        "mean_travel_time_s": metrics["mean_travel_time_s"],
        "stops_per_vehicle": metrics["stops_per_vehicle"],
        "idle_vehicle_seconds": idle_vehicle_seconds,
        "mean_queue_veh": float(sum(queue_samples) / max(len(queue_samples), 1)),
        "max_queue_veh": max_queue,
        "fuel_L_by_type": json.dumps(metrics["fuel_L_by_type"]),
        "ssm_conflicts": ssm_conflicts,
        "conflicts_per_1000_veh": conflicts_per_1000(ssm_conflicts, n_departed),
        "gridlock_flag": gridlock_flag,
        "wall_time_s": wall,
        "approach_veh_h": approach_veh_h,
        "green_alloc_s": green_alloc,
        "fuel_L_by_fuel": metrics.get("fuel_L_by_fuel") or {},
    }

    if save_timeseries:
        ts_path = ROOT / "results" / "timeseries" / f"{tag}.json"
        save_json(ts_path, {"rows": ts_rows, "green_alloc": green_alloc})

    # Metrics are already in the row. Keeping every dump fills the disk on a full search.
    for leftover in (tripinfo, ssm_out, cfg_path):
        try:
            leftover.unlink(missing_ok=True)
        except OSError:
            pass
    if emission_model == "alternate":
        alt_path = raw_dir / f"{tag}_vtypes_alt.add.xml"
        try:
            alt_path.unlink(missing_ok=True)
        except OSError:
            pass

    return row


def append_raw_row(row: Dict[str, Any], path: Optional[Path] = None) -> None:
    path = path or (ROOT / "results" / "raw_runs.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RAW_FIELDS, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in RAW_FIELDS})


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--scenario", default="balanced")
    p.add_argument("--controller", default="fixed")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--smoke", action="store_true")
    args = p.parse_args()
    from sim.build_network import build as build_net
    if not (ROOT / "results" / "networks" / "intersection.net.xml").exists():
        build_net()
    row = run_one(args.scenario, args.controller, args.seed, smoke=args.smoke)
    print(json.dumps({k: row[k] for k in RAW_FIELDS}, indent=2, default=str))
