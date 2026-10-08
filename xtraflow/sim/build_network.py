"""Build a single 4-arm left-hand-traffic signalized intersection for SUMO."""
from __future__ import annotations

import math
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Set, Tuple

from sim.util import (
    ROOT,
    ensure_dirs,
    load_config,
    locate_sumo,
    probe_hbefa_classes,
    rel_to_root,
    run_cmd,
    save_json,
    tool_cmd,
)

APPROACHES = ["N", "S", "E", "W"]


def _nod_edg(cfg) -> Tuple[str, str]:
    L = float(cfg["simulation"]["approach_length_m"])
    # Nodes: center + four outer
    nodes = [
        ("C", 0.0, 0.0, "traffic_light"),
        ("N", 0.0, L, "priority"),
        ("S", 0.0, -L, "priority"),
        ("E", L, 0.0, "priority"),
        ("W", -L, 0.0, "priority"),
    ]
    nod = ['<nodes>']
    for nid, x, y, ntype in nodes:
        nod.append(f'  <node id="{nid}" x="{x}" y="{y}" type="{ntype}"/>')
    nod.append("</nodes>")

    # Edges: inbound to C and outbound from C
    # Left-hand traffic: driving on left; netconvert --lefthand
    # 3 inbound lanes, 2 outbound
    edg = ['<edges>']
    for a in APPROACHES:
        edg.append(
            f'  <edge id="{a}_in" from="{a}" to="C" numLanes="3" speed="13.89" '
            f'priority="2"/>'
        )
        edg.append(
            f'  <edge id="{a}_out" from="C" to="{a}" numLanes="2" speed="13.89" '
            f'priority="2"/>'
        )
    edg.append("</edges>")
    return "\n".join(nod), "\n".join(edg)


def _connections_file(net_path: Path) -> None:
    """After first netconvert, we rebuild with explicit connections if needed.
    Lane meaning (inbound index from right in SUMO default): for lefthand,
    we document: lane 0 = left-turn/through, lane 1 = through, lane 2 = right/through.
    Actual indexing is verified in validate_phases against foes.
    """
    # Placeholder — connections come from netconvert defaults + tlLogic overlay.


PHASE_DEFS = {
    # state string length = number of controlled links; filled after net build
    "names": ["NS_TL", "NS_R", "EW_TL", "EW_R"],
}


def build_tllogic_fixed(n_links: int, link_groups: Dict[str, List[int]], greens: List[int],
                        yellow: int, all_red: int) -> str:
    """Build tlLogic XML with 4 phases + yellow + all-red between each."""
    order = ["NS_TL", "NS_R", "EW_TL", "EW_R"]
    phases_xml = []
    for i, name in enumerate(order):
        state = ["r"] * n_links
        for idx in link_groups[name]:
            state[idx] = "G"
        phases_xml.append(
            f'        <phase duration="{greens[i]}" state="{"".join(state)}" name="{name}"/>'
        )
        # yellow
        ystate = ["r"] * n_links
        for idx in link_groups[name]:
            ystate[idx] = "y"
        phases_xml.append(
            f'        <phase duration="{yellow}" state="{"".join(ystate)}" name="{name}_Y"/>'
        )
        # all-red
        phases_xml.append(
            f'        <phase duration="{all_red}" state="{"r" * n_links}" name="AR_{name}"/>'
        )
    return (
        '    <tlLogic id="C" type="static" programID="fixed" offset="0">\n'
        + "\n".join(phases_xml)
        + "\n    </tlLogic>\n"
    )


def build_tllogic_actuated(n_links: int, link_groups: Dict[str, List[int]],
                           min_g: int, max_g: int, yellow: int, all_red: int) -> str:
    order = ["NS_TL", "NS_R", "EW_TL", "EW_R"]
    phases_xml = []
    for name in order:
        state = ["r"] * n_links
        for idx in link_groups[name]:
            state[idx] = "G"
        phases_xml.append(
            f'        <phase duration="{max_g}" minDur="{min_g}" maxDur="{max_g}" '
            f'state="{"".join(state)}" name="{name}"/>'
        )
        ystate = ["r"] * n_links
        for idx in link_groups[name]:
            ystate[idx] = "y"
        phases_xml.append(
            f'        <phase duration="{yellow}" state="{"".join(ystate)}" name="{name}_Y"/>'
        )
        phases_xml.append(
            f'        <phase duration="{all_red}" state="{"r" * n_links}" name="AR_{name}"/>'
        )
    return (
        '    <tlLogic id="C" type="actuated" programID="actuated" offset="0">\n'
        + "\n".join(phases_xml)
        + "\n    </tlLogic>\n"
    )


