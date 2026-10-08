"""OCR evaluation harness — measures plate and character accuracy."""

from __future__ import annotations

import argparse
import csv
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from anpr_common.grammar import normalize_plate

from ocr_engine.recognize import MockRecognizer, ParseqRecognizer, Recognizer

logger = logging.getLogger(__name__)

TAGS = ("day", "night", "rain", "fog", "angle_gt30", "blur", "dirty", "damaged", "two_row")


@dataclass
class EvalRow:
    image_path: Path
    gt_plate: str
    tags: list[str]


@dataclass
class EvalMetrics:
    total: int = 0
    exact_match: int = 0
    char_correct: int = 0
    char_total: int = 0
    per_tag_total: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    per_tag_match: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    confusion: Counter = field(default_factory=Counter)
    failures: list[tuple[str, str, str, list[str]]] = field(default_factory=list)


def load_eval_csv(csv_path: Path, base_dir: Path) -> list[EvalRow]:
    rows: list[EvalRow] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            img = base_dir / row["image_path"]
            tags = [t.strip() for t in row.get("tags", "").split("|") if t.strip()]
            rows.append(EvalRow(image_path=img, gt_plate=row["gt_plate"].upper(), tags=tags))
    return rows


def char_accuracy(pred: str, gt: str) -> tuple[int, int]:
    if not gt:
        return 0, 0
    correct = sum(1 for a, b in zip(pred, gt, strict=False) if a == b)
    return correct, max(len(pred), len(gt))


def run_eval(
    rows: list[EvalRow],
    recognizer: Recognizer,
    model_ids: dict[str, str],
) -> EvalMetrics:
    metrics = EvalMetrics()
    for row in rows:
        if hasattr(recognizer, "recognize_path"):
            result = recognizer.recognize_path(row.image_path)  # type: ignore[attr-defined]
        else:
            import cv2

            img = cv2.imread(str(row.image_path))
            if img is None:
                logger.warning("Cannot read %s", row.image_path)
                continue
            result = recognizer.recognize(img)

        pred_norm = normalize_plate(result.text).norm
        gt_norm = normalize_plate(row.gt_plate).norm
        metrics.total += 1
        exact = pred_norm == gt_norm
        if exact:
            metrics.exact_match += 1
        else:
            metrics.confusion[(gt_norm, pred_norm)] += 1
            metrics.failures.append((str(row.image_path), gt_norm, pred_norm, row.tags))

        c_ok, c_tot = char_accuracy(pred_norm, gt_norm)
        metrics.char_correct += c_ok
        metrics.char_total += c_tot

        for tag in row.tags:
            metrics.per_tag_total[tag] += 1
            if exact:
                metrics.per_tag_match[tag] += 1

    metrics.model_ids = model_ids  # type: ignore[attr-defined]
    return metrics


def write_report(metrics: EvalMetrics, out_path: Path, model_ids: dict[str, str]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).isoformat()
    plate_acc = metrics.exact_match / metrics.total if metrics.total else 0.0
    char_acc = metrics.char_correct / metrics.char_total if metrics.char_total else 0.0

    lines = [
        "# OCR Evaluation Report",
        "",
        f"**Generated:** {ts}",
        "",
        "## Model identifiers",
        "",
    ]
    for k, v in model_ids.items():
        lines.append(f"- **{k}:** `{v}`")
    lines.extend(
        [
            "",
            "## Overall metrics",
            "",
            f"- **Samples:** {metrics.total}",
            f"- **Plate exact-match accuracy:** {plate_acc:.4f} ({metrics.exact_match}/{metrics.total})",
            f"- **Character accuracy:** {char_acc:.4f} ({metrics.char_correct}/{metrics.char_total})",
            "",
            "## Per-tag plate accuracy",
            "",
            "| Tag | Accuracy | Correct | Total |",
            "|-----|----------|---------|-------|",
        ]
    )
    for tag in TAGS:
        tot = metrics.per_tag_total.get(tag, 0)
        if tot == 0:
            continue
        match = metrics.per_tag_match.get(tag, 0)
        acc = match / tot
        lines.append(f"| {tag} | {acc:.4f} | {match} | {tot} |")

    lines.extend(["", "## Confusion pairs (gt → pred)", ""])
    for (gt, pred), count in metrics.confusion.most_common(20):
        lines.append(f"- `{gt}` → `{pred}`: {count}")

    lines.extend(["", "## Worst failures", ""])
    for path, gt, pred, tags in sorted(metrics.failures, key=lambda x: x[0])[:20]:
        tag_str = ", ".join(tags) if tags else "—"
        lines.append(f"- `{path}`: gt=`{gt}` pred=`{pred}` tags=[{tag_str}]")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Wrote report to %s", out_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OCR evaluation harness")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("services/ocr_engine/fixtures/eval_set"),
        help="Directory with labels.csv and images",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("reports/ocr_eval.md"),
        help="Output markdown report path",
    )
    parser.add_argument(
        "--recognizer",
        choices=("mock", "parseq", "plateocr"),
        default="mock",
        help="Recognizer to use (default: mock, no weights required)",
    )
    args = parser.parse_args(argv)

    fixture_dir = args.fixture.resolve()
    csv_path = fixture_dir / "labels.csv"
    if not csv_path.is_file():
        logger.error("Missing labels.csv in %s", fixture_dir)
        return 1

    rows = load_eval_csv(csv_path, fixture_dir)
    gt_map = {str(r.image_path): r.gt_plate for r in rows}
    gt_map.update({r.image_path.name: r.gt_plate for r in rows})

    recognizer: Any
    if args.recognizer == "mock":
        recognizer = MockRecognizer(ground_truth=gt_map)
        model_ids = {"recognizer": "MockRecognizer", "plate_det": "n/a (eval images only)"}
    elif args.recognizer == "plateocr":
        from anpr_common.config import get_settings

        from ocr_engine.plateocr_backend import PlateOCRRecognizer

        settings = get_settings()
        recognizer = PlateOCRRecognizer(
            detector_model=settings.plateocr_detector,
            ocr_model=settings.plateocr_ocr_model,
            device=settings.plateocr_device,
            det_conf=settings.plateocr_det_conf,
            min_ocr_conf=settings.plateocr_min_ocr_conf,
            ocr_config=settings.plateocr_ocr_config or None,
            plate_format=settings.plateocr_plate_format or None,
        )
        model_ids = {
            "recognizer": "PlateOCRRecognizer (FastALPR / PlateOCR)",
            "detector": settings.plateocr_detector,
            "ocr_model": settings.plateocr_ocr_model,
            "plate_format": settings.plateocr_plate_format or "auto",
        }
    else:
        from anpr_common.config import get_settings

        settings = get_settings()
        recognizer = ParseqRecognizer(weights_path=settings.parseq_weights or None)
        model_ids = {
            "recognizer": "ParseqRecognizer",
            "parseq_weights": settings.parseq_weights or "torch.hub/baudm/parseq",
            "plate_det": settings.plate_det_weights or "n/a",
        }

    metrics = run_eval(rows, recognizer, model_ids)
    write_report(metrics, args.report.resolve(), model_ids)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
