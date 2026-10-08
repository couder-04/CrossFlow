"""Local traffic videos used as a second camera network.

Files live in ``data/drive_cameras`` (or ``VIDEO_FEEDS_DIR``). Each file becomes
a camera id ``vid-01``, ``vid-02``, ... sorted by filename. The simulated city
uses every other camera id, so the two modes can share one database.
"""

from __future__ import annotations

import json
import math
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

VIDEO_PREFIX = "vid-"
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
# Same centre as the synthetic Pune graph, so the clips sit on the city map.
CITY_CENTER = (18.52, 73.85)
_PLAYBACK = re.compile(r"videoplayback(?: \((\d+)\))?$", re.IGNORECASE)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def feeds_dir() -> Path:
    override = os.environ.get("VIDEO_FEEDS_DIR")
    if override:
        return Path(override)
    return repo_root() / "data" / "drive_cameras"


def normalize_source(source: str | None) -> str:
    return "video" if source == "video" else "sim"


def camera_in_source(camera_id: str, source: str | None) -> bool:
    video = str(camera_id).startswith(VIDEO_PREFIX)
    return video if normalize_source(source) == "video" else not video


def display_name(path: Path) -> str:
    stem = path.stem.strip()
    stem = re.sub(r"^\d+_", "", stem)
    stem = stem.replace("_720P", "").replace("-SnapYT.App", "")
    playback = _PLAYBACK.fullmatch(stem.strip())
    if playback:
        number = playback.group(1)
        return f"Traffic clip {number}" if number else "Traffic clip"
    cleaned = stem.replace("_", " ").strip()
    return (cleaned or path.stem)[:80]


def discover_videos(directory: Path | None = None) -> list[Path]:
    folder = directory or feeds_dir()
    if not folder.is_dir():
        return []
    files = [
        path
        for path in folder.iterdir()
        if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in VIDEO_EXTS
    ]
    return sorted(files, key=lambda path: path.name.lower())


def camera_records(videos: list[Path]) -> list[dict[str, object]]:
    count = len(videos)
    if count == 0:
        return []
    cols = max(1, math.ceil(math.sqrt(count)))
    spacing = 0.012
    records: list[dict[str, object]] = []
    for index, path in enumerate(videos):
        row, col = divmod(index, cols)
        lat = CITY_CENTER[0] + (row - (cols - 1) / 2) * spacing
        lng = CITY_CENTER[1] + (col - (cols - 1) / 2) * spacing
        records.append(
            {
                "id": f"{VIDEO_PREFIX}{index + 1:02d}",
                "name": display_name(path),
                "lat": lat,
                "lng": lng,
                "heading_deg": float((index * 45) % 360),
                "lanes": 3,
                "source": str(path.resolve()),
            }
        )
    return records


def fleet_path(directory: Path | None = None) -> Path:
    return (directory or feeds_dir()) / "fleet.json"


def write_fleet(records: list[dict[str, object]], path: Path | None = None) -> Path:
    target = path or fleet_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cameras": [
            {
                "id": rec["id"],
                "source": rec["source"],
                "heading_deg": rec["heading_deg"],
                "lanes": rec["lanes"],
                "enabled": True,
            }
            for rec in records
        ]
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def _pid_path(directory: Path | None = None) -> Path:
    return (directory or feeds_dir()) / ".runner.pid"


def _read_pid(directory: Path | None = None) -> int | None:
    path = _pid_path(directory)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text.isdigit():
        return None
    return int(text)


def runner_alive(directory: Path | None = None) -> bool:
    pid = _read_pid(directory)
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _pid_command(pid: int) -> str | None:
    """Command line of ``pid`` (``/proc`` on Linux, ``ps`` elsewhere), or None if unknown."""
    proc = Path("/proc") / str(pid) / "cmdline"
    try:
        if proc.parent.is_dir():
            return proc.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _is_runner(pid: int) -> bool:
    """True only if ``pid`` is still our ``ocr_engine.cli video-city`` process."""
    command = _pid_command(pid)
    return command is not None and "ocr_engine.cli" in command and "video-city" in command


def stop_runner(directory: Path | None = None) -> None:
    pid = _read_pid(directory)
    path = _pid_path(directory)
    # A stale pid file may name a recycled pid that now belongs to an unrelated process group.
    if pid is not None and _is_runner(pid):
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
    if path.is_file():
        path.unlink()


def start_runner(config: Path, directory: Path | None = None) -> int:
    """Start the video-city process if it is not already running. Returns pid."""
    folder = directory or feeds_dir()
    if runner_alive(folder):
        pid = _read_pid(folder)
        if pid is not None:
            return pid
    folder.mkdir(parents=True, exist_ok=True)
    root = repo_root()
    extra = [
        root / "services" / "ocr_engine" / "src",
        root / "packages" / "anpr_common" / "src",
        root / "services" / "api" / "src",
    ]
    env = os.environ.copy()
    previous = env.get("PYTHONPATH", "")
    prefix = os.pathsep.join(str(path) for path in extra)
    env["PYTHONPATH"] = prefix + (os.pathsep + previous if previous else "")
    log_path = folder / "runner.log"
    log_handle = open(log_path, "ab", buffering=0)  # noqa: SIM115
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "ocr_engine.cli", "video-city", "--config", str(config)],
            cwd=str(root),
            env=env,
            start_new_session=True,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
    finally:
        log_handle.close()
    _pid_path(folder).write_text(str(proc.pid), encoding="utf-8")
    return proc.pid
