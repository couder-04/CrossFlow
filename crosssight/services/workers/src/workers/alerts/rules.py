"""Alert rule implementations."""

from __future__ import annotations

import inspect
import json
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from anpr_common.fuzzy import candidates
from anpr_common.geo import haversine_m, opposite_direction
from anpr_common.schemas import (
    Alert,
    AlertSeverity,
    AlertType,
    Direction,
    PlateRead,
)

from workers.alerts.registry import RegistryClient, RegistryRecord
from workers.db import CameraInfo, CameraPair

ALERT_DEDUP_MINUTES = 10
CONVOY_TIME_WINDOW_SEC = 20
CONVOY_MIN_COMMON_CAMERAS = 3
CONVOY_LOOKBACK_MIN = 30
LOITERING_MIN_SIGHTINGS = 5
LOITERING_WINDOW_MIN = 60


class RuleContext(Protocol):
    settings: Any
    cameras: dict[str, CameraInfo]
    watchlist: set[str]
    pair_distances: dict[tuple[str, str], float]
    redis: Any
    pg_pool: Any

    async def last_seen(self, plate_norm: str) -> tuple[str, datetime, float] | None: ...
    async def convoy_peers(self, plate_norm: str, camera_id: str, ts: datetime) -> set[str]: ...
    async def sensitive_zone_id(self, camera_id: str) -> str | None: ...
    async def restricted_zone(self, camera_id: str) -> tuple[str, dict] | None: ...
    async def plate_sightings_in_zone(
        self, plate_norm: str, zone_id: str, since: datetime, at: datetime | None = None
    ) -> int: ...


class Rule(ABC):
    name: str

    @abstractmethod
    async def evaluate(self, read: PlateRead, ctx: RuleContext) -> list[Alert]: ...


def _read_evidence(read: PlateRead) -> dict[str, Any]:
    return {
        "event_id": str(read.event_id),
        "camera_id": read.camera_id,
        "ts": read.ts.isoformat(),
        "plate_norm": read.plate_norm,
        "confidence": read.confidence,
        "direction": read.direction.value if read.direction else None,
        "vehicle_class": read.vehicle_class.value,
        "crop_key": read.crop_key,
        "lane": read.lane,
        "track_id": read.track_id,
        "bbox": read.bbox,
        "source_video_key": read.source_video_key,
    }


WATCHLIST_MIN_CONF_DEFAULT = 0.5


class WatchlistRule(Rule):
    name = "watchlist"

    async def evaluate(self, read: PlateRead, ctx: RuleContext) -> list[Alert]:
        # Low-confidence reads are mostly misreads; with fuzzy matching at edit cost 1 they can
        # land on a watchlisted plate and raise a false alert.
        min_conf = getattr(ctx.settings, "watchlist_min_conf", WATCHLIST_MIN_CONF_DEFAULT)
        if read.confidence < min_conf:
            return []
        plate = read.plate_norm.upper()
        if plate in ctx.watchlist:
            return [
                Alert(
                    type=AlertType.watchlist,
                    severity=AlertSeverity.high,
                    plate_norm=plate,
                    camera_ids=[read.camera_id],
                    evidence={"reads": [_read_evidence(read)], "match": "exact"},
                    ts=read.ts,
                )
            ]
        fuzzy_hits = candidates(plate, pool=list(ctx.watchlist), max_cost=1.0)
        if not fuzzy_hits:
            return []
        match_plate, cost = fuzzy_hits[0]
        return [
            Alert(
                type=AlertType.watchlist,
                severity=AlertSeverity.medium,
                plate_norm=plate,
                camera_ids=[read.camera_id],
                evidence={
                    "reads": [_read_evidence(read)],
                    "match": "fuzzy",
                    "watchlist_plate": match_plate,
                    "edit_cost": cost,
                },
                needs_verification=True,
                ts=read.ts,
            )
        ]


def _clone_speed_threshold(prev_cam: CameraInfo, cur_cam: CameraInfo, settings: Any) -> float:
    """Use the higher posted limit when both cameras have one; otherwise the urban cap."""
    urban = float(settings.max_urban_speed_kmh)
    if prev_cam.speed_limit_kmh is not None and cur_cam.speed_limit_kmh is not None:
        urban = max(prev_cam.speed_limit_kmh, cur_cam.speed_limit_kmh, urban)
    return urban * 1.5


