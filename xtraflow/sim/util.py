"""Shared utilities: config, SUMO location, seeds, hashing, paths."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or (ROOT / "config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_dirs() -> None:
    for p in [
        ROOT / "results" / "networks",
        ROOT / "results" / "demand",
        ROOT / "results" / "tables",
        ROOT / "results" / "figures",
        ROOT / "results" / "raw",
        ROOT / "results" / "timeseries",
        ROOT / "results" / "rl",
        ROOT / "results" / "demo",
    ]:
        p.mkdir(parents=True, exist_ok=True)


def locate_sumo() -> Tuple[str, str]:
    """Return (SUMO_HOME, sumo_binary). Prefer pip eclipse-sumo package."""
    sumo_home = os.environ.get("SUMO_HOME")
    if not sumo_home:
        try:
            import sumo  # type: ignore

            sumo_home = os.path.dirname(sumo.__file__)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "SUMO not found. Install eclipse-sumo or set SUMO_HOME."
            ) from e
    os.environ["SUMO_HOME"] = sumo_home
    tools = os.path.join(sumo_home, "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    candidates = []
    which = shutil.which("sumo")
    if which:
        candidates.append(Path(which))
    candidates.extend([
        Path(sumo_home) / "bin" / "sumo",
        Path(sumo_home).resolve().parent.parent.parent / "bin" / "sumo",
        ROOT / ".venv" / "bin" / "sumo",
        Path(sys.prefix) / "bin" / "sumo",
    ])
    binary = None
    for c in candidates:
        try:
            if c is not None and Path(c).exists():
                binary = str(Path(c).resolve())
                break
        except OSError:
            continue
    if binary is None:
        raise RuntimeError(f"sumo binary not found under SUMO_HOME={sumo_home}")
    # ensure bin on PATH
    bin_dir = str(Path(binary).parent)
    if bin_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
    return sumo_home, binary


def seed_range(lo_hi: Sequence[int]) -> List[int]:
    lo, hi = int(lo_hi[0]), int(lo_hi[1])
    return list(range(lo, hi + 1))


def config_sha256(path: Optional[Path] = None) -> str:
    path = path or (ROOT / "config.yaml")
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest()


def rel_to_root(path: Path) -> str:
    """Path relative to the repo root. Never an absolute path."""
    path = path.resolve()
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return path.name


def file_sha256(path: Path) -> Optional[str]:
    if not path.exists() or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Hashed into results/config.lock. Relative paths only.
LOCK_FILES = [
    "config.yaml",
    "results/tuned_params.json",
    "results/weights.json",
    "results/fixed_tuned.json",
    "results/emission_class_map.json",
    "results/rl/ppo_best.zip",
]


def git_commit() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return proc.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def freeze_config(cfg_path: Optional[Path] = None, lock_path: Optional[Path] = None) -> str:
    """Write the config lock. Does not run from the sweep; use `make freeze_config`."""
    ensure_dirs()
    cfg_path = cfg_path or (ROOT / "config.yaml")
    lock_path = lock_path or (ROOT / "results" / "config.lock")
    files: Dict[str, Optional[str]] = {}
    for rel in LOCK_FILES:
        if rel == "config.yaml":
            files["config.yaml"] = file_sha256(cfg_path)
        else:
            files[rel] = file_sha256(ROOT / rel)
    note = "TEST sweep must match these hashes. Paths are relative to the repo root."
    smoke_bits = []
    ft = ROOT / "results" / "fixed_tuned.json"
    rl_meta = ROOT / "results" / "rl" / "rl_selection.json"
    if ft.exists() and load_json(ft).get("smoke"):
        smoke_bits.append("results/fixed_tuned.json is a smoke artifact")
    if rl_meta.exists() and load_json(rl_meta).get("smoke"):
        smoke_bits.append("results/rl/ppo_best.zip is a smoke checkpoint")
    if smoke_bits:
        note += " " + "; ".join(smoke_bits) + ". Re-freeze after the VALIDATION search and config rl.total_timesteps."
    payload = {
        "files": files,
        "git_commit": git_commit(),
        "note": note,
    }
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return files["config.yaml"] or ""


def assert_config_locked(cfg_path: Optional[Path] = None, lock_path: Optional[Path] = None) -> None:
    cfg_path = cfg_path or (ROOT / "config.yaml")
    lock_path = lock_path or (ROOT / "results" / "config.lock")
    if not lock_path.exists():
        raise RuntimeError("config.lock missing; run `make freeze_config` before the TEST sweep")
    locked = json.loads(lock_path.read_text(encoding="utf-8"))
    files = locked.get("files")
    if not isinstance(files, dict):
        raise RuntimeError("config.lock is missing file hashes; re-run `make freeze_config`")
    current_cfg = file_sha256(cfg_path)
    if files.get("config.yaml") != current_cfg:
        raise RuntimeError(
            f"config.yaml hash changed after freeze: locked={files.get('config.yaml')} current={current_cfg}"
        )
    for rel, digest in files.items():
        if rel == "config.yaml":
            continue
        current = file_sha256(ROOT / rel)
        if digest != current:
            raise RuntimeError(f"{rel} hash changed after freeze: locked={digest} current={current}")


def tool_cmd(name: str) -> List[str]:
    """Launch a SUMO tool even if its shebang points at a missing interpreter."""
    candidates: List[Path] = []
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    candidates.append(ROOT / ".venv" / "bin" / name)
    for c in candidates:
        if not c.exists():
            continue
        try:
            first = c.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
        except OSError:
            return [str(c)]
        if first.startswith("#!"):
            interp = first[2:].strip().split()[0]
            if not Path(interp).exists():
                return [sys.executable, str(c)]
        return [str(c)]
    raise RuntimeError(f"tool not found: {name}")


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def run_cmd(cmd: List[str], cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd or ROOT, check=check, capture_output=True, text=True)


def _emission_class_exists(class_name: str, net_path: Optional[Path] = None) -> bool:
    """Return True if SUMO accepts this emissionClass on a trivial load."""
    locate_sumo()
    net_path = net_path or (ROOT / "results" / "networks" / "intersection.net.xml")
    if not net_path.exists():
        # Without a net, accept well-known HBEFA3 / PHEMlight names only
        return class_name.startswith(("HBEFA3/", "PHEMlight/", "Zero", "Energy/"))
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        add = td_path / "ec.add.xml"
        add.write_text(
            f'<additional><vType id="t" emissionClass="{class_name}"/></additional>\n',
            encoding="utf-8",
        )
        rou = td_path / "ec.rou.xml"
        rou.write_text(
            '<routes><vehicle id="v" type="t" depart="0">'
            '<route edges="N_in S_out"/></vehicle></routes>\n',
            encoding="utf-8",
        )
        cfg = td_path / "ec.sumocfg"
        cfg.write_text(
            f"""<configuration>
  <input>
    <net-file value="{net_path}"/>
    <route-files value="{rou}"/>
    <additional-files value="{add}"/>
  </input>
  <time><begin value="0"/><end value="1"/></time>
  <report><no-warnings value="true"/><no-step-log value="true"/></report>
