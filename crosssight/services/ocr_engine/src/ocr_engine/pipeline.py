"""Video OCR pipeline: detect → track → read → fuse → publish.

Backends:
  - plateocr (default): FastALPR YOLOv9 + CCT ONNX (https://github.com/Ajitesh-07/PlateOCR)
  - legacy: Ultralytics vehicle/plate YOLO + PARSeq
"""

from __future__ import annotations

import io
import json
import logging
import math
import statistics
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

import cv2
import numpy as np
from anpr_common.config import Settings
from anpr_common.geo import bearing_to_direction
from anpr_common.grammar import normalize_plate
from anpr_common.schemas import Direction, PlateFormat, PlateRead, VehicleClass

from ocr_engine.bytetrack import ByteTracker
from ocr_engine.detect import (
    PlateDetector,
    VehicleDetector,
    compute_quality,
    is_two_row_plate,
    rectify_plate,
    validate_plate_weights,
)
from ocr_engine.enhance import CONFIDENCE_THRESHOLD, ClassicalDeblurEnhancer, enhance_plate
from ocr_engine.fusion import FrameRead, fuse_track_reads
from ocr_engine.recognize import MockRecognizer, ParseqRecognizer, Recognizer

logger = logging.getLogger(__name__)

VEHICLE_CLASS_MAP = {
    "car": VehicleClass.car,
    "motorcycle": VehicleClass.motorcycle,
    "bus": VehicleClass.bus,
    "truck": VehicleClass.truck,
}

BackendName = Literal["plateocr", "legacy"]


@dataclass
class TrackState:
    track_id: int
    vehicle_class: str = "car"
    centers: list[tuple[float, float]] = field(default_factory=list)
    frame_reads: list[FrameRead] = field(default_factory=list)
    best_crop: np.ndarray | None = None
    best_quality: float = 0.0
    frames_seen: int = 0
    last_bbox: tuple[int, int, int, int] | None = None
    lane: int | None = None
    frame_width: int = 0
    last_text: str = ""
    last_ocr_conf: float = 0.0
    last_seen_ts: datetime | None = None  # capture time of the last frame with this plate


def estimate_lane(
    bbox: tuple[float, float, float, float], frame_width: int, num_lanes: int
) -> int | None:
    """Map plate bbox horizontal center to lane 1..N (left → right)."""
    if num_lanes < 1 or frame_width <= 0:
        return None
    cx = (bbox[0] + bbox[2]) / 2.0
    lane = int(cx / frame_width * num_lanes) + 1
    return max(1, min(num_lanes, lane))


def motion_bearing_deg(centers: list[tuple[float, float]]) -> float | None:
    if len(centers) < 2:
        return None
    x1, y1 = centers[0]
    x2, y2 = centers[-1]
    dx, dy = x2 - x1, y2 - y1
    if abs(dx) < 1e-3 and abs(dy) < 1e-3:
        return None
    # Image coords: x right, y down — convert to compass bearing (0=N).
    angle = math.degrees(math.atan2(dx, -dy))
    return (angle + 360.0) % 360.0


def estimate_direction(
    centers: list[tuple[float, float]], camera_heading_deg: float
) -> Direction | None:
    bearing = motion_bearing_deg(centers)
    if bearing is None:
        return None
    travel = (bearing + camera_heading_deg) % 360.0
    return bearing_to_direction(travel)


def _bbox_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _bbox_center(b: tuple[int, int, int, int]) -> tuple[float, float]:
    x1, y1, x2, y2 = b
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


def _ann_get(track: Any, name: str, default: Any = None) -> Any:
    if isinstance(track, dict):
        return track.get(name, default)
    return getattr(track, name, default)


def draw_annotations(frame: np.ndarray, tracks: list[Any]) -> np.ndarray:
    """Draw green boxes, labels, and plate crosshairs. Returns a copy."""
    out = frame.copy()
    height, width = out.shape[:2]
    for track in tracks:
        bbox = _ann_get(track, "bbox")
        if not bbox or len(bbox) < 4:
            continue
        x1, y1, x2, y2 = (int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]))
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(width - 1, x2), min(height - 1, y2)
        if x2 <= x1 or y2 <= y1:
            continue
        color = (0, 255, 0)
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        plate = _ann_get(track, "plate_norm") or "—"
        klass = _ann_get(track, "vehicle_class") or "vehicle"
        track_id = _ann_get(track, "track_id", "?")
        try:
            conf = float(_ann_get(track, "confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        parts = [str(plate), str(klass), f"#{track_id}", f"{conf:.2f}"]
        lane = _ann_get(track, "lane")
        direction = _ann_get(track, "direction")
        if lane is not None:
            parts.append(f"L{lane}")
        if direction:
            parts.append(str(direction))
        label = " • ".join(parts)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        bar_h = th + 8
        bar_top = y1 - bar_h if y1 - bar_h >= 0 else y1
        bar_bot = bar_top + bar_h
        cv2.rectangle(out, (x1, bar_top), (min(width - 1, x1 + tw + 8), bar_bot), color, -1)
        cv2.putText(
            out,
            label,
            (x1 + 4, bar_bot - 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 0, 0),
            1,
            cv2.LINE_AA,
        )
        center = _ann_get(track, "plate_center")
        if center and len(center) >= 2:
            cx, cy = int(center[0]), int(center[1])
        else:
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        arm = 6
        cv2.line(out, (cx - arm, cy), (cx + arm, cy), (0, 255, 255), 1)
        cv2.line(out, (cx, cy - arm), (cx, cy + arm), (0, 255, 255), 1)
    return out


PREVIEW_MAX_SIDE = 960
PREVIEW_JPEG_QUALITY = 75


def encode_preview_jpeg(image: np.ndarray) -> tuple[bytes, float]:
    """JPEG at quality 75, long side capped at 960px. Returns bytes and scale."""
    height, width = image.shape[:2]
    long_side = max(height, width)
    scale = 1.0
    preview = image
    if long_side > PREVIEW_MAX_SIDE:
        scale = PREVIEW_MAX_SIDE / float(long_side)
        preview = cv2.resize(
            image,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    ok, buf = cv2.imencode(".jpg", preview, [int(cv2.IMWRITE_JPEG_QUALITY), PREVIEW_JPEG_QUALITY])
    if not ok:
        raise RuntimeError("Failed to encode preview JPEG")
    return buf.tobytes(), scale


class MinioUploader:
    def __init__(self, settings: Settings) -> None:
        from minio import Minio

        self.settings = settings
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.settings.minio_bucket):
            self.client.make_bucket(self.settings.minio_bucket)

    def upload_crop(self, image: np.ndarray, camera_id: str, event_id: str) -> str:
        ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            raise RuntimeError("Failed to encode crop JPEG")
        data = io.BytesIO(buf.tobytes())
        key = f"{camera_id}/{event_id}.jpg"
        self.client.put_object(
            self.settings.minio_bucket,
            key,
            data,
            length=len(buf),
            content_type="image/jpeg",
        )
        return key

    def upload_jpeg(self, key: str, data: bytes, cache_control: str | None = None) -> str:
        meta: dict[str, str | list[str] | tuple[str]] | None = (
            {"Cache-Control": cache_control} if cache_control else None
        )
        self.client.put_object(
            self.settings.minio_bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type="image/jpeg",
            metadata=meta,
        )
        return key


def emit(
    event: dict[str, Any],
    dry_run: bool,
    *,
    producer: Any = None,
    topic: str = "",
    key: str | None = None,
) -> None:
    """Print one event, or publish it when a Kafka producer is connected."""
    if dry_run or producer is None:
        print(json.dumps(event, indent=2) if dry_run else json.dumps(event))
        return
    producer.send(topic, key=key, value=event)


class EventPublisher:
    def __init__(self, settings: Settings, dry_run: bool = False) -> None:
        self.settings = settings
        self.dry_run = dry_run
        self._producer: Any = None

    def connect(self) -> None:
        if self.dry_run or self._producer is not None:
            return
        try:
            from kafka import KafkaProducer

            self._producer = KafkaProducer(
                bootstrap_servers=self.settings.kafka_bootstrap,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
            )
        except (OSError, ImportError, ValueError) as exc:
            logger.warning("Kafka unavailable (%s); falling back to stdout", exc)
            self._producer = None

    def publish(self, read: PlateRead) -> None:
        emit(
            read.model_dump(mode="json"),
            self.dry_run,
            producer=self._producer,
            topic=self.settings.topic_reads,
            key=read.plate_norm,
        )

    def close(self) -> None:
        if self._producer:
            self._producer.flush()
            self._producer.close()
            self._producer = None


def build_recognizer(settings: Settings, use_mock: bool = False) -> Recognizer:
    if use_mock:
        return MockRecognizer()
    if settings.parseq_weights:
        return ParseqRecognizer(weights_path=settings.parseq_weights)
    return ParseqRecognizer()


class OCRPipeline:
    FUSION_MIN_FRAMES = 3
    IOU_MATCH_THRESH = 0.3  # legacy greedy fallback only

    def __init__(
        self,
        settings: Settings,
        camera_id: str,
        camera_heading_deg: float = 0.0,
        dry_run: bool = False,
        recognizer: Recognizer | None = None,
        backend: str | None = None,
        num_lanes: int = 3,
        use_bytetrack: bool = True,
    ) -> None:
        self.settings = settings
        self.camera_id = camera_id
        self.camera_heading_deg = camera_heading_deg
        self.dry_run = dry_run
        self.num_lanes = max(1, num_lanes)
        self.use_bytetrack = use_bytetrack
        chosen = (backend or settings.ocr_backend or "plateocr").lower()
        if chosen not in ("plateocr", "legacy"):
            raise ValueError(f"Unknown OCR backend: {chosen}")
        self.backend: BackendName = chosen  # type: ignore[assignment]

        self.vehicle_detector: VehicleDetector | None = None
        self.plate_detector: PlateDetector | None = None
        self.plateocr_reader: Any = None
        self.recognizer: Recognizer | None = recognizer
        self.enhancer = ClassicalDeblurEnhancer()
        self.uploader = MinioUploader(settings) if not dry_run else None
        self.publisher = EventPublisher(settings, dry_run=dry_run)
        self.tracks: dict[int, TrackState] = {}
        self._active_ids: set[int] = set()
        self._next_track_id = 1
        self.byte_tracker = ByteTracker(track_thresh=0.35, match_thresh=0.3, track_buffer=45)
        self._annotate_writer: cv2.VideoWriter | None = None
        self._last_frame_publish: dict[str, float] = {}
        self._redis: Any = None
        self._frame_ts: datetime | None = None
        self._alive_ids: set[int] = set()

        if self.backend == "plateocr":
            from ocr_engine.plateocr_backend import PlateOCRReader, ensure_plateocr_available

            ensure_plateocr_available()
            self.plateocr_reader = PlateOCRReader(
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
            logger.info(
                "OCR pipeline backend=plateocr (det=%s ocr=%s format=%s bytetrack=%s)",
                settings.plateocr_detector,
                settings.plateocr_ocr_model,
                settings.plateocr_plate_format or "auto",
                self.use_bytetrack,
            )
        else:
            weights = validate_plate_weights(settings.plate_det_weights)
            self.vehicle_detector = VehicleDetector()
            self.plate_detector = PlateDetector(weights)
            if self.recognizer is None:
                self.recognizer = build_recognizer(settings)
            logger.info("OCR pipeline backend=legacy (YOLO + PARSeq)")

    def run(
        self,
        source: str,
        max_frames: int | None = None,
        reconnect: bool | None = None,
        reconnect_delay: float = 2.0,
        annotate_out: str | None = None,
        ocr_every: int | None = None,
        release: bool = True,
        stride: int | None = None,
        source_start: datetime | None = None,
    ) -> int:
        """Process a video file or RTSP stream.

        RTSP sources reconnect by default; file sources do not.
        If ``annotate_out`` is set, write an MP4 with boxes, track IDs, and OCR text.
        ``ocr_every`` (alias ``stride``, CLI ``--stride``) runs detection on every Nth frame
        (live preview still updates); default ``OCR_FRAME_STRIDE``.
        ``release=False`` keeps the Kafka publisher open so the same pipeline can
        read the next file without reloading models.
        Event timestamps are capture times: wall clock for live streams, and
        ``source_start`` (default: now) + the frame's position for files.
        """
        every = max(
            1, int(ocr_every or stride or getattr(self.settings, "ocr_frame_stride", 1) or 1)
        )
        file_start = source_start or datetime.now(UTC)
        self.publisher.connect()
        self.byte_tracker.reset()
        self.tracks.clear()
        self._active_ids.clear()
        self._alive_ids = set()
        self._next_track_id = 1
        is_stream = str(source).lower().startswith(("rtsp://", "http://", "https://"))
        do_reconnect = is_stream if reconnect is None else reconnect
        emitted = 0
        frame_idx = 0
        try:
            while True:
                cap = cv2.VideoCapture(source)
                if not cap.isOpened():
                    if not do_reconnect:
                        raise RuntimeError(f"Cannot open video source: {source}")
                    logger.warning("Cannot open %s; retrying in %.1fs", source, reconnect_delay)
                    import time

                    time.sleep(reconnect_delay)
                    continue
                if is_stream:
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
                fps = float(cap.get(cv2.CAP_PROP_FPS) or 15.0)
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                if annotate_out and self._annotate_writer is None and width > 0:
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")  # type: ignore[attr-defined]
                    self._annotate_writer = cv2.VideoWriter(
                        annotate_out, fourcc, max(fps, 1.0), (width, height)
                    )
                emptied = False
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        emptied = True
                        break
                    frame_idx += 1
                    if max_frames and frame_idx > max_frames:
                        emptied = False
                        do_reconnect = False
                        break
                    if every <= 1 or frame_idx % every == 0:
                        if is_stream:
                            self._frame_ts = datetime.now(UTC)
                        else:
                            pos_ms = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
                            self._frame_ts = file_start + timedelta(milliseconds=pos_ms)
                        if self.backend == "plateocr":
                            self._process_frame_plateocr(frame)
                        else:
                            self._process_frame_legacy(frame)
                    self._publish_live_frame(frame)
                    if self._annotate_writer is not None:
                        self._annotate_writer.write(self._draw_annotations(frame))
                    emitted += self._finalize_stale_tracks()
                cap.release()
                if max_frames and frame_idx >= max_frames:
                    break
                if not do_reconnect or not emptied:
                    break
                logger.warning(
                    "Stream ended for %s; reconnecting in %.1fs", source, reconnect_delay
                )
                import time

                time.sleep(reconnect_delay)
        finally:
            emitted += self._finalize_all_tracks()
            if release:
                self.publisher.close()
            if self._annotate_writer is not None:
                self._annotate_writer.release()
                self._annotate_writer = None
                if annotate_out:
                    logger.info("Wrote annotated video → %s", annotate_out)
        return emitted

    def _live_tracks(self) -> list[dict[str, Any]]:
        tracks: list[dict[str, Any]] = []
        for tid in sorted(self._active_ids):
            state = self.tracks.get(tid)
            if state is None or state.last_bbox is None:
                continue
            direction = estimate_direction(state.centers, self.camera_heading_deg)
            tracks.append(
                {
                    "track_id": state.track_id,
                    "plate_norm": state.last_text,
                    "vehicle_class": state.vehicle_class,
                    "bbox": [int(v) for v in state.last_bbox],
                    "confidence": float(state.last_ocr_conf),
                    "lane": state.lane,
                    "direction": direction.value if direction else None,
                }
            )
        return tracks

    def _publish_live_frame(self, frame: np.ndarray) -> None:
        """Overwrite frames/latest/{camera}.jpg and notify Redis. Throttled to 2 Hz."""
        if not getattr(self.settings, "publish_annotated_frames", True):
            return
        if self.uploader is None:
            return
        now = time.monotonic()
        last = self._last_frame_publish.get(self.camera_id, 0.0)
        if now - last < 0.5:
            return
        self._last_frame_publish[self.camera_id] = now
        try:
            tracks = self._live_tracks()
            annotated = draw_annotations(frame, tracks)
            jpeg, scale = encode_preview_jpeg(annotated)
            key = f"frames/latest/{self.camera_id}.jpg"
            self.uploader.upload_jpeg(key, jpeg, cache_control="no-store")
            notice_tracks = []
            for track in tracks:
                bbox = track["bbox"]
                notice_tracks.append(
                    {
                        **track,
                        "bbox": [round(v * scale) for v in bbox],
                    }
                )
            payload = {
                "camera_id": self.camera_id,
                "ts": datetime.now(UTC).isoformat(),
                "key": key,
                "tracks": notice_tracks,
            }
            self._redis_publish_frame(payload)
        except Exception:
            logger.warning("Annotated frame publish failed for %s", self.camera_id, exc_info=True)

    def _redis_publish_frame(self, payload: dict[str, Any]) -> None:
        try:
            import redis

            if self._redis is None:
                self._redis = redis.Redis.from_url(self.settings.redis_url)
            self._redis.publish("frames", json.dumps(payload))
        except Exception:
            logger.warning("Redis frames publish failed for %s", self.camera_id, exc_info=True)

    def _draw_annotations(self, frame: np.ndarray) -> np.ndarray:
        """Overlay ByteTrack IDs, boxes, and latest OCR text for active tracks."""
        out = frame.copy()
        palette = [
            (0, 200, 255),
            (80, 220, 100),
            (255, 160, 40),
            (200, 80, 255),
            (60, 60, 255),
            (255, 80, 120),
        ]
        for tid in sorted(self._active_ids):
            state = self.tracks.get(tid)
            if state is None or state.last_bbox is None:
                continue
            x1, y1, x2, y2 = state.last_bbox
            color = palette[(tid - 1) % len(palette)]
            cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
            label = f"ID {tid}"
            if state.last_text:
                label += f"  {state.last_text}"
                if state.last_ocr_conf:
                    label += f"  {state.last_ocr_conf:.2f}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            ty = max(0, y1 - th - 8)
            cv2.rectangle(out, (x1, ty), (x1 + tw + 6, ty + th + 8), color, -1)
            cv2.putText(
                out,
                label,
                (x1 + 3, ty + th + 3),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 0, 0),
                2,
                cv2.LINE_AA,
            )
        return out

    def run_image(self, source: str) -> int:
        """Single-image ALPR (plateocr backend). Emits one event per plate found."""
        if self.backend != "plateocr" or self.plateocr_reader is None:
            raise RuntimeError("run_image requires OCR_BACKEND=plateocr")
        self.publisher.connect()
        img = cv2.imread(source)
        if img is None:
            raise RuntimeError(f"Cannot read image: {source}")
        self._frame_ts = datetime.now(UTC)
        hits = self.plateocr_reader.read(img)
        emitted = 0
        current_ids: set[int] = set()
        try:
            for hit in hits:
                x1, y1, x2, y2 = hit.bbox
                crop = img[max(0, y1) : max(0, y2), max(0, x1) : max(0, x2)]
                state = TrackState(
                    track_id=self._next_track_id,
                    centers=[_bbox_center(hit.bbox)],
                    frame_reads=[
                        FrameRead(
                            text=hit.text,
                            char_probs=hit.char_probs
                            or [hit.ocr_confidence] * max(1, len(hit.text)),
                            quality=hit.det_confidence,
                        )
                    ],
                    best_crop=crop.copy() if crop.size else None,
                    best_quality=hit.det_confidence,
                    frames_seen=self.FUSION_MIN_FRAMES,
                    last_bbox=hit.bbox,
                    last_text=hit.text,
                    last_ocr_conf=float(hit.ocr_confidence),
                    last_seen_ts=self._frame_ts,
                )
                self.tracks[state.track_id] = state
                current_ids.add(state.track_id)
                self._next_track_id += 1
                if self._emit_track(state):
                    emitted += 1
            self._active_ids = current_ids
            self._publish_live_frame(img)
        finally:
            self.publisher.close()
        return emitted

    def _process_frame_plateocr(self, frame: np.ndarray) -> None:
        assert self.plateocr_reader is not None
        hits = self.plateocr_reader.read(frame)
        frame_h, frame_w = frame.shape[:2]
        current_ids: set[int] = set()

        if self.use_bytetrack:
            dets = [(h.bbox, float(h.det_confidence)) for h in hits]
            tracks = self.byte_tracker.update(dets)
            self._alive_ids = self.byte_tracker.alive_ids()
            # Map ByteTrack outputs → OCR hits via det_index (preferred) or IoU.
            for tout in tracks:
                hit = None
                if tout.det_index is not None and 0 <= tout.det_index < len(hits):
                    hit = hits[tout.det_index]
                else:
                    best_iou, best_h = 0.0, None
                    for h in hits:
                        iou = _bbox_iou(tout.bbox, h.bbox)
                        if iou > best_iou:
                            best_iou, best_h = iou, h
                    if best_h is not None and best_iou >= 0.1:
                        hit = best_h
                if hit is None:
                    continue
                state = self.tracks.get(tout.track_id)
                if state is None:
                    state = TrackState(track_id=tout.track_id)
                    self.tracks[tout.track_id] = state
                self._accumulate_plateocr_hit(state, hit, frame, frame_h, frame_w)
                current_ids.add(tout.track_id)
        else:
            # Legacy greedy IoU (can swap IDs when multiple cars are close).
            unmatched = set(self.tracks.keys())
            for hit in hits:
                best_tid: int | None = None
                best_iou = 0.0
                for tid in list(unmatched):
                    prev = self.tracks[tid].last_bbox
                    if prev is None:
                        continue
                    iou = _bbox_iou(hit.bbox, prev)
                    if iou > best_iou:
                        best_iou = iou
                        best_tid = tid
                if best_tid is not None and best_iou >= self.IOU_MATCH_THRESH:
                    state = self.tracks[best_tid]
                    unmatched.discard(best_tid)
                else:
                    tid = self._next_track_id
                    self._next_track_id += 1
                    state = TrackState(track_id=tid)
                    self.tracks[tid] = state
                    best_tid = tid
                self._accumulate_plateocr_hit(state, hit, frame, frame_h, frame_w)
                current_ids.add(best_tid)

        self._active_ids = current_ids

    def _accumulate_plateocr_hit(
        self,
        state: TrackState,
        hit: Any,
        frame: np.ndarray,
        frame_h: int,
        frame_w: int,
    ) -> None:
        state.frames_seen += 1
        state.last_bbox = hit.bbox
        state.frame_width = frame_w
        state.centers.append(_bbox_center(hit.bbox))
        state.lane = estimate_lane(hit.bbox, frame_w, self.num_lanes)
        probs = hit.char_probs or [hit.ocr_confidence] * max(1, len(hit.text))
        quality = float(hit.det_confidence)
        x1, y1, x2, y2 = hit.bbox
        crop = frame[max(0, y1) : min(frame_h, y2), max(0, x1) : min(frame_w, x2)]
        enhanced_text = hit.text
        if crop.size:
            # Re-read an enhanced crop only when the first read is weak: doing it for every
            # plate on every frame doubled OCR cost for reads that were already confident.
            if hit.ocr_confidence < CONFIDENCE_THRESHOLD:
                enhanced = enhance_plate(crop, quality, hit.ocr_confidence, self.enhancer)
                try:
                    re_text, re_conf, re_probs = self.plateocr_reader.read_crop(enhanced)
                    if re_text and re_conf >= hit.ocr_confidence - 1e-6:
                        enhanced_text = re_text
                        probs = re_probs or [re_conf] * max(1, len(re_text))
                        quality = max(quality, float(re_conf))
                except Exception:
                    logger.debug("PlateOCR re-read after enhance failed", exc_info=True)
            # Evidence image: the unmodified camera pixels, never the sharpened copy.
            if quality >= state.best_quality:
                state.best_quality = quality
                state.best_crop = crop.copy()
        state.last_text = enhanced_text
        state.last_ocr_conf = float(statistics.mean(probs)) if probs else float(hit.ocr_confidence)
        state.last_seen_ts = self._frame_ts
        state.frame_reads.append(FrameRead(text=enhanced_text, char_probs=probs, quality=quality))

    def _process_frame_legacy(self, frame: np.ndarray) -> None:
        assert self.vehicle_detector is not None
        assert self.plate_detector is not None
        assert self.recognizer is not None

        vehicles = self.vehicle_detector.track(frame)
        current_ids: set[int] = set()
        for veh in vehicles:
            if veh.track_id is None:
                continue
            tid = veh.track_id
            current_ids.add(tid)
            state = self.tracks.get(tid)
            if state is None:
                state = TrackState(track_id=tid, vehicle_class=veh.vehicle_class)
                self.tracks[tid] = state
            state.vehicle_class = veh.vehicle_class
            state.frames_seen += 1
            x1, y1, x2, y2 = veh.bbox
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            state.centers.append((cx, cy))
            h, w = frame.shape[:2]
            state.frame_width = w
            state.lane = estimate_lane(veh.bbox, w, self.num_lanes)

            pad = 10
            vx1 = max(0, int(x1) - pad)
            vy1 = max(0, int(y1) - pad)
            vx2 = min(w, int(x2) + pad)
            vy2 = min(h, int(y2) + pad)
            crop = frame[vy1:vy2, vx1:vx2]
            if crop.size == 0:
                continue

            plates = self.plate_detector.detect(crop)
            if not plates:
                continue
            best = max(plates, key=lambda p: p.confidence)
            corners = best.corners.copy()
            corners[:, 0] += vx1
            corners[:, 1] += vy1
            two_row = is_two_row_plate(corners)
            rectified = rectify_plate(frame, corners, two_row=two_row)
            pixel_h = float(np.linalg.norm(corners[3] - corners[0]))
            quality = compute_quality(rectified, pixel_h)
            rec = self.recognizer.recognize(enhance_plate(rectified, quality, None, self.enhancer))
            rec2 = self.recognizer.recognize(
                enhance_plate(rectified, quality, rec.confidence, self.enhancer)
            )
            state.frame_reads.append(
                FrameRead(text=rec2.text, char_probs=rec2.char_probs, quality=quality)
            )
            state.last_seen_ts = self._frame_ts
            if quality > state.best_quality:
                state.best_quality = quality
                state.best_crop = rectified.copy()

        self._active_ids = current_ids

    def _finalize_stale_tracks(self) -> int:
        """Emit and forget tracks that have ended.

        With ByteTrack (plateocr backend), a track missing from this frame may still be
        re-matched with the same ID for ``track_buffer`` frames, so it only ends once the tracker
        drops it. Finalizing on the first missed frame split one vehicle into several events.
        """
        still_tracked = (
            self._alive_ids
            if self.backend == "plateocr" and self.use_bytetrack
            else self._active_ids
        )
        emitted = 0
        for tid, state in list(self.tracks.items()):
            if tid in still_tracked:
                continue
            if (
                state.frames_seen >= self.FUSION_MIN_FRAMES
                and state.frame_reads
                and self._emit_track(state)
            ):
                emitted += 1
            del self.tracks[tid]
        return emitted

    def _finalize_all_tracks(self) -> int:
        emitted = 0
        for state in self.tracks.values():
            if (
                state.frames_seen >= self.FUSION_MIN_FRAMES
                and state.frame_reads
                and self._emit_track(state)
            ):
                emitted += 1
        self.tracks.clear()
        return emitted

    def _emit_track(self, state: TrackState) -> bool:
        fused = fuse_track_reads(state.frame_reads)
        if not fused.text or fused.text == "UNKNOWN":
            return False

        grammar = normalize_plate(fused.text)
        direction = estimate_direction(state.centers, self.camera_heading_deg)
        event_id = uuid4()
        crop_key = None
        if state.best_crop is not None and self.uploader is not None:
            try:
                crop_key = self.uploader.upload_crop(state.best_crop, self.camera_id, str(event_id))
            except (OSError, ValueError) as exc:
                logger.warning("MinIO upload failed: %s", exc)

        read = PlateRead(
            event_id=event_id,
            camera_id=self.camera_id,
            ts=state.last_seen_ts or datetime.now(UTC),
            plate_raw=fused.text,
            plate_norm=grammar.norm,
            plate_valid=grammar.valid,
            plate_format=PlateFormat(grammar.format),
            confidence=round(fused.confidence, 3),
            char_conf=fused.char_conf,
            alternates=fused.alternates,
            lane=state.lane,
            direction=direction,
            vehicle_class=VEHICLE_CLASS_MAP.get(state.vehicle_class, VehicleClass.car),
            crop_key=crop_key,
            source="ocr",
            track_id=state.track_id,
            bbox=(
                (
                    float(state.last_bbox[0]),
                    float(state.last_bbox[1]),
                    float(state.last_bbox[2]),
                    float(state.last_bbox[3]),
                )
                if state.last_bbox and len(state.last_bbox) == 4
                else None
            ),
        )
        self.publisher.publish(read)
        return True
