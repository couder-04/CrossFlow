"""Presigned MinIO crop URLs for trajectory / alert evidence."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query, status

from api.deps import MinioPresignDep, SettingsDep, UserDep

router = APIRouter(prefix="/crops", tags=["crops"])


def _safe_key(key: str) -> str:
    cleaned = key.lstrip("/")
    if not cleaned or ".." in cleaned.split("/") or cleaned.startswith("\\"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid crop key")
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
