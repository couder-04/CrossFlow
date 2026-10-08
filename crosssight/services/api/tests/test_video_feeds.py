"""Video-camera catalog: stable ids, names, and fleet file."""

from __future__ import annotations

import json

from api.video_feeds import (
    camera_in_source,
    camera_records,
    discover_videos,
    display_name,
    write_fleet,
)


def test_discover_sorts_videos_and_skips_other_files(tmp_path):
    (tmp_path / "b clip.mp4").write_bytes(b"b")
    (tmp_path / "A clip.MP4").write_bytes(b"a")
    (tmp_path / "notes.txt").write_text("nope", encoding="utf-8")
    (tmp_path / ".runner.pid").write_text("1", encoding="utf-8")
    (tmp_path / "fleet.json").write_text("{}", encoding="utf-8")

    found = discover_videos(tmp_path)
    assert [path.name for path in found] == ["A clip.MP4", "b clip.mp4"]


def test_camera_records_are_stable_and_separate_from_the_sim(tmp_path):
    (tmp_path / "002_Delhi traffic_720P.mp4").write_bytes(b"d")
    (tmp_path / "videoplayback (1).mp4").write_bytes(b"v")
    (tmp_path / "001_Indian Number Plate Recognition_720P.mp4").write_bytes(b"i")

    records = camera_records(discover_videos(tmp_path))
    assert [rec["id"] for rec in records] == ["vid-01", "vid-02", "vid-03"]
    assert records[0]["name"] == "Indian Number Plate Recognition"
    assert records[1]["name"] == "Delhi traffic"
    assert records[2]["name"] == "Traffic clip 1"
    assert all(camera_in_source(str(rec["id"]), "video") for rec in records)
    assert not camera_in_source("cam-001", "video")
    assert camera_in_source("cam-001", "sim")

    fleet = write_fleet(records, tmp_path / "fleet.json")
    payload = json.loads(fleet.read_text(encoding="utf-8"))
    assert payload["cameras"][0]["id"] == "vid-01"
    assert str(payload["cameras"][0]["source"]).endswith(
        "001_Indian Number Plate Recognition_720P.mp4"
    )


def test_display_name_plain_playback():
    from pathlib import Path

    assert display_name(Path("videoplayback.mp4")) == "Traffic clip"
