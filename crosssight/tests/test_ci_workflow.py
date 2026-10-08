"""CrossSight CI runs lint, types, tests, and the dashboard build.

In CrossFlow the workflow lives at the repository root and is
scoped to ``crosssight/`` with a default working directory.
"""

from pathlib import Path

WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "crosssight.yml"
WORKFLOW = WORKFLOW_PATH.read_text()


def test_ci_workflow_covers_lint_types_tests_and_dashboard() -> None:
    for job in ("lint:", "types:", "tests:", "dashboard:"):
        assert job in WORKFLOW
    assert "uv sync --frozen --all-packages" in WORKFLOW
    assert "uv run ruff check ." in WORKFLOW
    assert "uv run ruff format --check ." in WORKFLOW
    assert (
        "uv run mypy packages services --ignore-missing-imports --python-version 3.11" in WORKFLOW
    )
    assert "uv run pytest -q" in WORKFLOW
    assert "npx pnpm@9 install --frozen-lockfile" in WORKFLOW
    assert "npx pnpm@9 typecheck" in WORKFLOW
    assert "npx pnpm@9 build" in WORKFLOW


def test_ci_workflow_is_scoped_to_the_crosssight_directory() -> None:
    assert "working-directory: crosssight" in WORKFLOW
    assert 'paths: ["crosssight/**"' in WORKFLOW
