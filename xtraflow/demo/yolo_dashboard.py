"""Streamlit YOLO demo — videos, phase decisions over time, idle-fuel proxy."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from demo.yolo_decisions import enrich_summary

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "results" / "demo" / "yolo" / "yolo_demo_summary.json"


def load_summary() -> dict | None:
    if not SUMMARY.exists():
        return None
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    # Enrich on the fly if an older summary lacks decision timelines.
    if not (summary.get("clubbed") or {}).get("decisions"):
        summary = enrich_summary(summary)
        SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def resolve(rel: str | None) -> Path | None:
    if not rel:
        return None
    p = ROOT / rel
    return p if p.exists() and p.stat().st_size > 0 else None


def cam_table(summary: dict) -> pd.DataFrame:
    rows = []
    for approach, stats in (summary.get("per_camera") or {}).items():
        ds = stats.get("decision_summary") or {}
        rows.append(
            {
                "approach": approach,
                "title": stats.get("title", ""),
                "peak_veh": stats.get("peak_vehicles"),
                "mean_veh": stats.get("mean_vehicles"),
                "dominant_phase": ds.get("dominant_phase"),
                "idle_cut_vs_fixed_%": ds.get("pct_idle_cut_vs_fixed"),
                "idle_saved_L_proxy": ds.get("idle_fuel_saved_L_proxy"),
            }
        )
    return pd.DataFrame(rows)


def series_df(stats: dict) -> pd.DataFrame:
    series = stats.get("totals_series") or []
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).set_index("frame")


def decisions_df(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "t_s" in df.columns:
        df = df.set_index("t_s")
    return df


def show_video(path: Path | None, caption: str) -> None:
    if path is None:
        st.warning(f"Missing video: {caption}")
        return
    st.caption(caption)
    st.video(str(path))


def render_decision_panel(rows: list, title: str) -> None:
    st.subheader(title)
    df = decisions_df(rows)
    if df.empty:
        st.caption("No decision series yet — run `make yolo_demo` or refresh.")
        return

    # Phase choice over time (categorical → numeric codes for chart)
    phase_order = ["NS_TL", "NS_R", "EW_TL", "EW_R"]
    phase_map = {p: i for i, p in enumerate(phase_order)}
    choice = pd.DataFrame(
        {
            "XtraFlow": df["next_phase"].map(phase_map),
            "count-only": df["count_phase"].map(phase_map),
            "fixed round-robin": df["fixed_phase"].map(phase_map),
        },
        index=df.index,
    )
    st.markdown("**Who gets green (phase index over time)**")
    st.caption("0=NS_TL · 1=NS_R · 2=EW_TL · 3=EW_R — XtraFlow tracks fuel-weighted pressure")
    st.line_chart(choice)

    # Pressure by phase
    press_cols = {}
    for r in rows:
        for ph, val in (r.get("pressures") or {}).items():
            press_cols.setdefault(ph, []).append(val)
    if press_cols:
        st.markdown("**Fuel-weighted phase pressure**")
        st.line_chart(pd.DataFrame(press_cols, index=df.index))

    st.markdown("**Idle-fuel proxy while waiting (mg/s on unserved approaches)**")
    idle = df[["xtra_idle_mg_s", "fixed_idle_mg_s", "count_idle_mg_s"]].rename(
        columns={
            "xtra_idle_mg_s": "XtraFlow",
            "fixed_idle_mg_s": "fixed",
            "count_idle_mg_s": "count-only",
        }
    )
    st.line_chart(idle)

    st.markdown("**Cumulative idle fuel avoided vs fixed**")
    saved = df["idle_saved_vs_fixed_mg_s"].cumsum()
    st.area_chart(pd.DataFrame({"idle_saved_mg_s_cum": saved}))

    # Phase share table
    share = df["next_phase"].value_counts(normalize=True).rename("share").reset_index()
    share.columns = ["phase", "share"]
    c1, c2 = st.columns([1, 2])
    with c1:
        st.dataframe(share, hide_index=True, use_container_width=True)
    with c2:
        st.dataframe(
            df[["next_phase", "served_pressure", "n_vehicles", "idle_saved_vs_fixed_mg_s"]].tail(12),
            use_container_width=True,
        )


def render_savings_banner(summary: dict) -> None:
    pub = summary.get("published_fuel_savings") or []
    club_ds = (summary.get("clubbed") or {}).get("decision_summary") or {}
    st.subheader("How XtraFlow allows traffic & saves fuel")
    st.write(summary.get("decision_label", ""))

    a, b, c, d = st.columns(4)
    a.metric(
        "Clip idle cut vs fixed",
        f"{club_ds.get('pct_idle_cut_vs_fixed', 0):.1f}%",
        help="Detector-based idle-fuel proxy on this mosaic — illustrative only",
    )
    b.metric(
        "Clip idle saved (L proxy)",
        f"{club_ds.get('idle_fuel_saved_L_proxy', 0):.4f}",
    )
    c.metric("Dominant phase (clubbed)", club_ds.get("dominant_phase", "—"))
    d.metric("Samples", club_ds.get("n_samples", "—"))

    st.caption(club_ds.get("note", ""))

    if pub:
        st.markdown("**Published SUMO fuel reduction** (locked study — not from this CCTV clip)")
        st.dataframe(pd.DataFrame(pub), hide_index=True, use_container_width=True)
        st.caption(
            "Headline vs best baseline is typically ~0–2%; vs legacy fixed timing ~8–17% "
            "depending on scenario. See results/headlines.json."
        )


st.set_page_config(page_title="XtraFlow YOLO demo", layout="wide")
st.title("XtraFlow · YOLO + decisions")
st.caption("CCTV detections drive fuel-weighted phase choice — clip proxies are illustrative")

summary = load_summary()
if summary is None:
    st.error("No YOLO summary found. Run `make yolo_demo` first.")
    st.stop()

st.info(summary.get("label", "Input feasibility — not a fuel-saving result"))
with st.expander("Source & attribution", expanded=False):
    st.write(summary.get("source", ""))
    st.write(summary.get("attribution", ""))
    st.write(summary.get("note", ""))

view = st.sidebar.radio(
    "View",
    ["Story video (simple)", "Clubbed (all cameras)", "Individual camera"],
    index=0,
)
page = st.sidebar.radio(
    "Panel",
    ["Overview + video", "Decisions & fuel"],
    index=0,
)
approaches = [c["approach"] for c in summary.get("cameras") or []]
if not approaches:
    approaches = list((summary.get("per_camera") or {}).keys())

# ── Simple story ─────────────────────────────────────────────────────────
if view.startswith("Story"):
    st.subheader("Which roads move, when")
    st.write(
        "One video of all four cameras. **GO** tiles are the approaches XtraFlow is serving; "
        "Hypothesis on camera: detect who waits, score **fuel-weighted pressure**, serve under "
        "**green → yellow → all-red**. GO plays; YELLOW clears; waiting axis PAUSE (frozen)."
    )
    story = resolve((summary.get("outputs") or {}).get("story"))
    if story is None:
        story = resolve("results/demo/yolo/yolo_story.mp4") or resolve("results/demo/yolo_story.mp4")
    if story is None:
        st.warning("Story video missing. Run: `python -m demo.yolo_story`")
    else:
        show_video(story, "Clubbed story — green = that road moves now")

    ds = (summary.get("clubbed") or {}).get("decision_summary") or {}
    a, b, c = st.columns(3)
    a.metric("Mostly serving", ds.get("dominant_phase", "—"))
    b.metric("Idle cut vs fixed (clip proxy)", f"{ds.get('pct_idle_cut_vs_fixed', 0):.1f}%")
    c.metric("Samples", ds.get("n_samples", "—"))

    rows = (summary.get("clubbed") or {}).get("decisions") or []
    if rows:
        plain = {
            "NS_TL": "North–South moving",
            "NS_R": "North–South (right) moving",
            "EW_TL": "East–West moving",
            "EW_R": "East–West (right) moving",
        }
        from demo.yolo_decisions import PHASE_APPROACHES as _PA

        timeline = []
        prev = None
        for r in rows:
            ph = r.get("next_phase")
            if ph != prev:
                timeline.append(
                    {
                        "time_s": r.get("t_s"),
                        "serving": plain.get(ph, ph),
                        "roads": " + ".join(_PA.get(ph, ())),
                        "vehicles_seen": r.get("n_vehicles"),
                    }
                )
                prev = ph
        st.markdown("**When the green switches**")
        st.dataframe(pd.DataFrame(timeline), hide_index=True, use_container_width=True)

# ── Clubbed ──────────────────────────────────────────────────────────────
elif view.startswith("Clubbed"):
    if page.startswith("Decisions"):
        render_savings_banner(summary)
        render_decision_panel(
            (summary.get("clubbed") or {}).get("decisions") or [],
            "Clubbed decisions (N/E/S/W fused as one intersection)",
        )
    else:
        st.subheader("All cameras")
        club = summary.get("clubbed") or {}
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Cameras", club.get("n_cameras", len(approaches)))
        c2.metric("Σ peak vehicles", club.get("peak_vehicles", "—"))
        c3.metric("Σ mean vehicles", club.get("mean_vehicles", "—"))
        ds = club.get("decision_summary") or {}
        c4.metric("Idle cut vs fixed", f"{ds.get('pct_idle_cut_vs_fixed', 0):.1f}%")

        mosaic = resolve((summary.get("outputs") or {}).get("mosaic"))
        if mosaic is None:
            mosaic = resolve("results/demo/yolo/yolo_mosaic.mp4")
        show_video(mosaic, "2×2 mosaic (N · E / S · W)")

        st.subheader("Per-camera summary")
        df = cam_table(summary)
        st.dataframe(df, use_container_width=True, hide_index=True)
        if not df.empty and df["mean_veh"].notna().any():
            st.bar_chart(df.set_index("approach")[["mean_veh", "peak_veh"]])

        st.subheader("Vehicle time series")
        cols = st.columns(2)
        for i, approach in enumerate(approaches):
            stats = (summary.get("per_camera") or {}).get(approach) or {}
            sdf = series_df(stats)
            with cols[i % 2]:
                st.markdown(f"**{approach}**")
                if sdf.empty:
                    st.caption("No series — re-run `make yolo_demo`.")
                else:
                    st.line_chart(sdf[["vehicles", "weighted_pressure"]])

        st.subheader("Overlays")
        g = st.columns(2)
        for i, approach in enumerate(approaches):
            stats = (summary.get("per_camera") or {}).get(approach) or {}
            ov = resolve(stats.get("overlay") or f"results/demo/yolo/overlay_{approach}.mp4")
            with g[i % 2]:
                show_video(ov, f"{approach} — {stats.get('title', '')}")

# ── Individual ───────────────────────────────────────────────────────────
else:
    cam = st.sidebar.selectbox("Camera", approaches, index=0)
    stats = (summary.get("per_camera") or {}).get(cam) or {}
    title = stats.get("title") or cam

    if page.startswith("Decisions"):
        ds = stats.get("decision_summary") or {}
        st.subheader(f"Decisions · camera {cam}")
        st.write(title)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Dominant phase", ds.get("dominant_phase", "—"))
        m2.metric("Idle cut vs fixed", f"{ds.get('pct_idle_cut_vs_fixed', 0):.1f}%")
        m3.metric("Idle saved L proxy", f"{ds.get('idle_fuel_saved_L_proxy', 0):.4f}")
        m4.metric("Mean served pressure", ds.get("mean_served_pressure", "—"))
        render_decision_panel(stats.get("decisions") or [], f"Phase timeline · {cam}")
        st.caption(ds.get("note", ""))
    else:
        st.subheader(f"Camera {cam}")
        st.write(title)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Peak vehicles", stats.get("peak_vehicles", "—"))
        m2.metric("Mean vehicles", stats.get("mean_vehicles", "—"))
        m3.metric("Mean pressure", stats.get("mean_weighted_pressure", "—"))
        ds = stats.get("decision_summary") or {}
        m4.metric("Dominant phase", ds.get("dominant_phase", "—"))

        ov = resolve(stats.get("overlay") or f"results/demo/yolo/overlay_{cam}.mp4")
        show_video(ov, f"YOLO overlay · {cam}")

        sdf = series_df(stats)
        if not sdf.empty:
            st.subheader("Counts over time")
            st.line_chart(sdf[["vehicles", "weighted_pressure"]])
            class_cols = [
                c
                for c in ("car", "truck", "bus", "two_wheeler", "auto_rickshaw")
                if c in sdf.columns
            ]
            if class_cols:
                st.subheader("Class mix over time")
                st.area_chart(sdf[class_cols])
        st.caption(f"Source clip: `{stats.get('source_video', '—')}`")

st.sidebar.divider()
st.sidebar.caption("Re-run: `make yolo_demo`")
st.sidebar.caption("Enrich only: `python -m demo.yolo_decisions`")
st.sidebar.caption(str(SUMMARY.relative_to(ROOT)))
