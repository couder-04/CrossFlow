"""Shared pytest fixtures."""

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def fixture_dir() -> Path:
    root = Path(__file__).resolve().parents[1] / "fixtures" / "eval_set"
    if not (root / "labels.csv").is_file():
        from ocr_engine.train.synth_plates import generate_eval_fixture

        generate_eval_fixture(root)
    return root
