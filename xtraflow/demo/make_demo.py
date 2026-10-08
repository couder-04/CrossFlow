"""Side-by-side Fixed vs XtraFlow demo MP4 (same demand seed)."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from sim.gen_demand import generate
from sim.util import ROOT, ensure_dirs, load_config, locate_sumo


TYPE_COLOR = {
    "two_wheeler": "#1b9e77",
    "auto_rickshaw": "#d95f02",
    "car": "#7570b3",
    "bus": "#e7298a",
    "truck": "#66a61e",
}


def _record(controller: str, seed: int, horizon: int = 300, stride: int = 2):
    cfg = load_config()
    _, sumo_bin = locate_sumo()
    import traci
    from sim.controllers import make_controller
    from sim.run_sim import _write_sumocfg

    net = ROOT / "results" / "networks" / "intersection.net.xml"
    vtypes = ROOT / "results" / "networks" / "vtypes.add.xml"
    tls = ROOT / "results" / "networks" / "tls.add.xml"
    routes = generate("dynamic", seed, cfg)
    raw = ROOT / "results" / "demo"
    raw.mkdir(parents=True, exist_ok=True)
    tag = f"demo_{controller}_seed{seed}"
    tripinfo = raw / f"{tag}.tripinfo.xml"
    ssm = raw / f"{tag}.ssm.xml"
    cfg_path = raw / f"{tag}.sumocfg"
    _write_sumocfg(net, routes, [vtypes, tls], cfg_path, tripinfo, ssm, end=horizon)
    traci.start([sumo_bin, "-c", str(cfg_path), "--duration-log.disable", "true"])
    ctrl = make_controller(controller, cfg=cfg, scenario="dynamic")
    frames = []
    cum_fuel = 0.0
    idle = 0.0
    for step in range(horizon):
        if controller != "actuated":
            ctrl.step(traci, float(step))
        traci.simulationStep()
        if step % stride != 0:
            continue
        vehs = []
        q = 0
        for vid in traci.vehicle.getIDList():
            x, y = traci.vehicle.getPosition(vid)
            vtype = traci.vehicle.getTypeID(vid)
            spd = traci.vehicle.getSpeed(vid)
            if spd < 0.1:
                q += 1
                idle += stride
            try:
                cum_fuel += traci.vehicle.getFuelConsumption(vid) * stride / 1e6  # rough kg
            except Exception:
                pass
            vehs.append((x, y, vtype, spd))
        phase = ctrl.current_phase_name()
        frames.append({"t": step, "vehs": vehs, "queue": q, "idle": idle, "fuel": cum_fuel, "phase": phase})
    traci.close()
    return frames


def render(frames_a, frames_b, out_path: Path, timelapse: float = 10.0):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Rectangle
    import imageio.v2 as imageio

    # determine bounds
    xs, ys = [], []
    for fr in frames_a + frames_b:
        for x, y, _, _ in fr["vehs"]:
            xs.append(x)
            ys.append(y)
    if not xs:
        xs, ys = [-50, 50], [-50, 50]
    xmin, xmax = min(xs) - 20, max(xs) + 20
    ymin, ymax = min(ys) - 20, max(ys) + 20

    images = []
    n = min(len(frames_a), len(frames_b))
    for i in range(n):
        fig, axes = plt.subplots(1, 2, figsize=(19.2, 10.8), dpi=100)
        for ax, fr, title in [
            (axes[0], frames_a[i], "Fixed"),
            (axes[1], frames_b[i], "XtraFlow"),
        ]:
            ax.set_xlim(xmin, xmax)
            ax.set_ylim(ymin, ymax)
            ax.set_aspect("equal")
            ax.set_facecolor("#e8eef2")
            # roads
            ax.axhline(0, color="#555", lw=18, alpha=0.25)
            ax.axvline(0, color="#555", lw=18, alpha=0.25)
            for x, y, vt, spd in fr["vehs"]:
                ax.add_patch(Circle((x, y), 2.2 if vt != "bus" else 3.5,
                                    color=TYPE_COLOR.get(vt, "#333"), alpha=0.9))
            ax.set_title(f"{title} | t={fr['t']}s | phase={fr['phase']}", fontsize=14)
            ax.text(0.02, 0.98,
                    f"queue={fr['queue']}\nidle_veh_s={fr['idle']:.0f}\n"
                    f"cum_fuel≈{fr['fuel']:.3f} (proxy kg)\n",
                    transform=ax.transAxes, va="top", fontsize=11,
                    bbox=dict(boxstyle="round", facecolor="white", alpha=0.8))
            ax.set_xticks([])
            ax.set_yticks([])
        fig.suptitle("Same traffic. Fixed vs XtraFlow.", fontsize=22, fontweight="bold")
        fig.text(0.5, 0.02, f"Simulation-based | time-lapse ≈ {timelapse:.0f}×", ha="center", fontsize=12)
        fig.tight_layout(rect=[0, 0.04, 1, 0.96])
        fig.canvas.draw()
        buf = np.asarray(fig.canvas.buffer_rgba())
        img = buf[:, :, :3].copy()
        images.append(img)
        plt.close(fig)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 1080p-ish from figsize
    imageio.mimsave(out_path, images, fps=10)
    # GIF fallback
    gif = out_path.with_suffix(".gif")
    imageio.mimsave(gif, images[::2], fps=5)
    return out_path, gif


def main():
    ensure_dirs()
    seed = 1  # TEST seed
    print("Recording fixed...")
    fa = _record("fixed", seed, horizon=240, stride=3)
    print("Recording XtraFlow...")
    fb = _record("XtraFlow", seed, horizon=240, stride=3)
    out = ROOT / "results" / "demo" / "demo.mp4"
    mp4, gif = render(fa, fb, out, timelapse=10.0)
    print(f"Wrote {mp4} and {gif}")


if __name__ == "__main__":
    main()
