"""Image and video processing that reuses PlateOCR. Uploaded files are never executed."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_recognizer: Any = None
_recognizer_lock = threading.Lock()


def _get_recognizer() -> Any:
    """One PlateOCR recognizer per API process, configured from Settings like the OCR engine."""
    global _recognizer
    with _recognizer_lock:
        if _recognizer is None:
            from anpr_common.config import get_settings
            from ocr_engine.plateocr_backend import PlateOCRRecognizer

            settings = get_settings()
            _recognizer = PlateOCRRecognizer(
                detector_model=settings.plateocr_detector,
                ocr_model=settings.plateocr_ocr_model,
                device=settings.plateocr_device,
                det_conf=settings.plateocr_det_conf,
                min_ocr_conf=settings.plateocr_min_ocr_conf,
                ocr_config=settings.plateocr_ocr_config or None,
                plate_format=settings.plateocr_plate_format or None,
                tta=settings.plateocr_tta,
                bbox_pad=settings.plateocr_bbox_pad,
            )
    return _recognizer


def detect_plates_bgr(image: Any) -> list[dict[str, Any]]:
    """Detect and read every plate in a full photo/frame. Raises if weights cannot be loaded.

    Uses the detector first: running plate OCR on a whole photo returns an invented plate,
    because the format-constrained decoder always produces some valid-looking string.
    Reads below ``PLATEOCR_MIN_OCR_CONF`` are already dropped by the reader.
    """
    hits = _get_recognizer().read_scene(image)
    return [
        {
            "plate": hit.text,
            "confidence": float(hit.ocr_confidence),
            "char_probs": [float(value) for value in (hit.char_probs or [])],
            "bbox": list(hit.bbox),
        }
        for hit in hits
    ]


def decode_image(data: bytes) -> Any:
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("could not decode image")
    return image


def sample_video(path: str, *, every_n: int = 15, limit: int = 40) -> tuple[list[Any], float]:
    import cv2

    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise ValueError("could not open video")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0) or 10.0
    frames: list[Any] = []
    index = 0
    try:
        while len(frames) < limit:
            ok, frame = capture.read()
            if not ok:
                break
            if index % every_n == 0:
                frames.append(frame)
            index += 1
    finally:
        capture.release()
    if not frames:
        raise ValueError("video contained no readable frames")
    return frames, fps


def reads_from_frames(
    frames: list[Any],
    *,
    camera_id: str,
    started_at: datetime,
    source_key: str,
    fps: float,
    every_n: int,
    detect: Callable[[Any], list[dict[str, Any]]] = detect_plates_bgr,
) -> list[dict[str, Any]]:
    """Turn sampled frames into plate-read records, one per distinct plate in the clip.

    The same vehicle usually appears in many sampled frames; each plate text is reported once,
    using the frame where it was read with the highest confidence.
    """
    from datetime import timedelta

    step_s = every_n / fps if fps else 1.0
    best: dict[str, dict[str, Any]] = {}
    for index, frame in enumerate(frames):
        try:
            found = detect(frame)
        except Exception as exc:  # noqa: BLE001
            logger.warning("frame OCR failed: %s", exc)
            continue
        for plate_hit in found:
            plate = str(plate_hit.get("plate") or "")
            if not plate or plate == "UNKNOWN":
                continue
            confidence = float(plate_hit.get("confidence") or 0.0)
            if plate in best and best[plate]["confidence"] >= confidence:
                continue
            best[plate] = {
                "camera_id": camera_id,
                "ts": (started_at + timedelta(seconds=index * step_s)).isoformat(),
                "plate": plate,
                "confidence": confidence,
                "source_video_key": source_key,
                "frame_index": index * every_n,
            }
    return sorted(best.values(), key=lambda read: read["frame_index"])
