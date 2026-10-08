"""End-to-end YOLO feasibility demo on highway / CCTV clips.

Prefers straight-road one-way clips (`cam_*_hwy.mp4`), falls back to Bellevue
junction clips. Runs overlay + counts, builds a 2×2 mosaic, writes provenance.

Label: Input feasibility — not a fuel-saving result
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from demo.yolo_decisions import enrich_summary
from sim.util import ROOT, save_json

# Prefer straight one-way highway clips; Bellevue junctions as fallback.
CAMERAS = [
    (
        "N",
        ["cam_N_hwy.mp4", "cam_N_eastgate.mp4"],
        "Straight multi-lane (Zenodo cam_10) / Bellevue Eastgate",
    ),
    (
        "E",
        ["cam_E_hwy.mp4", "cam_E_newport.mp4"],
        "Straight street (Zenodo cam_16) / Bellevue Newport",
    ),
    (
        "S",
        ["cam_S_hwy.mp4", "cam_S_se38th.mp4"],
        "I-5 S @ 188th St (WSDOT) / Bellevue SE 38th",
    ),
    (
        "W",
        ["cam_W_hwy.mp4", "cam_W_ne8th.mp4"],
        "Divided arterial (Zenodo cam_11) / Bellevue NE 8th",
    ),
]

HWY_META = {
    "source": "Mixed research CCTV — straight / one-way corridors",
    "attribution": (
        "Zenodo DOI 10.5281/zenodo.3986141 (CC BY 4.0); "
        "Seattle I-5 trafficdb (WSDOT / Chan–Vasconcelos)"
    ),
    "note": (
        "Four independent corridor cameras mapped to N/E/S/W for a mosaic. "
        "Not synchronized approaches of one intersection. "
        "S is true southbound one-way highway; N/E/W are Zenodo arterial cams."
    ),
}

BELLEVUE_META = {
    "source": "Bellevue Traffic Video Dataset (City of Bellevue) — research use",
    "attribution": "https://github.com/City-of-Bellevue/TrafficVideoDataset",
    "note": (
        "Four cameras are four different Bellevue junctions, mapped to N/E/S/W "
        "for a mosaic demo. Not synchronized approaches of one intersection."
    ),
}


def _have(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.check_call(cmd)


def ensure_roi(path: Path) -> Path:
    if path.exists():
        return path
    src = ROOT / "perception" / "roi.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def resolve_cameras(video_dir: Path) -> list[tuple[str, Path, str, str]]:
    """Return (approach, path, title, kind) where kind is hwy|bellevue."""
    present = []
    for approach, filenames, title in CAMERAS:
        chosen = None
        kind = "bellevue"
        for name in filenames:
            src = video_dir / name
            if _have(src):
                chosen = src
                kind = "hwy" if "_hwy" in name else "bellevue"
                break
        if chosen is None:
            print(f"skip missing {approach}: tried {filenames}", flush=True)
            continue
        present.append((approach, chosen, title, kind))
    return present


def _frame_totals(counts_blob: dict) -> dict:
    """Per-frame total vehicles + pressure across ROI approaches."""
    rows = []
    for step in counts_blob.get("series") or []:
        total_veh = 0
        total_p = 0.0
        by_class = {"car": 0, "two_wheeler": 0, "bus": 0, "truck": 0, "auto_rickshaw": 0}
        for vals in (step.get("counts") or {}).values():
            for k in by_class:
                by_class[k] += int(vals.get(k) or 0)
            total_p += float(vals.get("weighted_pressure") or 0)
            total_veh += sum(int(vals.get(k) or 0) for k in by_class)
        rows.append(
            {
                "frame": step.get("frame"),
                "vehicles": total_veh,
                "weighted_pressure": total_p,
                **by_class,
            }
        )
    return {"series": rows}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--video-dir", default=str(ROOT / "data" / "video"))
    p.add_argument("--roi", default=str(ROOT / "perception" / "roi.yaml"))
    p.add_argument("--model", default="yolov8n.pt")
    p.add_argument("--out-dir", default=str(ROOT / "results" / "demo" / "yolo"))
    p.add_argument("--skip-mosaic", action="store_true")
    args = p.parse_args()

    video_dir = Path(args.video_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    roi = ensure_roi(Path(args.roi))

    present = resolve_cameras(video_dir)
    if not present:
        raise SystemExit(
            f"No clips in {video_dir}. Place cam_{{N,E,S,W}}_hwy.mp4 "
            "(preferred) or Bellevue cam_*_*.mp4 files."
        )

    using_hwy = any(k == "hwy" for *_, k in present)
    meta = HWY_META if using_hwy else BELLEVUE_META

    py = sys.executable
    overlays = []
    counts_paths = []
    per_cam_stats = {}
    for approach, src, title, kind in present:
        overlay = out_dir / f"overlay_{approach}.mp4"
        counts = out_dir / f"counts_{approach}.json"
        _run(
            [
                py,
                "-m",
                "demo.yolo_overlay",
                "--video",
                str(src),
                "--roi",
                str(roi),
                "--model",
                args.model,
                "--out",
                str(overlay),
            ]
        )
        _run(
            [
                py,
                "-m",
                "perception.yolo_counts",
                "--video",
                str(src),
                "--roi",
                str(roi),
                "--model",
                args.model,
                "--out",
                str(counts),
                "--fps",
                "3",
            ]
        )
        overlays.append(overlay)
        counts_paths.append(counts)
        blob = json.loads(counts.read_text(encoding="utf-8"))
        totals = _frame_totals(blob)
        series = totals["series"]
        peak = max((r["vehicles"] for r in series), default=0)
        mean_v = (sum(r["vehicles"] for r in series) / len(series)) if series else 0.0
        mean_p = (
            sum(r["weighted_pressure"] for r in series) / len(series) if series else 0.0
        )
        per_cam_stats[approach] = {
            "title": title,
            "kind": kind,
            "source_video": str(src.relative_to(ROOT)),
            "overlay": str(overlay.relative_to(ROOT)),
            "counts": str(counts.relative_to(ROOT)),
            "n_samples": len(series),
            "peak_vehicles": peak,
            "mean_vehicles": round(mean_v, 2),
            "mean_weighted_pressure": round(mean_p, 2),
            "last": series[-1] if series else None,
            "totals_series": series,
        }

    mosaic = out_dir / "yolo_mosaic.mp4"
    if not args.skip_mosaic and len(overlays) >= 2:
        tiles = list(overlays)
        while len(tiles) < 4:
            tiles.append(tiles[-1])
        scaled = []
        for i, clip in enumerate(tiles[:4]):
            s = out_dir / f"_tile_{i}.mp4"
            _run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(clip),
                    "-vf",
                    "scale=640:360",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "23",
                    "-an",
                    str(s),
                ]
            )
            scaled.append(s)
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(scaled[0]),
                "-i",
                str(scaled[1]),
                "-i",
                str(scaled[2]),
                "-i",
                str(scaled[3]),
                "-filter_complex",
                "[0:v][1:v][2:v][3:v]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0[v]",
                "-map",
                "[v]",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-an",
                str(mosaic),
            ]
        )
        for s in scaled:
            s.unlink(missing_ok=True)

    primary = overlays[0]

    clubbed = {
        "peak_vehicles": sum(s["peak_vehicles"] for s in per_cam_stats.values()),
        "mean_vehicles": round(
            sum(s["mean_vehicles"] for s in per_cam_stats.values()), 2
        ),
        "mean_weighted_pressure": round(
            sum(s["mean_weighted_pressure"] for s in per_cam_stats.values()), 2
        ),
        "n_cameras": len(per_cam_stats),
    }

    summary = {
        "label": "Input feasibility — not a fuel-saving result",
        **meta,
        "cameras": [
            {
                "approach": a,
                "file": str(src.relative_to(ROOT)),
                "title": t,
                "kind": k,
            }
            for a, src, t, k in present
        ],
        "outputs": {
            "overlays": [str(p.relative_to(ROOT)) for p in overlays],
            "counts": [str(p.relative_to(ROOT)) for p in counts_paths],
            "mosaic": str(mosaic.relative_to(ROOT)) if mosaic.exists() else None,
            "primary_overlay": str(primary.relative_to(ROOT)),
        },
        "per_camera": per_cam_stats,
        "clubbed": clubbed,
    }
    pressures = {}
    for approach, stats in per_cam_stats.items():
        last = stats.get("last") or {}
        pressures[approach] = {
            "vehicles": last.get("vehicles"),
            "weighted_pressure": last.get("weighted_pressure"),
            "car": last.get("car"),
            "truck": last.get("truck"),
            "bus": last.get("bus"),
            "two_wheeler": last.get("two_wheeler"),
        }
    summary["last_frame"] = pressures
    summary = enrich_summary(summary, out_dir)

    save_json(out_dir / "yolo_demo_summary.json", summary)
    save_json(
        ROOT / "results" / "perception_counts.json",
        json.loads(counts_paths[0].read_text(encoding="utf-8")),
    )
    (out_dir / "SOURCE.md").write_text(
        "\n".join(
            [
                "# YOLO demo source",
                "",
                summary["label"],
                "",
                f"- Dataset: {summary['source']}",
                f"- Attribution: {summary['attribution']}",
                f"- Note: {summary['note']}",
                "",
                "Clips under `data/video/` are gitignored. Re-run:",
                "",
                "```bash",
                "make yolo_demo",
                "# or: python -m demo.run_yolo_demo",
                "```",
                "",
                "Dashboard:",
                "",
                "```bash",
                "make yolo_dashboard",
                "```",
                "",
                "Outputs:",
                "",
                "- `results/demo/yolo/overlay_N.mp4` — primary single-cam overlay",
                "- `results/demo/yolo/yolo_mosaic.mp4` — 2×2 mosaic",
                "- `results/demo/yolo/overlay_{N,E,S,W}.mp4`",
                "- `results/demo/yolo/counts_*.json`",
                "- `results/demo/yolo/yolo_demo_summary.json`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary["outputs"], indent=2))
    print("Wrote", out_dir / "yolo_demo_summary.json")

    # One simple story video: mosaic + which roads move when
    try:
        _run([py, "-m", "demo.yolo_story", "--summary", str(out_dir / "yolo_demo_summary.json")])
    except Exception as e:
        print("story video skipped:", e, flush=True)


if __name__ == "__main__":
    main()
