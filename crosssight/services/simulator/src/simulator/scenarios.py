"""Scripted scenario definitions and scenarios.json ground truth."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import numpy as np
from anpr_common.geo import opposite_direction
from anpr_common.grammar import normalize_plate
from anpr_common.schemas import Direction

from simulator.cameras import Camera
from simulator.vehicles import Vehicle, generate_unique_plate

if TYPE_CHECKING:
    import networkx as nx
    from anpr_common.config import Settings

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
SCENARIOS_PATH = DATA_DIR / "scenarios.json"


@dataclass
class ScenarioBundle:
    watchlist_plates: list[str]
    cloned_pairs: list[dict[str, Any]]
    convoy: dict[str, Any]
    loiterer: dict[str, Any]
    wrong_way: dict[str, Any]
    restricted_zone: dict[str, Any]
    all_scenario_plates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "watchlist_plates": self.watchlist_plates,
            "cloned_pairs": self.cloned_pairs,
            "convoy": self.convoy,
            "loiterer": self.loiterer,
            "wrong_way": self.wrong_way,
            "restricted_zone": self.restricted_zone,
            "expected_alerts": {
                "watchlist": self.watchlist_plates,
                "cloned_plate": [p["plate_norm"] for p in self.cloned_pairs],
                "convoy": [self.convoy["lead_plate"]],
                "loitering": [self.loiterer["plate_norm"]],
                "wrong_way": [self.wrong_way["plate_norm"]],
                "geofence": [self.restricted_zone["plate_norm"]],
            },
        }


def build_scenarios(
    cameras: list[Camera],
    graph: nx.MultiDiGraph,
    zone_records: list[dict[str, Any]],
    _settings: Settings,
    seed: int = 7,
) -> ScenarioBundle:
    """Create deterministic scenario ground truth from graph layout."""

    from simulator.cameras import compute_camera_pairs

    rng = np.random.default_rng(seed)
    used: set[str] = set()

    watchlist: list[str] = []
    for _ in range(10):
        _, norm, _, _ = generate_unique_plate(rng, used)
        used.add(norm)
        watchlist.append(norm)

    ranked = sorted(cameras, key=lambda c: c.betweenness, reverse=True)
    chain = ranked[: max(6, min(8, len(ranked)))]
    chain_ids = [c.id for c in chain]

    pairs_data = compute_camera_pairs(graph, cameras)
    # Prefer the farthest camera pairs so a short gap yields impossible speed.
    distant = sorted(pairs_data, key=lambda p: float(cast(Any, p["distance_m"])), reverse=True)
    # Deduplicate by unordered camera pair so we don't reuse the same link.
    seen_links: set[frozenset[str]] = set()
    unique_distant: list[dict[str, Any]] = []
    for p in distant:
        link = frozenset((cast(str, p["camera_a"]), cast(str, p["camera_b"])))
        if link in seen_links:
            continue
        seen_links.add(link)
        unique_distant.append(p)

    cloned_pairs: list[dict[str, Any]] = []
    for idx in range(2):
        if idx >= len(unique_distant):
            break
        pair = unique_distant[idx]
        _, plate, _, _ = generate_unique_plate(rng, used)
        used.add(plate)
        dist_m = float(pair["distance_m"])
        # Gap short enough that required speed > max_urban * 1.5 (clone threshold).
        threshold_kmh = float(_settings.max_urban_speed_kmh) * 1.5
        max_gap_s = max(5.0, (dist_m * 3.6) / threshold_kmh * 0.5)
        cloned_pairs.append(
            {
                "plate_norm": plate,
                "camera_a": pair["camera_a"],
                "camera_b": pair["camera_b"],
                "camera_c": pair["camera_a"],
                "camera_d": pair["camera_b"],
                "distance_m": dist_m,
                "gap_seconds": round(max_gap_s, 1),
                "description": "Same plate at distant cameras within impossible time",
            }
        )

    convoy_plates: list[str] = []
    for _ in range(3):
        _, norm, _, _ = generate_unique_plate(rng, used)
        used.add(norm)
        convoy_plates.append(norm)

    sensitive_zones = [z for z in zone_records if z.get("kind") == "sensitive"]
    loiter_zone = sensitive_zones[0]["id"] if sensitive_zones else zone_records[0]["id"]
    # Prefer cameras near the sensitive zone centroid for loiter injections.
    loiter_cams: list[str] = []
    if sensitive_zones:
        cz = sensitive_zones[0].get("centroid")
        if cz:
            lat0, lng0 = float(cz[0]), float(cz[1])
            ranked_near = sorted(
                cameras,
                key=lambda c: (c.lat - lat0) ** 2 + (c.lng - lng0) ** 2,
            )
            loiter_cams = [c.id for c in ranked_near[:3]]
    if not loiter_cams:
        loiter_cams = [c.id for c in ranked[:3]]
    _, loiter_plate, _, _ = generate_unique_plate(rng, used)
    used.add(loiter_plate)

    wrong_cam = ranked[len(ranked) // 3]
    _, wrong_plate, _, _ = generate_unique_plate(rng, used)
    used.add(wrong_plate)
    wrong_dir = opposite_direction(Direction(wrong_cam.allowed_direction)).value

    restricted = next((z for z in zone_records if z.get("kind") == "restricted"), zone_records[-1])
    _, restricted_plate, _, _ = generate_unique_plate(rng, used)
    used.add(restricted_plate)

    convoy_route: list[str] = []
    if len(chain) >= 4:
        convoy_route = chain_ids[: max(4, len(chain_ids))]

    bundle = ScenarioBundle(
        watchlist_plates=watchlist,
        cloned_pairs=cloned_pairs,
        convoy={
            "lead_plate": convoy_plates[0],
            "plates": convoy_plates,
            "cameras": convoy_route,
            "max_gap_seconds": 15,
            "min_cameras": 4,
        },
        loiterer={
            "plate_norm": loiter_plate,
            "zone_id": loiter_zone,
            "camera_ids": loiter_cams,
            "passes": 6,
            "window_minutes": 45,
        },
        wrong_way={
            "plate_norm": wrong_plate,
            "camera_id": wrong_cam.id,
            "allowed_direction": wrong_cam.allowed_direction,
            "reported_direction": wrong_dir,
        },
        restricted_zone={
            "plate_norm": restricted_plate,
            "zone_id": restricted["id"],
            "active_hours": restricted.get("active_hours", {"start": "22:00", "end": "06:00"}),
            "entry_hour_local": 23,
        },
        all_scenario_plates=sorted(
            set(
                watchlist
                + convoy_plates
                + [loiter_plate, wrong_plate, restricted_plate]
                + [p["plate_norm"] for p in cloned_pairs]
            )
        ),
    )
    return bundle


def write_scenarios(bundle: ScenarioBundle, path: Path | None = None) -> Path:
    path = path or SCENARIOS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = bundle.to_dict()
    payload["generated_at"] = datetime.now(UTC).isoformat()
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def load_scenarios(path: Path | None = None) -> dict[str, Any]:
    path = path or SCENARIOS_PATH
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class InjectedEvent:
    plate_norm: str
    camera_id: str
    ts: datetime
    direction: str | None = None
    force_emit: bool = True
    vehicle_class: str = "car"
    tags: list[str] = field(default_factory=list)


def _in_active_hours(active_hours: Any, ts: datetime) -> bool:
    if not active_hours:
        return True
    if isinstance(active_hours, str):
        try:
            active_hours = json.loads(active_hours)
        except json.JSONDecodeError:
            return True
    if not isinstance(active_hours, dict):
        return True
    if "start" in active_hours and "end" in active_hours:
        start_h, start_m = (int(part) for part in str(active_hours["start"]).split(":"))
        end_h, end_m = (int(part) for part in str(active_hours["end"]).split(":"))
        start_min = start_h * 60 + start_m
        end_min = end_h * 60 + end_m
        cur_min = ts.hour * 60 + ts.minute
        if start_min <= end_min:
            return start_min <= cur_min < end_min
        return cur_min >= start_min or cur_min < end_min
    return True


def scenario_injections(
    bundle: ScenarioBundle,
    cameras: list[Camera],
    graph: nx.MultiDiGraph,
    zone_node_map: dict[str, list[int | str]],
    start: datetime,
) -> list[InjectedEvent]:
    """Return timed injections that realize scripted scenarios."""

    events: list[InjectedEvent] = []
    cam_by_id = {c.id: c for c in cameras}
    ranked = sorted(cameras, key=lambda c: c.betweenness, reverse=True)

    # Watchlist — one forced read per plate within the first simulated minutes.
    for idx, plate in enumerate(bundle.watchlist_plates):
        cam = ranked[idx % len(ranked)]
        events.append(
            InjectedEvent(
                plate_norm=plate,
                camera_id=cam.id,
                ts=start + timedelta(minutes=2 + idx),
                tags=["watchlist"],
                force_emit=True,
            )
        )

    # Cloned plate pairs — two reads at distant cameras with an impossible transit time
    for idx, pair in enumerate(bundle.cloned_pairs):
        t0 = start + timedelta(minutes=12 + idx * 8)
        gap_s = float(pair.get("gap_seconds") or 15)
        events.append(
            InjectedEvent(
                plate_norm=pair["plate_norm"],
                camera_id=pair["camera_a"],
                ts=t0,
                tags=["cloned_plate"],
                force_emit=True,
            )
        )
        events.append(
            InjectedEvent(
                plate_norm=pair["plate_norm"],
                camera_id=pair["camera_b"],
                ts=t0 + timedelta(seconds=gap_s),
                tags=["cloned_plate"],
                force_emit=True,
            )
        )

    # Convoy — three plates through consecutive cameras within 15s
    convoy_cams = bundle.convoy.get("cameras", [])
    if convoy_cams:
        base = start + timedelta(minutes=20)
        convoy_plates = bundle.convoy["plates"]
        ordered_plates = convoy_plates[1:] + [convoy_plates[0]]
        for cam_idx, cam_id in enumerate(convoy_cams[: max(4, len(convoy_cams))]):
            for offset_s, plate in enumerate(ordered_plates):
                events.append(
                    InjectedEvent(
                        plate_norm=plate,
                        camera_id=cam_id,
                        ts=base + timedelta(seconds=cam_idx * 30 + offset_s * 10),
                        tags=["convoy"],
                        force_emit=True,
                    )
                )

    # Loiterer — repeated passes near sensitive zone
    loiter = bundle.loiterer
    loiter_cam_ids = loiter.get("camera_ids") or []
    loiter_cams = [cam_by_id[i] for i in loiter_cam_ids if i in cam_by_id]
    if not loiter_cams:
        loiter_nodes = zone_node_map.get(loiter["zone_id"], [])
        loiter_cams = [c for c in cameras if c.node_id in loiter_nodes] or cameras[:3]
    base = start + timedelta(minutes=28)
    for pass_idx in range(int(loiter["passes"])):
        cam = loiter_cams[pass_idx % len(loiter_cams)]
        events.append(
            InjectedEvent(
                plate_norm=loiter["plate_norm"],
                camera_id=cam.id,
                ts=base + timedelta(minutes=pass_idx * 7),
                tags=["loitering"],
                force_emit=True,
            )
        )

    # Wrong-way
    ww = bundle.wrong_way
    events.append(
        InjectedEvent(
            plate_norm=ww["plate_norm"],
            camera_id=ww["camera_id"],
            ts=start + timedelta(minutes=36),
            direction=ww["reported_direction"],
            tags=["wrong_way"],
            force_emit=True,
        )
    )

    # Restricted zone entry outside active hours (22:00–06:00).
    rz = bundle.restricted_zone
    rz_nodes = zone_node_map.get(rz["zone_id"], [])
    rz_cams = [c for c in cameras if c.node_id in rz_nodes] or [cameras[-1]]
    active_hours = rz.get("active_hours") or {"start": "22:00", "end": "06:00"}
    geofence_ts = start + timedelta(hours=5)
    while _in_active_hours(active_hours, geofence_ts):
        geofence_ts += timedelta(hours=1)
    events.append(
        InjectedEvent(
            plate_norm=rz["plate_norm"],
            camera_id=rz_cams[0].id,
            ts=geofence_ts,
            tags=["geofence"],
            force_emit=True,
        )
    )

    return events


def tag_vehicles_for_scenarios(vehicles: list[Vehicle], bundle: ScenarioBundle) -> None:
    """Attach scenario plates to fleet vehicles where possible."""
    plate_to_tags: dict[str, list[str]] = {}
    for plate in bundle.watchlist_plates:
        plate_to_tags.setdefault(plate, []).append("watchlist")
    for plate in bundle.convoy["plates"]:
        plate_to_tags.setdefault(plate, []).append("convoy")
    plate_to_tags.setdefault(bundle.loiterer["plate_norm"], []).append("loitering")
    plate_to_tags.setdefault(bundle.wrong_way["plate_norm"], []).append("wrong_way")
    plate_to_tags.setdefault(bundle.restricted_zone["plate_norm"], []).append("geofence")
    for pair in bundle.cloned_pairs:
        plate_to_tags.setdefault(pair["plate_norm"], []).append("cloned_plate")

    assigned = {v.plate_norm for v in vehicles}
    spare = [v for v in vehicles if "scenario" not in v.scenario_tags]

    for plate, tags in plate_to_tags.items():
        if plate in assigned:
            for v in vehicles:
                if v.plate_norm == plate:
                    v.scenario_tags.extend(tags)
            continue
        if spare:
            v = spare.pop()
            result = normalize_plate(plate)
            v.plate_raw = plate
            v.plate_norm = result.norm
            v.plate_valid = result.valid
            v.plate_format = result.format
            v.scenario_tags.extend(tags)
