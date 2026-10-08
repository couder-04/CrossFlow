"""Build a 2x2 grid of signalized intersections (200 m links), left-hand traffic."""
from __future__ import annotations

from pathlib import Path

from sim.build_network import infer_tls, validate_phase_conflicts
from sim.util import ROOT, ensure_dirs, load_config, locate_sumo, rel_to_root, run_cmd, save_json, tool_cmd


def junction_specs(net_path: Path | None = None) -> dict:
    """Per-TLS phase groups from the grid net. Does not write results files."""
    import sumolib

    net_path = net_path or (ROOT / "results" / "networks" / "grid2x2.net.xml")
    net = sumolib.net.readNet(str(net_path))
    tls_ids = [t.getID() for t in net.getTrafficLights()]
    junctions = {}
    for tid in tls_ids:
        spec = infer_tls(net, tid)
        report = validate_phase_conflicts(net_path, spec["groups"], spec["n_links"], tls_id=tid)
        spec["validation"] = report
        neighbors = [x for x in tls_ids if x != tid]
        spec["neighbor_ids"] = neighbors
        junctions[tid] = spec
    return {"tls_ids": tls_ids, "junctions": junctions}


def build(cfg=None, out_dir: Path | None = None) -> Path:
    cfg = cfg or load_config()
    ensure_dirs()
    locate_sumo()
    L = float(cfg["simulation"]["grid_link_length_m"])
    net_dir = out_dir or (ROOT / "results" / "networks")
    net_dir.mkdir(parents=True, exist_ok=True)
    nod_path = net_dir / "grid2x2.nod.xml"
    edg_path = net_dir / "grid2x2.edg.xml"
    net_path = net_dir / "grid2x2.net.xml"

    nodes = []
    for iy in range(2):
        for ix in range(2):
            nodes.append((f"J{ix}{iy}", ix * L, iy * L, "traffic_light"))
    externals = [
        ("N0", 0 * L, 2 * L), ("N1", 1 * L, 2 * L),
        ("S0", 0 * L, -L), ("S1", 1 * L, -L),
        ("E0", 2 * L, 0 * L), ("E1", 2 * L, 1 * L),
        ("W0", -L, 0 * L), ("W1", -L, 1 * L),
    ]
    for nid, x, y in externals:
        nodes.append((nid, x, y, "priority"))

    nod = ["<nodes>"]
    for nid, x, y, ntype in nodes:
        nod.append(f'  <node id="{nid}" x="{x}" y="{y}" type="{ntype}"/>')
    nod.append("</nodes>")

    edges = []
    for iy in range(2):
        edges.append((f"H_{0}_{iy}_e", f"J0{iy}", f"J1{iy}"))
        edges.append((f"H_{1}_{iy}_w", f"J1{iy}", f"J0{iy}"))
    for ix in range(2):
        edges.append((f"V_{ix}_{0}_n", f"J{ix}0", f"J{ix}1"))
        edges.append((f"V_{ix}_{1}_s", f"J{ix}1", f"J{ix}0"))
    stubs = [
        ("N0_in", "N0", "J01"), ("N0_out", "J01", "N0"),
        ("N1_in", "N1", "J11"), ("N1_out", "J11", "N1"),
        ("S0_in", "S0", "J00"), ("S0_out", "J00", "S0"),
        ("S1_in", "S1", "J10"), ("S1_out", "J10", "S1"),
        ("E0_in", "E0", "J10"), ("E0_out", "J10", "E0"),
        ("E1_in", "E1", "J11"), ("E1_out", "J11", "E1"),
        ("W0_in", "W0", "J00"), ("W0_out", "J00", "W0"),
        ("W1_in", "W1", "J01"), ("W1_out", "J01", "W1"),
    ]
    edges.extend(stubs)

    edg = ["<edges>"]
    for eid, frm, to in edges:
        edg.append(
            f'  <edge id="{eid}" from="{frm}" to="{to}" numLanes="2" speed="13.89" priority="2"/>'
        )
    edg.append("</edges>")

    nod_path.write_text("\n".join(nod), encoding="utf-8")
    edg_path.write_text("\n".join(edg), encoding="utf-8")

    cmd = tool_cmd("netconvert") + [
        "--node-files", str(nod_path),
        "--edge-files", str(edg_path),
        "--lefthand", "true",
        "--tls.guess", "true",
        "--tls.join", "true",
        "--no-turnarounds", "true",
        "--output-file", str(net_path),
    ]
    proc = run_cmd(cmd, check=False)
    if proc.returncode != 0:
        raise RuntimeError("grid netconvert failed:\n" + (proc.stderr or "")[-2000:])

    info = junction_specs(net_path)
    payload = {
        "net": rel_to_root(net_path) if out_dir is None else net_path.name,
        "tls_ids": info["tls_ids"],
        "link_length_m": L,
        "lefthand": True,
        "junctions": {
            tid: {
                "n_links": spec["n_links"],
                "groups": spec["groups"],
                "approach_edges": spec["approach_edges"],
                "downstream_edges": spec["downstream_edges"],
                "turn_lookup": spec["turn_lookup"],
                "neighbor_ids": spec["neighbor_ids"],
                "validation": spec["validation"],
            }
            for tid, spec in info["junctions"].items()
        },
    }
    if out_dir is None:
        save_json(ROOT / "results" / "grid_info.json", payload)
    print(f"Built grid: {net_path} tls={info['tls_ids']}")
    return net_path


if __name__ == "__main__":
    build()
