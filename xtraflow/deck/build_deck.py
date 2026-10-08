"""Build a 16:9 deck. Every reported number is loaded from results/ or config."""
from __future__ import annotations

import csv
import statistics
import subprocess
from collections import defaultdict
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

from sim.util import ROOT, load_config

TITLE = "Georgia"
BODY = "Calibri"

NAVY = RGBColor(0x0E, 0x1C, 0x2B)
NAVY_2 = RGBColor(0x17, 0x2A, 0x3C)
INK = RGBColor(0x1A, 0x28, 0x34)
MUTED = RGBColor(0x5E, 0x6E, 0x7C)
CREAM = RGBColor(0xF6, 0xF3, 0xEE)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
AMBER = RGBColor(0xC4, 0x84, 0x1D)
TEAL = RGBColor(0x1B, 0x6B, 0x5A)
CORAL = RGBColor(0xB8, 0x4E, 0x2A)
LINE = RGBColor(0xE3, 0xDB, 0xD0)
SOFT = RGBColor(0xFB, 0xF8, 0xF3)
SOFT_TEAL = RGBColor(0xE5, 0xF2, 0xEE)
SOFT_AMBER = RGBColor(0xFB, 0xF3, 0xE2)
SOFT_CORAL = RGBColor(0xFB, 0xEE, 0xE8)
MIST = RGBColor(0xC9, 0xD4, 0xDC)

L = 0.55
CONTENT_W = 12.28
PAGE_W = 13.333
PAGE_H = 7.5

SCENARIO = {
    "peak_unbalanced": "Peak, one-sided",
    "dynamic": "Shifting",
    "low_demand": "Low demand",
    "balanced": "Balanced",
}
VEHICLE = {
    "two_wheeler": "Two-wheeler",
    "auto_rickshaw": "Auto",
    "car": "Car",
    "bus": "Bus",
    "truck": "Truck",
}
MIX_NAME = {
    "two_wheeler": "two-wheelers",
    "car": "cars",
    "truck": "trucks",
    "auto_rickshaw": "autos",
    "bus": "buses",
}
CTRL = {
    "fixed": "Fixed time",
    "fixed_tuned": "Tuned fixed",
    "webster": "Webster",
    "actuated": "Actuated",
    "queue_pressure": "Queue pressure",
    "maxpressure": "Max-pressure",
    "ours_count": "Count only",
    "XtraFlow": "XtraFlow",
    "rl_ppo": "PPO",
}


