#!/usr/bin/env python3
"""Run PlateOCR evaluate.py across all downloaded labelled sets; write JSON+MD report.

Measured numbers only — never fabricate. Run from repo root:
  UV_PROJECT_ENVIRONMENT=.venv311 uv run python scripts/run_ocr_benchmarks.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "PlateOCR"
REPORTS = ROOT / "reports"
DATA = ROOT / "data"


def run_eval(
    sets: list[str],
    ocr: str,
    plate_format: str | None,
    failures_name: str,
    device: str = "cpu",
) -> str:
    """Invoke vendor evaluate.py; return stdout."""
    cmd = [
        sys.executable,
        "evaluate.py",
        "--sets",
        *sets,
        "--ocr",
        ocr,
        "--device",
        device,
        "--failures",
        str(REPORTS / failures_name),
    ]
    if plate_format:
        cmd.extend(["--plate-format", plate_format])
    env = os.environ.copy()
    env["PYTHONPATH"] = str(VENDOR) + os.pathsep + env.get("PYTHONPATH", "")
    print(f"\n=== RUN {' '.join(cmd[2:])} ===", flush=True)
    t0 = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=str(VENDOR),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = time.perf_counter() - t0
    out = (proc.stdout or "") + (proc.stderr or "")
    print(out, flush=True)
    if proc.returncode != 0:
        print(f"FAILED rc={proc.returncode} after {elapsed:.1f}s", flush=True)
    else:
        print(f"OK in {elapsed:.1f}s", flush=True)
    return out


def parse_table(stdout: str) -> list[dict]:
    rows = []
    for line in stdout.splitlines():
        parts = line.split()
        if not parts or parts[0] in ("set", "detector=") or parts[0].startswith("==="):
            continue
        # set plates found% exact% O=0% chars% extra ms_med ms_p95
        if len(parts) >= 9 and parts[0] in ("eu", "br", "us", "in_full", "in_crops"):
            try:
                rows.append(
                    {
                        "set": parts[0],
                        "plates": int(parts[1]),
                        "found": parts[2],
                        "exact": parts[3],
                        "exact_o0": parts[4],
                        "char_acc": parts[5],
                        "extra": int(parts[6]),
                        "ms_med": float(parts[7]),
                        "ms_p95": float(parts[8]),
                    }
                )
            except ValueError:
                continue
    return rows


def main() -> int:
    REPORTS.mkdir(parents=True, exist_ok=True)
    # Ensure vendor/data points at repo data/
    link = VENDOR / "data"
    if not link.exists():
        link.symlink_to(DATA)

    required = [
        DATA / "openalpr_benchmarks" / "endtoend",
        DATA / "indian_zenitsu",
        DATA / "indian_datacluster",
    ]
    for p in required:
        if not p.exists():
            print(f"Missing dataset: {p}", file=sys.stderr)
            return 1

    configs = [
        {
            "name": "openalpr_global",
            "sets": ["eu", "br", "us"],
            "ocr": "cct-s-v2-global-model",
            "plate_format": "none",
            "failures": "failures_openalpr_global.json",
        },
        {
            "name": "india_v1_format",
            "sets": ["in_full", "in_crops"],
            "ocr": "india-v1",
            "plate_format": "india",
            "failures": "failures_india_v1.json",
        },
        {
            "name": "india_crops_global_baseline",
            "sets": ["in_crops"],
            "ocr": "cct-s-v2-global-model",
            "plate_format": "none",
            "failures": "failures_india_global.json",
        },
        {
            "name": "openalpr_india_v1_cross",
            "sets": ["eu", "br", "us"],
            "ocr": "india-v1",
            "plate_format": "india",
            "failures": "failures_openalpr_india_v1.json",
        },
    ]

    all_results: dict = {
        "generated_at": datetime.now(UTC).isoformat(),
        "device_note": "CPU/auto via onnxruntime (macOS CoreML/CPU)",
        "runs": [],
    }

    for cfg in configs:
        stdout = run_eval(cfg["sets"], cfg["ocr"], cfg["plate_format"], cfg["failures"])
        rows = parse_table(stdout)
        all_results["runs"].append(
            {
                "name": cfg["name"],
                "ocr": cfg["ocr"],
                "plate_format": cfg["plate_format"],
                "sets": cfg["sets"],
                "rows": rows,
                "stdout": stdout[-4000:],
            }
        )

    (REPORTS / "ocr_benchmark.json").write_text(json.dumps(all_results, indent=2))

    # Markdown summary
    lines = [
        "# OCR benchmark results (measured)",
        "",
        f"Generated: `{all_results['generated_at']}`",
        "",
        "Numbers below come only from `vendor/PlateOCR/evaluate.py` on downloaded datasets.",
        "**No fabricated metrics.**",
        "",
    ]
    for run in all_results["runs"]:
        lines.append(f"## {run['name']}")
        lines.append("")
        lines.append(f"- OCR model: `{run['ocr']}`")
        lines.append(f"- Plate format: `{run['plate_format']}`")
        lines.append("")
        lines.append(
            "| set | plates | found | exact | exact (O=0) | char acc | extra | ms med | ms p95 |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for r in run["rows"]:
            lines.append(
                f"| {r['set']} | {r['plates']} | {r['found']} | {r['exact']} | {r['exact_o0']} | "
                f"{r['char_acc']} | {r['extra']} | {r['ms_med']} | {r['ms_p95']} |"
            )
        lines.append("")

    # Claims section derived from measured rows
    lines.extend(
        [
            "## Claims (from measurements above)",
            "",
        ]
    )
    claim_lines: list[str] = []
    for run in all_results["runs"]:
        if run["name"] == "openalpr_global" and run["rows"]:
            # Combined exact weighted by plates
            tot = sum(r["plates"] for r in run["rows"])

            # parse percent strings like 90.8%
            def pct(s: str) -> float:
                return float(s.strip("%")) / 100.0

            exact = sum(r["plates"] * pct(r["exact"]) for r in run["rows"]) / tot if tot else 0
            found = sum(r["plates"] * pct(r["found"]) for r in run["rows"]) / tot if tot else 0
            claim_lines.append(
                f"- **OpenALPR EU+BR+US** with `cct-s-v2-global-model`: "
                f"**{exact * 100:.1f}%** exact plate match, **{found * 100:.1f}%** plate found "
                f"(n={tot})."
            )
        if run["name"] == "india_v1_format":
            for r in run["rows"]:
                claim_lines.append(
                    f"- **{r['set']}** with `india-v1` + `plate_format=india`: "
                    f"**{r['exact']}** exact, **{r['char_acc']}** char acc "
                    f"(n={r['plates']}, found={r['found']})."
                )
        if run["name"] == "india_crops_global_baseline":
            for r in run["rows"]:
                claim_lines.append(
                    f"- **{r['set']}** with global `cct-s-v2` (no India fine-tune): "
                    f"**{r['exact']}** exact (n={r['plates']})."
                )
    if not claim_lines:
        claim_lines.append("- (No parsed table rows — see JSON stdout dumps.)")
    lines.extend(claim_lines)
    lines.append("")
    lines.append("Failure JSON dumps: `reports/failures_*.json`.")
    lines.append("")

    md_path = REPORTS / "ocr_benchmark.md"
    md_path.write_text("\n".join(lines))
    print(f"\nWrote {md_path}")
    print(f"Wrote {REPORTS / 'ocr_benchmark.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