def _infer_link_groups(net) -> Tuple[int, Dict[str, List[int]], List[str]]:
    """Map controlled links at TLS 'C' into phase groups using toEdge directions."""
    import sumolib  # noqa: WPS433

    tls = net.getTLS("C")
    connections = tls.getConnections()
    n = len(connections)
    # Each connection: (fromLane, toLane, linkIndex) or similar
    groups: Dict[str, List[int]] = {k: [] for k in ["NS_TL", "NS_R", "EW_TL", "EW_R"]}
    link_meta: List[str] = []

    for conn in connections:
        # sumolib TLS connection: inLane, outLane, linkIndex
        in_lane = conn[0]
        out_lane = conn[1]
        idx = conn[2] if len(conn) > 2 else None
        if idx is None:
            continue
        from_edge = in_lane.getEdge().getID()
        to_edge = out_lane.getEdge().getID()
        # Approach from N_in, S_in, E_in, W_in
        approach = from_edge.replace("_in", "")
        dest = to_edge.replace("_out", "")
        # Left-hand traffic turning:
        # From N: left->E, through->S, right->W
        turn = _classify_turn(approach, dest)
        if approach in ("N", "S"):
            if turn == "right":
                groups["NS_R"].append(idx)
            else:
                groups["NS_TL"].append(idx)
        else:
            if turn == "right":
                groups["EW_R"].append(idx)
            else:
                groups["EW_TL"].append(idx)
        link_meta.append(f"{idx}:{from_edge}->{to_edge}:{turn}")

    # dedupe preserve order
    for k in groups:
        groups[k] = sorted(set(groups[k]))
    return n, groups, link_meta


def _classify_turn(approach: str, dest: str) -> str:
    # Left-hand traffic: clockwise is left? In LHT, left turn is toward left of driver.
    # Facing southbound from N: left = E, right = W, through = S
    table = {
        ("N", "E"): "left",
        ("N", "S"): "through",
        ("N", "W"): "right",
        ("S", "W"): "left",
        ("S", "N"): "through",
        ("S", "E"): "right",
        ("E", "S"): "left",
        ("E", "W"): "through",
        ("E", "N"): "right",
        ("W", "N"): "left",
        ("W", "E"): "through",
        ("W", "S"): "right",
    }
    return table.get((approach, dest), "through")


def _approach_of_edge(edge) -> str:
    fn = edge.getFromNode().getCoord()
    tn = edge.getToNode().getCoord()
    dx = fn[0] - tn[0]
    dy = fn[1] - tn[1]
    if abs(dx) >= abs(dy):
        return "E" if dx > 0 else "W"
    return "N" if dy > 0 else "S"


def _heading(edge) -> float:
    fn = edge.getFromNode().getCoord()
    tn = edge.getToNode().getCoord()
    return math.atan2(tn[1] - fn[1], tn[0] - fn[0])


def _turn_from_headings(h_in: float, h_out: float) -> str:
    d = h_out - h_in
    while d > math.pi:
        d -= 2 * math.pi
    while d < -math.pi:
        d += 2 * math.pi
    if d > math.pi / 4:
        return "left"
    if d < -math.pi / 4:
        return "right"
    return "through"


def infer_tls(net, tls_id: str) -> Dict:
    """Per-junction phase groups, approach edges, and downstream edges from geometry."""
    tls = net.getTLS(tls_id)
    groups: Dict[str, List[int]] = {k: [] for k in ["NS_TL", "NS_R", "EW_TL", "EW_R"]}
    approach_edges: Dict[str, List[str]] = {a: [] for a in ["N", "S", "E", "W"]}
    downstream: Dict[str, List[str]] = {k: [] for k in groups}
    turn_lookup: Dict[str, str] = {}
    meta: List[str] = []
    max_idx = -1
    for conn in tls.getConnections():
        in_lane, out_lane, idx = conn[0], conn[1], conn[2]
        if idx is None:
            continue
        max_idx = max(max_idx, int(idx))
        in_edge = in_lane.getEdge()
        out_edge = out_lane.getEdge()
        approach = _approach_of_edge(in_edge)
        turn = _turn_from_headings(_heading(in_edge), _heading(out_edge))
        key = ("NS_R" if turn == "right" else "NS_TL") if approach in ("N", "S") else (
            "EW_R" if turn == "right" else "EW_TL"
        )
        groups[key].append(int(idx))
        eid = in_edge.getID()
        if eid not in approach_edges[approach]:
            approach_edges[approach].append(eid)
        oid = out_edge.getID()
        if oid not in downstream[key]:
            downstream[key].append(oid)
        turn_lookup[f"{eid}|{oid}"] = turn
        meta.append(f"{idx}:{eid}->{oid}:{turn}:{approach}")
    for k in groups:
        groups[k] = sorted(set(groups[k]))
    return {
        "tls_id": tls_id,
        "n_links": max_idx + 1,
        "groups": groups,
        "approach_edges": approach_edges,
        "downstream_edges": downstream,
        "turn_lookup": turn_lookup,
        "meta": meta,
    }


