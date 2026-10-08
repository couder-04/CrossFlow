"""OCR engine CLI."""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime

import click
from anpr_common.config import get_settings

from ocr_engine.pipeline import OCRPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@click.group()
def main() -> None:
    """ANPR OCR engine."""


@main.command("run")
@click.option("--source", required=True, help="Video file path or RTSP URL")
@click.option("--camera-id", required=True, help="Camera identifier for PlateRead events")
@click.option("--camera-heading", type=float, default=0.0, help="Camera compass heading in degrees")
@click.option(
    "--backend",
    type=click.Choice(["plateocr", "legacy"], case_sensitive=False),
    default=None,
    help="OCR backend (default: OCR_BACKEND env / plateocr)",
)
@click.option("--dry-run", is_flag=True, help="Print events instead of publishing to Kafka/MinIO")
@click.option("--max-frames", type=int, default=None, help="Stop after N frames (debug)")
@click.option("--lanes", type=int, default=3, help="Number of lanes for lane attribution")
@click.option(
    "--ocr-model",
    default=None,
    help="OCR model: india-v1.1 (default), india-v1, cct-s-v2-global-model, or path to .onnx",
)
@click.option(
    "--plate-format",
    type=click.Choice(["india", "none"], case_sensitive=False),
    default=None,
    help="Format-constrained decoding (india-v1.x defaults to india)",
)
@click.option(
    "--stride",
    type=click.IntRange(min=1),
    default=None,
    help="Process every Nth frame (default: OCR_FRAME_STRIDE / 1)",
)
@click.option(
    "--start-time",
    type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"]),
    default=None,
    help="Recording start time of a video file, UTC (event ts = start + frame offset)",
)
@click.option(
    "--annotate-out",
    type=click.Path(dir_okay=False),
    default=None,
    help="Write an annotated MP4 (boxes, track IDs, OCR text) to this path",
)
def run_cmd(
    source: str,
    camera_id: str,
    camera_heading: float,
    backend: str | None,
    dry_run: bool,
    max_frames: int | None,
    lanes: int,
    ocr_model: str | None,
    plate_format: str | None,
    stride: int | None,
    start_time: datetime | None,
    annotate_out: str | None,
) -> None:
    """Run the video OCR pipeline on a file or RTSP stream."""
    settings = get_settings()
    updates: dict = {}
    if ocr_model:
        updates["plateocr_ocr_model"] = ocr_model
    if plate_format:
        updates["plateocr_plate_format"] = plate_format
    if updates:
        settings = settings.model_copy(update=updates)
    pipeline = OCRPipeline(
        settings=settings,
        camera_id=camera_id,
        camera_heading_deg=camera_heading,
        dry_run=dry_run,
        backend=backend,
        num_lanes=lanes,
    )
    try:
        count = pipeline.run(
            source,
            max_frames=max_frames,
            annotate_out=annotate_out,
            stride=stride,
            source_start=start_time.replace(tzinfo=UTC) if start_time else None,
        )
        click.echo(f"Emitted {count} plate read(s)")
    except KeyboardInterrupt:
        click.echo("Interrupted", err=True)
        sys.exit(130)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        sys.exit(code)
    except (RuntimeError, OSError, ValueError) as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@main.command("fleet")
@click.option(
    "--config", required=True, type=click.Path(exists=True), help="YAML/JSON camera fleet config"
)
@click.option(
    "--backend",
    type=click.Choice(["plateocr", "legacy"], case_sensitive=False),
    default=None,
)
@click.option("--dry-run", is_flag=True, help="Print events instead of publishing")
@click.option("--max-frames", type=int, default=None, help="Per-camera frame cap (debug)")
@click.option("--workers", type=int, default=None, help="Max concurrent camera workers")
def fleet_cmd(
    config: str,
    backend: str | None,
    dry_run: bool,
    max_frames: int | None,
    workers: int | None,
) -> None:
    """Run OCR on multiple cameras concurrently (files and/or RTSP)."""
    from ocr_engine.fleet import run_fleet

    try:
        results = run_fleet(
            config,
            dry_run=dry_run,
            backend=backend,
            max_frames=max_frames,
            max_workers=workers,
        )
        total = sum(results.values())
        for cam_id, count in sorted(results.items()):
            click.echo(f"{cam_id}: {count}")
        click.echo(f"Total emitted: {total}")
    except KeyboardInterrupt:
        click.echo("Interrupted", err=True)
        sys.exit(130)
    except (RuntimeError, OSError, ValueError) as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@main.command("video-city")
@click.option(
    "--config", required=True, type=click.Path(exists=True), help="Fleet JSON of video files"
)
@click.option("--workers", type=int, default=None, help="OCR workers rotating across the videos")
@click.option("--stride", type=int, default=None, help="Run OCR on every Nth frame")
def video_city_cmd(config: str, workers: int | None, stride: int | None) -> None:
    """Loop video files as camera streams and read plates from them."""
    from ocr_engine.video_city import run_video_city

    try:
        run_video_city(config, workers=workers, stride=stride)
    except KeyboardInterrupt:
        click.echo("Interrupted", err=True)
        sys.exit(130)
    except (RuntimeError, OSError, ValueError) as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@main.command("image")
@click.option("--source", required=True, help="Image file path")
@click.option("--camera-id", default="cam-demo", help="Camera id for event payload")
@click.option("--dry-run", is_flag=True, default=True, help="Print JSON (default true)")
def image_cmd(source: str, camera_id: str, dry_run: bool) -> None:
    """Single-image PlateOCR smoke test (detect + read + grammar)."""
    settings = get_settings()
    pipeline = OCRPipeline(
        settings=settings,
        camera_id=camera_id,
        dry_run=dry_run,
        backend="plateocr",
    )
    try:
        count = pipeline.run_image(source)
        click.echo(f"Emitted {count} plate read(s)")
    except (RuntimeError, OSError, ValueError) as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        sys.exit(code)


if __name__ == "__main__":
    main()
