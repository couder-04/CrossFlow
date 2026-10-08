"""Heavy report artifacts stay out of git; the cited summaries stay in."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOVED = {
    "reports/video_demo_annotated2.mp4",
    "reports/city_load_run_A.log",
    "reports/city_load_run_A2.log",
    "reports/city_load_run_B.log",
    "reports/city_load_run_C.log",
}
KEPT = {
    "reports/ocr_benchmark.md",
    "reports/ocr_benchmark.json",
    "reports/FINAL_RESULTS.md",
    "reports/FINAL_RESULTS.json",
    "reports/scenario_proof.json",
    "reports/city_load_summary.json",
}


def test_heavy_reports_are_untracked_and_summaries_stay() -> None:
    tracked = set(
        subprocess.check_output(["git", "ls-files", "reports"], text=True, cwd=ROOT).split()
    )
    assert MOVED.isdisjoint(tracked)
    assert KEPT <= tracked
    ignore = (ROOT / ".gitignore").read_text()
    assert "/artifacts/" in ignore
    readme = (ROOT / "reports" / "README.md").read_text()
    assert "make benchmark-ocr" in readme
    assert "make city-load" in readme