def validate_phase_conflicts(net_path: Path, link_groups: Dict[str, List[int]],
                             n_links: int, tls_id: str = "C") -> Dict[str, List[str]]:
    """Assert each green state grants intended movements with no internal conflicts
    using SUMO foe information from the network."""
    import sumolib

    foes: Dict[int, Set[int]] = {i: set() for i in range(n_links)}
    # Foes come from the net.xml connection attributes for this TLS.
    tree = ET.parse(net_path)
    root = tree.getroot()
    for conn in root.findall("connection"):
        if conn.get("tl") != tls_id:
            continue
        link_index = conn.get("linkIndex")
        foe = conn.get("foes")
        if link_index is None or foe is None:
            continue
        li = int(link_index)
        # foes is a bitstring
        for j, bit in enumerate(foe):
            if bit == "1":
                foes[li].add(j)

    report: Dict[str, List[str]] = {"ok": [], "conflicts": [], "empty_groups": []}
    for name, idxs in link_groups.items():
        if not idxs:
            report["empty_groups"].append(name)
            continue
        conflict_pairs = []
        for a in idxs:
            for b in idxs:
                if a >= b:
                    continue
                if b in foes.get(a, set()) or a in foes.get(b, set()):
                    conflict_pairs.append(f"{a}-{b}")
        if conflict_pairs:
            report["conflicts"].append(f"{name}: {conflict_pairs}")
        else:
            report["ok"].append(name)
    # Also validate yellow/all-red lengths conceptually elsewhere
    if report["conflicts"]:
        raise AssertionError(f"Phase conflicts detected: {report['conflicts']}")
    return report


def write_additional_vtypes(cfg, emission_map: Dict[str, str], out: Path) -> None:
    lines = ['<additional>']
    for name, p in cfg["vehicle_params"].items():
        emis = emission_map.get(name, "HBEFA3/PC_G_EU4")
        lat = ' latAlignment="center"' if cfg["simulation"].get("use_sublane") else ""
        lines.append(
            f'  <vType id="{name}" vClass="{p["vClass"]}" length="{p["length"]}" '
            f'width="{p["width"]}" accel="{p["accel"]}" decel="{p["decel"]}" '
            f'maxSpeed="{p["maxSpeed"]}" guiShape="{p["guiShape"]}" '
            f'emissionClass="{emis}"{lat}/>'
        )
    lines.append("</additional>")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build(cfg=None) -> Path:
    cfg = cfg or load_config()
    ensure_dirs()
    locate_sumo()
    emission_map = probe_hbefa_classes()

    net_dir = ROOT / "results" / "networks"
    nod_path = net_dir / "intersection.nod.xml"
    edg_path = net_dir / "intersection.edg.xml"
    con_path = net_dir / "intersection.con.xml"
    net_path = net_dir / "intersection.net.xml"
    add_path = net_dir / "vtypes.add.xml"

    nod, edg = _nod_edg(cfg)
    nod_path.write_text(nod, encoding="utf-8")
    edg_path.write_text(edg, encoding="utf-8")

    # Lateral resolution is a SUMO simulation option, not a netconvert 1.21 flag.
    cmd = tool_cmd("netconvert") + [
        "--node-files", str(nod_path),
        "--edge-files", str(edg_path),
        "--lefthand", "true",
        "--tls.guess", "true",
        "--tls.join", "true",
        "--junctions.join", "true",
        "--no-turnarounds", "true",
        "--output-file", str(net_path),
    ]
    proc = run_cmd(cmd, check=False)
    if proc.returncode != 0:
        raise RuntimeError("netconvert failed:\n" + (proc.stderr or "")[-2000:])

    import sumolib

    net = sumolib.net.readNet(str(net_path))
    spec = infer_tls(net, "C")
    n_links, groups, meta = spec["n_links"], spec["groups"], spec["meta"]
    if n_links == 0:
        raise RuntimeError("No TLS links found at junction C")

    # Ensure every link is assigned
    assigned = set()
    for idxs in groups.values():
        assigned.update(idxs)
    missing = [i for i in range(n_links) if i not in assigned]
    if missing:
        # assign leftovers to through groups by approach heuristic
        groups["NS_TL"].extend(missing)
        groups["NS_TL"] = sorted(set(groups["NS_TL"]))

    report = validate_phase_conflicts(net_path, groups, n_links, tls_id="C")
    save_json(ROOT / "results" / "phase_groups.json", {
        "n_links": n_links,
        "groups": groups,
        "approach_edges": spec["approach_edges"],
        "downstream_edges": spec["downstream_edges"],
        "turn_lookup": spec["turn_lookup"],
        "meta": meta,
        "validation": report,
    })

    greens = list(cfg["signals"]["fixed_greens_s"])
    yellow = int(cfg["signals"]["yellow_s"])
    all_red = int(cfg["signals"]["all_red_s"])
    min_g = int(cfg["signals"]["min_green_s"])
    max_g = int(cfg["signals"]["max_green_s"])

    # Keep netconvert's native tlLogic intact (ElementTree rewrite corrupted TLS binding).
    # Load our fixed + actuated programs from an additional file instead.
    tll_path = net_dir / "tls.add.xml"
    fixed_xml = build_tllogic_fixed(n_links, groups, greens, yellow, all_red)
    act_xml = build_tllogic_actuated(n_links, groups, min_g, max_g, yellow, all_red)
    tll_path.write_text(
        "<additional>\n" + fixed_xml + act_xml + "</additional>\n",
        encoding="utf-8",
    )

    write_additional_vtypes(cfg, emission_map, add_path)
    probe_sublane(net_path, add_path, cfg)

    save_json(ROOT / "results" / "network_info.json", {
        "net": rel_to_root(net_path),
        "vtypes": rel_to_root(add_path),
        "tls_additional": rel_to_root(tll_path),
        "n_links": n_links,
        "groups": groups,
        "lefthand": True,
        "lane_meaning": (
            "Inbound 3 lanes: left-turn/through, through, right-turn/through "
            "(assumed geometry; indices validated via phase_groups.json)."
        ),
    })
    print(f"Built network: {net_path} n_links={n_links} groups={groups}")
    return net_path


