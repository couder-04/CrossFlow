"""Build story video that mirrors the real XtraFlow hypothesis.

Hypothesis (shown on-screen):
  detect who waits → score fuel-weighted phase pressure → serve that phase
  under green → yellow → all-red (same hard rules as the controller).

GO tiles play; YELLOW still plays while clearing; ALL RED / PAUSE freeze.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from demo.yolo_decisions import PHASE_APPROACHES, enrich_summary
from sim.controllers import PHASE_ORDER
from sim.util import ROOT, load_config

TILE_W, TILE_H = 640, 360
BANNER_H = 88
FOOTER_H = 56
ORDER = ("N", "E", "S", "W")
PHASE_PLAIN = {
    "NS_TL": "N-S through",
    "NS_R": "N-S right",
    "EW_TL": "E-W through",
    "EW_R": "E-W right",
}


def _open(path: Path):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"Cannot open {path}")
    return cap


def _read_scaled(cap, w=TILE_W, h=TILE_H):
    ok, frame = cap.read()
    if not ok:
        return None
    return cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)


def _pressures_at(decisions: list[dict], t_s: float) -> tuple[dict[str, float], str, int]:
    if not decisions:
        return {p: 0.0 for p in PHASE_ORDER}, "NS_TL", 0
    best = decisions[0]
    for row in decisions:
        if float(row.get("t_s", 0)) <= t_s + 1e-6:
            best = row
        else:
            break
    presses = {p: float((best.get("pressures") or {}).get(p) or 0) for p in PHASE_ORDER}
    desired = max(PHASE_ORDER, key=lambda p: presses[p])
    return presses, desired, int(best.get("n_vehicles") or 0)


def build_signal_plan(
    decisions: list[dict],
    duration_s: float,
    *,
    min_green: float,
    yellow: float,
    all_red: float,
    hysteresis: float,
    dt: float = 0.1,
) -> list[dict]:
    """Simulate XtraFlow hard rules over [0, duration_s].

    Returns per-tick: mode (green|yellow|all_red), phase, pressures, desired, reason.
    """
    if not decisions:
        return []
    _, first_des, _ = _pressures_at(decisions, 0.0)
    phase = first_des
    mode = "green"
    time_in_mode = 0.0
    pending = None
    plan = []
    t = 0.0
    while t <= duration_s + 1e-9:
        presses, desired, n_veh = _pressures_at(decisions, min(t, float(decisions[-1]["t_s"])))
        reason = "hold"
        if mode == "green":
            time_in_mode += dt
            beat = presses[desired] > presses[phase] * (1.0 + hysteresis)
            if time_in_mode >= min_green and desired != phase and beat:
                mode = "yellow"
                time_in_mode = 0.0
                pending = desired
                reason = "pressure"
            elif time_in_mode >= min_green and desired != phase and presses[desired] > presses[phase]:
                # weaker switch still allowed after long green if clearly ahead
                if presses[desired] >= presses[phase] + 0.5:
                    mode = "yellow"
                    time_in_mode = 0.0
                    pending = desired
                    reason = "pressure"
            else:
                reason = "min_green" if time_in_mode < min_green else "best"
        elif mode == "yellow":
            time_in_mode += dt
            reason = "yellow"
            if time_in_mode >= yellow:
                mode = "all_red"
                time_in_mode = 0.0
                reason = "all_red"
        elif mode == "all_red":
            time_in_mode += dt
            reason = "all_red"
            if time_in_mode >= all_red:
                phase = pending or desired
                pending = None
                mode = "green"
                time_in_mode = 0.0
                reason = "serve"

        plan.append(
            {
                "t_s": round(t, 2),
                "mode": mode,
                "phase": phase,
                "pending": pending,
                "pressures": {k: round(v, 2) for k, v in presses.items()},
                "desired": desired,
                "n_vehicles": n_veh,
                "reason": reason,
            }
        )
        t += dt
    return plan


def _plan_at(plan: list[dict], t_s: float) -> dict:
    best = plan[0]
    for row in plan:
        if row["t_s"] <= t_s + 1e-6:
            best = row
        else:
            break
    return best


def _dim(frame: np.ndarray, amount: float = 0.4) -> np.ndarray:
    return cv2.convertScaleAbs(frame, alpha=amount, beta=0)


def _blend(frame: np.ndarray, amount: float) -> np.ndarray:
    return cv2.convertScaleAbs(frame, alpha=float(np.clip(amount, 0.25, 1.0)), beta=0)


def _glow(frame: np.ndarray, color, thickness: int = 10) -> np.ndarray:
    out = frame.copy()
    h, w = out.shape[:2]
    cv2.rectangle(out, (0, 0), (w - 1, h - 1), color, thickness)
    return out


def _put(img, text, org, scale=0.7, color=(255, 255, 255), thick=2):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def _draw_pressure_bars(canvas, presses: dict, x0: int, y0: int, served: str):
    """Small fuel-pressure bars — the hypothesis score."""
    max_p = max(presses.values()) if presses else 1.0
    max_p = max(max_p, 0.5)
    x = x0
    for ph in PHASE_ORDER:
        val = float(presses.get(ph, 0))
        h = int(28 * (val / max_p))
        color = (80, 255, 140) if ph == served else (160, 160, 160)
        cv2.rectangle(canvas, (x, y0 + 28 - h), (x + 22, y0 + 28), color, -1)
        _put(canvas, ph.replace("_", "")[:4], (x - 2, y0 + 44), 0.32, color, 1)
        x += 34


def build_story(
    overlay_paths: dict[str, Path],
    decisions: list[dict],
    out_path: Path,
    fps: float = 10.0,
) -> Path:
    cfg = load_config()
    yellow = float(cfg["signals"]["yellow_s"])
    all_red = float(cfg["signals"]["all_red_s"])
    hyst = float(cfg["controller"]["hysteresis"])
    real_min_green = float(cfg["signals"]["min_green_s"])
    # Clip is short — keep real yellow/all-red; scale only min-green for the demo length.
    caps = {a: _open(overlay_paths[a]) for a in ORDER if a in overlay_paths}
    if len(caps) < 2:
        raise SystemExit("Need at least two overlay videos")

    last: dict[str, np.ndarray] = {}
    exhausted: set[str] = set()
    continuous_lens = []
    for a, cap in caps.items():
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if n > 0:
            continuous_lens.append(n)
        fr = _read_scaled(cap)
        if fr is None:
            exhausted.add(a)
        else:
            last[a] = fr

    max_frames = max(continuous_lens) if continuous_lens else int(fps * 15)
    duration_s = max_frames / fps
    # Fit at least one full green+yellow+all-red cycle in the clip.
    demo_min_green = max(2.5, min(real_min_green, duration_s * 0.28))
    plan = build_signal_plan(
        decisions,
        duration_s,
        min_green=demo_min_green,
        yellow=yellow,
        all_red=all_red,
        hysteresis=hyst,
        dt=1.0 / fps,
    )

    out_w = TILE_W * 2
    out_h = BANNER_H + TILE_H * 2 + FOOTER_H
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(out_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (out_w, out_h),
    )

    frame_i = 0
    while frame_i < max_frames:
        t_s = frame_i / fps
        tick = _plan_at(plan, t_s) if plan else {
            "mode": "green",
            "phase": "NS_TL",
            "pressures": {},
            "desired": "NS_TL",
            "n_vehicles": 0,
            "reason": "hold",
        }
        mode = tick["mode"]
        phase = tick["phase"]
        presses = tick.get("pressures") or {}
        served_aps = set(PHASE_APPROACHES.get(phase, ()))
        # Who advances video frames
        if mode == "green":
            play = served_aps
        elif mode == "yellow":
            play = served_aps  # clearing movement still rolls
        else:
            play = set()  # all-red: everyone frozen

        # yellow fade progress within yellow window
        y_prog = 0.0
        if mode == "yellow":
            y_start = t_s
            for row in reversed(plan):
                if row["t_s"] > t_s + 1e-9:
                    continue
                if row["mode"] != "yellow":
                    break
                y_start = row["t_s"]
            y_prog = float(np.clip((t_s - y_start) / max(yellow, 1e-6), 0, 1))

        for a, cap in caps.items():
            if a in exhausted or a not in play:
                continue
            fr = _read_scaled(cap)
            if fr is None:
                exhausted.add(a)
            else:
                last[a] = fr

        if not last:
            break

        canvas = np.zeros((out_h, out_w, 3), dtype=np.uint8)
        canvas[:BANNER_H, :] = (24, 24, 24)

        # Hypothesis line
        served_p = presses.get(phase, 0)
        desire = tick.get("desired") or phase
        desire_p = presses.get(desire, 0)
        if mode == "green":
            banner = f"SERVE {PHASE_PLAIN.get(phase, phase)}  (fuel pressure {served_p:.1f})"
            bcol = (80, 255, 140)
        elif mode == "yellow":
            banner = f"YELLOW clearing {PHASE_PLAIN.get(phase, phase)}"
            bcol = (40, 200, 255)
        else:
            nxt = tick.get("pending") or desire
            banner = f"ALL RED -> next {PHASE_PLAIN.get(nxt, nxt)}"
            bcol = (60, 60, 220)

        _put(canvas, f"t={t_s:4.1f}s", (12, 28), 0.55, (200, 200, 200), 1)
        _put(canvas, banner, (120, 28), 0.62, bcol, 2)
        _put(
            canvas,
            "Hypothesis: weight idle fuel by class, serve highest pressure under yellow+all-red",
            (12, 58),
            0.42,
            (170, 170, 170),
            1,
        )
        _put(
            canvas,
            f"desired={desire} ({desire_p:.1f})  veh={tick.get('n_vehicles', 0)}  "
            f"min_g={demo_min_green:.0f}s(demo) y={yellow:.0f}s ar={all_red:.0f}s",
            (12, 78),
            0.38,
            (140, 140, 140),
            1,
        )
        _draw_pressure_bars(canvas, presses, out_w - 160, 8, phase)

        positions = {
            "N": (0, BANNER_H),
            "E": (TILE_W, BANNER_H),
            "S": (0, BANNER_H + TILE_H),
            "W": (TILE_W, BANNER_H + TILE_H),
        }
        for a, (x, y) in positions.items():
            if a not in last:
                continue
            tile = last[a]
            if mode == "all_red":
                tile = _dim(tile, 0.32)
                tile = _glow(tile, (50, 50, 200), 8)
                badge, bcolor = "RED", (50, 50, 200)
            elif a in served_aps and mode == "green":
                tile = _glow(tile, (40, 220, 90))
                badge, bcolor = "GO", (40, 220, 90)
            elif a in served_aps and mode == "yellow":
                tile = _blend(tile, 1.0 - 0.5 * y_prog)
                tile = _glow(tile, (40, 200, 255), 10)
                badge, bcolor = "YELLOW", (40, 200, 255)
            else:
                tile = _dim(tile, 0.38)
                badge, bcolor = "PAUSE", (80, 80, 200)

            canvas[y : y + TILE_H, x : x + TILE_W] = tile
            bw = 130 if badge == "YELLOW" else 118
            cv2.rectangle(canvas, (x + 8, y + 8), (x + bw, y + 40), bcolor, -1)
            _put(canvas, f"{a} {badge}", (x + 14, y + 32), 0.65, (255, 255, 255), 2)

        canvas[out_h - FOOTER_H :, :] = (18, 18, 18)
        _put(
            canvas,
            "Real rule: detect -> fuel-weighted pressure -> green / yellow / all-red / switch",
            (12, out_h - 34),
            0.45,
            (190, 190, 190),
            1,
        )
        _put(
            canvas,
            "GO plays | YELLOW clears (still rolling) | ALL RED / PAUSE frozen | bars = phase fuel pressure",
            (12, out_h - 14),
            0.42,
            (150, 150, 150),
            1,
        )
        # progress
        bar_y = out_h - FOOTER_H + 8
        cv2.rectangle(canvas, (16, bar_y), (out_w - 16, bar_y + 6), (50, 50, 50), -1)
        cv2.rectangle(
            canvas,
            (16, bar_y),
            (16 + int((out_w - 32) * min(1.0, t_s / max(duration_s, 1e-6))), bar_y + 6),
            (80, 220, 140),
            -1,
        )

        writer.write(canvas)
        frame_i += 1

    for cap in caps.values():
        cap.release()
    writer.release()

    # Persist plan next to outputs for dashboard/debug
    plan_path = out_path.with_name("yolo_story_plan.json")
    plan_path.write_text(
        json.dumps(
            {
                "hypothesis": (
                    "Weight waiting vehicles by idle fuel class; serve the phase with "
                    "highest fuel-weighted pressure under min-green, yellow, and all-red."
                ),
                "timings": {
                    "demo_min_green_s": demo_min_green,
                    "real_min_green_s": real_min_green,
                    "yellow_s": yellow,
                    "all_red_s": all_red,
                    "hysteresis": hyst,
                    "note": "min_green scaled to fit the short clip; yellow/all-red match config.yaml",
                },
                "plan": plan,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return out_path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--summary",
        default=str(ROOT / "results" / "demo" / "yolo" / "yolo_demo_summary.json"),
    )
    p.add_argument(
        "--out",
        default=str(ROOT / "results" / "demo" / "yolo" / "yolo_story.mp4"),
    )
    args = p.parse_args()
    summary_path = Path(args.summary)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not (summary.get("clubbed") or {}).get("decisions"):
        summary = enrich_summary(summary)
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    overlays = {}
    for cam in summary.get("cameras") or []:
        a = cam["approach"]
        rel = (summary.get("per_camera") or {}).get(a, {}).get("overlay")
        path = ROOT / rel if rel else ROOT / f"results/demo/yolo/overlay_{a}.mp4"
        if path.exists():
            overlays[a] = path
    if len(overlays) < 2:
        raise SystemExit("Missing overlay videos — run make yolo_demo first")

    decisions = (summary.get("clubbed") or {}).get("decisions") or []
    # Re-time decisions onto video clock (counts used frame/3 which can exceed clip length).
    if decisions:
        t_end = float(decisions[-1]["t_s"] or 1)
        # Probe overlay length
        probe = next(iter(overlays.values()))
        cap = _open(probe)
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 100)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 10) or 10
        cap.release()
        vid_s = max(n / fps, 1.0)
        if t_end > vid_s * 1.2:
            scale = vid_s / t_end
            for row in decisions:
                row["t_s"] = round(float(row["t_s"]) * scale, 2)

    out = build_story(overlays, decisions, Path(args.out))
    copy = ROOT / "results" / "demo" / "yolo_story.mp4"
    copy.write_bytes(out.read_bytes())
    summary.setdefault("outputs", {})["story"] = str(out.relative_to(ROOT))
    summary.setdefault("outputs", {})["story_plan"] = str(
        out.with_name("yolo_story_plan.json").relative_to(ROOT)
    )
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(out)
    print(copy)


if __name__ == "__main__":
    main()
