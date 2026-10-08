"""Shared helpers for uploads, imports, exports, and evidence."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import aiofiles
from anpr_common.config import get_settings
from anpr_common.intelligence.access import role_can
from anpr_common.intelligence.exporters import rows_to_csv, rows_to_json, rows_to_pdf
from anpr_common.intelligence.filesafety import object_key
from anpr_common.intelligence.importers import (
    preview_calibration,
    preview_cameras,
    preview_registry,
    preview_watchlist,
    preview_zones,
)
from fastapi import HTTPException, UploadFile, status
from geoalchemy2 import WKTElement
from shapely.geometry import shape
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from api.db import (
    AlertRow,
    Camera,
    CameraCalibrationRow,
    ExportRow,
    UploadRow,
    VehicleRegistryRow,
    WatchlistEntry,
    Zone,
    get_session_factory,
)
from api.deps import get_clickhouse, get_minio
from api.media import decode_image, detect_plates_bgr, reads_from_frames, sample_video

logger = logging.getLogger(__name__)

_IMPORT_ACTION = {
    "watchlist": "import_watchlist",
    "registry": "import_registry",
    "cameras": "import_cameras",
    "calibration": "import_calibration",
    "zones": "import_zones",
}
_MEDIA_ACTION = {"video": "upload_video", "image": "upload_image"}
_EXPORT_KINDS = {
    "traffic",
    "od",
    "travel",
    "dwell",
    "vehicles",
    "incidents",
    "camera_health",
    "enforcement",
    "sightings",
    "investigation",
    "watchlist",
    "registry",
    "flow",
}
_UPLOAD_CHUNK = 1024 * 1024


def _forbid(role: str, action: str) -> None:
    if not role_can(role, action):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted")


async def _read_limited(upload: UploadFile, max_bytes: int) -> bytes:
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File is too large"
        )
    return data


def _store(minio, settings, key: str, data: bytes, content_type: str) -> None:
    if not minio.bucket_exists(settings.minio_bucket):
        minio.make_bucket(settings.minio_bucket)
    minio.put_object(
        settings.minio_bucket, key, io.BytesIO(data), length=len(data), content_type=content_type
    )


def _declared_length(upload: UploadFile) -> int | None:
    size = getattr(upload, "size", None)
    if isinstance(size, int) and size >= 0:
        return size
    headers = getattr(upload, "headers", None)
    raw = headers.get("content-length") if headers is not None else None
    if raw is not None and str(raw).isdigit():
        return int(raw)
    return None


async def _stream_upload(upload: UploadFile, max_bytes: int) -> tuple[Path, int, str, bytes]:
    """Write the upload to a temp file in 1 MiB chunks. Returns path, size, sha256, header."""
    declared = _declared_length(upload)
    if declared is not None and declared > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File is too large"
        )
    fd, name = tempfile.mkstemp(prefix="anpr-upload-")
    os.close(fd)
    path = Path(name)
    hasher = hashlib.sha256()
    total = 0
    header = b""
    try:
        async with aiofiles.open(path, "wb") as handle:
            while True:
                chunk = await upload.read(_UPLOAD_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="File is too large",
                    )
                if len(header) < 64:
                    header += chunk[: 64 - len(header)]
                hasher.update(chunk)
                await handle.write(chunk)
    except HTTPException:
        path.unlink(missing_ok=True)
        raise
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path, total, hasher.hexdigest(), header


def _store_file(minio, settings, key: str, path: Path, length: int, content_type: str) -> None:
    if not minio.bucket_exists(settings.minio_bucket):
        minio.make_bucket(settings.minio_bucket)
    with path.open("rb") as handle:
        minio.put_object(
            settings.minio_bucket, key, handle, length=length, content_type=content_type
        )


def _upload_out(row: UploadRow) -> dict[str, Any]:
    preview = row.preview or {}
    return {
        "id": str(row.id),
        "kind": row.kind,
        "filename": row.filename,
        "status": row.status,
        "uploaded_by": row.uploaded_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "size_bytes": row.size_bytes,
        "camera_id": row.camera_id,
        "error": row.error,
        "valid_count": preview.get("valid_count"),
        "invalid_count": preview.get("invalid_count"),
        "duplicate_count": preview.get("duplicate_count"),
        "result": row.result or {},
    }


async def _build_preview(session, kind: str, text: str, as_json: bool) -> dict:
    if kind == "watchlist":
        existing = {
            row.plate_norm for row in (await session.execute(select(WatchlistEntry))).scalars()
        }
        return preview_watchlist(text, existing)
    if kind == "registry":
        existing = {
            row.plate_norm for row in (await session.execute(select(VehicleRegistryRow))).scalars()
        }
        return preview_registry(text, existing)
    if kind == "cameras":
        existing = {row[0] for row in (await session.execute(select(Camera.id))).all()}
        return preview_cameras(text, fmt="json" if as_json else "csv", existing=existing)
    if kind == "calibration":
        return preview_calibration(text)
    return preview_zones(text)


def _counts(preview: dict) -> dict[str, int]:
    return {
        "valid": int(preview.get("valid_count") or 0),
        "invalid": int(preview.get("invalid_count") or 0),
        "duplicates": int(preview.get("duplicate_count") or 0),
    }


async def _apply_preview(session, kind: str, preview: dict, username: str, overwrite: bool) -> int:
    applied = 0
    if kind == "watchlist":
        for item in preview.get("valid") or []:
            if await session.get(WatchlistEntry, item["plate_norm"]) is not None:
                continue
            session.add(
                WatchlistEntry(
                    plate_norm=item["plate_norm"],
                    reason=item["reason"],
                    severity=item["severity"],
                    added_by=username,
                    expires_at=_dt(item.get("valid_until")),
                    notes=item.get("notes"),
                    priority=item.get("severity"),
                )
            )
            applied += 1
    elif kind == "registry":
        for item in preview.get("valid") or []:
            current = await session.get(VehicleRegistryRow, item["plate_norm"])
            payload = dict(item)
            payload["updated_by"] = username
            payload["valid_from"] = _dt(item.get("valid_from"))
            payload["valid_until"] = _dt(item.get("valid_until"))
            if current is None:
                session.add(VehicleRegistryRow(**payload))
            else:
                for key, value in payload.items():
                    setattr(current, key, value)
            applied += 1
    elif kind == "cameras":
        records = list(preview.get("valid") or [])
        if overwrite:
            records.extend(item for item in preview.get("duplicates") or [] if item.get("exists"))
        for item in records:
            await _upsert_camera(session, item)
            applied += 1
    elif kind == "calibration":
        for item in preview.get("valid") or []:
            current = await session.get(CameraCalibrationRow, item["camera_id"])
            if current is None:
                session.add(
                    CameraCalibrationRow(
                        camera_id=item["camera_id"],
                        homography=item.get("homography"),
                        coordinate_reference=item.get("coordinate_reference"),
                        lanes=item.get("lanes"),
                        speed_calibration=item.get("speed_calibration"),
                        updated_by=username,
                    )
                )
            else:
                current.homography = item.get("homography")
                current.coordinate_reference = item.get("coordinate_reference")
                current.lanes = item.get("lanes")
                current.speed_calibration = item.get("speed_calibration")
                current.updated_by = username
            applied += 1
    elif kind == "zones":
        for item in preview.get("valid") or []:
            geom = WKTElement(shape(item["geojson"]).wkt, srid=4326)
            current = await session.get(Zone, item["id"])
            if current is None:
                session.add(
                    Zone(
                        id=item["id"],
                        name=item["name"],
                        kind=item["kind"],
                        geom=geom,
                        active_hours=item.get("active_hours"),
                    )
                )
            else:
                current.name = item["name"]
                current.kind = item["kind"]
                current.geom = geom
                current.active_hours = item.get("active_hours")
            applied += 1
    return applied


async def _upsert_camera(session, item: dict) -> None:
    current = await session.get(Camera, item["camera_id"])
    geom = WKTElement(f"POINT({item['longitude']} {item['latitude']})", srid=4326)
    if current is None:
        session.add(
            Camera(
                id=item["camera_id"],
                name=item["name"],
                geom=geom,
                allowed_direction=item.get("direction"),
                status=item.get("status") or "active",
                ops_config={"stream_ref": item.get("stream_ref"), "zone": item.get("zone")},
            )
        )
        return
    current.name = item["name"]
    current.geom = geom
    current.allowed_direction = item.get("direction")
    current.status = item.get("status") or current.status
    current.ops_config = {
        **(current.ops_config or {}),
        "stream_ref": item.get("stream_ref"),
        "zone": item.get("zone"),
    }


def _dt(value: str | None):
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def read_stored(minio, settings, key: str) -> bytes:
    """Read an object and return the MinIO connection to the pool."""
    obj = minio.get_object(settings.minio_bucket, key)
    try:
        return obj.read()
    finally:
        obj.close()
        obj.release_conn()


async def _process_media(upload_id: UUID) -> None:
    settings = get_settings()
    factory = get_session_factory()
    started = datetime.now(UTC)
    async with factory() as session:
        row = await session.get(UploadRow, upload_id)
        if row is None or not row.object_key:
            return
        row.status = "processing"
        row.updated_at = started
        await session.commit()
        try:
            client = get_minio(settings)
            obj = client.get_object(settings.minio_bucket, row.object_key)
            data = obj.read()
            obj.close()
            obj.release_conn()
            result: dict[str, Any]
            if row.kind == "image":
                ts = (row.captured_at or started).isoformat()
                result = {
                    "reads": [
                        {
                            "plate": found["plate"],
                            "confidence": found["confidence"],
                            "ts": ts,
                            "camera_id": row.camera_id,
                        }
                        for found in detect_plates_bgr(decode_image(data))
                    ],
                    "source_key": row.object_key,
                }
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "clip"
                    path.write_bytes(data)
                    frames, fps = sample_video(str(path))
                    result = {
                        "reads": reads_from_frames(
                            frames,
                            camera_id=row.camera_id or "",
                            started_at=row.captured_at or started,
                            source_key=row.object_key,
                            fps=fps,
                            every_n=15,
                        ),
                        "frames": len(frames),
                        "source_key": row.object_key,
                    }
            try:
                result["published"] = await _publish_reads(settings, result["reads"], row)
                row.status = "completed"
                row.error = None
            except Exception as exc:  # noqa: BLE001
                result["publish_error"] = str(exc)[:500]
                row.status = "failed"
                row.error = f"OCR finished but Kafka publish failed: {exc}"[:500]
            row.result = result
        except Exception as exc:
            logger.exception("media processing failed")
            row.status = "failed"
            row.error = str(exc)[:500]
        row.updated_at = datetime.now(UTC)
        row.result = {
            **(row.result or {}),
            "duration_s": (row.updated_at - started).total_seconds(),
        }
        await session.commit()


async def _publish_reads(settings, reads: list[dict], row: UploadRow) -> int:
    publishable = [
        item
        for item in reads
        if item.get("plate") and item.get("plate") != "UNKNOWN" and item.get("ts")
    ]
    if not publishable:
        return 0
    from aiokafka import AIOKafkaProducer
    from anpr_common.grammar import normalize_plate
    from anpr_common.schemas import PlateRead

    producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap)
    await producer.start()
    sent = 0
    try:
        for item in publishable:
            grammar = normalize_plate(str(item.get("plate") or ""))
            if not grammar.norm:
                continue
            confidence = float(item.get("confidence") or 0)
            event = PlateRead(
                camera_id=row.camera_id or item.get("camera_id") or "cam-upload",
                ts=datetime.fromisoformat(item["ts"]),
                plate_raw=grammar.norm,
                plate_norm=grammar.norm,
                plate_valid=grammar.valid,
                plate_format=grammar.format,  # type: ignore[arg-type]
                confidence=min(1.0, max(0.0, confidence)),
                source="ocr",
                source_video_key=row.object_key,
            )
            await producer.send_and_wait(settings.topic_reads, event.model_dump_json().encode())
            sent += 1
    finally:
        await producer.stop()
    return sent


async def _run_export(export_id: UUID, role: str) -> None:
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(ExportRow, export_id)
        if row is None:
            return
        row.status = "processing"
        await session.commit()
        try:
            headers, records, sections = await _collect(
                session,
                await run_in_threadpool(get_clickhouse, settings),
                settings,
                row.kind,
                row.filters,
                role,
            )
            if row.format == "csv":
                payload = rows_to_csv(headers, records)
                content_type = "text/csv; charset=utf-8"
            elif row.format == "json":
                payload = rows_to_json(
                    {"kind": row.kind, "filters": row.filters, "headers": headers, "rows": records}
                )
                content_type = "application/json"
            else:
                payload = rows_to_pdf(
                    row.kind.replace("_", " ").title(),
                    [
                        f"Generated {datetime.now(UTC).isoformat()}",
                        f"Filters {json.dumps(row.filters, default=str)}",
                    ],
                    sections,
                )
                content_type = "application/pdf"
            key = object_key("exports", str(row.id), row.filename or "export.bin")
            _store(get_minio(settings), settings, key, payload, content_type)
            row.object_key = key
            row.status = "ready"
            row.error = None
        except Exception as exc:
            logger.exception("export failed")
            row.status = "failed"
            row.error = str(exc)[:500]
        await session.commit()


async def _collect(session, ch, settings, kind: str, filters: dict, role: str):
    from api.auth import Role, UserContext
    from api.routes.platform import camera_health, camera_od, dwell, travel_times, vehicle_classes

    actor = UserContext(id="", username="export", role=Role(role))
    row_limit = min(5000, max(1, int(settings.export_sync_row_limit)))

    start = _dt(filters.get("start")) if filters.get("start") else None
    end = _dt(filters.get("end")) if filters.get("end") else None
    if kind in {"od", "flow"}:
        data = await camera_od(ch, session, settings, actor, start, end)
        headers = ["origin", "destination", "trip_count", "unique_vehicles"]
        records = [
            [c["origin"], c["destination"], c["trip_count"], c["unique_vehicles"]]
            for c in data["cells"]
        ]
    elif kind == "travel":
        data = await travel_times(
            ch, settings, actor, start, end, filters.get("origin"), filters.get("destination")
        )
        headers = ["origin", "destination", "count", "avg_s", "median_s", "min_s", "max_s", "p90_s"]
        records = [[r.get(h) for h in headers] for r in data["routes"]]
    elif kind == "dwell":
        data = await dwell(
            ch, settings, actor, start, end, filters.get("camera_id"), limit=row_limit
        )
        headers = ["plate", "camera_id", "dwell_s", "classification", "entry_ts", "exit_ts"]
        records = [[s.get(h) for h in headers] for s in data["sessions"]]
    elif kind in {"vehicles", "traffic"}:
        data = await vehicle_classes(ch, actor, start, end, filters.get("camera_id"))
        headers = ["vehicle_class", "count", "share"]
        records = [[k, data["counts"].get(k, 0), data["shares"].get(k, 0)] for k in data["counts"]]
    elif kind == "camera_health":
        data = await camera_health(ch, session, settings, actor, start, end)
        headers = ["camera_id", "state", "last_read", "age_s", "read_rate_per_min"]
        records = [[c[h] for h in headers] for c in data["cameras"]]
    elif kind in {"incidents", "enforcement"}:
        stmt = select(AlertRow).order_by(AlertRow.created_at.desc()).limit(5000)
        if filters.get("status"):
            stmt = stmt.where(AlertRow.status == filters["status"])
        if filters.get("type"):
            stmt = stmt.where(AlertRow.type == filters["type"])
        rows = list((await session.execute(stmt)).scalars())
        headers = ["id", "type", "status", "severity", "plate_norm", "created_at"]
        records = [
            [str(r.id), r.type, r.status, r.severity, r.plate_norm, r.created_at.isoformat()]
            for r in rows
        ]
        data = {"rows": len(records)}
    elif kind == "watchlist":
        rows = list((await session.execute(select(WatchlistEntry))).scalars())
        headers = ["plate_norm", "reason", "severity", "expires_at"]
        records = [
            [r.plate_norm, r.reason, r.severity, r.expires_at.isoformat() if r.expires_at else ""]
            for r in rows
        ]
        data = {}
    elif kind == "registry":
        rows = list((await session.execute(select(VehicleRegistryRow))).scalars())
        headers = [
            "plate_norm",
            "vehicle_class",
            "make",
            "model",
            "color",
            "registration_status",
            "owner_ref",
        ]
        records = [[getattr(r, h) for h in headers] for r in rows]
        data = {}
    elif kind == "sightings":
        from api.routes.platform import _naive, _query, _window

        begin, finish = _window(start, end)
        params: dict[str, Any] = {
            "start": _naive(begin),
            "end": _naive(finish),
            "limit": row_limit,
        }
        where = "ts >= {start:DateTime} AND ts < {end:DateTime}"
        if filters.get("camera_id"):
            where += " AND camera_id = {camera:String}"
            params["camera"] = filters["camera_id"]
        rows = await _query(
            ch,
            f"""
            SELECT camera_id, ts, plate_norm, confidence, vehicle_class
            FROM anpr_reads
            WHERE {where}
            ORDER BY ts DESC
            LIMIT {{limit:UInt32}}
            """,
            params,
        )
        headers = ["camera_id", "ts", "plate_norm", "confidence", "vehicle_class"]
        records = [[row[0], str(row[1]), row[2], float(row[3] or 0), row[4]] for row in rows]
        data = {"rows": len(records)}
    else:
        plate = str(filters.get("plate") or "")
        if not plate:
            raise ValueError("investigation export requires a plate filter")
        from api.routes.platform import investigation

        data = await investigation(
            ch,
            session,
            actor,
            plate,
            filters.get("camera_id"),
            start,
            end,
            limit=row_limit,
        )
        headers = ["camera_id", "ts", "plate_norm", "confidence", "vehicle_class"]
        records = [[s.get(h) for h in headers] for s in data["sightings"]]
    lines = [", ".join(str(cell) for cell in record) for record in records[:40]]
    sections = [(kind, lines or ["No rows for the selected filters"])]
    return headers, records, sections


def _export_out(row: ExportRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "format": row.format,
        "status": row.status,
        "requested_by": row.requested_by,
        "requested_at": row.created_at.isoformat() if row.created_at else None,
        "filters": row.filters or {},
        "filename": row.filename,
        "error": row.error,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }
