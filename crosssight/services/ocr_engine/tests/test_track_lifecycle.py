"""PlateOCR track lifecycle: one vehicle -> one event, stamped with capture time."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import numpy as np
import pytest
from anpr_common.schemas import PlateRead
from ocr_engine.bytetrack import ByteTracker
from ocr_engine.enhance import ClassicalDeblurEnhancer
from ocr_engine.pipeline import OCRPipeline
from ocr_engine.plateocr_backend import PlateHit

PLATE = "MH12AB1234"


class _StubReader:
    """Detector sees the plate on every frame except those in ``missed``."""

    def __init__(self, missed: set[int], conf: float = 0.95) -> None:
        self.missed = missed
        self.conf = conf
        self.frame = 0
        self.crop_reads = 0

    def read(self, _img):
        self.frame += 1
        if self.frame in self.missed:
            return []
        x = 100 + 8 * self.frame  # plate drifting right
        return [
            PlateHit(PLATE, self.conf, 0.9, (x, 300, x + 120, 330), char_probs=[self.conf] * 10)
        ]

    def read_crop(self, _crop, **_kw):
        self.crop_reads += 1
        return PLATE, self.conf, [self.conf] * 10


class _Publisher:
    def __init__(self) -> None:
        self.events: list[PlateRead] = []

    def publish(self, read: PlateRead) -> None:
        self.events.append(read)


def _pipeline(reader: _StubReader) -> tuple[OCRPipeline, _Publisher]:
    publisher = _Publisher()
    p = object.__new__(OCRPipeline)
    p.backend = "plateocr"
    p.plateocr_reader = reader
    p.enhancer = ClassicalDeblurEnhancer()
    p.publisher = cast(Any, publisher)
    p.uploader = None
    p.tracks = {}
    p._active_ids = set()
    p._alive_ids = set()
    p._frame_ts = None
    p.use_bytetrack = True
    p.num_lanes = 3
    p.camera_heading_deg = 0.0
    p.camera_id = "cam-test"
    p.byte_tracker = ByteTracker(track_thresh=0.35, match_thresh=0.3, track_buffer=45)
    return p, publisher


def _run(p: OCRPipeline, frames: int, t0: datetime) -> None:
    frame = np.zeros((720, 1280, 3), np.uint8)
    for i in range(frames):
        p._frame_ts = t0 + timedelta(seconds=i)
        p._process_frame_plateocr(frame)
        p._finalize_stale_tracks()
    p._finalize_all_tracks()


@pytest.mark.parametrize("missed", [set(), {5}, {4, 5, 6}])
def test_one_vehicle_one_event_despite_missed_detections(missed):
    p, pub = _pipeline(_StubReader(missed))
    _run(p, 10, datetime(2026, 6, 1, tzinfo=UTC))
    assert [e.plate_norm for e in pub.events] == [PLATE]


def test_event_timestamp_is_capture_time_of_last_sighting():
    t0 = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    p, pub = _pipeline(_StubReader(missed={10}))  # last sighting is frame 9 (index 8)
    _run(p, 10, t0)
    assert pub.events[0].ts == t0 + timedelta(seconds=8)


def test_confident_reads_skip_enhanced_reread():
    reader = _StubReader(missed=set(), conf=0.95)
    _run(_pipeline(reader)[0], 5, datetime(2026, 6, 1, tzinfo=UTC))
    assert reader.crop_reads == 0


def test_weak_reads_get_enhanced_reread():
    reader = _StubReader(missed=set(), conf=0.4)
    _run(_pipeline(reader)[0], 5, datetime(2026, 6, 1, tzinfo=UTC))
    assert reader.crop_reads == 5


# --- run() end to end on a real (tiny) video file ------------------------------------------------
# Guards the call signatures used by the CLI (stride/source_start) and video_city
# (ocr_every/release); a merge once left run() referencing parameters it no longer had.


class _RunPublisher(_Publisher):
    def __init__(self) -> None:
        super().__init__()
        self.closed = 0

    def connect(self) -> None:
        pass

    def close(self) -> None:
        self.closed += 1


def _tiny_video(path, frames: int = 12, fps: float = 10.0) -> str:
    import cv2

    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"MJPG"), fps, (320, 240))
    for i in range(frames):
        writer.write(np.full((240, 320, 3), 20 * (i % 10), np.uint8))
    writer.release()
    return str(path)


def _run_pipeline(reader: _StubReader) -> tuple[OCRPipeline, _RunPublisher]:
    from types import SimpleNamespace

    p, _ = _pipeline(reader)
    pub = _RunPublisher()
    p.publisher = cast(Any, pub)
    p.settings = cast(Any, SimpleNamespace(ocr_frame_stride=1, publish_annotated_frames=False))
    p._annotate_writer = None
    p._next_track_id = 1
    p._last_frame_publish = {}
    p._redis = None
    return p, pub


def test_run_cli_signature_stamps_capture_time(tmp_path):
    video = _tiny_video(tmp_path / "clip.avi")
    t0 = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
    p, pub = _run_pipeline(_StubReader(missed=set()))
    emitted = p.run(video, stride=1, source_start=t0)
    assert emitted == 1
    assert [e.plate_norm for e in pub.events] == [PLATE]
    # Last sighting is the 12th frame at 10 fps -> ~1.1 s after the recording start.
    assert t0 <= pub.events[0].ts <= t0 + timedelta(seconds=1.5)
    assert pub.closed == 1


def test_run_video_city_signature_keeps_publisher_open(tmp_path):
    video = _tiny_video(tmp_path / "clip.avi")
    reader = _StubReader(missed=set())
    p, pub = _run_pipeline(reader)
    emitted = p.run(video, reconnect=False, ocr_every=2, release=False)
    assert emitted == 1
    assert reader.frame == 6  # every 2nd of 12 frames processed
    assert pub.closed == 0
