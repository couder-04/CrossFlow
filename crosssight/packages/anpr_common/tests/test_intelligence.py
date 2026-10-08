"""Unit tests for dwell, health, OD, travel, matching, review, imports, and exports."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from anpr_common.intelligence.access import export_action, role_can
from anpr_common.intelligence.calibration import apply_homography, speed_kmh, validate_calibration
from anpr_common.intelligence.dwell import (
    DwellObservation,
    DwellThresholds,
    advance_dwell,
    classify_dwell,
    dwell_anomaly,
    thresholds_for_camera,
)
from anpr_common.intelligence.evalmetrics import (
    character_accuracy,
    latency_summary,
    matching_summary,
    summarize_ocr,
    tracking_consistency,
)
from anpr_common.intelligence.exporters import (
    clip_window,
    export_filename,
    overlay_plan,
    rows_to_csv,
    rows_to_json,
    rows_to_pdf,
)
from anpr_common.intelligence.filesafety import (
    object_key,
    redact_stream_url,
    safe_filename,
    validate_upload,
)
from anpr_common.intelligence.health import CameraHealthThresholds, classify_camera_health
from anpr_common.intelligence.importers import (
    preview_calibration,
    preview_cameras,
    preview_registry,
    preview_watchlist,
    preview_zones,
)
from anpr_common.intelligence.journeys import (
    aggregate_od,
    aggregate_vehicle_classes,
    journeys_from_sightings,
    sessionize_dwell,
    travel_statistics,
    valid_travel,
)
from anpr_common.intelligence.matching import UnavailableReIDEncoder, classify_link
from anpr_common.intelligence.review import ReviewError, transition_status

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
THRESH = DwellThresholds(short_s=45, excessive_s=120, incident_s=180, gap_s=90)
HEALTH = CameraHealthThresholds()


def _obs(seconds: int, camera: str = "cam-a", plate: str = "MH12AB1234") -> DwellObservation:
    return DwellObservation(
        vehicle_id=plate,
        plate=plate,
        camera_id=camera,
        ts=T0 + timedelta(seconds=seconds),
        confidence=0.91,
        vehicle_class="car",
        zone_id="zone-1",
        crop_key="crops/a.jpg",
        lat=18.5,
        lng=73.8,
        track_id=7,
    )


def test_dwell_classes():
    assert classify_dwell(30, THRESH).value == "short_stop"
    assert classify_dwell(150, THRESH).value == "excessive_dwell"
    assert classify_dwell(180, THRESH).value == "stopped_vehicle"


def test_stopped_vehicle_alerts_once_and_keeps_evidence():
    state, closed, alert = advance_dwell(None, _obs(0), THRESH)
    assert alert is False and closed is None
    state, _, alert = advance_dwell(state, _obs(80), THRESH)
    assert alert is False
    state, _, alert = advance_dwell(state, _obs(160), THRESH)
    assert alert is False
    state, _, alert = advance_dwell(state, _obs(240), THRESH)
    assert alert is True
    assert state.alerted is True
    assert state.dwell_s == 240
    state, _, alert = advance_dwell(state, _obs(260), THRESH)
    assert alert is False
    assert state.crop_key == "crops/a.jpg"
    assert state.track_id == 7


def test_camera_change_closes_dwell_without_merging():
    state, _, _ = advance_dwell(None, _obs(0), THRESH)
    state, _, _ = advance_dwell(state, _obs(60), THRESH)
    _state, closed, alert = advance_dwell(state, _obs(70, camera="cam-b"), THRESH)
    assert alert is False
    assert closed is not None
    assert closed["classification"] == "short_stop"
    assert closed["camera_id"] == "cam-a"


def test_per_camera_threshold_override():
    custom = thresholds_for_camera(
        "cam-a", THRESH, {"cam-a": {"incident_s": 240, "excessive_s": 200}}
    )
    assert classify_dwell(190, custom).value == "short_stop"
    assert thresholds_for_camera("cam-b", THRESH, {"cam-a": {"incident_s": 240}}) == THRESH


def test_dwell_anomaly_against_expected():
    assert dwell_anomaly(100, None) is None
    assert dwell_anomaly(400, 120)["kind"] == "long"
    assert dwell_anomaly(120, 120)["kind"] == "normal"


def test_camera_health_states():
    now = T0
    offline = classify_camera_health(
        status="offline", now=now, last_read=now, reads_in_window=10, thresholds=HEALTH
    )
    assert offline["state"] == "OFFLINE"
    stale = classify_camera_health(
        status="active",
        now=now,
        last_read=now - timedelta(seconds=400),
        reads_in_window=2,
        thresholds=HEALTH,
    )
    assert stale["state"] == "STALE"
    down = classify_camera_health(
        status="active", now=now, last_read=None, reads_in_window=0, thresholds=HEALTH
    )
    assert down["state"] == "OFFLINE"
    healthy = classify_camera_health(
        status="active",
        now=now,
        last_read=now - timedelta(seconds=20),
        reads_in_window=5,
        thresholds=HEALTH,
    )
    assert healthy["state"] == "HEALTHY"
    assert healthy["age_s"] == 20
    degraded = classify_camera_health(
        status="active",
        now=now,
        last_read=now,
        reads_in_window=0,
        thresholds=HEALTH,
        event_count=50,
        expected_events=1,
    )
    assert degraded["state"] == "DEGRADED"


def test_od_aggregation_counts_unique_vehicles():
    sightings = [
        ("v1", "cam-a", T0),
        ("v1", "cam-b", T0 + timedelta(minutes=5)),
        ("v2", "cam-a", T0 + timedelta(minutes=1)),
        ("v2", "cam-b", T0 + timedelta(minutes=6)),
        ("v1", "cam-a", T0 + timedelta(hours=2)),
    ]
    journeys = journeys_from_sightings(sightings, gap_s=1800)
    cells = aggregate_od(journeys)
    assert cells[0]["origin"] == "cam-a"
    assert cells[0]["destination"] == "cam-b"
    assert cells[0]["unique_vehicles"] == 2
    assert cells[0]["trip_count"] == 2


def test_travel_rejects_impossible_and_summarizes():
    assert valid_travel(0, 1000, max_speed_kmh=120) is None
    assert valid_travel(-5, 1000, max_speed_kmh=120) is None
    assert valid_travel(5, 5000, max_speed_kmh=120) is None
    ok = valid_travel(60, 1000, max_speed_kmh=120)
    assert ok is not None and ok["speed_kmh"] == pytest.approx(60.0)
    stats = travel_statistics([ok, valid_travel(120, 1000, max_speed_kmh=120)])
    assert stats["count"] == 2
    assert stats["min_s"] == 60
    assert stats["max_s"] == 120
    assert stats["median_s"] == 90
    assert stats["p90_s"] == pytest.approx(114.0)


def test_session_dwell_compares_expected():
    rows = [
        {
            "plate": "MH12AB1234",
            "camera_id": "cam-a",
            "ts": T0,
            "vehicle_class": "car",
            "confidence": 0.9,
        },
        {
            "plate": "MH12AB1234",
            "camera_id": "cam-a",
            "ts": T0 + timedelta(seconds=200),
            "vehicle_class": "car",
        },
    ]
    sessions = sessionize_dwell(
        rows,
        DwellThresholds(short_s=45, excessive_s=120, incident_s=180, gap_s=300),
        expected_by_camera={"cam-a": 60},
    )
    assert sessions[0]["dwell_s"] == 200
    assert sessions[0]["classification"] == "stopped_vehicle"
    assert sessions[0]["anomaly"]["kind"] == "long"


def test_vehicle_class_shares_use_detector_labels():
    summary = aggregate_vehicle_classes(
        [
            {"vehicle_class": "car", "camera_id": "cam-a", "zone_id": "z1"},
            {"vehicle_class": "car", "camera_id": "cam-a", "zone_id": "z1"},
            {"vehicle_class": "truck", "camera_id": "cam-b"},
            {"vehicle_class": "motorcycle", "camera_id": "cam-a"},
        ]
    )
    assert summary["total"] == 4
    assert summary["counts"]["car"] == 2
    assert summary["shares"]["truck"] == 0.25
    assert summary["by_camera"]["cam-a"]["motorcycle"] == 1


def test_plate_match_beats_topology_and_feature_is_not_claimed_without_embeddings():
    high = classify_link(
        plate_a="MH12AB1234",
        plate_b="mh12ab1234",
        conf_a=0.95,
        conf_b=0.9,
        class_a="car",
        class_b="car",
        elapsed_s=30,
        min_travel_s=10,
        max_travel_s=90,
        topology_ok=True,
    )
    assert high["kind"] == "HIGH_CONFIDENCE_PLATE_MATCH"
    fuzzy = classify_link(plate_a="MH12AB1234", plate_b="MH12AB1235", conf_a=0.9, conf_b=0.9)
    assert fuzzy["kind"] == "FUZZY_PLATE_MATCH"
    topo = classify_link(
        plate_a="MH12AB1234",
        plate_b="KA01ZZ0001",
        conf_a=0.4,
        conf_b=0.4,
        class_a="bus",
        class_b="bus",
        elapsed_s=40,
        min_travel_s=20,
        max_travel_s=80,
        topology_ok=True,
    )
    assert topo["kind"] == "TOPOLOGY_TIME_MATCH"
    feature = classify_link(
        plate_a="MH12AB1234",
        plate_b="KA01ZZ0001",
        conf_a=0.2,
        conf_b=0.2,
        class_a="car",
        class_b="car",
        embedding_a=[1.0, 0.0],
        embedding_b=[1.0, 0.0],
    )
    assert feature["kind"] == "FEATURE_MATCH"
    assert feature["evaluated"] is False
    with pytest.raises(RuntimeError):
        UnavailableReIDEncoder().embed(b"frame")


def test_review_transitions_require_notes_and_block_illegal_moves():
    assert transition_status("new", "reviewing", None) == "reviewing"
    assert transition_status("reviewing", "approved", "checked plate") == "approved"
    with pytest.raises(ReviewError):
        transition_status("reviewing", "approved", "  ")
    with pytest.raises(ReviewError):
        transition_status("closed", "approved", "nope")
    with pytest.raises(ReviewError):
        transition_status("dismissed", "approved", "note")


def test_rbac_matrix():
    assert role_can("analyst", "view_health")
    assert not role_can("analyst", "upload_video")
    assert not role_can("analyst", "review_alert")
    assert not role_can("operator", "import_registry")
    assert role_can("admin", "import_calibration")
    assert export_action("od") == "export_aggregates"
    assert export_action("investigation") == "export_plates"
    assert not role_can("analyst", export_action("watchlist"))


def test_upload_magic_bytes_and_limits():
    jpeg = b"\xff\xd8\xff" + b"\x00" * 16
    meta = validate_upload(
        filename="plate.jpg", data=jpeg, kind="image", max_bytes=1024, claimed_type="image/jpeg"
    )
    assert meta["mime"] == "image/jpeg"
    with pytest.raises(ValueError):
        validate_upload(filename="plate.jpg", data=b"not-an-image", kind="image", max_bytes=1024)
    with pytest.raises(ValueError):
        validate_upload(filename="../etc/passwd.jpg", data=jpeg, kind="image", max_bytes=1024)
    with pytest.raises(ValueError):
        validate_upload(filename="big.jpg", data=jpeg, kind="image", max_bytes=8)
    mp4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 8
    assert (
        validate_upload(filename="cctv.mp4", data=mp4, kind="video", max_bytes=1024)["mime"]
        == "video/mp4"
    )
    with pytest.raises(ValueError):
        validate_upload(filename="cctv.exe", data=mp4, kind="video", max_bytes=1024)
    assert safe_filename("../../a b.jpg") == "a_b.jpg"
    assert object_key("uploads/../video", "abc", "cam.mp4") == "uploads/video/abc/cam.mp4"
    assert redact_stream_url("rtsp://user:secret@10.0.0.5:554/live") == "rtsp://10.0.0.5:554/live"


def test_watchlist_preview_keeps_invalid_and_duplicates():
    text = "plate,reason,priority,valid_from,valid_until,notes\nMH12AB1234,stolen,high,2026-01-01,2026-12-31,night\nNOTAPLATE,x,high,,,\nMH12AB1234,again,high,,,\n"
    preview = preview_watchlist(text, existing=set())
    assert preview["valid_count"] == 1
    assert preview["invalid_count"] == 1
    assert preview["duplicate_count"] == 1
    assert preview["invalid"][0]["errors"]
    again = preview_watchlist(
        "plate,reason\nMH12AB1234,stolen\n",
        existing={"MH12AB1234"},
    )
    assert again["duplicate_count"] == 1
    assert again["valid_count"] == 0
    with pytest.raises(ValueError):
        preview_watchlist("reason\nstolen\n")
    with pytest.raises(ValueError):
        preview_watchlist('plate,reason\n"unterminated,x\n')


def test_registry_rejects_personal_columns_and_bad_class():
    with pytest.raises(ValueError):
        preview_registry("plate,owner_name\nMH12AB1234,Ada\n")
    preview = preview_registry(
        "plate,vehicle_class,color,registration_status,owner_ref\nMH12AB1234,car,white,active,REF1\nMH12AB1234,car,white,active,REF1\nKA01AA0001,spaceship,red,active,\n"
    )
    assert preview["valid_count"] == 1
    assert preview["duplicate_count"] == 1
    assert preview["invalid_count"] == 1
    assert "owner_ref" in preview["valid"][0]


def test_camera_calibration_and_zone_previews():
    cams = preview_cameras(
        "camera_id,name,latitude,longitude,status,rtsp_url\ncam-9,Gate,18.5,73.8,active,rtsp://10.0.0.8/live\ncam-1,Old,18.5,73.8,active,\n",
        fmt="csv",
        existing={"cam-1"},
    )
    assert cams["valid_count"] == 1
    assert cams["duplicate_count"] == 1
    assert cams["valid"][0]["stream_ref"] == "rtsp://10.0.0.8/live"
    secrets = preview_cameras(
        "camera_id,name,latitude,longitude,rtsp_url\ncam-3,X,1,2,rtsp://user:secret@10.0.0.1/live\n",
        fmt="csv",
        existing=set(),
    )
    assert secrets["invalid_count"] == 1
    calib = preview_calibration(
        '{"camera_id":"cam-1","homography":[[1,0,0],[0,1,0],[0,0,1]],"coordinate_reference":"epsg:32643"}'
    )
    assert calib["valid_count"] == 1
    bad = preview_calibration('{"camera_id":"cam-1","homography":[[0,0,0],[0,0,0],[0,0,0]]}')
    assert bad["invalid_count"] == 1
    assert validate_calibration({"camera_id": ""})
    zones = preview_zones(
        '{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"id":"z1","name":"Ward","kind":"ward"},"geometry":{"type":"Polygon","coordinates":[[[73.8,18.5],[73.9,18.5],[73.9,18.6],[73.8,18.5]]]}}]}'
    )
    assert zones["valid_count"] == 1
    broken = preview_zones(
        '{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"id":"z2","kind":"nope"},"geometry":{"type":"Point","coordinates":[0,0]}}]}'
    )
    assert broken["invalid_count"] == 1


def test_homography_maps_pixels():
    x, y = apply_homography([[2, 0, 10], [0, 3, -5], [0, 0, 1]], 4, 6)
    assert (x, y) == (18, 13)
    assert speed_kmh((0, 0), (1000, 0), 60) == pytest.approx(60.0)
    assert speed_kmh((0, 0), (1, 0), 0) is None


def test_exports_are_structured_and_pdf_is_a_pdf():
    csv_bytes = rows_to_csv(["plate", "note"], [["MH12AB1234", "=cmd"], ["KA01AA0001", "ok"]])
    text = csv_bytes.decode("utf-8")
    assert text.startswith("plate,note")
    assert "'=cmd" in text
    payload = rows_to_json({"kind": "od", "rows": [{"origin": "a"}]})
    assert b'"origin"' in payload
    pdf = rows_to_pdf(
        "OD matrix",
        ["generated 2026-06-01", "cameras: cam-a"],
        [("Trips", ["cam-a -> cam-b  12"])],
    )
    assert pdf.startswith(b"%PDF-1.4")
    assert b"CrossSight" in pdf
    name = export_filename("od", "csv", T0, "abcdef123456")
    assert name.startswith("crosssight_od_")
    assert name.endswith(".csv")


def test_evidence_clip_window_and_overlay_use_existing_metadata():
    assert clip_window(10, 5, 5, 30) == (5, 15)
    assert clip_window(1, 5, 2, 30) == (0, 3)
    assert clip_window(10, 1, 1, 0) is None
    plan = overlay_plan(
        {
            "bbox": [1, 2, 3, 4],
            "track_id": 9,
            "plate": "MH12AB1234",
            "confidence": 0.8,
            "vehicle_class": "car",
            "alert": "stopped_vehicle",
        }
    )
    assert plan[0]["op"] == "rect"
    assert any(str(item.get("text", "")).startswith("MH12AB1234") for item in plan)


def test_eval_metrics_do_not_invent_accuracy():
    empty = summarize_ocr([])
    assert empty["exact"] is None
    assert "not claimed" in empty["claim"]
    scored = summarize_ocr([("MH12AB1234", "MH12AB1234"), ("MH12AB1235", "MH12AB9999")])
    assert scored["n"] == 2
    assert scored["exact"] == 0.5
    assert character_accuracy("MH12AB1234", "MH12AB1234") == 1
    assert (
        tracking_consistency(
            [("1", "MH12AB1234"), ("1", "MH12AB1234"), ("2", "KA01AA0001"), ("2", "DL1AA0001")]
        )
        == 0.5
    )
    assert matching_summary([{"kind": "FUZZY_PLATE_MATCH"}])["accuracy"] is None
    assert latency_summary([10, 30, 20])["mean_ms"] == 20
