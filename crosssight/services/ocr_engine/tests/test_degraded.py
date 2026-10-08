"""Tests for degraded mode when plate weights are missing."""

import subprocess
import sys


def test_validate_plate_weights_exits_on_missing():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from ocr_engine.detect import validate_plate_weights; validate_plate_weights('')",
        ],
        capture_output=True,
        text=True,
        check=False,
        env={
            **__import__("os").environ,
            "PYTHONPATH": "services/ocr_engine/src:packages/anpr_common/src",
        },
    )
    assert result.returncode == 1
    assert "PLATE_DET_WEIGHTS" in result.stderr or "PLATE_DET_WEIGHTS" in result.stdout


def test_validate_plate_weights_exits_on_not_found():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from ocr_engine.detect import validate_plate_weights; "
                "validate_plate_weights('/nonexistent/plate.pt')"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={
            **__import__("os").environ,
            "PYTHONPATH": "services/ocr_engine/src:packages/anpr_common/src",
        },
    )
    assert result.returncode == 1
