"""Tests for the OCR evaluation harness."""

from pathlib import Path

from ocr_engine.eval import load_eval_csv, run_eval, write_report
from ocr_engine.recognize import MockRecognizer


def test_load_eval_csv(fixture_dir: Path):
    rows = load_eval_csv(fixture_dir / "labels.csv", fixture_dir)
    assert len(rows) >= 3
    assert rows[0].gt_plate


def test_eval_harness_runs(fixture_dir: Path, tmp_path: Path):
    rows = load_eval_csv(fixture_dir / "labels.csv", fixture_dir)
    gt_map = {str(r.image_path): r.gt_plate for r in rows}
    gt_map.update({r.image_path.name: r.gt_plate for r in rows})
    recognizer = MockRecognizer(ground_truth=gt_map)
    metrics = run_eval(rows, recognizer, {"recognizer": "MockRecognizer"})
    assert metrics.total == len(rows)
    assert metrics.exact_match == metrics.total
    report = tmp_path / "report.md"
    write_report(metrics, report, {"recognizer": "MockRecognizer"})
    text = report.read_text()
    assert "Plate exact-match accuracy" in text
    assert "1.0000" in text