</configuration>
""",
            encoding="utf-8",
        )
        proc = subprocess.run(tool_cmd("sumo") + ["-c", str(cfg)], capture_output=True, text=True)
        err = (proc.stderr or "") + (proc.stdout or "")
        return "emissionClass" not in err and "Quitting" not in err


def probe_hbefa_classes() -> Dict[str, str]:
    """Map our vehicle types to emission classes that exist in the installed SUMO."""
    preferred = {
        "two_wheeler": ["HBEFA3/LDV_G_EU4", "HBEFA3/PC_G_EU4", "PHEMlight/PC_G_EU4"],
        "auto_rickshaw": ["HBEFA3/PC_G_EU4", "PHEMlight/PC_G_EU4"],
        "car": ["HBEFA3/PC_G_EU4", "PHEMlight/PC_G_EU4"],
        "bus": ["HBEFA3/Bus", "PHEMlight/PC_G_EU4"],
        "truck": ["HBEFA3/HDV", "HBEFA3/Bus"],
    }
    # This eclipse-sumo 1.21 wheel ships HBEFA3 + PHEMlight, not HBEFA4.
    mapping: Dict[str, str] = {}
    tested: Dict[str, bool] = {}
    net = ROOT / "results" / "networks" / "intersection.net.xml"
    for vtype, cands in preferred.items():
        chosen = None
        for c in cands:
            if c not in tested:
                tested[c] = _emission_class_exists(c, net if net.exists() else None)
            if tested[c]:
                chosen = c
                break
        mapping[vtype] = chosen or "HBEFA3/PC_G_EU4"

    # Alternate keeps buses and trucks off passenger-car classes.
    # auto_rickshaw has no dedicated class in this build (passenger-car proxy).
    # two_wheeler primary is LDV_G_EU4 when that class loads (light-duty proxy).
    alt_cands = {
        "two_wheeler": ["HBEFA3/LDV_G_EU3", "HBEFA3/PC_G_EU3", "PHEMlight/LDV_G_EU4"],
        "auto_rickshaw": ["HBEFA3/PC_G_EU3", "HBEFA3/PC_G_EU2"],
        "car": ["PHEMlight/PC_G_EU4", "HBEFA3/PC_G_EU3"],
        "bus": ["HBEFA3/HDV", "HBEFA3/HDV_D_EU4", "PHEMlight/HDV_D_EU4", "HBEFA3/Bus"],
        "truck": ["HBEFA3/HDV_D_EU4", "PHEMlight/HDV_D_EU4", "HBEFA3/HDV", "HBEFA3/Bus"],
    }
    alt: Dict[str, str] = {}
    for vtype, cands in alt_cands.items():
        chosen = None
        for c in cands:
            if vtype in ("bus", "truck") and "PC_" in c:
                continue
            if c not in tested:
                tested[c] = _emission_class_exists(c, net if net.exists() else None)
            if tested[c] and c != mapping.get(vtype):
                chosen = c
                break
        if chosen is None and vtype in ("bus", "truck"):
            for c, ok in tested.items():
                if ok and c != mapping.get(vtype) and "PC_" not in c and ("HDV" in c or "Bus" in c or "bus" in c):
                    chosen = c
                    break
        alt[vtype] = chosen or mapping[vtype]

    out = {
        "primary": mapping,
        "alternate": alt,
        "tested": tested,
        "proxies": {
            "auto_rickshaw": "passenger-car class (no auto-rickshaw emission class in this SUMO build)",
            "two_wheeler": "LDV_G_EU4 when available, else the first accepted light/passenger class",
            "weights": "results/weights.json idle-fuel weights inherit these emission classes",
        },
        "note": (
            "Installed SUMO 1.21: HBEFA3/PHEMlight available; HBEFA4 not present in this build. "
            "Alternate bus/truck classes stay heavy-duty or bus, not passenger car. "
            "Simulation-based estimate; assumed traffic mix."
        ),
    }
    ensure_dirs()
    save_json(ROOT / "results" / "emission_class_map.json", out)
    return mapping


def mg_to_litres(fuel_mg: float, density_kg_per_L: float) -> float:
    """fuel_mg (milligrams) -> litres using density kg/L."""
    # mg -> kg: /1e6; kg / (kg/L) = L
    if density_kg_per_L <= 0:
        raise ValueError("density must be positive")
    return (fuel_mg / 1e6) / density_kg_per_L


def mg_to_kg(mass_mg: float) -> float:
    return mass_mg / 1e6


def kmh_to_ms(kmh: float) -> float:
    return kmh / 3.6


CONTROLLER_NAMES = [
    "fixed",
    "fixed_tuned",
    "webster",
    "actuated",
    "queue_pressure",
    "maxpressure",
    "ours_count",
    "XtraFlow",
    "rl_ppo",
]

# Non-oracle baselines eligible for the headline (legacy fixed is secondary).
HEADLINE_BASELINES = ["fixed_tuned", "actuated", "queue_pressure", "maxpressure"]

SCENARIOS = ["balanced", "peak_unbalanced", "dynamic", "low_demand"]