class ClonedPlateRule(Rule):
    name = "cloned_plate"

    async def evaluate(self, read: PlateRead, ctx: RuleContext) -> list[Alert]:
        if read.confidence < 0.8:
            return []
        last = await ctx.last_seen(read.plate_norm)
        if last is None:
            return []
        prev_cam_id, prev_ts, prev_conf = last
        if prev_conf < 0.8:
            return []
        if prev_cam_id == read.camera_id:
            return []
        prev_cam = ctx.cameras.get(prev_cam_id)
        cur_cam = ctx.cameras.get(read.camera_id)
        if prev_cam is None or cur_cam is None:
            return []
        dist = ctx.pair_distances.get((prev_cam_id, read.camera_id))
        if dist is None:
            dist = haversine_m(prev_cam.lat, prev_cam.lng, cur_cam.lat, cur_cam.lng)
        elapsed = (read.ts - prev_ts).total_seconds()
        if elapsed <= 0:
            return []
        speed_kmh = (dist / elapsed) * 3.6
        threshold = _clone_speed_threshold(prev_cam, cur_cam, ctx.settings)
        if speed_kmh <= threshold:
            return []
        return [
            Alert(
                type=AlertType.cloned_plate,
                severity=AlertSeverity.critical,
                plate_norm=read.plate_norm,
                camera_ids=[prev_cam_id, read.camera_id],
                evidence={
                    "reads": [_read_evidence(read)],
                    "prev_camera": prev_cam_id,
                    "prev_ts": prev_ts.isoformat(),
                    "distance_m": dist,
                    "elapsed_s": elapsed,
                    "required_speed_kmh": speed_kmh,
                    "threshold_kmh": threshold,
                },
                ts=read.ts,
            )
        ]


class ConvoyRule(Rule):
    name = "convoy"

    async def evaluate(self, read: PlateRead, ctx: RuleContext) -> list[Alert]:
        peers = await ctx.convoy_peers(read.plate_norm, read.camera_id, read.ts)
        if len(peers) < 2:
            return []
        return [
            Alert(
                type=AlertType.convoy,
                severity=AlertSeverity.medium,
                plate_norm=read.plate_norm,
                camera_ids=[read.camera_id],
                evidence={
                    "reads": [_read_evidence(read)],
                    "peer_plates": sorted(peers),
                    "common_cameras_min": CONVOY_MIN_COMMON_CAMERAS,
                },
                ts=read.ts,
            )
        ]


class LoiteringRule(Rule):
    name = "loitering"

    async def evaluate(self, read: PlateRead, ctx: RuleContext) -> list[Alert]:
        zone_id = await ctx.sensitive_zone_id(read.camera_id)
        if zone_id is None:
            return []
        since = read.ts - timedelta(minutes=LOITERING_WINDOW_MIN)
        count = await ctx.plate_sightings_in_zone(read.plate_norm, zone_id, since, read.ts)
        if count < LOITERING_MIN_SIGHTINGS:
            return []
        return [
            Alert(
                type=AlertType.loitering,
                severity=AlertSeverity.high,
                plate_norm=read.plate_norm,
                camera_ids=[read.camera_id],
                evidence={
                    "reads": [_read_evidence(read)],
                    "zone_id": zone_id,
                    "sightings": count,
                    "window_min": LOITERING_WINDOW_MIN,
                },
                ts=read.ts,
            )
        ]


def _parse_active_hours(active_hours: Any) -> dict[str, Any]:
    if not active_hours:
        return {}
    if isinstance(active_hours, str):
        try:
            parsed = json.loads(active_hours)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
        return {}
    if isinstance(active_hours, dict):
        return active_hours
    return {}


def _in_active_hours(active_hours: Any, ts: datetime) -> bool:
    hours = _parse_active_hours(active_hours)
    if not hours:
        return True

    if "start" in hours and "end" in hours:
        start_h, start_m = (int(part) for part in str(hours["start"]).split(":"))
        end_h, end_m = (int(part) for part in str(hours["end"]).split(":"))
        start_min = start_h * 60 + start_m
        end_min = end_h * 60 + end_m
        cur_min = ts.hour * 60 + ts.minute
        if start_min <= end_min:
            return start_min <= cur_min < end_min
        return cur_min >= start_min or cur_min < end_min

    dow = ts.strftime("%a").lower()[:3]
    hour = ts.hour
    windows = hours.get(dow) or hours.get("default") or []
    for w in windows:
        start = int(w.get("start", 0))
        end = int(w.get("end", 24))
        if start <= hour < end:
            return True
    return False


