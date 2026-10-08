"""Synthetic Indian license plate renderer and augmentation.

Fonts
-----
Uses Pillow's default bitmap font for CI-friendly rendering. For production-quality
synthetic data, install an open-licensed plate font (e.g. FE-Schrift derivative or
a commercially licensed Indian plate TTF) and pass ``--font /path/to/font.ttf``.

Datasets (documented for pretraining / fine-tuning)
---------------------------------------------------
- OpenALPR benchmark crops (CC BY-SA) — useful for detector pretraining
- UFPR-ALPR dataset — academic, request access
- Local labelled ANPR footage — required before trusting field accuracy
"""

from __future__ import annotations

import argparse
import csv
import random
import string
from pathlib import Path

import albumentations as A
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

STANDARD_SAMPLES = [
    "MH12AB1234",
    "DL3CAB1234",
    "KA01MN5678",
    "TN09BX4321",
    "BR01XY9876",
]
BH_SAMPLES = ["22BH1234AA", "21BH5678B", "19BH9012XY"]


def _random_standard(rng: random.Random) -> str:
    states = ["MH", "DL", "KA", "TN", "BR", "GJ", "UP", "RJ"]
    st = rng.choice(states)
    rto = rng.randint(1, 99)
    letters = "".join(rng.choices(string.ascii_uppercase, k=rng.randint(1, 3)))
    serial = rng.randint(1000, 9999)
    return f"{st}{rto:02d}{letters}{serial}"


def _random_bh(rng: random.Random) -> str:
    yr = rng.randint(19, 24)
    serial = rng.randint(1000, 9999)
    suffix = "".join(rng.choices(string.ascii_uppercase, k=rng.choice([1, 2])))
    return f"{yr:02d}BH{serial}{suffix}"


def _load_font(size: int, font_path: str | None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if font_path and Path(font_path).is_file():
        return ImageFont.truetype(font_path, size=size)
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size=size)
    except OSError:
        return ImageFont.load_default()


def render_plate(
    text: str,
    two_row: bool = False,
    font_path: str | None = None,
    width: int = 400,
    height: int = 100,
) -> np.ndarray:
    """Render a white plate with black embossed-style text."""
    img = Image.new("RGB", (width, height if not two_row else 140), color=(245, 245, 245))
    draw = ImageDraw.Draw(img)
    font = _load_font(36 if not two_row else 28, font_path)

    if two_row and len(text) > 6:
        mid = len(text) // 2
        lines = [text[:mid], text[mid:]]
    else:
        lines = [text]

    y = 20
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        x = (width - tw) // 2
        draw.text((x, y), line, fill=(20, 20, 20), font=font)
        y += int(bbox[3] - bbox[1]) + 8

    # Blue IND strip
    draw.rectangle([0, 0, 18, height if not two_row else 140], fill=(0, 60, 160))
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def augmentation_pipeline(tag: str, rng: random.Random) -> A.Compose:
    """Build albumentations pipeline for a condition tag."""
    transforms: list[A.BasicTransform] = [
        A.Perspective(scale=(0.02, 0.12), p=0.7 if tag == "angle_gt30" else 0.3),
        A.RandomBrightnessContrast(p=0.5),
        A.ImageCompression(quality_range=(40, 90), p=0.4),
    ]
    if tag == "blur":
        transforms.append(A.MotionBlur(blur_limit=9, p=0.8))
    if tag == "rain":
        transforms.append(A.RandomRain(p=0.8))
    if tag == "fog":
        transforms.append(A.RandomFog(p=0.7))
    if tag == "night":
        transforms.append(A.RandomBrightnessContrast(brightness_limit=(-0.6, -0.2), p=0.9))
    if tag == "dirty":
        transforms.append(
            A.CoarseDropout(
                num_holes_range=(4, 8), hole_height_range=(8, 12), hole_width_range=(8, 12), p=0.7
            )
        )
    if tag == "damaged":
        transforms.append(A.GridDropout(ratio=0.15, p=0.6))
    if tag == "day":
        transforms.append(A.RandomBrightnessContrast(brightness_limit=(0, 0.2), p=0.5))
    return A.Compose(transforms)


def generate_eval_fixture(out_dir: Path, seed: int = 42) -> None:
    """Write tiny synthetic eval set (PNGs + labels.csv)."""
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    tag_list = ["day", "night", "blur", "two_row", "angle_gt30"]

    plates = STANDARD_SAMPLES + BH_SAMPLES
    for i, plate in enumerate(plates):
        two_row = "BH" in plate or (i % 3 == 0)
        primary_tag = tag_list[i % len(tag_list)]
        tags = [primary_tag]
        if two_row:
            tags.append("two_row")

        base = render_plate(plate, two_row=two_row)
        aug = augmentation_pipeline(primary_tag, rng)
        augmented = aug(image=base)["image"]
        fname = f"plate_{i:03d}.png"
        cv2.imwrite(str(out_dir / fname), augmented)
        rows.append({"image_path": fname, "gt_plate": plate, "tags": "|".join(tags)})

    csv_path = out_dir / "labels.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["image_path", "gt_plate", "tags"])
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render synthetic Indian plates")
    parser.add_argument("--out", type=Path, default=Path("out/synth"))
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--font", type=str, default=None)
    parser.add_argument(
        "--eval-fixture", type=Path, default=None, help="Also write eval fixture here"
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    rng = random.Random(args.seed)

    if args.eval_fixture:
        generate_eval_fixture(args.eval_fixture, seed=args.seed)
        print(f"Wrote eval fixture to {args.eval_fixture}")

    args.out.mkdir(parents=True, exist_ok=True)
    for i in range(args.count):
        plate = _random_bh(rng) if rng.random() < 0.2 else _random_standard(rng)
        two_row = rng.random() < 0.25
        img = render_plate(plate, two_row=two_row, font_path=args.font)
        tag = rng.choice(["day", "night", "rain", "fog", "blur", "dirty"])
        aug = augmentation_pipeline(tag, rng)
        img = aug(image=img)["image"]
        cv2.imwrite(str(args.out / f"{plate}_{i:04d}.jpg"), img)
    print(f"Wrote {args.count} images to {args.out}")


if __name__ == "__main__":
    main()
