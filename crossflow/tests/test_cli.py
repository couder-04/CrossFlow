from __future__ import annotations

from pathlib import Path

from crossflow.cli import main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_status_runs_against_the_shipped_results(capsys):
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "XtraFlow published results: available" in out
    assert "balanced" in out and "95% CI" in out


def test_bridge_subcommand_writes_counts(tmp_path, capsys):
    out = tmp_path / "observed.csv"
    code = main(
        [
            "bridge",
            "--flows",
            str(EXAMPLES / "flows.sample.jsonl"),
            "--camera-map",
            str(EXAMPLES / "camera_map.json"),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    assert out.read_text().splitlines()[0] == "approach,count_veh_h"
    assert "N=528" in capsys.readouterr().out


def test_bridge_subcommand_reports_bad_input(tmp_path, capsys):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"camera_id": "x"}\n')
    assert main(["bridge", "--flows", str(bad), "--out", str(tmp_path / "o.csv")]) == 2
    assert "error:" in capsys.readouterr().err
