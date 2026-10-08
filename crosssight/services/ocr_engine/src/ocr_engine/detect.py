"""Vehicle and plate detection, rectification, and quality scoring."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

COCO_VEHICLE_IDS = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}

PLATE_DET_HELP = """
PLATE_DET_WEIGHTS is missing or invalid.

To run the OCR pipeline you need a trained plate detector (YOLO bbox or pose).

Options:
  1. Train your own:  python -m ocr_engine.train.train_plate_detector --data your_data.yaml
  2. Export a .pt file and set PLATE_DET_WEIGHTS=/path/to/plate_det.pt in .env
  3. Place weights under models/plate_det.pt and point PLATE_DET_WEIGHTS there

See services/ocr_engine/src/ocr_engine/train/train_plate_detector.py for the Ultralytics template.
"""


def validate_plate_weights(weights_path: str) -> Path:
    """Validate plate detector weights; log help and exit non-zero on failure."""
    if not weights_path or not weights_path.strip():
        logger.error("PLATE_DET_WEIGHTS is empty.%s", PLATE_DET_HELP)
        sys.exit(1)
    path = Path(weights_path).expanduser()
    if not path.is_file():
        logger.error("PLATE_DET_WEIGHTS not found: %s%s", path, PLATE_DET_HELP)
        sys.exit(1)
    return path


@dataclass
class PlateDetection:
    corners: np.ndarray  # shape (4, 2) float32
    confidence: float
    bbox: tuple[float, float, float, float]


@dataclass
class VehicleDetection:
    bbox: tuple[float, float, float, float]
    confidence: float
    vehicle_class: str
    track_id: int | None = None


def plate_corners_from_bbox(x1: float, y1: float, x2: float, y2: float) -> np.ndarray:
    """Estimate plate quadrilateral from axis-aligned bbox using minAreaRect."""
    pts = np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float32)
    rect = cv2.minAreaRect(pts)
    box = cv2.boxPoints(rect)
    return np.array(box, dtype=np.float32)


def order_corners(pts: np.ndarray) -> np.ndarray:
    """Order 4 points: top-left, top-right, bottom-right, bottom-left."""
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def is_two_row_plate(corners: np.ndarray) -> bool:
    w = np.linalg.norm(corners[1] - corners[0])
    h = np.linalg.norm(corners[3] - corners[0])
    aspect = float(max(w, h)) / max(float(min(w, h)), 1e-6)
    return aspect < 2.2


def rectify_plate(image: np.ndarray, corners: np.ndarray, two_row: bool = False) -> np.ndarray:
    """Perspective-warp plate to canonical size."""
    ordered = order_corners(corners.astype(np.float32))
    width = 200
    height = 80 if two_row else 50
    dst = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(ordered, dst)
    return cv2.warpPerspective(image, m, (width, height))


def compute_quality(plate_image: np.ndarray, pixel_height: float) -> float:
    """Quality score in [0,1] from sharpness, height, and exposure."""
    gray = plate_image if plate_image.ndim == 2 else cv2.cvtColor(plate_image, cv2.COLOR_BGR2GRAY)
    lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()
    sharp = min(1.0, lap_var / 500.0)
    height_score = min(1.0, pixel_height / 40.0)
    mean_val = float(np.mean(gray))
    exposure = 1.0 - abs(mean_val - 128.0) / 128.0
    return float(0.45 * sharp + 0.35 * height_score + 0.20 * exposure)


class VehicleDetector:
    """YOLO COCO vehicle detector with optional ByteTrack tracking."""

    def __init__(self, model_name: str = "yolov8n.pt") -> None:
        from ultralytics import YOLO

        self.model = YOLO(model_name)

    def track(self, frame: np.ndarray) -> list[VehicleDetection]:
        results = self.model.track(frame, persist=True, verbose=False)
        detections: list[VehicleDetection] = []
        if not results:
            return detections
        r = results[0]
        if r.boxes is None:
            return detections
        for box in r.boxes:  # type: ignore[attr-defined]
            cls_id = int(box.cls.item())
            if cls_id not in COCO_VEHICLE_IDS:
                continue
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            tid = int(box.id.item()) if box.id is not None else None
            detections.append(
                VehicleDetection(
                    bbox=(x1, y1, x2, y2),
                    confidence=float(box.conf.item()),
                    vehicle_class=COCO_VEHICLE_IDS[cls_id],
                    track_id=tid,
                )
            )
        return detections


class PlateDetector:
    """Custom YOLO plate detector; supports pose keypoints or bbox fallback."""

    def __init__(self, weights: str | Path) -> None:
        from ultralytics import YOLO

        self.weights = str(weights)
        self.model = YOLO(self.weights)
        self.is_pose = "pose" in self.weights.lower() or hasattr(self.model.model, "kpt_shape")

    def detect(self, crop: np.ndarray) -> list[PlateDetection]:
        results = self.model(crop, verbose=False)
        out: list[PlateDetection] = []
        if not results:
            return out
        r = results[0]  # type: ignore[index]
        if r.boxes is None:  # type: ignore[union-attr]
            return out

        kpts = getattr(r, "keypoints", None)
        for i, box in enumerate(r.boxes):  # type: ignore[union-attr]
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            conf = float(box.conf.item())
            corners: np.ndarray
            if kpts is not None and kpts.xy is not None and len(kpts.xy) > i:
                kp = kpts.xy[i].cpu().numpy()
                if kp.shape[0] >= 4:
                    corners = kp[:4].astype(np.float32)
                else:
                    corners = plate_corners_from_bbox(x1, y1, x2, y2)
            else:
                corners = plate_corners_from_bbox(x1, y1, x2, y2)
            out.append(PlateDetection(corners=corners, confidence=conf, bbox=(x1, y1, x2, y2)))
        return out
