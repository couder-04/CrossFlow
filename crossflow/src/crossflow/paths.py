"""Where things live. Everything can be overridden with an environment variable."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """Repository root: the nearest ancestor holding both ``crosssight/`` and ``xtraflow/``."""
    override = os.environ.get("CROSSFLOW_ROOT")
    if override:
        return Path(override).resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "xtraflow" / "config.yaml").exists() and (parent / "crosssight").is_dir():
            return parent
    raise FileNotFoundError(
        "Could not locate the CrossFlow repository root; set CROSSFLOW_ROOT to the checkout."
    )


def xtraflow_dir() -> Path:
    override = os.environ.get("CROSSFLOW_XTRAFLOW_DIR")
    return Path(override).resolve() if override else repo_root() / "xtraflow"


def xtraflow_results() -> Path:
    override = os.environ.get("CROSSFLOW_XTRAFLOW_RESULTS")
    return Path(override).resolve() if override else xtraflow_dir() / "results"


def runs_dir() -> Path:
    """Where ``crossflow run`` writes its reports (git-ignored)."""
    override = os.environ.get("CROSSFLOW_RUNS_DIR")
    return Path(override).resolve() if override else repo_root() / "runs"
