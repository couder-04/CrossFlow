"""Fine-tune PARSeq on synthetic + labelled plate crops.

Usage
-----
1. Generate synthetic data: ``python -m ocr_engine.train.synth_plates --out data/synth --count 5000``
2. Organise real crops into ``data/real/{images,labels.txt}`` (one plate string per line).
3. Edit ``config`` below (epochs, batch size, learning rate).
4. Run: ``python -m ocr_engine.train.finetune_parseq --data-dir data/synth --output models/parseq_ft.pt``

Requires GPU for reasonable training time. This script is a template; adjust paths and
hyperparameters for your dataset.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "epochs": 20,
    "batch_size": 32,
    "lr": 1e-4,
    "img_size": (32, 128),
    "charset": "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
}


def finetune(data_dir: Path, output: Path, config: dict | None = None) -> None:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    logger.info("PARSeq fine-tune config: %s", cfg)
    logger.info("Data directory: %s", data_dir)
    logger.info(
        "This is a training template. Wire your DataLoader and torch.hub PARSeq model here. "
        "See https://github.com/baudm/parseq for the official training loop."
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("# placeholder — replace with torch.save(state_dict)\n", encoding="utf-8")
    logger.info("Wrote placeholder checkpoint path at %s", output)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Fine-tune PARSeq recognizer")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("models/parseq_ft.pt"))
    parser.add_argument("--epochs", type=int, default=DEFAULT_CONFIG["epochs"])
    args = parser.parse_args()
    finetune(args.data_dir, args.output, {"epochs": args.epochs})


if __name__ == "__main__":
    main()
