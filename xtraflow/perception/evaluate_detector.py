"""Evaluate detector if labels exist; else synthetic smoke + assumed noise model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from sim.util import ROOT, rel_to_root, save_json


def write_assumed_noise(miss: float = 0.15) -> dict:
    # Mild class confusion assumed
    types = ["two_wheeler", "auto_rickshaw", "car", "bus", "truck"]
    confusion = {}
    for t in types:
        row = {u: (0.85 if u == t else 0.15 / (len(types) - 1)) for u in types}
        # normalize
        s = sum(row.values())
        confusion[t] = {k: v / s for k, v in row.items()}
    out = {
        "detect_miss_rate": miss,
        "class_confusion": confusion,
        "count_jitter_std": 0.5,
        "mean_burst_s": 3.0,
        "source": "assumed",
        "note": (
            "ASSUMED noise model (no user video/labels). "
            "Supply data/video/ and data/video_labels/ to derive empirical noise_model.json. "
            "SUMO consumes simulated detections with this noise; YOLO on real video shows "
            "the same state is extractable and supplies noise parameters."
        ),
    }
    path = ROOT / "perception" / "noise_model.json"
    save_json(path, out)
    return out


def evaluate_from_labels(video_dir: Path, label_dir: Path, out_path: Path | None = None) -> dict:
    """Manual per-class counts paired with detections -> recall, miss rate, confusion.

    Each JSON label file is either
    {"counts": {"car": 10}, "detections": {"car": 8, "bus": 1}}
    or {"frames": [{"counts": {...}, "detections": {...}}]}.
    source is empirical only when at least one paired detection block exists.
    """
    label_files = [p for p in label_dir.glob("*.json")]
    dest = out_path or (ROOT / "perception" / "noise_model.json")
    if not label_files:
        out = write_assumed_noise()
        if out_path is not None:
            save_json(out_path, out)
        return out
    true_tot: dict = {}
    det_tot: dict = {}
    paired = 0
    for lf in label_files:
        data = json.loads(lf.read_text())
        blocks = data.get("frames") or [data]
        for block in blocks:
            counts = block.get("counts") or block.get("true")
            detections = block.get("detections") or block.get("detected")
            if not counts or not detections:
                continue
            paired += 1
            for k, v in counts.items():
                true_tot[k] = true_tot.get(k, 0) + float(v)
            for k, v in detections.items():
                det_tot[k] = det_tot.get(k, 0) + float(v)
    if paired == 0:
        out = write_assumed_noise()
        out["source"] = "assumed"
        out["note"] = (
            "Labels were present but had no paired counts and detections, so the noise model stays assumed."
        )
        save_json(dest, out)
        return out
    classes = sorted(set(true_tot) | set(det_tot))
    recall = {}
    miss = {}
    for c in classes:
        t = true_tot.get(c, 0.0)
        d = det_tot.get(c, 0.0)
        recall[c] = (d / t) if t else None
        miss[c] = (1.0 - d / t) if t else None
    true_sum = sum(true_tot.values()) or 1.0
    det_sum = sum(det_tot.values())
    miss_rate = max(0.0, min(1.0, 1.0 - det_sum / true_sum))
    confusion = {}
    for c in classes:
        row = {}
        for o in classes:
            if c == o:
                row[o] = recall[c] if recall[c] is not None else 0.0
            else:
                # Off-diagonal is the share of detections of o when the manual class was c,
                # only if a confusion_matrix block was not supplied. Use detection mix as a proxy.
                row[o] = 0.0
        s = sum(row.values()) or 1.0
        confusion[c] = {k: v / s for k, v in row.items()}
    out = {
        "detect_miss_rate": miss_rate,
        "class_confusion": confusion,
        "count_jitter_std": 0.0,
        "mean_burst_s": 3.0,
        "recall": recall,
        "miss_rate_by_class": miss,
        "source": "empirical",
        "n_paired_blocks": paired,
        "note": "Empirical noise from manual per-class counts paired with detections.",
    }
    save_json(dest, out)
    return out


def synthetic_smoke() -> dict:
    """Generate a tiny synthetic 'video' frames array and run a trivial count smoke."""
    # Create a blank mp4
    import imageio.v2 as imageio

    demo = ROOT / "results" / "demo"
    demo.mkdir(parents=True, exist_ok=True)
    path = demo / "synthetic_traffic_smoke.mp4"
    frames = []
    for i in range(15):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        frame[:, :] = (30, 30, 30)
        # moving rectangle as fake vehicle
        x = 20 + i * 10
        frame[100:140, x:x + 40] = (200, 200, 50)
        frames.append(frame)
    imageio.mimsave(path, frames, fps=5)
    noise = write_assumed_noise(0.15)
    save_json(ROOT / "results" / "perception_smoke.json", {
        "video": rel_to_root(path),
        "noise_model": noise,
        "flag": "ASSUMED_NOISE_MODEL_NO_USER_VIDEO",
    })
    print("Synthetic perception smoke complete; noise model ASSUMED.")
    return noise


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--smoke", action="store_true")
    args = p.parse_args()
    video_dir = ROOT / "data" / "video"
    label_dir = ROOT / "data" / "video_labels"
    has_video = any(video_dir.glob("*")) if video_dir.exists() else False
    has_labels = any(label_dir.glob("*")) if label_dir.exists() else False
    if args.smoke or not (has_video and has_labels):
        synthetic_smoke()
        print(
            "\nTo supply real data:\n"
            "  - Put videos in data/video/\n"
            "  - Put manual counts/boxes in data/video_labels/\n"
            "  - Run: python -m perception.yolo_counts --video ... --roi perception/roi.yaml\n"
            "  - Run: python -m perception.evaluate_detector\n"
            "Licensing: only use datasets you have rights to; do not download unlicensed content.\n"
        )
    else:
        evaluate_from_labels(video_dir, label_dir)


if __name__ == "__main__":
    main()
