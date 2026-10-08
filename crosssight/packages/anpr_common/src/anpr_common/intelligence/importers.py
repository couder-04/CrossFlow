"""CSV and JSON import previews. Invalid rows are returned, never dropped quietly."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from anpr_common.grammar import normalize_plate
from anpr_common.intelligence.calibration import validate_calibration
from anpr_common.intelligence.filesafety import redact_stream_url
from anpr_common.schemas import VehicleClass

_PERSONAL = {"owner_name", "owner", "phone", "mobile", "email", "address", "aadhaar", "aadhar"}
_PRIORITIES = {"low", "medium", "high", "critical"}
_VEHICLE_CLASSES = {item.value for item in VehicleClass}
_ZONE_KINDS = {"sensitive", "restricted", "ward"}


def _preview(valid: list[dict], invalid: list[dict], duplicates: list[dict]) -> dict:
    return {
        "valid_count": len(valid),
        "invalid_count": len(invalid),
        "duplicate_count": len(duplicates),
        "valid": valid,
        "invalid": invalid,
        "duplicates": duplicates,
    }


def _read_csv(text: str) -> tuple[list[str], list[dict[str, str]]]:
    if not text.strip():
        raise ValueError("csv has no header")
    if text.count('"') % 2 == 1:
        raise ValueError("malformed csv: unterminated quote")
    try:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValueError("csv has no header")
        fields = [str(f).strip() for f in reader.fieldnames]
        if any(not f for f in fields):
            raise ValueError("csv has an empty header")
        rows: list[dict[str, str]] = []
        for raw in reader:
            rows.append({(k or "").strip(): (v or "").strip() for k, v in raw.items()})
    except csv.Error as exc:
        raise ValueError(f"malformed csv: {exc}") from exc
    return fields, rows


def _parse_dt(value: str, label: str) -> tuple[datetime | None, str | None]:
    if not value:
        return None, None
    try:
        return datetime.fromisoformat(value), None
    except ValueError:
        return None, f"{label} is not an ISO date"


def _plate(raw: str) -> tuple[str | None, str | None]:
    result = normalize_plate(raw)
    if not result.valid or not result.norm:
        return None, "plate is not a valid Indian registration"
    return result.norm, None


def preview_watchlist(text: str, existing: set[str] | None = None) -> dict:
    existing = {p.upper() for p in (existing or set())}
    fields, rows = _read_csv(text)
    if "plate" not in fields and "plate_norm" not in fields:
        raise ValueError("missing required column plate")
    valid: list[dict] = []
    invalid: list[dict] = []
    duplicates: list[dict] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, start=2):
        plate_raw = row.get("plate") or row.get("plate_norm") or ""
        reason = row.get("reason") or ""
        errors: list[str] = []
        plate, perr = _plate(plate_raw)
        if perr:
            errors.append(perr)
        if not reason:
            errors.append("reason is required")
        priority = (row.get("priority") or row.get("severity") or "high").lower()
        if priority not in _PRIORITIES:
            errors.append("priority must be low, medium, high, or critical")
        start, serr = _parse_dt(row.get("valid_from", ""), "valid_from")
        end, eerr = _parse_dt(row.get("valid_until") or row.get("expires_at") or "", "valid_until")
        errors.extend(e for e in (serr, eerr) if e)
        if start and end and start > end:
            errors.append("valid_from is after valid_until")
        if errors or plate is None:
            invalid.append({"row": index, "plate": plate_raw, "errors": errors})
            continue
        record = {
            "plate_norm": plate,
            "reason": reason,
            "severity": priority,
            "valid_from": start.isoformat() if start else None,
            "valid_until": end.isoformat() if end else None,
            "notes": row.get("notes") or "",
        }
        if plate in existing or plate in seen:
            duplicates.append({"row": index, **record})
            continue
        seen.add(plate)
        valid.append(record)
    return _preview(valid, invalid, duplicates)


def preview_registry(text: str, existing: set[str] | None = None) -> dict:
    existing = {p.upper() for p in (existing or set())}
    fields, rows = _read_csv(text)
    personal = _PERSONAL.intersection({k.lower() for k in fields})
    if personal:
        raise ValueError("refusing personal columns: " + ", ".join(sorted(personal)))
    if "plate" not in fields and "plate_norm" not in fields:
        raise ValueError("missing required column plate")
    valid: list[dict] = []
    invalid: list[dict] = []
    duplicates: list[dict] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, start=2):
        plate, perr = _plate(row.get("plate") or row.get("plate_norm") or "")
        errors = [perr] if perr else []
        klass = (row.get("vehicle_class") or "").lower()
        if klass and klass not in _VEHICLE_CLASSES:
            errors.append("vehicle_class is not produced by the detector")
        status = (row.get("registration_status") or "active").lower()
        if status not in {"active", "expired", "suspended", "unknown"}:
            errors.append("registration_status is invalid")
        start, serr = _parse_dt(row.get("valid_from", ""), "valid_from")
        end, eerr = _parse_dt(row.get("valid_until", ""), "valid_until")
        errors.extend(e for e in (serr, eerr) if e)
        if start and end and start > end:
            errors.append("valid_from is after valid_until")
        if errors or plate is None:
            invalid.append({"row": index, "errors": errors})
            continue
        record = {
            "plate_norm": plate,
            "vehicle_class": klass or None,
            "make": row.get("make") or None,
            "model": row.get("model") or None,
            "color": row.get("color") or None,
            "registration_status": status,
            "owner_ref": row.get("owner_ref") or row.get("owner_reference") or None,
            "source": row.get("source") or "import",
            "valid_from": start.isoformat() if start else None,
            "valid_until": end.isoformat() if end else None,
        }
        if plate in existing or plate in seen:
            duplicates.append({"row": index, **record})
            continue
        seen.add(plate)
        valid.append(record)
    return _preview(valid, invalid, duplicates)


def preview_cameras(text: str, *, fmt: str, existing: set[str]) -> dict:
    if fmt == "json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed json: {exc}") from exc
        rows = (
            payload
            if isinstance(payload, list)
            else payload.get("cameras")
            if isinstance(payload, dict)
            else None
        )
        if not isinstance(rows, list):
            raise ValueError("camera config must be a list or {cameras: []}")
        dict_rows: list[dict] = []
        for row in rows:
            if not isinstance(row, dict):
                dict_rows.append({})
            else:
                dict_rows.append({k: "" if v is None else str(v) for k, v in row.items()})
    else:
        _fields, dict_rows = _read_csv(text)
    valid: list[dict] = []
    invalid: list[dict] = []
    duplicates: list[dict] = []
    seen: set[str] = set()
    for index, row in enumerate(dict_rows, start=1):
        errors: list[str] = []
        camera_id = (row.get("camera_id") or row.get("id") or "").strip()
        name = (row.get("name") or "").strip()
        if not camera_id:
            errors.append("camera_id is required")
        if not name:
            errors.append("name is required")
        try:
            lat = float(row.get("latitude") or row.get("lat") or "")
            lng = float(row.get("longitude") or row.get("lng") or row.get("lon") or "")
        except ValueError:
            lat = lng = 0.0
            errors.append("latitude and longitude must be numeric")
        else:
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                errors.append("coordinates out of range")
        status = (row.get("status") or "active").lower()
        if status not in {"active", "offline", "disabled"}:
            errors.append("status must be active, offline, or disabled")
        stream = row.get("rtsp_url") or row.get("stream_url") or ""
        if stream and ("@" in stream or "password" in stream.lower()):
            errors.append("RTSP credentials must not be included; configure secrets out of band")
        if errors:
            invalid.append({"row": index, "camera_id": camera_id, "errors": errors})
            continue
        record = {
            "camera_id": camera_id,
            "name": name,
            "latitude": lat,
            "longitude": lng,
            "zone": row.get("zone") or None,
            "direction": row.get("direction") or row.get("allowed_direction") or None,
            "status": status,
            "stream_ref": redact_stream_url(stream) if stream else None,
            "calibration_reference": row.get("calibration_reference") or None,
            "exists": camera_id in existing,
        }
        if camera_id in seen:
            duplicates.append({"row": index, **record})
            continue
        seen.add(camera_id)
        if camera_id in existing:
            duplicates.append({"row": index, **record, "conflict": "exists"})
            continue
        valid.append(record)
    return _preview(valid, invalid, duplicates)


def preview_calibration(text: str) -> dict:
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed json: {exc}") from exc
    docs = doc if isinstance(doc, list) else [doc]
    valid: list[dict] = []
    invalid: list[dict] = []
    for index, item in enumerate(docs, start=1):
        errors = validate_calibration(item)
        if errors:
            invalid.append({"row": index, "errors": errors})
        else:
            valid.append(item)
    return _preview(valid, invalid, [])


def preview_zones(text: str) -> dict:
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed json: {exc}") from exc
    features = doc.get("features") if isinstance(doc, dict) else None
    if not isinstance(features, list):
        raise ValueError("expected a GeoJSON FeatureCollection")  # noqa: TRY004
    valid: list[dict] = []
    invalid: list[dict] = []
    seen: set[str] = set()
    duplicates: list[dict] = []
    for index, feature in enumerate(features, start=1):
        errors = _zone_errors(feature)
        props = feature.get("properties") if isinstance(feature, dict) else {}
        if not isinstance(props, dict):
            props = {}
        zone_id = str(props.get("id") or props.get("zone_id") or "")
        if errors:
            invalid.append({"row": index, "id": zone_id, "errors": errors})
            continue
        if zone_id in seen:
            duplicates.append({"row": index, "id": zone_id})
            continue
        seen.add(zone_id)
        valid.append(
            {
                "id": zone_id,
                "name": props.get("name") or zone_id,
                "kind": str(props.get("kind")),
                "geojson": feature.get("geometry"),
                "active_hours": props.get("active_hours"),
            }
        )
    return _preview(valid, invalid, duplicates)


def _zone_errors(feature: object) -> list[str]:
    if not isinstance(feature, dict):
        return ["feature must be an object"]
    errors: list[str] = []
    raw_props = feature.get("properties")
    props = raw_props if isinstance(raw_props, dict) else {}
    zone_id = str(props.get("id") or props.get("zone_id") or "")
    if not zone_id:
        errors.append("feature id is required")
    kind = props.get("kind")
    if kind not in _ZONE_KINDS:
        errors.append("kind must be sensitive, restricted, or ward")
    geom = feature.get("geometry")
    if not isinstance(geom, dict) or geom.get("type") not in {"Polygon", "MultiPolygon"}:
        errors.append("geometry must be a Polygon or MultiPolygon")
        return errors
    coords = geom.get("coordinates")
    if not _rings_ok(coords, geom["type"]):
        errors.append("geometry coordinates are invalid")
    return errors


def _rings_ok(coords: object, kind: str) -> bool:
    rings = coords if kind == "MultiPolygon" else [coords]
    if not isinstance(rings, list) or not rings:
        return False
    for polygon in rings:
        if not isinstance(polygon, list) or not polygon:
            return False
        for ring in polygon:
            if not isinstance(ring, list) or len(ring) < 4:
                return False
            if ring[0] != ring[-1]:
                return False
            for point in ring:
                if not isinstance(point, list) or len(point) < 2:
                    return False
                try:
                    lng, lat = float(point[0]), float(point[1])
                except (TypeError, ValueError):
                    return False
                if not (-180 <= lng <= 180 and -90 <= lat <= 90):
                    return False
    return True
