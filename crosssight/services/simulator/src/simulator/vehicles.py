"""Synthetic vehicle fleet with valid Indian plates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
from anpr_common.grammar import STATE_CODES, normalize_plate
from anpr_common.schemas import VehicleClass

if TYPE_CHECKING:
    from anpr_common.config import Settings

STATE_WEIGHTS: dict[str, float] = {
    "MH": 0.28,
    "KA": 0.08,
    "GJ": 0.07,
    "TN": 0.07,
    "DL": 0.06,
    "UP": 0.06,
    "RJ": 0.05,
    "MP": 0.05,
    "WB": 0.04,
    "AP": 0.04,
    "TS": 0.04,
    "KL": 0.04,
    "HR": 0.03,
    "PB": 0.03,
    "BR": 0.02,
    "CG": 0.02,
    "OD": 0.02,
    "UK": 0.02,
    "JH": 0.02,
    "GA": 0.01,
    "HP": 0.01,
    "AS": 0.01,
    "CH": 0.01,
}

LETTER_POOL = "ABCDEFGHJKLMNPRSTUVWXY"
COLORS = ["white", "silver", "black", "grey", "red", "blue", "maroon", "brown"]
MAKES = ["Maruti", "Hyundai", "Tata", "Mahindra", "Toyota", "Honda", "Kia", "Renault"]
CLASS_WEIGHTS = [
    (VehicleClass.car, 0.62),
    (VehicleClass.motorcycle, 0.18),
    (VehicleClass.auto, 0.08),
    (VehicleClass.truck, 0.05),
    (VehicleClass.bus, 0.04),
    (VehicleClass.other, 0.03),
]


@dataclass
class Vehicle:
    vehicle_id: str
    plate_norm: str
    plate_raw: str
    plate_valid: bool
    plate_format: str
    vehicle_class: VehicleClass
    color: str
    make: str
    home_zone: str
    work_zone: str
    scenario_tags: list[str] = field(default_factory=list)


def _generate_standard_plate(state: str, rng: np.random.Generator) -> str:
    if rng.random() < 0.25:
        rto = f"{rng.integers(1, 10)}{rng.choice(list(LETTER_POOL))}"
    else:
        rto = str(int(rng.integers(1, 100)))
    letter_len = int(rng.integers(0, 3))
    letters = "".join(rng.choice(list(LETTER_POOL), letter_len))
    serial = f"{int(rng.integers(0, 10000)):04d}"
    return f"{state}{rto}{letters}{serial}"


def _generate_bh_plate(rng: np.random.Generator) -> str:
    year = f"{int(rng.integers(19, 25)):02d}"
    serial = f"{int(rng.integers(0, 10000)):04d}"
    suffix_len = int(rng.integers(1, 3))
    suffix = "".join(rng.choice(list(LETTER_POOL), suffix_len))
    return f"{year}BH{serial}{suffix}"


def generate_unique_plate(rng: np.random.Generator, used: set[str]) -> tuple[str, str, bool, str]:
    states = list(STATE_WEIGHTS.keys())
    weights = [STATE_WEIGHTS[s] for s in states]
    for _ in range(200):
        if rng.random() < 0.04:
            raw = _generate_bh_plate(rng)
        else:
            state = str(rng.choice(states, p=np.array(weights) / sum(weights)))
            raw = _generate_standard_plate(state, rng)
        result = normalize_plate(raw)
        if result.valid and result.norm not in used:
            return raw, result.norm, result.valid, result.format
    # Fallback deterministic plate
    for code in sorted(STATE_CODES):
        candidate = f"{code}01A{int(rng.integers(1000, 9999)):04d}"
        result = normalize_plate(candidate)
        if result.valid and result.norm not in used:
            return candidate, result.norm, True, "standard"
    raise RuntimeError("Unable to generate unique plate")


def generate_fleet(
    settings: Settings,
    zone_ids: list[str],
    seed: int = 42,
    reserved_plates: list[str] | None = None,
) -> list[Vehicle]:
    rng = np.random.default_rng(seed)
    used: set[str] = set()
    vehicles: list[Vehicle] = []

    if reserved_plates:
        for plate in reserved_plates:
            result = normalize_plate(plate)
            if result.norm in used:
                continue
            used.add(result.norm)
            home = str(rng.choice(zone_ids))
            work = str(rng.choice([z for z in zone_ids if z != home] or zone_ids))
            vehicles.append(
                Vehicle(
                    vehicle_id=f"veh-{len(vehicles) + 1:05d}",
                    plate_raw=plate,
                    plate_norm=result.norm,
                    plate_valid=result.valid,
                    plate_format=result.format,
                    vehicle_class=VehicleClass.car,
                    color=str(rng.choice(COLORS)),
                    make=str(rng.choice(MAKES)),
                    home_zone=home,
                    work_zone=work,
                    scenario_tags=["scenario"],
                )
            )

    while len(vehicles) < settings.num_vehicles:
        raw, norm, valid, fmt = generate_unique_plate(rng, used)
        used.add(norm)
        probs = np.array([w for _, w in CLASS_WEIGHTS])
        vclass = CLASS_WEIGHTS[int(rng.choice(len(CLASS_WEIGHTS), p=probs / probs.sum()))][0]
        home = str(rng.choice(zone_ids))
        work = str(rng.choice([z for z in zone_ids if z != home] or zone_ids))
        vehicles.append(
            Vehicle(
                vehicle_id=f"veh-{len(vehicles) + 1:05d}",
                plate_raw=raw,
                plate_norm=norm,
                plate_valid=valid,
                plate_format=fmt,
                vehicle_class=vclass,
                color=str(rng.choice(COLORS)),
                make=str(rng.choice(MAKES)),
                home_zone=home,
                work_zone=work,
            )
        )
    return vehicles
