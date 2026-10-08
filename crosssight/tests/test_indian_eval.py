"""The Indian evaluation report stays empty of accuracy claims without ground truth."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.indian.run_eval import evaluate_tree


def test_empty_scene_tree_does_not_claim_accuracy(tmp_path: Path):
    report = evaluate_tree(tmp_path)
    assert report["ocr"]["exact"] is None
    assert "not claimed" in report["ocr"]["claim"]
    assert {scene["scene"] for scene in report["scenes"]} == {
        "day",
        "night",
        "toll",
        "highway",
        "urban",
        "gantry",
    }


def test_ground_truth_is_scored_only_when_predictions_exist(tmp_path: Path):
    day = tmp_path / "day"
    day.mkdir()
    (day / "clip.mp4").write_bytes(b"not-a-real-video")
    (day / "gt.csv").write_text("image_or_video,plate\nclip.mp4,MH12AB1234\n", encoding="utf-8")
    missed = evaluate_tree(tmp_path)
    assert missed["ocr"]["n"] == 0
    scored = evaluate_tree(tmp_path, {"day/clip.mp4": "MH12AB1234"})
    assert scored["ocr"]["exact"] == 1
    assert scored["scenes"][0]["media"] == 1
