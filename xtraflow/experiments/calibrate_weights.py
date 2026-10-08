"""Measure idle fuel rate per vehicle type via TraCI; write results/weights.json."""
from __future__ import annotations

import time
from pathlib import Path

from sim.util import ROOT, ensure_dirs, load_config, locate_sumo, save_json


def measure_idle_fuel(duration_s: float = 60.0) -> dict:
    cfg = load_config()
    ensure_dirs()
    sumo_home, sumo_bin = locate_sumo()

    net = ROOT / "results" / "networks" / "intersection.net.xml"
    vtypes = ROOT / "results" / "networks" / "vtypes.add.xml"
    if not net.exists():
        from sim.build_network import build
        build()

    # Minimal route: park each type on N_in by setting speed max 0 via traj
    raw = ROOT / "results" / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    rates = {}
    for vtype in cfg["vehicle_params"].keys():
        rou = raw / f"idle_{vtype}.rou.xml"
        rou.write_text(
            f"""<routes>
  <vehicle id="probe" type="{vtype}" depart="0" departPos="50" departSpeed="0">
    <route edges="N_in S_out"/>
    <stop lane="N_in_1" endPos="55" duration="{int(duration_s)+5}"/>
  </vehicle>
</routes>
""",
            encoding="utf-8",
        )
        tripinfo = raw / f"idle_{vtype}.tripinfo.xml"
        sumocfg = raw / f"idle_{vtype}.sumocfg"
        sumocfg.write_text(
            f"""<configuration>
  <input>
    <net-file value="{net}"/>
    <route-files value="{rou}"/>
    <additional-files value="{vtypes}"/>
  </input>
  <output>
    <tripinfo-output value="{tripinfo}"/>
  </output>
  <time>
    <begin value="0"/>
    <end value="{int(duration_s)+10}"/>
  </time>
  <processing>
    <device.emissions.probability value="1"/>
  </processing>
  <report>
    <no-warnings value="true"/>
    <no-step-log value="true"/>
  </report>
</configuration>
""",
            encoding="utf-8",
        )
        import traci

        traci.start([sumo_bin, "-c", str(sumocfg), "--duration-log.disable", "true"])
        fuel_samples = []
        t = 0
        while t < duration_s + 5:
            traci.simulationStep()
            t += 1
            try:
                if "probe" in traci.vehicle.getIDList():
                    # mg/s
                    fuel_samples.append(traci.vehicle.getFuelConsumption("probe"))
            except Exception:
                pass
        traci.close()
        # mean idle rate over last 40s of standing
        if len(fuel_samples) > 20:
            steady = fuel_samples[-40:] if len(fuel_samples) >= 40 else fuel_samples[10:]
            rates[vtype] = float(sum(steady) / len(steady))
        else:
            rates[vtype] = float(sum(fuel_samples) / max(len(fuel_samples), 1))

    car = rates.get("car") or 1.0
    w_type = {k: (v / car if car > 0 else 1.0) for k, v in rates.items()}
    out = {
        "idle_fuel_mg_per_s": rates,
        "w_type": w_type,
        "reference": "car",
        "duration_s": duration_s,
        "note": (
            "Measured via TraCI getFuelConsumption while stopped. "
            "auto-rickshaw uses a passenger-car emission class and two-wheeler uses LDV_G_EU4 "
            "when that class loads; these idle-fuel weights inherit those proxies. "
            "Simulation-based estimate; assumed traffic mix."
        ),
    }
    path = ROOT / "results" / "weights.json"
    save_json(path, out)
    print(f"Wrote {path}: {w_type}")
    return out


if __name__ == "__main__":
    measure_idle_fuel(60.0)
