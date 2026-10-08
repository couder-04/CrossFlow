"""Streamlit dashboard reading results files (no hardcoded metrics)."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]


def load_json(p: Path):
    if p.exists():
        return json.loads(p.read_text())
    return None


st.set_page_config(page_title="XtraFlow", layout="wide")
st.title("XtraFlow")
st.caption("Fuel-weighted traffic signals. Simulation-based estimate; not a real-world deployment result")

raw = ROOT / "results" / "raw_runs.csv"
if not raw.exists():
    raw = ROOT / "results" / "raw_runs_smoke.csv"
if not raw.exists():
    st.error("No results CSV found. Run the sweep first.")
    st.stop()

df = pd.read_csv(raw)
scenarios = sorted(df.scenario.unique())
controllers = sorted(df.controller.unique())
sc = st.sidebar.selectbox("Scenario", scenarios)
ctrl = st.sidebar.multiselect("Controllers", controllers, default=controllers)

sub = df[(df.scenario == sc) & (df.controller.isin(ctrl))]
st.subheader("Metric table")
st.dataframe(sub.groupby("controller")[
    ["fuel_per_vehicle_L", "total_CO2_kg", "mean_waiting_s", "mean_queue_veh", "n_completed"]
].mean())

st.subheader("Charts")
st.bar_chart(sub.groupby("controller")["fuel_per_vehicle_L"].mean())

# Extrapolation sliders from saved per-vehicle savings
st.subheader("Extrapolation (live, from measured savings)")
ex = load_json(ROOT / "results" / "extrapolation.json")
head = load_json(ROOT / "results" / "headlines.json")
if ex:
    base_save = ex["measured_saving_L_per_veh"]["mean"]
    st.write(f"Measured saving (scenario {ex.get('scenario_basis')}): {base_save:.4f} L/veh")
    vph = st.slider("Vehicles per peak hour", 200, 3000, 1400)
    phd = st.slider("Peak hours/day", 1, 12, 5)
    dpy = st.slider("Days/year", 100, 365, 300)
    nint = st.slider("Intersections", 1, 2000, 200)
    price = st.slider("Fuel price INR/L (PLACEHOLDER)", 50, 150, 100)
    annual_L = base_save * vph * phd * dpy * nint
    st.metric("Annual litres (sim-based)", f"{annual_L:,.0f}")
    st.metric("Annual INR (placeholder price)", f"{annual_L * price:,.0f}")
else:
    st.info("Run extrapolate.py to enable savings-based recalculation.")

figs = ROOT / "results" / "figures"
if figs.exists():
    st.subheader("Figures")
    for p in sorted(figs.glob("*.png")):
        st.image(str(p), caption=p.name)
