"""Generate a synthetic YOLO plate-detection dataset for local training.

Creates vehicle-scene-like canvases with a pasted Indian plate and YOLO labels.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import cv2
import numpy as np

from ocr_engine.train.synth_plates import _random_bh, _random_standard, render_plate


def _scene(rng: random.Random, size: int = 640) -> np.ndarray:
    """Random asphalt-like background with simple car-body rectangle."""
    bg = np.full((size, size, 3), rng.randint(40, 90), dtype=np.uint8)
    noise = np.random.default_rng(rng.randint(0, 10_000)).integers(0, 25, bg.shape, dtype=np.uint8)
    bg = np.asarray(cv2.add(bg, noise), dtype=np.uint8)
    # faux vehicle body
    color = (rng.randint(20, 200), rng.randint(20, 200), rng.randint(20, 200))
    x1, y1 = rng.randint(40, 120), rng.randint(160, 280)
    x2, y2 = rng.randint(480, 600), rng.randint(420, 560)
    cv2.rectangle(bg, (x1, y1), (x2, y2), color, -1)
    return bg


def _paste_plate(
    scene: np.ndarray, plate: np.ndarray, rng: random.Random
) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    h, w = scene.shape[:2]
    scale = rng.uniform(0.25, 0.55)
    pw = max(40, int(plate.shape[1] * scale))
    ph = max(16, int(plate.shape[0] * scale))
    plate_r = cv2.resize(plate, (pw, ph), interpolation=cv2.INTER_AREA)

    # optional slight rotation
    angle = rng.uniform(-12, 12)
    M = cv2.getRotationMatrix2D((pw / 2, ph / 2), angle, 1.0)
    plate_r = cv2.warpAffine(
        plate_r, M, (pw, ph), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    )

    max_x = max(1, w - pw - 10)
    max_y = max(1, h - ph - 10)
    x = rng.randint(10, max_x)
    y = rng.randint(int(h * 0.45), max_y)  # plates usually lower on vehicle

    scene[y : y + ph, x : x + pw] = plate_r
    cx = (x + pw / 2) / w
    cy = (y + ph / 2) / h
    nw = pw / w
    nh = ph / h
    return scene, (cx, cy, nw, nh)


def generate(out_root: Path, n_train: int = 200, n_val: int = 40, seed: int = 42) -> Path:
    rng = random.Random(seed)
    for split, n in (("train", n_train), ("val", n_val)):
        img_dir = out_root / "images" / split
        lbl_dir = out_root / "labels" / split
        img_dir.mkdir(parents=True, exist_ok=True)
        lbl_dir.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            text = _random_bh(rng) if rng.random() < 0.15 else _random_standard(rng)
            two_row = rng.random() < 0.25
            plate = render_plate(text, two_row=two_row)
            scene = _scene(rng)
            scene, box = _paste_plate(scene, plate, rng)
            stem = f"plate_{split}_{i:04d}"
            cv2.imwrite(str(img_dir / f"{stem}.jpg"), scene)
            cx, cy, bw, bh = box
            (lbl_dir / f"{stem}.txt").write_text(
                f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n", encoding="utf-8"
            )
    yaml_path = out_root / "data.yaml"
    yaml_path.write_text(
        f"""# Auto-generated synthetic plate detection dataset
path: {out_root.resolve()}
train: images/train
val: images/val

nc: 1
names:
  0: plate
""",
        encoding="utf-8",
    )
    return yaml_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("datasets/plates_synth"))
    parser.add_argument("--train", type=int, default=200)
    parser.add_argument("--val", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    path = generate(args.out, n_train=args.train, n_val=args.val, seed=args.seed)
    print(f"Wrote dataset + {path}")


if __name__ == "__main__":
    main()
