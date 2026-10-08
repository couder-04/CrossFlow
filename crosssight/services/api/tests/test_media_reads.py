"""Uploaded video frames become plate reads through the scene detector interface."""

from datetime import UTC, datetime

from api.media import reads_from_frames


def _reads(frames, detect):
    return reads_from_frames(
        frames,
        camera_id="cam-9",
        started_at=datetime(2026, 6, 1, tzinfo=UTC),
        source_key="uploads/video/1/clip.mp4",
        fps=10,
        every_n=10,
        detect=detect,
    )


def test_reads_from_frames_skip_empty_frames_and_keep_source():
    def detect(frame):
        if frame == "b":
            return [{"plate": "MH12AB1234", "confidence": 0.88}]
        return []

    reads = _reads(["a", "b", "c"], detect)
    assert len(reads) == 1
    assert reads[0]["plate"] == "MH12AB1234"
    assert reads[0]["camera_id"] == "cam-9"
    assert reads[0]["source_video_key"].endswith("clip.mp4")
    assert reads[0]["confidence"] == 0.88
    assert reads[0]["frame_index"] == 10


def test_reads_from_frames_reports_every_plate_in_a_frame():
    def detect(frame):
        return [
            {"plate": "MH12AB1234", "confidence": 0.9},
            {"plate": "KL35H5834", "confidence": 0.97},
        ]

    reads = _reads(["a"], detect)
    assert sorted(r["plate"] for r in reads) == ["KL35H5834", "MH12AB1234"]


def test_reads_from_frames_one_read_per_plate_at_best_frame():
    confidences = {"a": 0.6, "b": 0.95, "c": 0.7}

    def detect(frame):
        return [{"plate": "MH12AB1234", "confidence": confidences[frame]}]

    reads = _reads(["a", "b", "c"], detect)
    assert len(reads) == 1
    assert reads[0]["confidence"] == 0.95
    assert reads[0]["frame_index"] == 10  # frame "b"
    assert reads[0]["ts"] == datetime(2026, 6, 1, 0, 0, 1, tzinfo=UTC).isoformat()


def test_reads_from_frames_skips_unknown_and_failing_frames():
    def detect(frame):
        if frame == "boom":
            raise RuntimeError("decoder crashed")
        return [{"plate": "UNKNOWN", "confidence": 0.0}]

    assert _reads(["boom", "a"], detect) == []
