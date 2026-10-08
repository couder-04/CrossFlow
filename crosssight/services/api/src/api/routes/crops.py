"""Presigned MinIO crop URLs for trajectory / alert evidence."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query, status

from api.deps import MinioPresignDep, SettingsDep, UserDep

router = APIRouter(prefix="/crops", tags=["crops"])

# The OCR engine writes crops as ``<camera_id>/<event_id>.jpg`` (ocr_engine.pipeline
# ``upload_crop``): there is no fixed crop prefix, so the key shape is the allow-list. Every
# other object in the bucket lives under a reserved top-level prefix or is deeper than two
# segments (``exports/<id>/<file>``, ``uploads/<kind>/<id>/<file>``, ``frames/latest/<cam>.jpg``).
# Those are served by routes that enforce RBAC, expiry and audit, so /crops must never sign them.
_RESERVED_PREFIXES = frozenset({"exports", "uploads", "imports", "frames", "evidence", "clips"})


def _safe_key(key: str) -> str:
    cleaned = key.lstrip("/")
    if not cleaned or ".." in cleaned.split("/") or "\\" in cleaned:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid crop key")
    parts = cleaned.split("/")
    if (
        len(parts) != 2
        or not all(parts)
        or parts[0].lower() in _RESERVED_PREFIXES
        or not parts[1].lower().endswith(".jpg")
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a crop key")
    return cleaned


@router.get("")
async def crop_url(
    settings: SettingsDep,
    minio: MinioPresignDep,
    _user: UserDep,
    key: str = Query(..., description="MinIO object key for a plate crop"),
) -> dict[str, str]:
    """Return a short-lived presigned GET URL for a plate crop."""
    crop_key = _safe_key(key)
    try:
        url = minio.presigned_get_object(
            settings.minio_bucket,
            crop_key,
            expires=timedelta(hours=1),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Crop not found or MinIO unavailable",
        ) from exc
    return {"key": crop_key, "url": url}