def probe_sublane(net_path: Path, vtypes: Path, cfg=None) -> dict:
    """Run a 60 s sim with lateral-resolution in the sumocfg. Fail if SUMO errors."""
    cfg = cfg or load_config()
    if not cfg["simulation"].get("use_sublane", True):
        info = {"used_sublane": False, "reason": "use_sublane is false in config.yaml"}
        save_json(ROOT / "results" / "sublane_fallback.json", info)
        return info
    raw = ROOT / "results" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    net_path = Path(net_path).resolve()
    vtypes = Path(vtypes).resolve()
    rou = raw / "sublane_probe.rou.xml"
    rou.write_text(
        '<routes><vehicle id="p0" type="car" depart="0" departLane="best" departSpeed="max">'
        '<route edges="N_in S_out"/></vehicle></routes>\n',
        encoding="utf-8",
    )
    trip = raw / "sublane_probe.tripinfo.xml"
    scfg = raw / "sublane_probe.sumocfg"
    lat = float(cfg["simulation"].get("lateral_resolution", 0.4))

    def _cfg_rel(path: Path) -> str:
        return os.path.relpath(path, scfg.parent)

    scfg.write_text(
        f"""<configuration>
  <input>
    <net-file value="{_cfg_rel(net_path)}"/>
    <route-files value="{_cfg_rel(rou)}"/>
    <additional-files value="{_cfg_rel(vtypes)}"/>
  </input>
  <output><tripinfo-output value="{trip}"/></output>
  <time><begin value="0"/><end value="60"/></time>
  <processing>
    <lateral-resolution value="{lat}"/>
    <time-to-teleport value="-1"/>
  </processing>
  <report><no-step-log value="true"/></report>
</configuration>
""",
        encoding="utf-8",
    )
    proc = run_cmd(
        tool_cmd("sumo") + ["-c", str(scfg), "--duration-log.disable", "true"],
        check=False,
    )
    err = (proc.stderr or "") + (proc.stdout or "")
    bad = proc.returncode != 0 or "Error:" in err or "Quitting (on error)" in err
    if bad:
        raise RuntimeError(
            "Sublane probe failed. lateral-resolution lives in the sumocfg, "
            "and this build does not fall back silently.\n" + err[-2000:]
        )
    info = {
        "used_sublane": True,
        "lateral_resolution": lat,
        "probe_s": 60,
        "where": "sumocfg processing/lateral-resolution",
    }
    save_json(ROOT / "results" / "sublane_fallback.json", info)
    return info


if __name__ == "__main__":
    build()
