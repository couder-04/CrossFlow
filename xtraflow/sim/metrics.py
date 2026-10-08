"""Parse tripinfo/emissions and aggregate run metrics."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

from sim.util import load_config, mg_to_kg, mg_to_litres


def _is_unfinished(ti: ET.Element) -> bool:
    """SUMO writes unfinished trips with arrival < 0 when write-unfinished is set."""
    if (ti.get("incomplete") or "").lower() in ("1", "true"):
        return True
    arrival = ti.get("arrival")
    if arrival is None:
        return True
    try:
        return float(arrival) < 0
    except ValueError:
        return False


def parse_tripinfo(tripinfo_path: Path, cfg: Optional[dict] = None) -> Dict[str, Any]:
    cfg = cfg or load_config()
    densities = cfg["fuel"]["density_kg_per_L"]
    fuel_type = cfg["fuel"]["type"]
    occupancy = cfg["occupancy_persons"]

    if not tripinfo_path.exists():
        return _empty()

    try:
        tree = ET.parse(tripinfo_path)
    except ET.ParseError:
        return _empty()
    root = tree.getroot()
    n_completed = 0
    n_unfinished = 0
    total_fuel_L = 0.0
    total_CO2_kg = 0.0
    total_wait = 0.0
    total_timeloss = 0.0
    total_travel = 0.0
    total_stops = 0.0
    person_delay = 0.0
    fuel_by_type: Dict[str, float] = {}
    fuel_L_by_fuel: Dict[str, float] = {"petrol": 0.0, "diesel": 0.0}
    persons_total = 0.0
    waits: List[float] = []

    for ti in root.findall("tripinfo"):
        unfinished = _is_unfinished(ti)
        if unfinished:
            n_unfinished += 1
        else:
            n_completed += 1
        vtype = ti.get("vType") or ti.get("type") or "car"
        wait = float(ti.get("waitingTime", 0) or 0)
        timeloss = float(ti.get("timeLoss", 0) or 0)
        duration = float(ti.get("duration", 0) or 0)
        stops = float(ti.get("waitingCount", 0) or 0)
        total_wait += wait
        total_timeloss += timeloss
        total_travel += duration
        total_stops += stops
        waits.append(wait)
        occ = float(occupancy.get(vtype, 1.0))
        persons_total += occ
        person_delay += wait * occ

        emis = ti.find("emissions")
        fuel_mg = 0.0
        co2_mg = 0.0
        if emis is not None:
            fuel_mg = float(emis.get("fuel_abs", emis.get("fuel", 0)) or 0)
            co2_mg = float(emis.get("CO2_abs", emis.get("CO2", 0)) or 0)
        ftype = fuel_type.get(vtype, "petrol")
        dens = float(densities[ftype])
        fuel_L = mg_to_litres(fuel_mg, dens)
        co2_kg = mg_to_kg(co2_mg)
        total_fuel_L += fuel_L
        total_CO2_kg += co2_kg
        fuel_by_type[vtype] = fuel_by_type.get(vtype, 0.0) + fuel_L
        fuel_L_by_fuel[ftype] = fuel_L_by_fuel.get(ftype, 0.0) + fuel_L

    n_accounted = n_completed + n_unfinished
    denom = max(n_accounted, 1)
    waits_sorted = sorted(waits)
    if waits_sorted:
        idx = min(len(waits_sorted) - 1, max(0, int(round(0.95 * (len(waits_sorted) - 1)))))
        p95 = float(waits_sorted[idx])
    else:
        p95 = 0.0
    return {
        "n_completed": n_completed,
        "n_unfinished": n_unfinished,
        "n_accounted": n_accounted,
        "total_fuel_L": total_fuel_L,
        "fuel_per_vehicle_L": total_fuel_L / denom,
        "fuel_per_person_L": total_fuel_L / max(persons_total, 1.0),
        "total_CO2_kg": total_CO2_kg,
        "mean_waiting_s": total_wait / denom,
        "p95_waiting_s": p95,
        "mean_timeLoss_s": total_timeloss / denom,
        "person_delay_s": person_delay / max(persons_total, 1.0),
        "mean_travel_time_s": total_travel / denom,
        "stops_per_vehicle": total_stops / denom,
        "fuel_L_by_type": fuel_by_type,
        "fuel_L_by_fuel": fuel_L_by_fuel,
        "persons_total": persons_total,
    }


def fuel_per_departed(total_fuel_L: float, n_departed: int) -> float:
    """Fuel per vehicle that entered the network, including unfinished trips."""
    if n_departed <= 0:
        return 0.0
    return float(total_fuel_L) / float(n_departed)


def parse_ssm_details(ssm_path: Path, ttc_threshold: float = 1.5) -> Dict[str, Any]:
    """Count <conflict> elements whose child minTTC@value is below threshold.

    Also records PET and DRAC child values when those measures are in the file.
    """
    empty = {
        "n_conflicts": 0,
        "n_ttc_below": 0,
        "min_ttc": [],
        "pet": [],
        "drac": [],
    }
    if not ssm_path.exists():
        return empty
    try:
        tree = ET.parse(ssm_path)
    except ET.ParseError:
        return empty
    n_below = 0
    min_ttc: List[float] = []
    pet: List[float] = []
    drac: List[float] = []
    conflicts = list(tree.iter("conflict"))
    for conflict in conflicts:
        node = conflict.find("minTTC")
        if node is not None and node.get("value") is not None:
            try:
                value = float(node.get("value"))
            except ValueError:
                value = None
            if value is not None:
                min_ttc.append(value)
                if value < ttc_threshold:
                    n_below += 1
        for tag, sink in (("PET", pet), ("pet", pet), ("DRAC", drac), ("drac", drac)):
            child = conflict.find(tag)
            if child is not None and child.get("value") is not None:
                try:
                    sink.append(float(child.get("value")))
                except ValueError:
                    pass
    return {
        "n_conflicts": len(conflicts),
        "n_ttc_below": n_below,
        "min_ttc": min_ttc,
        "pet": pet,
        "drac": drac,
    }


def parse_ssm_conflicts(ssm_path: Path, ttc_threshold: float = 1.5) -> int:
    return int(parse_ssm_details(ssm_path, ttc_threshold)["n_ttc_below"])


def conflicts_per_1000(n_conflicts: int, n_departed: int) -> float:
    if n_departed <= 0:
        return 0.0
    return 1000.0 * float(n_conflicts) / float(n_departed)


def _empty() -> Dict[str, Any]:
    return {
        "n_completed": 0,
        "n_unfinished": 0,
        "n_accounted": 0,
        "total_fuel_L": 0.0,
        "fuel_per_vehicle_L": 0.0,
        "fuel_per_person_L": 0.0,
        "total_CO2_kg": 0.0,
        "mean_waiting_s": 0.0,
        "p95_waiting_s": 0.0,
        "mean_timeLoss_s": 0.0,
        "person_delay_s": 0.0,
        "mean_travel_time_s": 0.0,
        "stops_per_vehicle": 0.0,
        "fuel_L_by_type": {},
        "fuel_L_by_fuel": {"petrol": 0.0, "diesel": 0.0},
        "persons_total": 0.0,
    }
