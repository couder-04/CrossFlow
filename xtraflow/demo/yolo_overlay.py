"""Draw detections, per-approach fuel-pressure bars, and the next XtraFlow phase.

Reads a video you supply under data/video/. Does not download anything.
On-screen label: Input feasibility — not a fuel-saving result.
Writes results/demo/yolo_overlay.mp4.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
import yaml
from ultralytics import YOLO

from perception.replay import replay_decision
from perception.yolo_counts import CLASS_MAP, halted_from_track, point_in_poly, weighted_pressure
from sim.util import ROOT, load_json


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--video", default="")
    p.add_argument("--roi", default=str(ROOT / "perception" / "roi.yaml"))
    p.add_argument("--model", default="yolov8n.pt")
    p.add_argument("--out", default=str(ROOT / "results" / "demo" / "yolo_overlay.mp4"))
    p.add_argument("--max-frames", type=int, default=0, help="Stop after N processed frames (0 = all)")
    args = p.parse_args()
    video_dir = ROOT / "data" / "video"
    video = Path(args.video) if args.video else None
    if video is None:
        candidates = [c for c in video_dir.glob("*") if c.suffix.lower() in {".mp4", ".mov", ".avi"}]
        video = candidates[0] if candidates else None
    if video is None or not video.exists():
        raise SystemExit(
            "No video. Place a file you have rights to in data/video/ and pass --video. "
            "This tool does not download footage."
        )

    roi = yaml.safe_load(Path(args.roi).read_text())
    model = YOLO(args.model)
    weights = {}
    wpath = ROOT / "results" / "weights.json"
    if wpath.exists():
        weights = load_json(wpath).get("w_type", {})
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"Cannot open {video}")
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    tracks: dict = {}
    label = "Input feasibility - not a fuel-saving result"
    processed = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        results = model.track(frame, persist=True, tracker="bytetrack.yaml", verbose=False)
        annotated = results[0].plot()
        for ap, spec in roi.get("approaches", {}).items():
            pts = []
            for x_f, y_f in spec.get("polygon") or []:
                pts.append([int(float(x_f) * w), int(float(y_f) * h)])
            if len(pts) >= 3:
                cv2.polylines(annotated, [np.asarray(pts, dtype="int32")], True, (200, 180, 60), 2)
                cv2.putText(
                    annotated,
                    ap,
                    (pts[0][0] + 4, max(18, pts[0][1] + 18)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (200, 180, 60),
                    2,
                )
        per_ap = {a: {"car": 0, "two_wheeler": 0, "bus": 0, "truck": 0, "halting_est": 0} for a in roi["approaches"]}
        r0 = results[0]
        if r0.boxes is not None and r0.boxes.id is not None:
            for box, tid in zip(r0.boxes, r0.boxes.id.tolist()):
                cls = int(box.cls.item())
                if cls not in CLASS_MAP:
                    continue
                xyxy = box.xyxy[0].tolist()
                cx = ((xyxy[0] + xyxy[2]) / 2) / max(w, 1)
                cy = ((xyxy[1] + xyxy[3]) / 2) / max(h, 1)
                hist = tracks.setdefault(int(tid), [])
                hist.append((cx, cy))
                vtype = CLASS_MAP[cls]
                halted = halted_from_track(hist)
                for ap, spec in roi["approaches"].items():
                    if point_in_poly(cx, cy, spec["polygon"]):
                        per_ap[ap][vtype] += 1
                        if halted:
                            per_ap[ap]["halting_est"] += 1
        decision = replay_decision(per_ap)
        pressure = weighted_pressure(per_ap, weights)
        cv2.putText(annotated, label, (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.putText(
            annotated,
            f"next {decision['next_phase']}",
            (16, 64),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (80, 220, 180),
            2,
        )
        x = 16
        for ap, value in pressure.items():
            bar = int(min(120, value * 8))
            cv2.rectangle(annotated, (x, h - 20 - bar), (x + 28, h - 20), (80, 180, 220), -1)
            cv2.putText(annotated, ap, (x, h - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
            x += 40
        writer.write(annotated)
        processed += 1
        if args.max_frames and processed >= args.max_frames:
            break
    cap.release()
    writer.release()
    print(out_path)


if __name__ == "__main__":
    main()
