"""Multi-camera OCR fleet runner.

Reads a YAML/JSON camera list and runs one OCRPipeline per camera
(concurrent threads). Designed for mixed file + RTSP sources.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anpr_common.config import Settings, get_settings

from ocr_engine.pipeline import OCRPipeline

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CameraSource:
    id: str
    source: str
    heading_deg: float = 0.0
    lanes: int = 3
    enabled: bool = True


def load_camera_fleet(path: str | Path) -> list[CameraSource]:
    """Load camera fleet config from YAML or JSON.

    Expected shape::

        cameras:
          - id: cam-001
            source: rtsp://...
            heading_deg: 90
            lanes: 3
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    data: Any
    if p.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError(
                "PyYAML is required for YAML fleet configs. Use JSON or `uv add pyyaml`."
            ) from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)

    if isinstance(data, dict) and "cameras" in data:
        rows = data["cameras"]
    elif isinstance(data, list):
        rows = data
    else:
        raise ValueError("Fleet config must be a list or {cameras: [...]}")

    out: list[CameraSource] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("enabled", True) is False:
            continue
        cam_id = str(row["id"])
        source = str(row["source"])
        out.append(
            CameraSource(
                id=cam_id,
                source=source,
                heading_deg=float(row.get("heading_deg", 0.0)),
                lanes=int(row.get("lanes", 3)),
                enabled=True,
            )
        )
    return out


def _run_one(
    cam: CameraSource,
    settings: Settings,
    dry_run: bool,
    backend: str | None,
    max_frames: int | None,
) -> tuple[str, int, str | None]:
    try:
        pipeline = OCRPipeline(
            settings=settings,
            camera_id=cam.id,
            camera_heading_deg=cam.heading_deg,
            dry_run=dry_run,
            backend=backend,
            num_lanes=cam.lanes,
        )
        lower = cam.source.lower()
        if lower.endswith((".png", ".jpg", ".jpeg", ".bmp", ".webp")):
            count = pipeline.run_image(cam.source)
        else:
            count = pipeline.run(cam.source, max_frames=max_frames)
        return cam.id, count, None
    except Exception as exc:
        logger.exception("Camera %s failed", cam.id)
        return cam.id, 0, str(exc)


def run_fleet(
    config_path: str | Path,
    *,
    dry_run: bool = False,
    backend: str | None = None,
    max_frames: int | None = None,
    max_workers: int | None = None,
    settings: Settings | None = None,
) -> dict[str, int]:
    """Run OCR on every camera in the fleet concurrently. Returns emitted counts."""
    settings = settings or get_settings()
    cameras = load_camera_fleet(config_path)
    if not cameras:
        raise RuntimeError(f"No enabled cameras in fleet config: {config_path}")

    workers = max_workers or min(8, len(cameras))
    results: dict[str, int] = {}
    logger.info("Starting fleet of %d camera(s) with %d workers", len(cameras), workers)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_run_one, cam, settings, dry_run, backend, max_frames): cam
            for cam in cameras
        }
        for fut in as_completed(futures):
            cam_id, count, err = fut.result()
            results[cam_id] = count
            if err:
                logger.error("Camera %s error: %s", cam_id, err)
            else:
                logger.info("Camera %s emitted %d read(s)", cam_id, count)
    return results
