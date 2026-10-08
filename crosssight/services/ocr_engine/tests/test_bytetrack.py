"""Unit tests for ByteTracker plate association."""

from __future__ import annotations

from ocr_engine.bytetrack import ByteTracker


def test_bytetrack_keeps_ids_across_frames():
    tracker = ByteTracker(track_thresh=0.3, match_thresh=0.25, track_buffer=10)
    # Two plates side by side
    left = (100, 200, 220, 260)
    right = (500, 200, 620, 260)
    t1 = tracker.update([(left, 0.9), (right, 0.85)])
    assert len(t1) == 2
    ids1 = {t.track_id for t in t1}
    assert len(ids1) == 2

    # Move slightly — IDs must stay stable
    left2 = (110, 205, 230, 265)
    right2 = (510, 195, 630, 255)
    t2 = tracker.update([(left2, 0.88), (right2, 0.9)])
    assert {t.track_id for t in t2} == ids1

    # Map by x-center: left track stays left
    by_id = {t.track_id: t for t in t2}
    left_id = min(t1, key=lambda t: t.bbox[0]).track_id
    right_id = max(t1, key=lambda t: t.bbox[0]).track_id
    assert by_id[left_id].bbox[0] < by_id[right_id].bbox[0]


def test_bytetrack_new_id_for_new_plate():
    tracker = ByteTracker(track_thresh=0.3, match_thresh=0.25)
    t1 = tracker.update([((50, 50, 150, 100), 0.9)])
    id1 = t1[0].track_id
    t2 = tracker.update([((50, 50, 150, 100), 0.9), ((400, 50, 500, 100), 0.9)])
    ids = {t.track_id for t in t2}
    assert id1 in ids
    assert len(ids) == 2