def _load(path: Path):
    if not path.exists():
        return None
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _repo_url() -> str:
    try:
        raw = subprocess.check_output(
            ["git", "remote", "get-url", "origin"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""
    if raw.endswith(".git"):
        raw = raw[:-4]
    return raw.removeprefix("https://").removeprefix("http://")


def _means(path: Path) -> dict:
    if not path.exists():
        return {}
    out = {}
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            parsed = {}
            for key, value in row.items():
                if key in ("scenario", "controller"):
                    continue
                parsed[key] = float(value)
            out[(row["scenario"], row["controller"])] = parsed
    return out


def _test_seeds(path: Path) -> list[int]:
    if not path.exists():
        return []
    seeds: set[int] = set()
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                seeds.add(int(row["seed"]))
            except (KeyError, TypeError, ValueError):
                continue
    return sorted(seeds)


def _headline_pct(row: dict) -> float:
    if row.get("pct_fuel_reduction_vs_best") is not None:
        return float(row["pct_fuel_reduction_vs_best"])
    return float(row.get("pct_fuel_reduction_vs_fixed") or 0)


def _legacy_pct(row: dict) -> float | None:
    value = row.get("pct_fuel_reduction_vs_fixed_legacy")
    if value is None:
        return None
    return float(value)


def _robust(blob: dict | None) -> list[dict]:
    if not blob:
        return []
    groups: dict[tuple, list] = defaultdict(list)
    for row in blob.get("rows", []):
        groups[(row["controller"], row.get("kind"), float(row.get("miss") or 0))].append(
            float(row["fuel_per_vehicle_L"])
        )
    rows = []
    for (controller, kind, miss), vals in groups.items():
        rows.append(
            {
                "controller": controller,
                "kind": kind,
                "miss": miss,
                "n": len(vals),
                "mean": statistics.fmean(vals),
                "std": statistics.pstdev(vals) if len(vals) > 1 else 0.0,
            }
        )
    return rows


def load() -> dict:
    cfg = load_config()
    summary = _load(ROOT / "results" / "summary.json") or {}
    weights = _load(ROOT / "results" / "weights.json") or {}
    tuned = _load(ROOT / "results" / "tuned_params.json") or {}
    safety = _load(ROOT / "results" / "safety.json") or {}
    xcheck = _load(ROOT / "results" / "emission_xcheck.json") or {}
    grid = _load(ROOT / "results" / "grid_results.json") or {}
    extra = _load(ROOT / "results" / "extrapolation.json") or {}
    perc = _load(ROOT / "results" / "perception_smoke.json") or {}
    sublane = _load(ROOT / "results" / "sublane_fallback.json") or {}
    rl_meta = _load(ROOT / "results" / "rl" / "rl_selection.json") or {}
    yolo = _load(ROOT / "results" / "demo" / "yolo" / "yolo_demo_summary.json") or {}
    headlines = list(summary.get("headlines") or _load(ROOT / "results" / "headlines.json") or [])
    headlines.sort(key=lambda row: -_headline_pct(row))
    comps = {}
    for row in summary.get("comparisons", []):
        if row.get("metric") == "fuel_per_vehicle_L":
            comps[(row["scenario"], row["baseline"])] = row
    test_seeds = _test_seeds(ROOT / "results" / "raw_runs.csv")
    controllers = list(summary.get("controllers") or [])
    return {
        "cfg": cfg,
        "summary": summary,
        "weights": weights,
        "tuned": tuned,
        "safety": safety,
        "xcheck": xcheck,
        "grid": grid,
        "extra": extra,
        "perc": perc,
        "sublane": sublane,
        "rl_meta": rl_meta,
        "yolo": yolo,
        "headlines": headlines,
        "comps": comps,
        "means": _means(ROOT / "results" / "tables" / "summary_mean_std.csv"),
        "robust": _robust(_load(ROOT / "results" / "robustness.json")),
        "test_seeds": test_seeds,
        "controllers": controllers,
        "url": _repo_url(),
        "label": summary.get("label")
        or extra.get("label")
        or "Simulation-based estimate; not a real-world deployment result",
    }


def _style(run, font, size, color, bold=False):
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = False
    run.font.color.rgb = color
    rpr = run._r.get_or_add_rPr()
    for tag in ("latin", "ea", "cs"):
        node = rpr.find(qn(f"a:{tag}"))
        if node is None:
            node = rpr.makeelement(qn(f"a:{tag}"))
            rpr.append(node)
        node.set("typeface", font)


def write(slide, l, t, w, h, paragraphs, align=PP_ALIGN.LEFT, anchor="t"):
    box = slide.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.auto_size = None
    tf.margin_left = Emu(0)
    tf.margin_right = Emu(0)
    tf.margin_top = Emu(0)
    tf.margin_bottom = Emu(0)
    anchor_map = {"t": MSO_ANCHOR.TOP, "ctr": MSO_ANCHOR.MIDDLE, "b": MSO_ANCHOR.BOTTOM}
    tf.paragraphs[0].alignment = align
    try:
        tf._txBody.bodyPr.set("anchor", {"t": "t", "ctr": "ctr", "b": "b"}[anchor])
    except Exception:
        _ = anchor_map
    for i, runs in enumerate(paragraphs):
        paragraph = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        paragraph.alignment = align
        paragraph.space_before = Pt(0)
        paragraph.space_after = Pt(0)
        for text, size, color, bold, font in runs:
            run = paragraph.add_run()
            run.text = text
            _style(run, font or BODY, size, color, bold)
    return box


def notes(slide, text):
    slide.notes_slide.notes_text_frame.text = text


def bg(slide, color):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def rect(slide, l, t, w, h, fill, line=None, radius=None):
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(kind, Inches(l), Inches(t), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line
        shape.line.width = Pt(1)
    if radius:
        try:
            shape.adjustments[0] = radius
        except Exception:
            pass
    return shape


def blank(prs, dark=False):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg(slide, NAVY if dark else CREAM)
    rect(slide, 0, 0, 0.08, PAGE_H, AMBER)
    return slide


def footer(slide, page, dark=False):
    color = MIST if dark else MUTED
    write(
        slide,
        L,
        7.12,
        9.2,
        0.26,
        [[("XtraFlow    ·    Simulation-based estimate", 12, color, False, BODY)]],
    )
    write(
        slide,
        10.6,
        7.12,
        2.2,
        0.26,
        [[(f"{page:02d}", 12, color, False, TITLE)]],
        align=PP_ALIGN.RIGHT,
    )


def chrome(slide, kicker, title, dek, page):
    write(slide, L, 0.28, 10, 0.26, [[(kicker.upper(), 12, AMBER, True, BODY)]])
    write(slide, L, 0.52, CONTENT_W, 0.52, [[(title, 28, INK, True, TITLE)]])
    if dek:
        write(slide, L, 1.08, CONTENT_W, 0.46, [[(dek, 15, MUTED, False, BODY)]])
    footer(slide, page)


def _card(slide, l, t, w, h, fill=WHITE):
    return rect(slide, l, t, w, h, fill, line=LINE, radius=0.08)


def picture(slide, path: Path, l, t, w, h=None):
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return None
    kwargs = {"left": Inches(l), "top": Inches(t), "width": Inches(w)}
    if h is not None:
        kwargs["height"] = Inches(h)
    return slide.shapes.add_picture(str(path), **kwargs)


def movie(slide, movie_path: Path, poster_path: Path | None, l, t, w, h):
    """Embed an MP4 so PowerPoint can play it in-slide (click to play)."""
    movie_path = Path(movie_path)
    if not movie_path.exists() or movie_path.stat().st_size == 0:
        return None
    poster = None
    if poster_path is not None:
        poster_path = Path(poster_path)
        if poster_path.exists() and poster_path.stat().st_size > 0:
            poster = str(poster_path)
    return slide.shapes.add_movie(
        str(movie_path),
        Inches(l),
        Inches(t),
        Inches(w),
        Inches(h),
        poster_frame_image=poster,
        mime_type="video/mp4",
    )


def _fmt_pct(value, digits=1) -> str:
    return f"{value:.{digits}f}%"


def _scenario(name: str) -> str:
    return SCENARIO.get(name, name.replace("_", " "))


def _baseline_name(key: str | None) -> str:
    if not key:
        return "best baseline"
    return CTRL.get(key, key.replace("_", " "))


def _focus(data) -> str:
    tuned = data["tuned"].get("scenario")
    if tuned:
        return tuned
    # Prefer the peak scenario for the rider-experience slide when no single tune target exists.
    names = {row["scenario"] for row in data["headlines"]}
    if "peak_unbalanced" in names:
        return "peak_unbalanced"
    if data["headlines"]:
        return data["headlines"][0]["scenario"]
    return "peak_unbalanced"


def _mean(data, scenario, controller, key):
    row = data["means"].get((scenario, controller))
    if not row:
        return None
    return row.get(key)


def _comp(data, scenario, baseline):
    return data["comps"].get((scenario, baseline))


def _scope_bits(data) -> tuple[str, str, str]:
    n_sc = len(data["summary"].get("scenarios") or data["cfg"].get("demand") or {})
    n_ctrl = len(data["controllers"] or data["summary"].get("controllers") or [])
    seeds = data["test_seeds"]
    if seeds:
        if seeds[0] == seeds[-1]:
            seed_bit = f"{len(seeds)} test seed ({seeds[0]})"
        else:
            seed_bit = f"{len(seeds)} test seeds ({seeds[0]}–{seeds[-1]})"
    else:
        test = (data["cfg"].get("seed_protocol") or {}).get("test") or [1, 30]
        seed_bit = f"{int(test[1]) - int(test[0]) + 1} test seeds (config)"
    return str(n_sc or "—"), seed_bit, str(n_ctrl or "—")


def slide_title(prs, data, page, _total):
    slide = blank(prs, dark=True)
    write(slide, 0.78, 1.42, 10, 0.28, [[("SIMULATION STUDY", 13, AMBER, True, BODY)]])
    write(slide, 0.75, 1.78, 11, 0.95, [[("XtraFlow", 54, WHITE, True, TITLE)]])
    rect(slide, 0.8, 2.82, 2.15, 0.035, AMBER)
    write(
        slide,
        0.78,
        3.05,
        10,
        0.5,
        [[("Fuel-weighted traffic signals", 26, RGBColor(0xF0, 0xC2, 0x6A), False, TITLE)]],
    )
    n_sc, seed_bit, n_ctrl = _scope_bits(data)
    yolo = data.get("yolo") or {}
    write(
        slide,
        0.78,
        3.7,
        9.2,
        0.85,
        [[(
            f"A locked comparison on mixed Indian traffic. Same demand, {n_ctrl} controllers, "
            f"{n_sc} demand patterns. Not a field deployment."
            + (" Live demos: SUMO side-by-side + YOLO camera clips." if yolo else ""),
            18,
            MIST,
            False,
            BODY,
        )]],
    )
    if yolo:
        facts = [
            (n_sc, "scenarios"),
            (seed_bit.split()[0], "test seeds, scored once"),
            ("2", "live clips to play"),
        ]
    else:
        facts = [
            (n_sc, "scenarios"),
            (seed_bit.split()[0], "test seeds, scored once"),
            (n_ctrl, "controllers in TEST"),
        ]
    x = 0.78
    for value, label in facts:
        write(slide, x, 5.15, 3.3, 0.5, [[(value, 28, WHITE, True, TITLE)]])
        write(slide, x, 5.68, 3.3, 0.35, [[(label, 13, MIST, False, BODY)]])
        x += 3.5
    url = data["url"] or "local study"
    write(slide, 0.78, 6.55, 8, 0.3, [[(url, 13, MIST, False, BODY)]])
    write(slide, 0.78, 6.88, 10, 0.28, [[(data["label"], 12, RGBColor(0x8A, 0x9B, 0xA8), False, BODY)]])
    notes(
        slide,
        "Open on energy, not on travel time. Say out loud that this is a simulation before the first percentage. "
        f"Scope: {seed_bit}. Source label: {data['label']}. "
        "Demo order later: results/demo/demo.mp4, then results/demo/yolo/yolo_story.mp4.",
    )


def slide_agenda(prs, data, page, _total):
    slide = blank(prs)
    yolo = data.get("yolo") or {}
    chrome(
        slide,
        "Talk map",
        "What you will see",
        "Numbers first from the locked TEST. Clips last — they illustrate, they do not replace the headline.",
        page,
    )
    blocks = [
        ("01", "Idea", "Adaptive pressure vs fixed timing; fuel weights are a small add-on."),
        ("02", "Locked TEST", "Same demand, fair baselines, full figure gallery on disk."),
        ("03", "Live · SUMO", "Side-by-side: fixed timing vs XtraFlow on one seed."),
        (
            "04",
            "Live · YOLO",
            "Camera mosaic with GO/WAIT story, then per-cam overlays if asked."
            if yolo
            else "Camera path: run make yolo_demo to fill the clips.",
        ),
        ("05", "Limits", "Detection miss, unscored grid, oracle TEST, next field steps."),
    ]
    gap = 0.14
    width = (CONTENT_W - 4 * gap) / 5
    for i, (num, title, body) in enumerate(blocks):
        x = L + i * (width + gap)
        _card(slide, x, 1.75, width, 4.15)
        write(slide, x + 0.18, 1.95, width - 0.35, 0.35, [[(num, 18, AMBER, True, TITLE)]])
        write(slide, x + 0.18, 2.45, width - 0.35, 0.7, [[(title, 18, INK, True, TITLE)]])
        write(slide, x + 0.18, 3.3, width - 0.38, 2.2, [[(body, 14, INK, False, BODY)]])
    notes(
        slide,
        "Skip nothing in 01–03 for a short talk. Play both live clips. "
        "Robustness / grid / scaling are backup if asked.",
    )


def slide_problem(prs, data, page, _total):
    slide = blank(prs)
    weights = (data["weights"].get("w_type") or {})
    truck = weights.get("truck")
    bike = weights.get("two_wheeler")
    truck_bit = f"{truck:.1f}× a car" if truck is not None else "several times a car"
    bike_bit = f"{bike:.2f}×" if bike is not None else "a fraction of"
    chrome(
        slide,
        "The problem",
        "A stopped queue is burning fuel",
        "Fixed green cannot see which approach is waiting, or what that wait costs.",
        page,
    )
    cards = [
        (
            "01",
            "Fixed green is blind",
            "The split stays put when one road is empty and the other is full. Webster helps the average hour. It still lags a peak that moves.",
        ),
        (
            "02",
            "A count is the wrong unit",
            f"Here a truck idles at {truck_bit}, a two-wheeler at {bike_bit}. Treating them as one vehicle spends green on the wrong queue.",
        ),
        (
            "03",
            "The question",
            "If the signal weighs each vehicle by idle fuel, do fuel, CO₂, delay, and queues fall — inside hard green, yellow, and all-red limits?",
        ),
    ]
    gap = 0.18
    width = (CONTENT_W - 2 * gap) / 3
    for i, (num, title, body) in enumerate(cards):
        x = L + i * (width + gap)
        _card(slide, x, 1.72, width, 4.05)
        write(slide, x + 0.26, 1.96, width - 0.5, 0.4, [[(num, 20, AMBER, True, TITLE)]])
        write(slide, x + 0.26, 2.55, width - 0.5, 0.85, [[(title, 20, INK, True, TITLE)]])
        write(slide, x + 0.26, 3.5, width - 0.52, 1.9, [[(body, 15, INK, False, BODY)]])
    mix = data["cfg"].get("vehicle_mix") or {}
    parts = [
        f"{MIX_NAME.get(key, key)} {share * 100:.0f}%"
        for key, share in sorted(mix.items(), key=lambda item: -item[1])
    ]
    mix_line = "Assumed mix   ·   " + "   ·   ".join(parts) if parts else "Vehicle mix not loaded"
    rect(slide, L, 5.95, CONTENT_W, 0.95, SOFT, line=LINE, radius=0.08)
    write(slide, L + 0.28, 6.08, CONTENT_W - 0.5, 0.28, [[("LEFT-HAND TRAFFIC  ·  ONE FOUR-ARM JUNCTION", 12, AMBER, True, BODY)]])
    write(slide, L + 0.28, 6.38, CONTENT_W - 0.5, 0.35, [[(mix_line, 14, INK, False, BODY)]])
    notes(
        slide,
        "The mix is assumed until observed counts are supplied. Do not present it as a city survey. "
        "Idle-fuel ratios are measured in SUMO, not guessed.",
    )


def slide_weights(prs, data, page, _total):
    slide = blank(prs)
    weights = data["weights"]
    wtype = weights.get("w_type") or {}
    tuned = data["tuned"]
    duration = weights.get("duration_s")
    chrome(
        slide,
        "The controller",
        "Weigh the queue by the fuel it burns",
        "Idle-fuel weights are a small add-on on top of adaptive pressure (about 0–2% vs count-only on TEST).",
        page,
    )
    probe = f"{duration:.0f}-second" if duration else "stopped"
    write(
        slide,
        L,
        1.75,
        5.35,
        1.5,
        [[(
            f"Weights come from a {probe} probe of fuel burned while the vehicle is stopped. "
            "Emission classes are assumed. Autos and cars share a class in this run, so they share a weight.",
            16,
            INK,
            False,
            BODY,
        )]],
    )
    if tuned:
        write(
            slide,
            L,
            3.45,
            5.35,
            1.35,
            [[
                ("Chosen on validation only", 13, AMBER, True, BODY),
            ], [
                (
                    f"Look back {tuned.get('detection_distance_m', 0):.0f} m. "
                    f"Count moving vehicles at {tuned.get('alpha_moving', 0):.2f}. "
                    f"Switch only past hysteresis {tuned.get('hysteresis', 0):.2f}. "
                    f"Scenario: {_scenario(tuned.get('scenario', ''))}.",
                    15,
                    INK,
                    False,
                    BODY,
                ),
            ]],
        )
    order = [key for key in ("two_wheeler", "auto_rickshaw", "car", "bus", "truck") if key in wtype]
    max_w = max((wtype[key] for key in order), default=1) or 1
    top = 1.78
    for key in order:
        value = wtype[key]
        write(slide, 6.05, top, 1.55, 0.46, [[(VEHICLE.get(key, key), 14, INK, False, BODY)]], anchor="ctr")
        rect(slide, 7.7, top + 0.14, 3.15, 0.16, LINE)
        rect(slide, 7.7, top + 0.14, 3.15 * (value / max_w), 0.16, TEAL)
        write(
            slide,
            11.15,
            top,
            1.35,
            0.46,
            [[(f"{value:.2f}×", 15, TEAL, True, TITLE)]],
            anchor="ctr",
        )
        top += 0.62
    sig = data["cfg"].get("signals") or {}
    chips = [
        ("Min green", sig.get("min_green_s")),
        ("Max green", sig.get("max_green_s")),
        ("Yellow", sig.get("yellow_s")),
        ("All-red", sig.get("all_red_s")),
        ("Must serve", sig.get("starvation_s")),
    ]
    chips = [(name, value) for name, value in chips if value is not None]
    rect(slide, L, 5.45, CONTENT_W, 1.42, NAVY, radius=0.08)
    write(slide, L + 0.28, 5.58, 8, 0.28, [[("TIMING THAT STAYS IN FORCE", 12, AMBER, True, BODY)]])
    if chips:
        cw = (CONTENT_W - 0.5) / len(chips)
        for i, (name, value) in enumerate(chips):
            x = L + 0.22 + i * cw
            write(slide, x, 5.95, cw - 0.1, 0.4, [[(f"{value:g} s", 20, WHITE, True, TITLE)]])
            write(slide, x, 6.4, cw - 0.1, 0.28, [[(name, 12, MIST, False, BODY)]])
    notes(
        slide,
        "Explain hysteresis: the new phase must beat the current pressure by the tuned margin. "
        "Must-serve forces a switch so a waiting approach is not ignored. Weights file: results/weights.json. "
        "Tuned parameters: results/tuned_params.json, validation seeds only.",
    )


def slide_cycle(prs, data, page, _total):
    slide = blank(prs)
    flag = (data["perc"].get("flag") or "").replace("_", " ").lower()
    chrome(
        slide,
        "How a decision is made",
        "See the queue. Score the fuel. Keep the timing.",
        "The signal never skips yellow or all-red, and it cannot hold green past the cap.",
        page,
    )
    miss = (data["perc"].get("noise_model") or {}).get("detect_miss_rate")
    assumed = "ASSUMED" in str(data["perc"].get("flag", "")) or "assumed" in flag
    yolo = data.get("yolo") or {}
    if yolo:
        see = (
            "Detectors on each approach, or a camera. A YOLO overlay demo is on a later slide — "
            "input feasibility, not a fuel-saving result. Locked TEST scores still use the oracle detector"
        )
        if assumed:
            see += "; detector-miss noise for robustness stays assumed"
            see += f" ({miss:.0%} in the smoke check)." if isinstance(miss, (int, float)) else "."
        else:
            see += ". Detector error in this run comes from supplied video."
    elif assumed:
        see = "Detectors on each approach, or a camera. YOLO with a region of interest can read the same queue. This run had no field video, so detector error is assumed"
        see += f" ({miss:.0%} miss in the smoke check)." if isinstance(miss, (int, float)) else "."
    else:
        see = "Detectors on each approach, or a camera. YOLO with a region of interest can read the same queue. Detector error in this run comes from supplied video."
    cards = [
        (
            "01",
            "See",
            see,
        ),
        (
            "02",
            "Score",
            "Each queued vehicle is scaled by its idle-fuel weight. Moving vehicles count at the tuned fraction. The phase changes only after minimum green, and only if the gain clears hysteresis.",
        ),
        (
            "03",
            "Measure",
            "Fuel and CO₂ come from SUMO on every completed trip. Waiting and queues use the same seed the other controllers saw. The test set was not used to pick the settings.",
        ),
    ]
    gap = 0.18
    width = (CONTENT_W - 2 * gap) / 3
    for i, (num, title, body) in enumerate(cards):
        x = L + i * (width + gap)
        _card(slide, x, 1.75, width, 4.15)
        write(slide, x + 0.26, 1.98, width - 0.5, 0.36, [[(num, 18, AMBER, True, TITLE)]])
        write(slide, x + 0.26, 2.45, width - 0.5, 0.5, [[(title, 26, INK, True, TITLE)]])
        write(slide, x + 0.26, 3.2, width - 0.52, 2.4, [[(body, 15, INK, False, BODY)]])
    notes(
        slide,
        "Play the YOLO story after the side-by-side SUMO demo (yolo_story.mp4). "
        "Say clearly: YOLO is input feasibility; headline fuel numbers are oracle TEST. "
        "Do not claim a field detector result.",
    )


def _demand_blurb(name, demand) -> str:
    block = demand.get(name) or {}
    if name == "dynamic":
        first = (block.get("blocks") or [{}])[0]
        minutes = float(first.get("duration", 0)) / 60
        return f"The heavy axis swaps every {minutes:.0f} min. {first.get('N', 0):.0f} against {first.get('E', 0):.0f} veh/h."
    if name == "peak_unbalanced":
        return f"North–south {block.get('N', 0):.0f} veh/h. East–west {block.get('E', 0):.0f}."
    return f"{block.get('N', 0):.0f} veh/h on every approach."


def slide_design(prs, data, page, _total):
    slide = blank(prs)
    _, seed_bit, n_ctrl = _scope_bits(data)
    chrome(
        slide,
        "The experiment",
        "Same cars for every controller",
        f"Settings are frozen before the test. {seed_bit.capitalize()}. A seed is one hour of the same demand, replayed.",
        page,
    )
    protocol = data["cfg"].get("seed_protocol") or {}
    seeds = data["test_seeds"]
    if seeds:
        test_label = f"{seeds[0]} – {seeds[-1]}" if len(seeds) > 1 else str(seeds[0])
        test_role = f"{len(seeds)} locked seeds in raw_runs.csv"
    else:
        span = protocol.get("test")
        test_label = f"{span[0]} – {span[1]}" if span and len(span) == 2 else "—"
        test_role = "The numbers in the rest of this deck"
    roles = [
        ("Train", protocol.get("train"), "Reinforcement-learning baseline only"),
        ("Validation", protocol.get("validation"), "Settings chosen here, then frozen"),
        ("Test", test_label, test_role),
    ]
    gap = 0.16
    width = (CONTENT_W - 2 * gap) / 3
    for i, (name, span, role) in enumerate(roles):
        x = L + i * (width + gap)
        _card(slide, x, 1.7, width, 1.55)
        write(slide, x + 0.22, 1.82, width - 0.4, 0.26, [[(name.upper(), 12, AMBER, True, BODY)]])
        if name == "Test":
            label = span if isinstance(span, str) else "—"
        else:
            label = f"{span[0]} – {span[1]}" if span and len(span) == 2 else "—"
        write(slide, x + 0.22, 2.1, width - 0.4, 0.42, [[(label, 22, INK, True, TITLE)]])
        write(slide, x + 0.22, 2.58, width - 0.4, 0.48, [[(role, 13, MUTED, False, BODY)]])
    demand = data["cfg"].get("demand") or {}
    names = [row["scenario"] for row in data["headlines"]] or list(demand)
    gap = 0.16
    width = (CONTENT_W - 3 * gap) / 4
    for i, name in enumerate(names[:4]):
        x = L + i * (width + gap)
        _card(slide, x, 3.45, width, 1.85)
        write(slide, x + 0.18, 3.58, width - 0.32, 0.45, [[(_scenario(name), 16, INK, True, TITLE)]])
        write(slide, x + 0.18, 4.12, width - 0.34, 0.95, [[(_demand_blurb(name, demand), 13, MUTED, False, BODY)]])
    present = set(data["controllers"] or data["summary"].get("controllers") or [])
    others = [CTRL.get(name, name) for name in ("fixed", "fixed_tuned", "webster", "actuated", "queue_pressure", "maxpressure", "ours_count") if name in present]
    compared = "Compared with " + ", ".join(others) + "." if others else f"{n_ctrl} controllers in the locked TEST."
    if "rl_ppo" not in present:
        compared += " PPO was trained to the config budget and is not in this TEST table."
    write(
        slide,
        L,
        5.5,
        CONTENT_W,
        0.7,
        [[(compared, 15, INK, False, BODY)]],
    )
    sub = data["sublane"]
    if sub and sub.get("used_sublane") is False:
        write(
            slide,
            L,
            6.2,
            CONTENT_W,
            0.35,
            [[("Built with standard lanes.", 13, MUTED, False, BODY)]],
        )
    elif sub and sub.get("used_sublane") is True:
        write(
            slide,
            L,
            6.2,
            CONTENT_W,
            0.35,
            [[("Sublane probe used. Full-demand stability under that setting is not re-claimed here.", 13, MUTED, False, BODY)]],
        )
    notes(
        slide,
        "Same seed means identical demand. Test seeds are the ones stored in results/raw_runs.csv. "
        "Sublane use is the used_sublane field in results/sublane_fallback.json. "
        f"RL note: {(data.get('rl_meta') or {}).get('note') or 'see rl_selection.json'}",
    )


def slide_headlines(prs, data, page, _total):
    slide = blank(prs)
    headlines = data["headlines"]
    n_pairs = None
    if headlines:
        best = headlines[0].get("best_baseline") or "maxpressure"
        comp = _comp(data, headlines[0]["scenario"], best) or _comp(data, headlines[0]["scenario"], "fixed")
        if comp:
            n_pairs = comp.get("n_pairs")
    dek = (
        "Fuel vs best tuned baseline is only 0.3–1.9% (n=5; Holm p=1). "
        "The 8–17% story is vs fixed / Webster / actuated — see next slide."
    )
    if n_pairs:
        dek = (
            f"Fuel vs best tuned baseline is only 0.3–1.9% ({n_pairs:.0f} paired seeds; Holm p=1). "
            "Large cuts are vs fixed / Webster / actuated, not class fuel weights."
        )
    if headlines and all(_headline_pct(row) > 0 for row in headlines):
        title = "Small edge vs the strongest baseline (0.3–1.9%)"
    elif headlines:
        title = "Headline fuel change versus the strongest baseline"
    else:
        title = "Headline fuel change"
    chrome(slide, "Test set", title, dek, page)
    if not headlines:
        write(slide, L, 2.4, CONTENT_W, 0.5, [[("Run analyze.py to fill results/headlines.json.", 16, MUTED, False, BODY)]])
        return
    gap = 0.16
    width = (CONTENT_W - 3 * gap) / 4
    peak = max(abs(_headline_pct(row)) for row in headlines) or 1
    for i, row in enumerate(headlines[:4]):
        x = L + i * (width + gap)
        pct = _headline_pct(row)
        legacy = _legacy_pct(row)
        best = _baseline_name(row.get("best_baseline"))
        tone = TEAL if pct > 0 else CORAL
        _card(slide, x, 1.75, width, 3.85)
        rect(slide, x + 0.24, 1.98, 0.55, 0.045, AMBER)
        write(slide, x + 0.22, 2.15, width - 0.4, 0.45, [[(_scenario(row["scenario"]), 15, INK, True, TITLE)]])
        write(
            slide,
            x + 0.22,
            2.7,
            width - 0.4,
            0.65,
            [[
                (f"{pct:.1f}", 34, tone, True, TITLE),
                ("%", 16, tone, True, TITLE),
            ]],
        )
        write(
            slide,
            x + 0.22,
            3.4,
            width - 0.4,
            0.45,
            [[(f"{row.get('ci_lo', 0):.1f}  –  {row.get('ci_hi', 0):.1f}", 13, MUTED, False, BODY)]],
        )
        write(slide, x + 0.22, 3.85, width - 0.4, 0.28, [[(f"vs {best}", 12, MUTED, False, BODY)]])
        if legacy is not None:
            write(
                slide,
                x + 0.22,
                4.25,
                width - 0.4,
                0.45,
                [[(f"vs legacy fixed  {_fmt_pct(legacy)}", 12, INK, False, BODY)]],
            )
        bar = (width - 0.48) * (abs(pct) / peak)
        rect(slide, x + 0.24, 5.15, width - 0.48, 0.08, LINE)
        rect(slide, x + 0.24, 5.15, max(bar, 0.08), 0.08, tone)
        flag = row.get("sanity_flag", "")
        if flag != "OK":
            write(slide, x + 0.22, 4.85, width - 0.4, 0.28, [[(str(flag), 12, CORAL, True, BODY)]])
    above = all(float(row.get("ci_lo") or 0) > 0 for row in headlines)
    if above:
        note = "Every 95% interval versus the best baseline sits above zero. Tuning stopped before these seeds were scored."
    else:
        note = (
            "Mean fuel is lower than the best baseline in every scenario. "
            "Some 95% intervals still cross zero — read the interval, not only the point estimate."
        )
    rect(slide, L, 5.85, CONTENT_W, 1.0, SOFT_TEAL, radius=0.08)
    write(slide, L + 0.28, 6.12, CONTENT_W - 0.5, 0.5, [[(note, 14, INK, False, BODY)]], anchor="ctr")
    notes(
        slide,
        "Primary headline vs best of fixed_tuned / actuated / queue_pressure / maxpressure "
        "is only 0.3–1.9% (n=5; Holm p=1). The 8–17% cuts are vs fixed / Webster / actuated. "
        "Published maxpressure ≡ queue_pressure (downstream was ~0); re-run after occupancy fix. "
        "Numbers are from results/headlines.json.",
    )


def slide_baselines(prs, data, page, _total):
    slide = blank(prs)
    chrome(
        slide,
        "The honest comparison",
        "Versus strong adaptive controllers, the edge is small",
        "Against max-pressure / queue_pressure the cut is 0.3–1.9% (and those two matched bit-for-bit in this publish). Legacy fixed is the easy win.",
        page,
    )
    scenarios = [row["scenario"] for row in data["headlines"]]
    columns = [
        ("Scenario", 2.35),
        ("vs legacy fixed", 2.35),
        ("vs tuned fixed", 2.35),
        ("vs actuated", 2.35),
        ("vs max-pressure", 2.88),
    ]
    baselines = [None, "fixed", "fixed_tuned", "actuated", "maxpressure"]
    top = 1.72
    header_h = 0.46
    row_h = 0.78
    x0 = L
    rect(slide, x0, top, CONTENT_W, header_h, NAVY)
    x = x0
    for title, width in columns:
        write(slide, x + 0.12, top, width - 0.16, header_h, [[(title, 12, WHITE, True, BODY)]], anchor="ctr")
        x += width
    for r, scenario in enumerate(scenarios):
        y = top + header_h + r * row_h
        rect(slide, x0, y, CONTENT_W, row_h, WHITE if r % 2 == 0 else SOFT)
        x = x0
        for c, (_title, width) in enumerate(columns):
            if c == 0:
                write(slide, x + 0.14, y, width - 0.2, row_h, [[(_scenario(scenario), 14, INK, True, BODY)]], anchor="ctr")
            else:
                comp = _comp(data, scenario, baselines[c])
                if not comp:
                    write(slide, x + 0.08, y, width - 0.12, row_h, [[("—", 14, MUTED, False, BODY)]], align=PP_ALIGN.CENTER, anchor="ctr")
                else:
                    tone = TEAL if comp["pct_reduction_mean"] > 0 else CORAL
                    write(
                        slide,
                        x + 0.08,
                        y + 0.08,
                        width - 0.12,
                        0.36,
                        [[(_fmt_pct(comp["pct_reduction_mean"]), 16, tone, True, TITLE)]],
                        align=PP_ALIGN.CENTER,
                    )
                    write(
                        slide,
                        x + 0.06,
                        y + 0.4,
                        width - 0.1,
                        0.28,
                        [[(f"{comp['pct_reduction_ci_lo']:.1f} – {comp['pct_reduction_ci_hi']:.1f}", 11, MUTED, False, BODY)]],
                        align=PP_ALIGN.CENTER,
                    )
            x += width
    mp = [_comp(data, s, "maxpressure") for s in scenarios]
    mp = [row for row in mp if row]
    ct = [_comp(data, s, "ours_count") for s in scenarios]
    ct = [row for row in ct if row]
    bits = []
    if mp:
        lo = min(row["pct_reduction_mean"] for row in mp)
        hi = max(row["pct_reduction_mean"] for row in mp)
        above = all(row["pct_reduction_ci_lo"] > 0 for row in mp)
        tail = " Every 95% interval is above zero." if above else " Some intervals still cross zero."
        bits.append(f"Versus max-pressure: {_fmt_pct(lo)} to {_fmt_pct(hi)}.{tail}")
    if ct:
        lo = min(row["pct_reduction_mean"] for row in ct)
        hi = max(row["pct_reduction_mean"] for row in ct)
        bits.append(f"Versus the count-only ablation: {_fmt_pct(lo)} to {_fmt_pct(hi)}.")
    present = set(data["controllers"] or [])
    if "rl_ppo" not in present:
        bits.append("PPO is trained but not scored in this TEST table.")
    y = top + header_h + len(scenarios) * row_h + 0.16
    if bits:
        rect(slide, L, y, CONTENT_W, 0.95, SOFT_AMBER, radius=0.08)
        write(slide, L + 0.24, y + 0.14, CONTENT_W - 0.45, 0.7, [[(" ".join(bits), 14, INK, False, BODY)]])
    notes(
        slide,
        "Read the paired intervals on this slide from the loaded comparison table. "
        "Do not add a verdict that is not in those intervals. RL is a baseline trained with the budget in rl_selection.json.",
    )


def slide_experience(prs, data, page, _total):
    slide = blank(prs)
    focus = _focus(data)
    fixed_n = _mean(data, focus, "fixed", "n_completed_mean")
    ours_n = _mean(data, focus, "XtraFlow", "n_completed_mean")
    matched = fixed_n is not None and ours_n is not None and abs(fixed_n - ours_n) < 0.05
    tuned_one = data["tuned"].get("scenario")
    if tuned_one == focus:
        dek = f"{_scenario(focus)}. This is the scenario the settings were tuned on. Figures are the locked test."
        if matched:
            dek = (
                f"{_scenario(focus)}, the tuning scenario, on the locked test. "
                f"Completed trips match fixed time ({fixed_n:,.0f})."
            )
    else:
        dek = f"{_scenario(focus)} on the locked test, versus legacy fixed time."
        if matched:
            dek = (
                f"{_scenario(focus)} on the locked test. "
                f"Completed trips match fixed time ({fixed_n:,.0f})."
            )
    chrome(slide, "What changes", "Shorter waits, shorter queues, less CO₂", dek, page)
    specs = [
        ("Fuel / vehicle", "fuel_per_vehicle_L_mean", "L", 3),
        ("Mean wait", "mean_waiting_s_mean", "s", 1),
        ("Mean queue", "mean_queue_veh_mean", "veh", 1),
        ("CO₂", "total_CO2_kg_mean", "kg", 0),
    ]
    gap = 0.16
    width = (CONTENT_W - 3 * gap) / 4
    for i, (label, key, unit, digits) in enumerate(specs):
        before = _mean(data, focus, "fixed", key)
        after = _mean(data, focus, "XtraFlow", key)
        x = L + i * (width + gap)
        _card(slide, x, 1.75, width, 2.15)
        write(slide, x + 0.18, 1.88, width - 0.32, 0.3, [[(label.upper(), 12, AMBER, True, BODY)]])
        if before is None or after is None:
            write(slide, x + 0.18, 2.4, width - 0.32, 0.4, [[("—", 18, MUTED, False, BODY)]])
            continue
        spec = f"{{:.{digits}f}}"
        write(slide, x + 0.18, 2.28, width - 0.32, 0.4, [[(spec.format(before) + f" {unit}", 14, MUTED, False, BODY)]])
        write(slide, x + 0.18, 2.68, width - 0.32, 0.55, [[(spec.format(after) + f" {unit}", 22, TEAL, True, TITLE)]])
        write(slide, x + 0.18, 3.3, width - 0.32, 0.3, [[("from fixed time", 12, MUTED, False, BODY)]])
    others = [row["scenario"] for row in data["headlines"] if row["scenario"] != focus]
    write(slide, L, 4.12, CONTENT_W, 0.3, [[("WAITING TIME ON THE OTHER SCENARIOS", 12, AMBER, True, BODY)]])
    y = 4.48
    for scenario in others:
        before = _mean(data, scenario, "fixed", "mean_waiting_s_mean")
        after = _mean(data, scenario, "XtraFlow", "mean_waiting_s_mean")
        _card(slide, L, y, CONTENT_W, 0.62)
        write(slide, L + 0.22, y, 3.2, 0.62, [[(_scenario(scenario), 15, INK, True, BODY)]], anchor="ctr")
        if before is not None and after is not None and before:
            cut = (before - after) / before * 100
            write(
                slide,
                L + 3.4,
                y,
                5.4,
                0.62,
                [[(f"{before:.1f} s   to   {after:.1f} s", 15, INK, False, BODY)]],
                anchor="ctr",
            )
            write(
                slide,
                L + 8.8,
                y,
                3.1,
                0.62,
                [[(f"{cut:.0f}% shorter", 15, TEAL, True, BODY)]],
                align=PP_ALIGN.RIGHT,
                anchor="ctr",
            )
        y += 0.72
    notes(
        slide,
        "Levels are means from results/tables/summary_mean_std.csv. The fuel percentage on the previous slide "
        "is the mean of paired percent cuts, which is not identical to the percent gap between these two means. "
        "Waiting cuts are percent gaps between means. Say that if someone mixes the two.",
    )


def slide_robust(prs, data, page, _total):
    slide = blank(prs)
    curve = [row for row in data["robust"] if row["controller"] == "XtraFlow" and row["kind"] == "XtraFlow"]
    curve.sort(key=lambda row: row["miss"])
    refs = {
        row["controller"]: row
        for row in data["robust"]
        if row["kind"] == "reference" and row["miss"] == 0
    }
    empirical = next(
        (row for row in data["robust"] if row["controller"] == "XtraFlow" and row["kind"] == "empirical_noise"),
        None,
    )
    n = curve[0]["n"] if curve else None
    dek = "Peak, one-sided. Vehicles are dropped from the detector at random."
    if n:
        dek = f"Peak, one-sided. {n} seeds. Vehicles are dropped from the detector at random."
    title = "Detector-miss results are not loaded"
    if curve:
        high = max(curve, key=lambda row: row["miss"])
        ref_means = [row["mean"] for row in refs.values()]
        if ref_means and high["mean"] < min(ref_means):
            title = "At the highest miss rate, mean fuel is under the reference lines"
        elif ref_means and high["mean"] > max(ref_means):
            title = "At the highest miss rate, mean fuel is above the reference lines"
        elif ref_means:
            title = "At the highest miss rate, mean fuel sits among the reference lines"
        else:
            title = "Detector-miss curve"
    chrome(slide, "If the camera misses", title, dek, page)
    if not curve:
        write(slide, L, 2.2, CONTENT_W, 0.4, [[("Robustness results are not loaded.", 16, MUTED, False, BODY)]])
        return
    gap = 0.16
    width = (CONTENT_W - (len(curve) - 1) * gap) / len(curve)
    for i, row in enumerate(curve):
        x = L + i * (width + gap)
        _card(slide, x, 1.75, width, 2.45)
        write(slide, x + 0.18, 1.92, width - 0.32, 0.3, [[(f"{row['miss']:.0%} missed", 13, AMBER, True, BODY)]])
        write(slide, x + 0.18, 2.35, width - 0.32, 0.6, [[(f"{row['mean']:.3f}", 28, TEAL, True, TITLE)]])
        write(slide, x + 0.18, 3.05, width - 0.32, 0.3, [[("litres / vehicle", 13, MUTED, False, BODY)]])
        write(slide, x + 0.18, 3.5, width - 0.32, 0.35, [[(f"± {row['std']:.3f}   ·   n={row['n']}", 12, MUTED, False, BODY)]])
    rect(slide, L, 4.45, CONTENT_W, 2.35, WHITE, line=LINE, radius=0.08)
    write(slide, L + 0.3, 4.62, 8, 0.3, [[("SAME SEEDS, NO MISSES", 12, AMBER, True, BODY)]])
    rx = L + 0.3
    for key, label in (("XtraFlow", "XtraFlow"), ("actuated", "Actuated"), ("fixed", "Fixed time")):
        row = refs.get(key)
        if key == "XtraFlow":
            row = next((item for item in curve if item["miss"] == 0), row)
        if not row:
            continue
        write(slide, rx, 5.05, 3.4, 0.45, [[(f"{row['mean']:.3f} L", 22, TEAL if key == "XtraFlow" else INK, True, TITLE)]])
        write(slide, rx, 5.55, 3.4, 0.3, [[(label, 13, MUTED, False, BODY)]])
        rx += 3.6
    extra = ""
    if empirical:
        source = empirical.get("source") or "see noise_model.json"
        extra = f" Noise-model point ({source}) is {empirical['mean']:.3f} L."
    under = bool(curve) and all(
        row["mean"] < min(ref["mean"] for ref in refs.values())
        for row in curve
    ) if refs else False
    if refs and curve and under:
        sentence = "Every miss level on this slide has lower mean fuel than every reference line."
    elif refs and curve:
        sentence = "Not every miss level is under every reference line."
    else:
        sentence = "Reference lines are not loaded."
    write(
        slide,
        L + 0.3,
        6.05,
        CONTENT_W - 0.6,
        0.5,
        [[(sentence + extra, 14, INK, False, BODY)]],
    )
    notes(
        slide,
        "Means are grouped from results/robustness.json. Do not describe the curve as flat unless the loaded means say so. "
        "The noise-model source field is empirical only when labels exist.",
    )


def slide_grid(prs, data, page, _total):
    slide = blank(prs)
    means = (data["grid"].get("summary_means") or {}).get("fuel_per_vehicle_L") or {}
    waits = (data["grid"].get("summary_means") or {}).get("mean_waiting_s") or {}
    rows = data["grid"].get("rows") or []
    n = len([row for row in rows if row.get("controller") == "XtraFlow"])
    coord = data["grid"].get("coordination_reduces_fuel")
    same = (
        "XtraFlow" in means
        and "XtraFlow_coord" in means
        and abs(means["XtraFlow"] - means["XtraFlow_coord"]) < 1e-9
    )
    note = data["grid"].get("note") or ""
    scored = bool(means) or bool(rows)
    if not scored:
        chrome(
            slide,
            "Where it does not transfer",
            "The 2×2 grid was not scored in this publish",
            "A corridor result is only quoted from results/grid_results.json after a successful sweep.",
            page,
        )
        rect(slide, L, 1.9, CONTENT_W, 3.6, NAVY, radius=0.08)
        write(slide, L + 0.4, 2.25, CONTENT_W - 0.8, 0.4, [[("NO NUMERIC GRID CLAIM", 14, AMBER, True, BODY)]])
        write(
            slide,
            L + 0.4,
            2.85,
            CONTENT_W - 0.8,
            1.8,
            [[(
                note
                or "The grid network and the single-intersection signal file do not match. SUMO aborts before a fair comparison.",
                18,
                WHITE,
                False,
                BODY,
            )]],
        )
        write(
            slide,
            L + 0.4,
            4.85,
            CONTENT_W - 0.8,
            0.4,
            [[("Do not scale the single-junction result to a city.", 15, MIST, False, BODY)]],
        )
        notes(
            slide,
            "Read results/grid_results.json. If rows and summary_means are empty, say the grid was not scored. "
            "Label: simulation-based estimate; assumed traffic mix.",
        )
        return
    dek = "A 2×2 network. The controller was tuned for a single junction."
    if n:
        dek = f"A 2×2 network, {n} seeds. The controller was tuned for a single junction."
    if "XtraFlow" in means and "fixed" in means and means["XtraFlow"] > means["fixed"]:
        grid_title = "On this grid, XtraFlow mean fuel is higher than fixed time"
    elif "XtraFlow" in means and "fixed" in means and means["XtraFlow"] < means["fixed"]:
        grid_title = "On this grid, XtraFlow mean fuel is lower than fixed time"
    elif means:
        grid_title = "Grid means are loaded; compare the cards"
    else:
        grid_title = "Grid results are not loaded"
    chrome(slide, "Where it does not transfer", grid_title, dek, page)
    order = [("actuated", "Actuated", "Lowest fuel here"), ("fixed", "Fixed time", "The simple baseline"), ("XtraFlow", "XtraFlow", "Higher fuel")]
    gap = 0.18
    width = (CONTENT_W - 2 * gap) / 3
    for i, (key, label, caption) in enumerate(order):
        x = L + i * (width + gap)
        tone = CORAL if key == "XtraFlow" else TEAL
        fill = SOFT_CORAL if key == "XtraFlow" else WHITE
        _card(slide, x, 1.75, width, 3.35, fill=fill)
        write(slide, x + 0.24, 1.95, width - 0.45, 0.3, [[(label.upper(), 13, tone, True, BODY)]])
        fuel = means.get(key)
        wait = waits.get(key)
        write(
            slide,
            x + 0.24,
            2.45,
            width - 0.45,
            0.7,
            [[(f"{fuel:.3f}" if fuel is not None else "—", 32, tone, True, TITLE)]],
        )
        write(slide, x + 0.24, 3.2, width - 0.45, 0.3, [[("litres / vehicle", 13, MUTED, False, BODY)]])
        if wait is not None:
            write(slide, x + 0.24, 3.65, width - 0.45, 0.35, [[(f"{wait:.1f} s mean wait", 15, INK, False, BODY)]])
        write(slide, x + 0.24, 4.15, width - 0.45, 0.4, [[(caption, 14, MUTED, False, BODY)]])
    if coord is False and same:
        sentence = "A neighbor-pressure coordination term matched independent control exactly. It did not reduce fuel or waiting."
    elif coord is False:
        sentence = "The coordination variant did not reduce fuel."
    else:
        sentence = "Read results/grid_results.json before describing coordination."
    rect(slide, L, 5.3, CONTENT_W, 1.5, NAVY, radius=0.08)
    write(slide, L + 0.32, 5.5, CONTENT_W - 0.6, 0.35, [[("DO NOT SCALE THIS TO A CITY", 12, AMBER, True, BODY)]])
    write(slide, L + 0.32, 5.9, CONTENT_W - 0.6, 0.65, [[(sentence + " A corridor needs its own design.", 16, WHITE, False, BODY)]])
    notes(
        slide,
        "Read results/grid_results.json. The title is computed from those means. "
        "Do not describe coordination unless coordination_reduces_fuel is in that file. "
        "Label: simulation-based estimate; assumed traffic mix.",
    )


def _plain_verdict(value: str) -> str:
    mapping = {
        "no_increase": "No increase",
        "unchanged_direction": "Same direction",
        "decrease": "Fewer conflicts",
        "increase": "More conflicts",
        "ci_overlap": "Intervals overlap",
        "same_sign": "Same direction",
    }
    return mapping.get(value, (value or "Not loaded").replace("_", " "))


# All analyze/study PNGs under results/figures/ (filename, short title, one-line caption).
FIGURE_CATALOG = [
    ("i_grouped_bars.png", "Metrics by controller", "Fuel, CO₂, wait, and queue (mean±std)"),
    ("ii_pct_reduction.png", "Fuel cut vs baselines", "Paired % reduction versus tuned baselines"),
    ("iii_dynamic_queue_ts.png", "Dynamic queue series", "Queue time series on the dynamic scenario"),
    ("iv_green_allocation.png", "Green allocation", "XtraFlow green share versus fixed timing"),
    ("v_fuel_by_type.png", "Fuel by vehicle type", "Mean fuel split across the assumed mix"),
    ("vi_gain_vs_saturation.png", "Gain vs saturation", "Fuel gain vs demand multiplier proxy (sensitivity data; re-run pending)"),
    ("vii_robustness.png", "Detection-miss robustness", "Fuel under rising detector miss rates"),
    ("viii_rl_training.png", "RL training", "PPO training curve (unscored on TEST)"),
    ("ix_emission_xcheck.png", "Emission cross-check", "Primary vs alternate emission classes"),
    ("x_grid_results.png", "2×2 grid", "Grid sweep (unscored TLS caveat applies)"),
    ("xi_extrapolation.png", "Extrapolation", "Placeholder scaled fuel / INR range"),
    ("xtraflow_choice.png", "Phase choice sketch", "How fuel-weighted pressure picks a phase"),
]


def slide_figure_pair(prs, data, page, _total, pair):
    """One slide with one or two figures from FIGURE_CATALOG."""
    slide = blank(prs)
    if len(pair) == 1:
        fname, title, caption = pair[0]
        chrome(slide, "Figures", title, caption, page)
        path = ROOT / "results" / "figures" / fname
        _card(slide, L, 1.7, CONTENT_W, 4.85)
        if not picture(slide, path, L + 0.35, 1.95, CONTENT_W - 0.7, 4.4):
            write(slide, L + 0.5, 3.5, 10, 0.4, [[(f"{fname} not found", 14, MUTED, False, BODY)]])
    else:
        (f1, t1, c1), (f2, t2, c2) = pair
        chrome(
            slide,
            "Figures",
            f"{t1}  ·  {t2}",
            "Regenerated from results/ by analyze.py and the study scripts.",
            page,
        )
        half = (CONTENT_W - 0.28) / 2
        for i, (fname, title, caption) in enumerate(pair):
            x = L + i * (half + 0.28)
            _card(slide, x, 1.7, half, 4.85)
            write(slide, x + 0.2, 1.85, half - 0.4, 0.28, [[(title.upper(), 11, AMBER, True, BODY)]])
            path = ROOT / "results" / "figures" / fname
            if not picture(slide, path, x + 0.2, 2.2, half - 0.4, 3.55):
                write(slide, x + 0.25, 3.5, half - 0.5, 0.4, [[(f"{fname} not found", 13, MUTED, False, BODY)]])
            write(slide, x + 0.2, 5.85, half - 0.4, 0.5, [[(caption, 11, MUTED, False, BODY)]])
    notes(
        slide,
        "PNGs live in results/figures/. Do not describe a trend that is not visible in the figure. "
        "RL / grid / extrapolation slides inherit the same caveats as STATE.md.",
    )


def slide_figures(prs, data, page, _total):
    """Legacy single entry point — unused when main() expands FIGURE_CATALOG."""
    slide_figure_pair(prs, data, page, _total, FIGURE_CATALOG[:2])


def slide_demo(prs, data, page, _total):
    slide = blank(prs)
    chrome(
        slide,
        "Live clip 1 · control",
        "Fixed timing on the left. XtraFlow on the right.",
        "One seed, same arrivals. Qualitative demo — the fuel numbers are on the previous slides.",
        page,
    )
    demo = ROOT / "results" / "demo" / "demo.mp4"
    gif = ROOT / "results" / "demo" / "demo.gif"
    path = demo if demo.exists() else gif
    rect(slide, L, 1.7, 7.7, 4.85, NAVY, radius=0.08)
    write(slide, L + 0.35, 2.15, 7.0, 0.3, [[("PLAY NOW", 13, AMBER, True, BODY)]])
    if path.exists():
        rel = path.relative_to(ROOT).as_posix()
        write(slide, L + 0.35, 2.65, 7.0, 0.55, [[(rel, 22, WHITE, True, TITLE)]])
        write(
            slide,
            L + 0.35,
            3.45,
            7.0,
            1.4,
            [[(
                "Open the file in a video player beside the deck. "
                "Same demand seed: legacy fixed versus XtraFlow. "
                "Do not treat the clip as a percentage claim.",
                16,
                MIST,
                False,
                BODY,
            )]],
        )
        write(
            slide,
            L + 0.35,
            5.3,
            7.0,
            0.7,
            [[("Next: YOLO story clip — GO/WAIT on four cameras.", 15, RGBColor(0xF0, 0xC2, 0x6A), False, BODY)]],
        )
    else:
        write(
            slide,
            L + 0.35,
            3.2,
            7.0,
            0.5,
            [[("Run make demo to fill results/demo/demo.mp4.", 18, MIST, False, BODY)]],
        )
    _card(slide, L + 8.0, 1.7, 4.28, 4.85)
    write(slide, L + 8.22, 1.95, 3.9, 0.35, [[("WHAT TO SAY", 12, AMBER, True, BODY)]])
    talking = [
        "Same cars on both sides.",
        "Left is fixed green.",
        "Right is fuel-weighted pressure.",
        "Watch which approach gets green when the queue is heavy.",
    ]
    y = 2.5
    for i, line in enumerate(talking, start=1):
        write(slide, L + 8.22, y, 3.9, 0.7, [[(f"{i}.  {line}", 15, INK, False, BODY)]])
        y += 0.85
    notes(
        slide,
        "Open results/demo/demo.mp4 (or demo.gif) while presenting. "
        "Then advance to the YOLO story slide and play yolo_story.mp4. "
        "Say that the SUMO demo is qualitative; the headline numbers are on the previous slides.",
    )


def _yolo_still(approach: str, kind: str | None = None) -> Path | None:
    dirs: list[Path] = []
    if kind == "hwy":
        dirs.append(ROOT / "results" / "demo" / "yolo_stills_hwy")
    dirs.append(ROOT / "results" / "demo" / "yolo_stills")
    dirs.append(ROOT / "results" / "demo" / "yolo_stills_hwy")
    for still_dir in dirs:
        if not still_dir.exists():
            continue
        matches = sorted(still_dir.glob(f"cam_{approach}_*.jpg"))
        if matches:
            return matches[0]
    return None


def slide_yolo(prs, data, page, _total):
    """Main perception live clip: GO/WAIT story mosaic (embedded, click to play)."""
    slide = blank(prs)
    yolo = data.get("yolo") or {}
    label = yolo.get("label") or "Input feasibility — not a fuel-saving result"
    chrome(
        slide,
        "Live clip 2 · perception",
        "YOLO story video — click to play in PowerPoint",
        label,
        page,
    )
    outputs = yolo.get("outputs") or {}
    story_rel = outputs.get("story") or "results/demo/yolo/yolo_story.mp4"
    story_path = ROOT / story_rel
    still = ROOT / "results" / "demo" / "yolo" / "yolo_story_still.jpg"
    club = (yolo.get("clubbed") or {}).get("decision_summary") or {}

    rect(slide, L, 1.68, 7.55, 4.0, NAVY, radius=0.08)
    embedded = movie(slide, story_path, still if still.exists() else None, L + 0.18, 1.86, 7.2, 3.65)
    if not embedded:
        if still.exists() and picture(slide, still, L + 0.18, 1.86, 7.2, 3.65):
            write(slide, L + 0.35, 5.2, 7.0, 0.3, [[("Video missing — still only. Run make yolo_demo.", 12, MIST, False, BODY)]])
        else:
            write(slide, L + 0.35, 3.2, 7.0, 0.5, [[("Run make yolo_demo for yolo_story.mp4", 16, MIST, False, BODY)]])

    _card(slide, L + 7.8, 1.68, 4.48, 4.0)
    write(slide, L + 8.02, 1.88, 4.1, 0.28, [[("CLICK VIDEO TO PLAY", 12, AMBER, True, BODY)]])
    exists = story_path.exists()
    write(
        slide,
        L + 8.02,
        2.2,
        4.1,
        0.55,
        [[(
            "Embedded on this slide." if exists else "Video file missing.",
            15,
            TEAL if exists else CORAL,
            True,
            TITLE,
        )]],
    )
    write(
        slide,
        L + 8.02,
        2.75,
        4.1,
        0.35,
        [[(story_rel, 11, MUTED, False, BODY)]],
    )
    talking = [
        "Green = that axis moves. Red = wait.",
        "Phase picks the highest fuel-weighted pressure.",
        "Four independent corridor cams — not one junction.",
        "Not the locked SUMO fuel headline.",
    ]
    y = 3.2
    for i, line in enumerate(talking, start=1):
        write(slide, L + 8.02, y, 4.1, 0.5, [[(f"{i}.  {line}", 14, INK, False, BODY)]])
        y += 0.52

    proxy = club.get("pct_idle_cut_vs_fixed")
    peak = (yolo.get("clubbed") or {}).get("peak_vehicles")
    mean_v = (yolo.get("clubbed") or {}).get("mean_vehicles")
    rect(slide, L, 5.85, CONTENT_W, 0.95, NAVY, radius=0.08)
    bits = []
    if mean_v is not None:
        bits.append(f"mean ~{mean_v:.0f} veh across cams")
    if peak is not None:
        bits.append(f"peak {peak:.0f}")
    if proxy is not None:
        bits.append(f"idle-fuel proxy vs fixed {proxy:.0f}% on this clip only")
    bit_line = "  ·  ".join(bits) if bits else "See yolo_demo_summary.json"
    write(slide, L + 0.32, 6.0, CONTENT_W - 0.6, 0.28, [[("CLIP STATS (PROXY — NOT TEST)", 12, AMBER, True, BODY)]])
    write(slide, L + 0.32, 6.35, CONTENT_W - 0.6, 0.3, [[(bit_line, 14, WHITE, False, BODY)]])
    notes(
        slide,
        "Click the embedded yolo_story.mp4 on this slide to play in PowerPoint (Slideshow or Normal view). "
        "Say: GO/WAIT is illustrative from detector counts; published fuel is oracle SUMO TEST. "
        "Next slide has mosaic + per-cam overlays if you want to linger.",
    )


def slide_yolo_overlays(prs, data, page, _total):
    """Backup play list: mosaic + N/E/S/W overlays."""
    slide = blank(prs)
    yolo = data.get("yolo") or {}
    label = yolo.get("label") or "Input feasibility — not a fuel-saving result"
    chrome(
        slide,
        "Live clip 2b · optional",
        "Mosaic and per-camera overlays",
        "Play if someone asks how each approach looks. Same caveat: input feasibility only.",
        page,
    )
    cams = yolo.get("cameras") or [
        {"approach": "N", "title": "North"},
        {"approach": "E", "title": "East"},
        {"approach": "S", "title": "South"},
        {"approach": "W", "title": "West"},
    ]
    cell_w, cell_h = 2.85, 1.7
    gap_x, gap_y = 0.12, 0.12
    origin_x, origin_y = L, 1.68
    order = ["N", "E", "S", "W"]
    by_approach = {c.get("approach"): c for c in cams}
    for i, approach in enumerate(order):
        col, row = i % 2, i // 2
        x = origin_x + col * (cell_w + gap_x)
        y = origin_y + row * (cell_h + gap_y)
        cam = by_approach.get(approach) or {"approach": approach, "title": approach}
        title = cam.get("title") or approach
        short = title.split("/")[0].strip()
        still = _yolo_still(approach, cam.get("kind"))
        rect(slide, x, y, cell_w, cell_h, NAVY, radius=0.06)
        if still and picture(slide, still, x + 0.08, y + 0.28, cell_w - 0.16, cell_h - 0.4):
            write(slide, x + 0.1, y + 0.04, cell_w - 0.2, 0.24, [[(f"{approach}  ·  {short}", 11, WHITE, True, BODY)]])
        else:
            write(slide, x + 0.15, y + 0.55, cell_w - 0.3, 0.5, [[(f"{approach}  ·  {short}", 14, MIST, True, BODY)]])
            write(slide, x + 0.15, y + 1.05, cell_w - 0.3, 0.35, [[("still missing — run make yolo_demo", 12, MIST, False, BODY)]])

    outputs = yolo.get("outputs") or {}
    mosaic = outputs.get("mosaic") or "results/demo/yolo/yolo_mosaic.mp4"
    primary = outputs.get("primary_overlay") or "results/demo/yolo/overlay_N.mp4"
    overlays = outputs.get("overlays") or [f"results/demo/yolo/overlay_{a}.mp4" for a in order]
    panel_x = L + 2 * (cell_w + gap_x) + 0.2
    panel_w = CONTENT_W - (panel_x - L)
    _card(slide, panel_x, 1.68, panel_w, 3.52)
    write(slide, panel_x + 0.22, 1.85, panel_w - 0.4, 0.28, [[("PLAY THESE", 12, AMBER, True, BODY)]])
    play_rows = [("Mosaic (2×2)", mosaic), ("Primary overlay", primary)]
    for approach, path in zip(order, overlays):
        play_rows.append((f"Cam {approach}", path))
    y = 2.25
    for name, path in play_rows:
        exists = (ROOT / path).exists() if path else False
        tone = TEAL if exists else CORAL
        mark = "●" if exists else "○"
        write(slide, panel_x + 0.22, y, panel_w - 0.4, 0.22, [[(f"{mark}  {name}", 13, tone, True, BODY)]])
        write(slide, panel_x + 0.42, y + 0.22, panel_w - 0.6, 0.22, [[(path, 11, MUTED, False, BODY)]])
        y += 0.48

    note = yolo.get("note") or (
        "Four independent corridor cameras mapped to N/E/S/W. Not synchronized approaches of one intersection."
    )
    source = yolo.get("source") or ""
    attr = yolo.get("attribution") or ""
    rect(slide, L, 5.35, CONTENT_W, 1.45, NAVY, radius=0.08)
    write(slide, L + 0.32, 5.5, CONTENT_W - 0.6, 0.28, [[("ATTRIBUTION", 12, AMBER, True, BODY)]])
    write(slide, L + 0.32, 5.85, CONTENT_W - 0.6, 0.4, [[(note, 14, WHITE, False, BODY)]])
    foot = "  ·  ".join(p for p in (source, attr) if p)
    write(slide, L + 0.32, 6.4, CONTENT_W - 0.6, 0.25, [[(foot or label, 12, MIST, False, BODY)]])
    notes(
        slide,
        "Optional. Prefer yolo_story on the previous slide for the talk. "
        "Use mosaic or one overlay only if asked. Do not claim a synchronized field site.",
    )


def _safety_by_baseline(safety: dict) -> dict:
    return {c.get("baseline"): c.get("verdict", "") for c in (safety.get("comparisons") or [])}


def _safety_headline(safety: dict) -> str:
    """Short headline; detail by baseline lives in the Safety card."""
    v = _safety_by_baseline(safety)
    if not v:
        return "Safety: see results/safety.json."
    return "Safety: mixed."


def slide_checks(prs, data, page, _total):
    slide = blank(prs)
    chrome(
        slide,
        "Beside the headline",
        _safety_headline(data["safety"]) + f" Emissions: {_plain_verdict(data['xcheck'].get('verdict', ''))}.",
        "Both lines are the verdict fields in the loaded JSON, not a fixed sentence.",
        page,
    )
    safety = data["safety"]
    xcheck = data["xcheck"]
    perc = data["perc"]
    noise = perc.get("noise_model") or {}
    miss = noise.get("detect_miss_rate")
    assumed = "ASSUMED" in str(perc.get("flag", ""))
    yolo = data.get("yolo") or {}
    cards = []
    delta = safety.get("ours_minus_fixed_mean_conflicts")
    safety_body = "Time-to-collision conflicts from the simulator."
    if delta is not None:
        safety_body += f" XtraFlow minus fixed time averages {delta:.0f}."
    _v = _safety_by_baseline(safety)
    safety_body += (
        " Verdict vs fixed only; vs max-pressure/queue-pressure the CI includes 0"
        + ("; vs count-only (ours_count) XtraFlow shows MORE conflicts" if _v.get("ours_count") == "increase" else "")
        + "."
    )
    safety_body += " A model count, not a crash record."
    cards.append((AMBER, "Mixed", "Safety", safety_body))
    primary = xcheck.get("pct_reduction_primary")
    alternate = xcheck.get("pct_reduction_alternate")
    emis = "Peak scenario, cross-check seeds."
    if primary is not None and alternate is not None:
        emis = (
            f"Primary model {_fmt_pct(primary)}. Alternate model {_fmt_pct(alternate)}. "
            "The reduction shrinks. It does not flip."
        )
    cards.append((TEAL, _plain_verdict(xcheck.get("verdict", "")), "Emissions model", emis))
    if yolo:
        perc_title = "YOLO demo ready"
        perc_body = (
            "Camera overlays exist under results/demo/yolo/. "
            "They are input feasibility only. Locked TEST fuel still uses the oracle detector."
        )
        if assumed:
            perc_body += " Robustness miss rates remain assumed until labeled video replaces the noise model."
    else:
        perc_title = "Assumed" if assumed else "From video"
        perc_body = "No field video in this run." if assumed else "Noise model derived from supplied video."
        if isinstance(miss, (int, float)):
            perc_body += f" Smoke-test miss rate {miss:.0%}."
        if noise.get("note"):
            perc_body += " Treat detector error as assumed until labels exist."
    cards.append((AMBER, perc_title, "Perception", perc_body))
    gap = 0.18
    width = (CONTENT_W - 2 * gap) / 3
    for i, (tone, big, label, body) in enumerate(cards):
        x = L + i * (width + gap)
        _card(slide, x, 1.75, width, 4.15)
        rect(slide, x + 0.26, 2.0, 0.55, 0.045, tone)
        write(slide, x + 0.24, 2.2, width - 0.45, 0.3, [[(label.upper(), 12, tone, True, BODY)]])
        write(slide, x + 0.24, 2.55, width - 0.45, 0.85, [[(big, 28, INK, True, TITLE)]])
        write(slide, x + 0.24, 3.55, width - 0.48, 2.05, [[(body, 15, INK, False, BODY)]])
    notes(
        slide,
        "Safety verdict comes from results/safety.json. Emission cross-check is HBEFA3 versus PHEMlight on a smaller seed set "
        "than the main test; quote both percentages. YOLO demo does not replace the oracle TEST claim.",
    )


def _millions(value: float) -> str:
    return f"{value / 1e6:.1f} million litres"


def _tonnes(value: float) -> str:
    return f"{value:,.0f} tonnes"


def slide_scale(prs, data, page, _total):
    slide = blank(prs)
    extra = data["extra"]
    chrome(
        slide,
        "Scaling",
        "Per vehicle is measured. Per year is a range.",
        "Illustrative only: built from a 0.3–1.9% gap whose CIs include 0 on 2 of 4 scenarios (n=5). Not a forecast.",
        page,
    )
    saving = extra.get("measured_saving_L_per_veh") or {}
    annual = extra.get("annual_litres") or {}
    co2 = extra.get("annual_tonnes_CO2") or {}
    basis_key = extra.get("scenario_basis")
    if basis_key:
        measure_label = f"MEASURED ON {_scenario(basis_key).upper()}"
    else:
        measure_label = "MEASURED VS BEST BASELINE (SCENARIO-WEIGHTED)"
    _card(slide, L, 1.72, 7.35, 2.15)
    if saving:
        write(slide, L + 0.28, 1.88, 6.8, 0.28, [[(measure_label, 12, AMBER, True, BODY)]])
        write(
            slide,
            L + 0.28,
            2.22,
            6.8,
            0.65,
            [[
                (f"{saving['mean']:.3f}", 32, TEAL, True, TITLE),
                ("  L saved / vehicle", 16, INK, False, BODY),
            ]],
        )
        write(
            slide,
            L + 0.28,
            3.05,
            6.8,
            0.5,
            [[(
                f"5th – 95th percentile of that mean: {saving['p05']:.3f} – {saving['p95']:.3f} L",
                14,
                MUTED,
                False,
                BODY,
            )]],
        )
    if annual:
        p05, med, p95 = annual["p05"], annual["median"], annual["p95"]
        write(slide, L, 4.05, 7.3, 0.28, [[("IF THAT SAVING IS MULTIPLIED OUT", 12, AMBER, True, BODY)]])
        span = p95 - p05
        rect(slide, L, 4.42, 7.35, 0.14, RGBColor(0xC5, 0xDD, 0xD4))
        if span > 0:
            tick = L + 7.35 * ((med - p05) / span)
            rect(slide, tick - 0.03, 4.28, 0.06, 0.42, TEAL)
        write(slide, L, 4.78, 3.2, 0.28, [[(_millions(p05), 12, MUTED, False, BODY)]])
        write(slide, L + 4.15, 4.78, 3.2, 0.28, [[(_millions(p95), 12, MUTED, False, BODY)]], align=PP_ALIGN.RIGHT)
        write(slide, L, 5.2, 7.3, 0.48, [[(_millions(med), 22, INK, True, TITLE)]])
        write(slide, L, 5.68, 7.3, 0.28, [[("median of the scaled draws", 13, MUTED, False, BODY)]])
        if co2:
            write(
                slide,
                L,
                6.05,
                7.3,
                0.55,
                [[(
                    f"CO₂ median {_tonnes(co2['median'])}   ·   {_tonnes(co2['p05'])} – {_tonnes(co2['p95'])}",
                    13,
                    MUTED,
                    False,
                    BODY,
                )]],
            )
    ex = data["cfg"].get("extrapolation") or {}
    _card(slide, 8.1, 1.72, 4.73, 4.95)
    write(slide, 8.34, 1.9, 4.3, 0.7, [[("Why the bar is wide", 18, INK, True, TITLE)]])
    ranges = [
        ("Junctions", ex.get("n_intersections")),
        ("Vehicles / peak hour", ex.get("vehicles_per_peak_hour")),
        ("Peak hours / day", ex.get("peak_hours_per_day")),
        ("Days / year", ex.get("days_per_year")),
    ]
    y = 2.7
    for label, block in ranges:
        if not block:
            continue
        write(slide, 8.34, y, 4.2, 0.24, [[(label, 12, MUTED, False, BODY)]])
        write(
            slide,
            8.34,
            y + 0.22,
            4.2,
            0.32,
            [[(f"{block['low']:,.0f}  –  {block['high']:,.0f}", 16, INK, True, TITLE)]],
        )
        y += 0.72
    write(
        slide,
        8.34,
        5.85,
        4.25,
        0.6,
        [[("Rupee value uses a placeholder price, so it is not shown.", 13, MUTED, False, BODY)]],
    )
    notes(
        slide,
        "Lead with the per-vehicle litres. The annual figure multiplies that saving by the ranges on the right, "
        "Monte Carlo from config.yaml. The price in fuel.price_inr_per_L is a placeholder. "
        "Quote the extrapolation label: simulation-based estimate, not a deployment result.",
    )


def slide_close(prs, data, page, _total):
    slide = blank(prs, dark=True)
    _, seed_bit, n_ctrl = _scope_bits(data)
    yolo = data.get("yolo") or {}
    write(slide, 0.78, 0.85, 8, 0.28, [[("BEFORE A STRONGER CLAIM", 13, AMBER, True, BODY)]])
    write(slide, 0.75, 1.2, 11, 0.8, [[("XtraFlow", 44, WHITE, True, TITLE)]])
    write(
        slide,
        0.78,
        2.1,
        10.5,
        0.7,
        [[("Smarter signals should burn less fuel.", 24, RGBColor(0xF0, 0xC2, 0x6A), False, TITLE)]],
    )
    film_body = (
        "YOLO overlays are a feasibility demo. Replace assumed detector-error with labeled site video."
        if yolo
        else "A real video replaces the assumed detector-error model."
    )
    steps = [
        ("01", "Count the junction", "Replace the assumed flows with observed demand before quoting a site."),
        ("02", "Film the queue", film_body),
        ("03", "Score the corridor", "Quote a grid result only from results/grid_results.json after a successful sweep."),
    ]
    gap = 0.16
    width = (CONTENT_W - 2 * gap) / 3
    for i, (num, title, body) in enumerate(steps):
        x = L + i * (width + gap)
        rect(slide, x, 3.15, width, 2.2, NAVY_2, radius=0.08)
        write(slide, x + 0.22, 3.32, width - 0.4, 0.3, [[(num, 14, AMBER, True, TITLE)]])
        write(slide, x + 0.22, 3.7, width - 0.42, 0.55, [[(title, 18, WHITE, True, TITLE)]])
        write(slide, x + 0.22, 4.35, width - 0.42, 0.8, [[(body, 14, MIST, False, BODY)]])
    scope = f"This deck: {n_ctrl} controllers · {seed_bit} · oracle detector unless camera mode is on."
    if yolo:
        scope += " · YOLO demo included"
    write(
        slide,
        0.78,
        5.55,
        11,
        0.35,
        [[(scope, 13, MIST, False, BODY)]],
    )
    write(slide, 0.78, 5.95, 10, 0.3, [[(data["url"] or "", 14, MIST, False, BODY)]])
    write(slide, 0.78, 6.3, 11, 0.4, [[(data["label"], 14, RGBColor(0x8A, 0x9B, 0xA8), False, BODY)]])
    footer(slide, page, dark=True)
    notes(
        slide,
        "Close on the three conditions. Demo order was: SUMO side-by-side, YOLO story, optional overlays. "
        "Do not end on the annual litre range. The headline comparison is in results/headlines.json.",
    )


def main():
    data = load()
    prs = Presentation()
    prs.slide_width = Inches(PAGE_W)
    prs.slide_height = Inches(PAGE_H)
    prs.core_properties.title = "XtraFlow"
    prs.core_properties.subject = "Fuel-weighted traffic signals"
    prs.core_properties.category = data["label"]

    figure_pairs = [FIGURE_CATALOG[i:i + 2] for i in range(0, len(FIGURE_CATALOG), 2)]

    builders = [
        slide_title,
        slide_agenda,
        slide_problem,
        slide_weights,
        slide_cycle,
        slide_design,
        slide_headlines,
        slide_baselines,
        slide_experience,
    ]
    for pair in figure_pairs:
        builders.append(lambda prs, data, page, total, pair=pair: slide_figure_pair(prs, data, page, total, pair))
    builders.extend([
        slide_demo,
        slide_yolo,
        slide_yolo_overlays,
        slide_robust,
        slide_grid,
        slide_checks,
        slide_scale,
        slide_close,
    ])
    total = len(builders)
    for page, builder in enumerate(builders, start=1):
        builder(prs, data, page, total)
    out = ROOT / "results" / "deck.pptx"
    prs.save(str(out))
    print(f"Wrote {out} ({total} slides, {len(FIGURE_CATALOG)} figures)")


if __name__ == "__main__":
    main()
