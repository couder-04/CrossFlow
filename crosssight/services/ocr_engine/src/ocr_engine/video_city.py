"""Play traffic files as live cameras and read plates from them.

Every file loops on the camera wall. A small pool of OCR workers rotates
through the files so plate reads are produced from the footage itself.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from pathlib import Path

import cv2
from anpr_common.config import get_settings

from ocr_engine.fleet import CameraSource, load_camera_fleet
from ocr_engine.pipeline import MinioUploader, OCRPipeline, encode_preview_jpeg

logger = logging.getLogger(__name__)


def _publish_frame(
    uploader: MinioUploader, redis_client: list, settings, camera_id: str, frame
) -> None:
    jpeg, _scale = encode_preview_jpeg(frame)
    key = f"frames/latest/{camera_id}.jpg"
    uploader.upload_jpeg(key, jpeg, cache_control="no-store")
    payload = json.dumps(
        {
            "camera_id": camera_id,
            "key": key,
            "tracks": [],
        }
    )
    try:
        import redis

        if redis_client[0] is None:
            redis_client[0] = redis.Redis.from_url(settings.redis_url)
        redis_client[0].publish("frames", payload)
    except Exception:
        logger.warning("Frame notify failed for %s", camera_id, exc_info=True)


def _stream_camera(
    cam: CameraSource,
    busy: set[str],
    lock: threading.Lock,
    stop: threading.Event,
) -> None:
    """Replay a file at its own frame rate and publish a preview JPEG."""
    settings = get_settings()
    try:
        uploader = MinioUploader(settings)
    except Exception:
        logger.exception("Camera %s cannot reach frame storage", cam.id)
        return
    redis_client: list = [None]
    while not stop.is_set():
        cap = cv2.VideoCapture(cam.source)
        if not cap.isOpened():
            logger.warning("Cannot open %s; retrying", cam.source)
            if stop.wait(2.0):
                return
            continue
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
        delay = 1.0 / max(min(fps, 30.0), 1.0)
        next_publish = 0.0
        while not stop.is_set():
            ok, frame = cap.read()
            if not ok:
                break
            now = time.monotonic()
            with lock:
                held = cam.id in busy
            if not held and now >= next_publish:
                next_publish = now + 0.25
                try:
                    _publish_frame(uploader, redis_client, settings, cam.id, frame)
                except Exception:
                    logger.warning("Preview publish failed for %s", cam.id, exc_info=True)
            if stop.wait(delay):
                cap.release()
                return
        cap.release()


def _ocr_loop(
    pending: queue.Queue[CameraSource],
    busy: set[str],
    lock: threading.Lock,
    stop: threading.Event,
    stride: int,
) -> None:
    settings = get_settings()
    pipeline: OCRPipeline | None = None
    while not stop.is_set():
        try:
            cam = pending.get(timeout=0.5)
        except queue.Empty:
            continue
        with lock:
            busy.add(cam.id)
        try:
            if pipeline is None:
                pipeline = OCRPipeline(
                    settings=settings,
                    camera_id=cam.id,
                    camera_heading_deg=cam.heading_deg,
                    num_lanes=cam.lanes,
                )
            pipeline.camera_id = cam.id
            pipeline.camera_heading_deg = cam.heading_deg
            pipeline.num_lanes = max(1, cam.lanes)
            count = pipeline.run(cam.source, reconnect=False, ocr_every=stride, release=False)
            logger.info("Camera %s emitted %d plate read(s) this pass", cam.id, count)
        except Exception:
            logger.exception("OCR pass failed for %s", cam.id)
        finally:
            with lock:
                busy.discard(cam.id)
            pending.put(cam)


def run_video_city(
    config_path: str | Path,
    *,
    workers: int | None = None,
    stride: int | None = None,
) -> None:
    cameras = load_camera_fleet(config_path)
    if not cameras:
        raise RuntimeError(f"No cameras in {config_path}")
    worker_count = (
        workers if workers is not None else int(os.environ.get("VIDEO_FEED_WORKERS", "2"))
    )
    frame_stride = stride if stride is not None else int(os.environ.get("VIDEO_FEED_STRIDE", "3"))
    worker_count = max(1, min(worker_count, len(cameras)))
    frame_stride = max(1, frame_stride)
    stop = threading.Event()
    busy: set[str] = set()
    lock = threading.Lock()
    pending: queue.Queue[CameraSource] = queue.Queue()
    for cam in cameras:
        pending.put(cam)
    logger.info(
        "Video city: %d camera(s), %d OCR worker(s), stride %d",
        len(cameras),
        worker_count,
        frame_stride,
    )
    for cam in cameras:
        threading.Thread(
            target=_stream_camera,
            args=(cam, busy, lock, stop),
            name=f"stream-{cam.id}",
            daemon=True,
        ).start()
    for index in range(worker_count):
        threading.Thread(
            target=_ocr_loop,
            args=(pending, busy, lock, stop, frame_stride),
            name=f"ocr-{index}",
            daemon=True,
        ).start()
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        stop.set()
