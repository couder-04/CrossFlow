"""Evaluate external Indian ANPR footage. This script does not train a model.

Place clips under evaluation/indian/<scene>/ and an optional gt.csv beside them:

    image_or_video,plate
    clip.mp4,MH12AB1234

Scenes: day, night, toll, highway, urban, gantry.
Accuracy is reported only when ground truth rows exist.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from anpr_common.intelligence.evalmetrics import (
    latency_summary,
    matching_summary,
    summarize_ocr,
    tracking_consistency,
)

SCENES = ("day", "night", "toll", "highway", "urban", "gantry")


def load_ground_truth(directory: Path) -> dict[str, str]:
    path = directory / "gt.csv"
    if not path.exists():
        return {}
    truth: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("image_or_video") or row.get("file") or "").strip()
            plate = (row.get("plate") or row.get("gt_plate") or "").strip()
            if name and plate:
                truth[name] = plate
    return truth


def evaluate_tree(root: Path, predictions: dict[str, str] | None = None) -> dict:
    """Score files already recognized, or record that a scene has no footage.

    ``predictions`` maps relative paths to plate text. When omitted, the report
    lists media that is present and does not invent OCR numbers.
    """
    predictions = predictions or {}
    scenes = []
    pairs: list[tuple[str, str]] = []
    for scene in SCENES:
        folder = root / scene
        folder.mkdir(parents=True, exist_ok=True)
        media = [
            path.name
            for path in folder.iterdir()
            if path.is_file()
            and path.suffix.lower()
            in {".mp4", ".mov", ".avi", ".mkv", ".webm", ".jpg", ".jpeg", ".png"}
        ]
        truth = load_ground_truth(folder)
        scene_pairs = []
        for name in media:
            rel = f"{scene}/{name}"
            if name in truth and rel in predictions:
                scene_pairs.append((predictions[rel], truth[name]))
            elif name in truth and name in predictions:
                scene_pairs.append((predictions[name], truth[name]))
        pairs.extend(scene_pairs)
        scenes.append(
            {
                "scene": scene,
                "media": len(media),
                "ground_truth": len(truth),
                "scored": len(scene_pairs),
            }
        )
    return {
        "root": str(root),
        "scenes": scenes,
        "ocr": summarize_ocr(pairs),
        "tracking": tracking_consistency([]),
        "matching": matching_summary([]),
        "latency": latency_summary([]),
        "note": "No model is trained here. Empty scenes do not produce an accuracy claim.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Indian footage evaluation report")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--predictions", default="", help="Optional JSON map of file -> plate")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    predictions = json.loads(Path(args.predictions).read_text()) if args.predictions else {}
    report = evaluate_tree(Path(args.root), predictions)
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
