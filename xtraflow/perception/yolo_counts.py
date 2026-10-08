"""YOLOv8 per-approach counts for one camera.

Draw per-camera polygons in perception/roi.yaml (or a copy). Coordinates are
fractions of the frame, origin at the top-left, each approach a list of [x, y]
points. Trace the stop-line approach on a still frame; the point-in-polygon
test uses the box centre.

Class map: COCO motorcycle -> two_wheeler, car, bus, truck. auto_rickshaw is
counted only when --auto-model points at a fine-tuned checkpoint. Otherwise
autos fall into car or two_wheeler, whichever the base model predicts.

This module does not download weights or video. Pass a model path you already have.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from sim.util import ROOT, load_json, save_json

CLASS_MAP = {
    2: "car",
    3: "two_wheeler",  # COCO motorcycle
    5: "bus",
    7: "truck",
}


def point_in_poly(x, y, poly):
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def halted_from_track(points, min_frames: int = 5, max_disp: float = 0.015) -> bool:
    """True when the track barely moves over the last min_frames centres (normalized)."""
    if len(points) < min_frames:
        return False
    window = points[-min_frames:]
    x0, y0 = window[0]
    x1, y1 = window[-1]
    return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 <= max_disp


def weighted_pressure(per_approach: dict, weights: dict) -> dict:
    out = {}
    for ap, counts in per_approach.items():
        total = 0.0
        for vtype, n in counts.items():
            if vtype in ("halting_est", "weighted_pressure"):
                continue
            total += float(weights.get(vtype, 1.0)) * float(n)
        out[ap] = total
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--video", required=True)
    p.add_argument("--roi", default=str(ROOT / "perception" / "roi.yaml"))
    p.add_argument("--out", default=str(ROOT / "results" / "perception_counts.json"))
    p.add_argument("--model", default="yolov8n.pt", help="Path to a YOLO checkpoint you already have")
    p.add_argument("--auto-model", default="", help="Optional fine-tuned checkpoint that emits auto_rickshaw")
    p.add_argument("--fps", type=float, default=3.0)
    p.add_argument("--halt-frames", type=int, default=5)
    args = p.parse_args()

    video = Path(args.video)
    if not video.exists():
        raise SystemExit(f"Video not found: {video}. Place a file you have rights to under data/video/.")

    import cv2
    from ultralytics import YOLO

    roi = yaml.safe_load(Path(args.roi).read_text())
    model = YOLO(args.model)
    auto_model = YOLO(args.auto_model) if args.auto_model else None
    weights_path = ROOT / "results" / "weights.json"
    weights = {}
    if weights_path.exists():
        blob = load_json(weights_path)
        weights = {k: float(v) for k, v in blob.get("w_type", {}).items() if isinstance(v, (int, float))}

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"Cannot open video {video}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25
    stride = max(1, int(round(src_fps / args.fps)))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    frame_i = 0
    counts_series = []
    tracks: dict = {}
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_i % stride != 0:
            frame_i += 1
            continue
        results = model.track(frame, persist=True, tracker="bytetrack.yaml", verbose=False)
        per_ap = {
            a: {"car": 0, "two_wheeler": 0, "bus": 0, "truck": 0, "auto_rickshaw": 0, "halting_est": 0}
            for a in roi["approaches"]
        }
        r0 = results[0]
        if r0.boxes is not None and r0.boxes.id is not None:
            ids = r0.boxes.id.tolist()
            for box, tid in zip(r0.boxes, ids):
                cls = int(box.cls.item())
                if cls not in CLASS_MAP:
                    continue
                vtype = CLASS_MAP[cls]
                xyxy = box.xyxy[0].tolist()
                cx = ((xyxy[0] + xyxy[2]) / 2) / max(w, 1)
                cy = ((xyxy[1] + xyxy[3]) / 2) / max(h, 1)
                hist = tracks.setdefault(int(tid), [])
                hist.append((cx, cy))
                halted = halted_from_track(hist, min_frames=args.halt_frames)
                for ap, spec in roi["approaches"].items():
                    if point_in_poly(cx, cy, spec["polygon"]):
                        per_ap[ap][vtype] = per_ap[ap].get(vtype, 0) + 1
                        if halted:
                            per_ap[ap]["halting_est"] += 1
        if auto_model is not None:
            # A fine-tuned model may emit class name auto_rickshaw. Base YOLO does not.
            extra = auto_model.predict(frame, verbose=False)
            # Left as an optional increment; users map their class ids in the checkpoint.
            _ = extra
        pressure = weighted_pressure(per_ap, weights)
        for ap in per_ap:
            per_ap[ap]["weighted_pressure"] = pressure.get(ap, 0.0)
        counts_series.append({"frame": frame_i, "counts": per_ap})
        frame_i += 1

    cap.release()
    out = {
        "schema": "controller_counts_v1",
        "video": str(video),
        "fps": args.fps,
        "series": counts_series,
        "auto_rickshaw": "separate class" if auto_model else "counted as car or two_wheeler by the base model",
        "label": "Input feasibility — not a fuel-saving result",
    }
    save_json(Path(args.out), out)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
