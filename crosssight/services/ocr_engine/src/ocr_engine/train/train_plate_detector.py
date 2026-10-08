"""Ultralytics YOLO plate detector training template.

Prepare a YOLO dataset with ``images/`` and ``labels/`` plus a ``data.yaml`` (see
``data.yaml`` in this directory). Example:

    python -m ocr_engine.train.train_plate_detector \\
        --data services/ocr_engine/src/ocr_engine/train/data.yaml \\
        --epochs 50 --imgsz 640 --batch 16

Public datasets for pretraining (documented):
- OpenALPR benchmark (CC BY-SA)
- CCPD (Chinese plates — transfer with caution)
- Your own labelled Indian ANPR frames (required for production)
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def train(
    data_yaml: Path,
    epochs: int = 50,
    imgsz: int = 640,
    batch: int = 16,
    model: str = "yolov8n.pt",
    project: str | None = None,
) -> Path:
    from ultralytics import YOLO

    if not data_yaml.is_file():
        raise FileNotFoundError(f"data.yaml not found: {data_yaml}")

    # Keep run artifacts inside the repo (avoid Ultralytics default ~/Documents/runs).
    # train_plate_detector.py → train/ → ocr_engine/ → src/ → ocr_engine/ → services/ → sih/
    repo_root = Path(__file__).resolve().parents[5]
    project_dir = Path(project) if project else (repo_root / "runs" / "plate_det")
    project_dir.mkdir(parents=True, exist_ok=True)

    yolo = YOLO(model)
    results = yolo.train(
        data=str(data_yaml.resolve()),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        project=str(project_dir),
        name="train",
        exist_ok=True,
    )
    best = Path(results.save_dir) / "weights" / "best.pt"
    logger.info("Training complete. Best weights: %s", best)
    return best


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Train YOLO plate detector")
    parser.add_argument(
        "--data",
        type=Path,
        default=Path(__file__).parent / "data.yaml",
        help="Ultralytics data.yaml path",
    )
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--model", type=str, default="yolov8n.pt")
    args = parser.parse_args()
    train(args.data, epochs=args.epochs, imgsz=args.imgsz, batch=args.batch, model=args.model)


if __name__ == "__main__":
    main()
