"""Untrusted upload checks. Magic bytes win over the client MIME type and filename."""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")

VIDEO_EXT = {
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "avi": "video/x-msvideo",
    "mkv": "video/x-matroska",
    "webm": "video/webm",
}
IMAGE_EXT = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
TABLE_EXT = {"csv": "text/csv", "json": "application/json", "geojson": "application/geo+json"}


def safe_filename(name: str) -> str:
    base = (name or "upload").replace("\\", "/").split("/")[-1]
    base = base.replace("..", "")
    cleaned = _SAFE.sub("_", base).strip("._")
    return (cleaned or "upload")[:180]


def extension_of(name: str) -> str:
    safe = safe_filename(name).lower()
    if "." not in safe:
        return ""
    return safe.rsplit(".", 1)[-1]


def sniff_media(data: bytes) -> str | None:
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in {b"qt  ", b"moov"}:
            return "video/quicktime"
        return "video/mp4"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm" if b"webm" in data[:64] else "video/x-matroska"
    if data.startswith(b"RIFF") and data[8:12] == b"AVI ":
        return "video/x-msvideo"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_upload(
    *,
    filename: str,
    data: bytes,
    kind: str,
    max_bytes: int,
    claimed_type: str | None = None,
) -> dict:
    """Return {filename, mime, ext} or raise ValueError."""
    if len(data) == 0:
        raise ValueError("empty file")
    if len(data) > max_bytes:
        raise ValueError(f"file exceeds {max_bytes} bytes")
    if "\x00" in filename or ".." in filename.replace("\\", "/"):
        raise ValueError("unsafe filename")
    safe = safe_filename(filename)
    ext = extension_of(safe)
    if kind == "video":
        allowed = VIDEO_EXT
        sniffed = sniff_media(data)
        if ext not in allowed:
            raise ValueError("unsupported video extension")
        if sniffed not in set(allowed.values()):
            raise ValueError("file content is not a supported video")
        mime = sniffed
    elif kind == "image":
        allowed = IMAGE_EXT
        sniffed = sniff_media(data)
        if ext not in allowed:
            raise ValueError("unsupported image extension")
        if sniffed not in set(allowed.values()):
            raise ValueError("file content is not a supported image")
        mime = sniffed
    elif kind in {"csv", "watchlist", "registry", "cameras"}:
        if ext != "csv":
            raise ValueError("expected a .csv file")
        if data.startswith((b"\xff\xd8", b"\x89PNG")) or data[4:8] == b"ftyp":
            raise ValueError("csv content looks like media")
        mime = "text/csv"
    elif kind in {"json", "calibration", "cameras_json"}:
        if ext != "json":
            raise ValueError("expected a .json file")
        mime = "application/json"
    elif kind in {"geojson", "zones"}:
        if ext not in {"json", "geojson"}:
            raise ValueError("expected GeoJSON")
        mime = "application/geo+json"
    else:
        raise ValueError(f"unknown upload kind {kind}")
    # Client MIME is advisory. Reject only an obvious contradiction for media.
    claimed = claimed_type.split(";")[0].strip().lower() if claimed_type else ""
    if (
        claimed_type
        and claimed not in {mime, "application/octet-stream", ""}
        and kind in {"video", "image"}
        and not claimed_type.lower().startswith(mime.split("/")[0])
    ):
        raise ValueError("declared content type does not match file bytes")
    return {"filename": safe, "mime": mime, "ext": ext, "size": len(data)}


def object_key(prefix: str, object_id: str, filename: str) -> str:
    safe_prefix = "/".join(part for part in prefix.split("/") if part and part not in {".", ".."})
    return f"{safe_prefix}/{object_id}/{safe_filename(filename)}"


def redact_stream_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url)
    if not parts.scheme:
        return None
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))
