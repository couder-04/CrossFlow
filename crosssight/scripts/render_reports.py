#!/usr/bin/env python3
"""Render markdown tables from measured report JSON. Does not invent metrics."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"

RATE_KEYS = ("found", "exact", "exact_o0", "char_acc")


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, (dict, list)):
        return ""
    return str(value).replace("|", "\\|")


def _rate(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        number = float(value)
        if 0.0 <= number <= 1.0:
            return f"{number * 100:.1f}%"
        return f"{number:.4g}"
    return _cell(value)


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines).rstrip() + "\n"
    path.write_text(text, encoding="utf-8")
    print(f"Wrote {path}")


def render_ocr_benchmark(payload: dict) -> list[str]:
    lines = [
        "# OCR benchmark results (measured)",
        "",
        f"Generated: `{payload.get('generated_at', '')}`",
        "",
        "Numbers below are copied from `reports/ocr_benchmark.json`.",
        "**No fabricated metrics.**",
        "",
    ]
    device = payload.get("device_note")
    if device:
        lines.append(f"Device: {device}")
        lines.append("")
    for run in payload.get("runs") or []:
        lines.append(f"## {run.get('name', 'run')}")
        lines.append("")
        lines.append(f"- OCR model: `{run.get('ocr', '')}`")
        lines.append(f"- Plate format: `{run.get('plate_format', '')}`")
        note = run.get("note")
        if note:
            lines.append(f"- Note: {note}")
        lines.append("")
        rows = run.get("rows") or []
        lines.append(
            "| set | plates | found | exact | exact (O=0) | char acc | extra | ms med | ms p95 |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for row in rows:
            lines.append(
                "| {set} | {plates} | {found} | {exact} | {exact_o0} | {char_acc} | {extra} | {ms_med} | {ms_p95} |".format(
                    set=row.get("set", ""),
                    plates=row.get("plates", ""),
                    found=row.get("found", ""),
                    exact=row.get("exact", ""),
                    exact_o0=row.get("exact_o0", ""),
                    char_acc=row.get("char_acc", ""),
                    extra=row.get("extra", ""),
                    ms_med=row.get("ms_med", ""),
                    ms_p95=row.get("ms_p95", ""),
                )
            )
            by_state = row.get("by_state")
            if isinstance(by_state, dict) and by_state:
                lines.append("")
                lines.append(f"### {row.get('set', 'set')} by state")
                lines.append("")
                lines.append("| state | n | exact |")
                lines.append("|---|---:|---:|")
                for state, stats in by_state.items():
                    if not isinstance(stats, dict):
                        continue
                    lines.append(
                        f"| {state} | {stats.get('n', '')} | {_rate(stats.get('exact'))} |"
                    )
                lines.append("")
        lines.append("")
    return lines


def render_final_results(payload: dict) -> list[str]:
    lines = [
        "# Final measured OCR results",
        "",
        f"Generated: `{payload.get('generated_at', '')}`",
        "",
        "Source: `reports/FINAL_RESULTS.json`. Rates are the stored fractions shown as percents.",
        "**No fabricated metrics.**",
        "",
    ]
    for note in payload.get("notes") or []:
        lines.append(f"- {note}")
    if payload.get("notes"):
        lines.append("")
    headers = [
        "tag",
        "n",
        "found",
        "exact",
        "exact_o0",
        "char_acc",
        "extra",
        "ms_med",
        "ms_p95",
        "failure_count",
    ]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join("---:" if h != "tag" else "---" for h in headers) + "|")
    state_blocks: list[tuple[str, dict]] = []
    for row in payload.get("results") or []:
        cells = []
        for key in headers:
            value = row.get(key, "")
            if key in RATE_KEYS:
                cells.append(_rate(value) if value != "" else "")
            elif key in ("ms_med", "ms_p95") and isinstance(value, float):
                cells.append(f"{value:.1f}")
            else:
                cells.append(_cell(value))
        lines.append("| " + " | ".join(cells) + " |")
        by_state = row.get("by_state")
        if isinstance(by_state, dict) and by_state:
            state_blocks.append((str(row.get("tag", "")), by_state))
    lines.append("")
    for tag, by_state in state_blocks:
        lines.append(f"## {tag} by state")
        lines.append("")
        lines.append("| state | n | exact |")
        lines.append("|---|---:|---:|")
        for state, stats in by_state.items():
            if not isinstance(stats, dict):
                continue
            lines.append(f"| {state} | {stats.get('n', '')} | {_rate(stats.get('exact'))} |")
        lines.append("")
    return lines


def main() -> int:
    mapping = {
        "ocr_benchmark.json": ("ocr_benchmark.md", render_ocr_benchmark),
        "FINAL_RESULTS.json": ("FINAL_RESULTS.md", render_final_results),
    }
    wrote = 0
    for src_name, (dest_name, renderer) in mapping.items():
        src = REPORTS / src_name
        if not src.is_file():
            print(f"skip {src_name} (not present)")
            continue
        payload = json.loads(src.read_text(encoding="utf-8"))
        _write(REPORTS / dest_name, renderer(payload))
        wrote += 1
    if wrote == 0:
        print("No report JSON found to render")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