class GeofenceRule(Rule):
    name = "geofence"

    async def evaluate(self, read: PlateRead, ctx: RuleContext) -> list[Alert]:
        zone = await ctx.restricted_zone(read.camera_id)
        if zone is None:
            return []
        zone_id, active_hours = zone
        if _in_active_hours(active_hours, read.ts):
            return []
        return [
            Alert(
                type=AlertType.geofence,
                severity=AlertSeverity.high,
                plate_norm=read.plate_norm,
                camera_ids=[read.camera_id],
                evidence={
                    "reads": [_read_evidence(read)],
                    "zone_id": zone_id,
                    "active_hours": active_hours,
                },
                ts=read.ts,
            )
        ]


class WrongWayRule(Rule):
    name = "wrong_way"

    async def evaluate(self, read: PlateRead, ctx: RuleContext) -> list[Alert]:
        if read.direction is None:
            return []
        cam = ctx.cameras.get(read.camera_id)
        if cam is None or not cam.allowed_direction:
            return []
        allowed = Direction(cam.allowed_direction)
        if read.direction != opposite_direction(allowed):
            return []
        return [
            Alert(
                type=AlertType.wrong_way,
                severity=AlertSeverity.high,
                plate_norm=read.plate_norm,
                camera_ids=[read.camera_id],
                evidence={
                    "reads": [_read_evidence(read)],
                    "allowed_direction": allowed.value,
                    "observed_direction": read.direction.value,
                },
                ts=read.ts,
            )
        ]


class PlateVehicleMismatchRule(Rule):
    """Compares OCR vehicle class against registry (Vahan stub or FileRegistry)."""

    name = "plate_vehicle_mismatch"

    def __init__(self, registry: RegistryClient) -> None:
        self.registry = registry

    async def evaluate(self, read: PlateRead, ctx: RuleContext) -> list[Alert]:
        record: RegistryRecord = await self.registry.lookup(read.plate_norm)
        if record.status == "unknown" or record.vehicle_class is None:
            return []
        if record.vehicle_class == read.vehicle_class:
            return []
        return [
            Alert(
                type=AlertType.plate_vehicle_mismatch,
                severity=AlertSeverity.medium,
                plate_norm=read.plate_norm,
                camera_ids=[read.camera_id],
                evidence={
                    "reads": [_read_evidence(read)],
                    "registry": {
                        "vehicle_class": record.vehicle_class.value,
                        "color": record.color,
                        "make": record.make,
                        "status": record.status,
                    },
                    "observed_class": read.vehicle_class.value,
                },
                needs_verification=True,
                ts=read.ts,
            )
        ]


class RouteAnomalyRule(Rule):
    """Flag plates that suddenly appear far from their established corridor.

    After a plate has been seen at ``ROUTE_ANOMALY_MIN_CAMERAS`` distinct cameras,
    a new sighting whose camera is farther than ``ROUTE_ANOMALY_DISTANCE_M`` from
    the plate's geographic centroid *and* not adjacent to the last camera is
    treated as a suspicious route anomaly (not a clone-speed hop).
    """

    name = "route_anomaly"

    async def evaluate(self, read: PlateRead, ctx: RuleContext) -> list[Alert]:
        plate = read.plate_norm.upper()
        cam = ctx.cameras.get(read.camera_id)
        if cam is None:
            return []

        history_key = f"plate:cams:{plate}"
        # Record this sighting for future evaluations.
        await ctx.redis.zadd(history_key, {read.camera_id: read.ts.timestamp()})
        await ctx.redis.expire(history_key, 7 * 24 * 3600)

        members = await ctx.redis.zrange(history_key, 0, -1)
        prior = [m for m in members if m != read.camera_id]
        min_cams = getattr(ctx.settings, "route_anomaly_min_cameras", 3)
        if len(set(prior)) < min_cams:
            return []

        # Geographic centroid of prior cameras.
        prior_cams = [ctx.cameras[c] for c in set(prior) if c in ctx.cameras]
        if not prior_cams:
            return []
        lat_c = sum(c.lat for c in prior_cams) / len(prior_cams)
        lng_c = sum(c.lng for c in prior_cams) / len(prior_cams)
        dist_from_centroid = haversine_m(lat_c, lng_c, cam.lat, cam.lng)
        threshold = getattr(ctx.settings, "route_anomaly_distance_m", 8000.0)
        if dist_from_centroid < threshold:
            return []

        # Adjacent hop is a normal corridor continuation — skip.
        last = await ctx.last_seen(plate)
        if last is not None:
            prev_cam_id, _, _ = last
            if (prev_cam_id, read.camera_id) in ctx.pair_distances or (
                read.camera_id,
                prev_cam_id,
            ) in ctx.pair_distances:
                # Only skip if marked adjacent at short range; pair_distances may
                # include non-adjacent pairs when loaded with adjacent_only=False.
                # Prefer explicit adjacent check via distance under 2 km.
                pair_dist = ctx.pair_distances.get(
                    (prev_cam_id, read.camera_id)
                ) or ctx.pair_distances.get((read.camera_id, prev_cam_id))
                if pair_dist is not None and pair_dist < 2500:
                    return []

        return [
            Alert(
                type=AlertType.route_anomaly,
                severity=AlertSeverity.high,
                plate_norm=plate,
                camera_ids=[read.camera_id],
                evidence={
                    "reads": [_read_evidence(read)],
                    "prior_cameras": sorted(set(prior)),
                    "centroid_lat": lat_c,
                    "centroid_lng": lng_c,
                    "distance_from_centroid_m": dist_from_centroid,
                    "threshold_m": threshold,
                },
                needs_verification=True,
                ts=read.ts,
            )
        ]


