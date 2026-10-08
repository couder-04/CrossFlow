"""Synthetic latest-frame JPEGs so the camera wall works without RTSP."""

from __future__ import annotations

import io
import json
import logging
import time
from datetime import datetime
from typing import Any

import cv2
import numpy as np

logger = logging.getLogger(__name__)

FRAME_W = 960
FRAME_H = 540


def render_synthetic_frame(
    *,
    camera_name: str,
    ts: datetime,
    plate_norm: str,
    vehicle_class: str,
    track_id: int,
    tick: int,
) -> tuple[np.ndarray, list[int]]:
    """960×540 preview: camera name, timestamp, and a moving labelled car."""
    frame = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8)
    frame[:] = (28, 32, 40)
    car_w, car_h = 200, 84
    x1 = 36 + (tick * 53) % max(1, FRAME_W - car_w - 72)
    y1 = 160 + (tick * 29) % 180
    x2, y2 = x1 + car_w, y1 + car_h
    cv2.rectangle(frame, (x1, y1), (x2, y2), (70, 160, 90), -1)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (120, 220, 140), 2)
    label = f"{plate_norm} • {vehicle_class} • #{track_id}"
    cv2.putText(
        frame,
        label,
        (x1, max(24, y1 - 10)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (230, 230, 230),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        frame,
        camera_name,
        (24, 36),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (210, 210, 210),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        frame,
        ts.isoformat(),
        (24, 64),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (170, 180, 190),
        1,
        cv2.LINE_AA,
    )
    return frame, [x1, y1, x2, y2]


def encode_jpeg(frame: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
    if not ok:
        raise RuntimeError("Failed to encode synthetic frame")
    return buf.tobytes()


class FramePublisher:
    """Overwrite frames/latest/{camera_id}.jpg and notify Redis, at most 2 Hz."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.enabled = bool(getattr(settings, "sim_publish_frames", True))
        self._last: dict[str, float] = {}
        self._ticks: dict[str, int] = {}
        self._minio: Any = None
        self._redis: Any = None

    def publish(self, read: Any, camera_name: str | None = None) -> None:
        if not self.enabled:
            return
        camera_id = read.camera_id
        now = time.monotonic()
        if now - self._last.get(camera_id, 0.0) < 0.5:
            return
        self._last[camera_id] = now
        self._ticks[camera_id] = self._ticks.get(camera_id, 0) + 1
        tick = self._ticks[camera_id]
        try:
            frame, bbox = render_synthetic_frame(
                camera_name=camera_name or camera_id,
                ts=read.ts,
                plate_norm=read.plate_norm,
                vehicle_class=read.vehicle_class.value,
                track_id=tick,
                tick=tick,
            )
            jpeg = encode_jpeg(frame)
            key = f"frames/latest/{camera_id}.jpg"
            self._put_jpeg(key, jpeg)
            direction = read.direction.value if read.direction else None
            payload = {
                "camera_id": camera_id,
                "ts": read.ts.isoformat(),
                "key": key,
                "tracks": [
                    {
                        "track_id": tick,
                        "plate_norm": read.plate_norm,
                        "vehicle_class": read.vehicle_class.value,
                        "bbox": bbox,
                        "confidence": read.confidence,
                        "lane": read.lane,
                        "direction": direction,
                    }
                ],
            }
            self._notify(payload)
        except Exception:
            logger.warning("Synthetic frame publish failed for %s", camera_id, exc_info=True)

    def close(self) -> None:
        if self._redis is not None:
            try:
                self._redis.close()
            except Exception:
                logger.debug("Redis frame client close failed", exc_info=True)
            self._redis = None

    def _put_jpeg(self, key: str, data: bytes) -> None:
        client = self._minio_client()
        bucket = self.settings.minio_bucket
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
        client.put_object(
            bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type="image/jpeg",
            metadata={"Cache-Control": "no-store"},
        )

    def _notify(self, payload: dict[str, Any]) -> None:
        client = self._redis_client()
        client.publish("frames", json.dumps(payload))

    def _minio_client(self) -> Any:
        if self._minio is None:
            from minio import Minio

            self._minio = Minio(
                self.settings.minio_endpoint,
                access_key=self.settings.minio_access_key,
                secret_key=self.settings.minio_secret_key,
                secure=self.settings.minio_secure,
            )
        return self._minio

    def _redis_client(self) -> Any:
        if self._redis is None:
            import redis

            self._redis = redis.Redis.from_url(self.settings.redis_url)
        return self._redis
