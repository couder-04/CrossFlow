"""Synthetic wall frames change between simulation ticks."""

from datetime import UTC, datetime, timedelta

from simulator.frames import encode_jpeg, render_synthetic_frame


def test_two_ticks_produce_distinct_jpegs():
    ts = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    first, _bbox_a = render_synthetic_frame(
        camera_name="Gate A",
        ts=ts,
        plate_norm="MH01AB1234",
        vehicle_class="car",
        track_id=1,
        tick=0,
    )
    second, _bbox_b = render_synthetic_frame(
        camera_name="Gate A",
        ts=ts + timedelta(seconds=1),
        plate_norm="MH01AB1234",
        vehicle_class="car",
        track_id=2,
        tick=1,
    )
    jpeg_a = encode_jpeg(first)
    jpeg_b = encode_jpeg(second)
    assert jpeg_a != jpeg_b
    assert jpeg_a.startswith(b"\xff\xd8")
    assert jpeg_b.startswith(b"\xff\xd8")