class StoppedVehicleRule(Rule):
    """Stationary dwell at one camera. This does not replace zone loitering."""

    name = "stopped_vehicle"

    async def evaluate(self, read: PlateRead, ctx: RuleContext) -> list[Alert]:
        observe = getattr(ctx, "observe_dwell", None)
        if observe is None:
            return []
        result = await observe(read)
        if not result or not result.get("should_alert"):
            return []
        evidence = dict(result.get("evidence") or {})
        evidence["reads"] = [_read_evidence(read)]
        return [
            Alert(
                type=AlertType.stopped_vehicle,
                severity=AlertSeverity.high,
                plate_norm=read.plate_norm,
                camera_ids=[read.camera_id],
                evidence=evidence,
                ts=read.ts,
            )
        ]


class AlertDeduper:
    """Deduplicate alerts per (type, plate) within 10 minutes."""

    def __init__(self) -> None:
        self._recent: dict[tuple[str, str], datetime] = {}

    def is_duplicate(self, alert: Alert, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        key = (alert.type.value, alert.plate_norm.upper())
        last = self._recent.get(key)
        if last and (now - last) < timedelta(minutes=ALERT_DEDUP_MINUTES):
            return True
        self._recent[key] = now
        cutoff = now - timedelta(minutes=ALERT_DEDUP_MINUTES)
        self._recent = {k: v for k, v in self._recent.items() if v >= cutoff}
        return False


class RedisAlertDeduper:
    """Deduplicate alerts across workers with Redis ``SET key value NX EX``."""

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def is_duplicate(self, alert: Alert, now: datetime | None = None) -> bool:
        """Return True when this (type, plate) was already claimed inside the window.

        ``now`` matches ``AlertDeduper``; Redis applies the TTL from the server clock.
        SET NX returns 0 when the key exists. redis-py returns None for that same case.
        """
        del now
        key = f"alert:dedup:{alert.type.value}:{alert.plate_norm.upper()}"
        result = self._redis.set(key, "1", nx=True, ex=ALERT_DEDUP_MINUTES * 60)
        if inspect.isawaitable(result):
            result = await result
        return result == 0 or result is None


class DefaultRuleContext:
    def __init__(
        self,
        settings: Any,
        cameras: dict[str, CameraInfo],
        watchlist: set[str],
        pairs: list[CameraPair],
        redis: Any,
        pg_pool: Any,
        *,
        sensitive_zones: dict[str, str] | None = None,
        restricted_zones: dict[str, tuple[str, dict]] | None = None,
        dwell_overrides: dict[str, dict] | None = None,
    ) -> None:
        self.settings = settings
        self.cameras = cameras
        self.watchlist = watchlist
        self.redis = redis
        self.pg_pool = pg_pool
        self.pair_distances: dict[tuple[str, str], float] = {
            (p.camera_a, p.camera_b): p.distance_m for p in pairs
        }
        self.sensitive_zones: dict[str, str] = dict(sensitive_zones or {})
        self.restricted_zones: dict[str, tuple[str, dict]] = dict(restricted_zones or {})
        self.dwell_overrides: dict[str, dict] = dict(dwell_overrides or {})
        self._loiter_counts: dict[tuple[str, str], list[datetime]] = defaultdict(list)

    def set_zone_cache(
        self,
        sensitive: dict[str, str],
        restricted: dict[str, tuple[str, dict]],
    ) -> None:
        self.sensitive_zones = dict(sensitive)
        self.restricted_zones = dict(restricted)

    async def last_seen(self, plate_norm: str) -> tuple[str, datetime, float] | None:
        data = await self.redis.hgetall(f"alerts:lastseen:{plate_norm.upper()}")
        if not data or "camera_id" not in data or "ts" not in data:
            return None
        ts = datetime.fromisoformat(data["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        conf = float(data.get("confidence", "0"))
        return data["camera_id"], ts, conf

    async def convoy_peers(self, plate_norm: str, camera_id: str, ts: datetime) -> set[str]:
        """Find co-travellers using plate→camera indexes (not O(|cameras|))."""
        zkey = f"convoy:cam:{camera_id}"
        min_score = ts.timestamp() - CONVOY_TIME_WINDOW_SEC
        max_score = ts.timestamp() + CONVOY_TIME_WINDOW_SEC
        members = await self.redis.zrangebyscore(zkey, min_score, max_score)
        plate = plate_norm.upper()
        peers = {m for m in members if m != plate}
        if len(peers) < 2:
            return set()
        lookback = ts.timestamp() - CONVOY_LOOKBACK_MIN * 60
        plate_cams = await self.redis.zrangebyscore(f"convoy:plate:{plate}", lookback, "+inf")
        plate_cam_set = set(plate_cams)
        if len(plate_cam_set) < CONVOY_MIN_COMMON_CAMERAS:
            return set()
        common: set[str] = set()
        for peer in peers:
            peer_cams = await self.redis.zrangebyscore(f"convoy:plate:{peer}", lookback, "+inf")
            shared_cams = plate_cam_set.intersection(peer_cams)
            if len(shared_cams) < CONVOY_MIN_COMMON_CAMERAS:
                continue
            shared = 0
            for cam_id in shared_cams:
                z = f"convoy:cam:{cam_id}"
                a = await self.redis.zscore(z, plate)
                b = await self.redis.zscore(z, peer)
                if (
                    a
                    and b
                    and a >= lookback
                    and b >= lookback
                    and abs(a - b) <= CONVOY_TIME_WINDOW_SEC
                ):
                    shared += 1
                    if shared >= CONVOY_MIN_COMMON_CAMERAS:
                        common.add(peer)
                        break
        return common

    async def sensitive_zone_id(self, camera_id: str) -> str | None:
        if camera_id in self.sensitive_zones:
            return self.sensitive_zones[camera_id]
        if self.pg_pool is None:
            return None
        # Fallback to PostGIS if cache cold (e.g. new camera)
        from workers.db import camera_in_zone

        zone_id = await camera_in_zone(self.pg_pool, camera_id, "sensitive")
        if zone_id:
            self.sensitive_zones[camera_id] = zone_id
        return zone_id

    async def restricted_zone(self, camera_id: str) -> tuple[str, dict] | None:
        if camera_id in self.restricted_zones:
            return self.restricted_zones[camera_id]
        if self.pg_pool is None:
            return None
        from workers.db import camera_in_restricted_zone

        zone = await camera_in_restricted_zone(self.pg_pool, camera_id)
        if zone:
            self.restricted_zones[camera_id] = zone
        return zone

    async def plate_sightings_in_zone(
        self, plate_norm: str, zone_id: str, since: datetime, at: datetime | None = None
    ) -> int:
        """Redis-backed so multi-replica alert workers share loiter state."""
        at = at or datetime.now(UTC)
        plate = plate_norm.upper()
        key = f"loiter:{plate}:{zone_id}"
        score = at.timestamp()
        await self.redis.zadd(key, {f"{score}:{at.microsecond}": score})
        await self.redis.expire(key, LOITERING_WINDOW_MIN * 60 + 60)
        # Drop old entries and count
        await self.redis.zremrangebyscore(key, 0, since.timestamp() - 0.001)
        return int(await self.redis.zcount(key, since.timestamp(), "+inf"))

    async def observe_dwell(self, read: PlateRead) -> dict[str, Any]:
        from anpr_common.intelligence.dwell import (
            DwellObservation,
            DwellThresholds,
            advance_dwell,
            classify_dwell,
            thresholds_for_camera,
        )

        plate = read.plate_norm.upper()
        key = f"dwell:{plate}"
        raw = await self.redis.hgetall(key)
        state = _dwell_from_redis(raw)
        defaults = DwellThresholds(
            short_s=float(getattr(self.settings, "stopped_short_s", 45)),
            excessive_s=float(getattr(self.settings, "stopped_excessive_s", 120)),
            incident_s=float(getattr(self.settings, "stopped_incident_s", 180)),
            gap_s=float(getattr(self.settings, "stopped_gap_s", 90)),
        )
        thresholds = thresholds_for_camera(read.camera_id, defaults, self.dwell_overrides)
        cam = self.cameras.get(read.camera_id)
        obs = DwellObservation(
            vehicle_id=plate,
            plate=plate,
            camera_id=read.camera_id,
            ts=read.ts,
            confidence=read.confidence,
            vehicle_class=read.vehicle_class.value,
            zone_id=self.sensitive_zones.get(read.camera_id),
            crop_key=read.crop_key,
            lat=cam.lat if cam else None,
            lng=cam.lng if cam else None,
            track_id=read.track_id,
        )
        new_state, _closed, should_alert = advance_dwell(state, obs, thresholds)
        await self.redis.hset(key, mapping=_dwell_to_redis(new_state))
        await self.redis.expire(key, int(thresholds.incident_s + thresholds.gap_s + 600))
        kind = classify_dwell(new_state.dwell_s, thresholds)
        return {
            "should_alert": should_alert,
            "classification": kind.value,
            "evidence": {
                "vehicle_id": new_state.vehicle_id,
                "plate_norm": new_state.plate,
                "camera_id": new_state.camera_id,
                "zone_id": new_state.zone_id,
                "first_seen": new_state.first_seen.isoformat(),
                "last_seen": new_state.last_seen.isoformat(),
                "dwell_s": new_state.dwell_s,
                "classification": kind.value,
                "confidence": new_state.confidence,
                "crop_key": new_state.crop_key,
                "lat": new_state.lat,
                "lng": new_state.lng,
                "track_id": new_state.track_id,
                "sightings": new_state.sightings,
                "vehicle_class": new_state.vehicle_class,
            },
        }


def _dwell_from_redis(raw: dict) -> Any:
    from anpr_common.intelligence.dwell import DwellState

    if not raw or "first_seen" not in raw:
        return None
    first = datetime.fromisoformat(raw["first_seen"])
    last = datetime.fromisoformat(raw["last_seen"])
    if first.tzinfo is None:
        first = first.replace(tzinfo=UTC)
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    track = raw.get("track_id")
    return DwellState(
        vehicle_id=raw.get("vehicle_id") or raw.get("plate") or "",
        plate=raw.get("plate") or "",
        camera_id=raw["camera_id"],
        zone_id=raw.get("zone_id") or None,
        first_seen=first,
        last_seen=last,
        sightings=int(raw.get("sightings") or 1),
        confidence=float(raw.get("confidence") or 0),
        crop_key=raw.get("crop_key") or None,
        vehicle_class=raw.get("vehicle_class") or "car",
        lat=float(raw["lat"]) if raw.get("lat") else None,
        lng=float(raw["lng"]) if raw.get("lng") else None,
        track_id=int(track) if track not in (None, "") else None,
        alerted=raw.get("alerted") == "1",
    )


def _dwell_to_redis(state: Any) -> dict[str, str]:
    return {
        "vehicle_id": state.vehicle_id,
        "plate": state.plate,
        "camera_id": state.camera_id,
        "zone_id": state.zone_id or "",
        "first_seen": state.first_seen.isoformat(),
        "last_seen": state.last_seen.isoformat(),
        "sightings": str(state.sightings),
        "confidence": str(state.confidence),
        "crop_key": state.crop_key or "",
        "vehicle_class": state.vehicle_class,
        "lat": "" if state.lat is None else str(state.lat),
        "lng": "" if state.lng is None else str(state.lng),
        "track_id": "" if state.track_id is None else str(state.track_id),
        "alerted": "1" if state.alerted else "0",
    }
