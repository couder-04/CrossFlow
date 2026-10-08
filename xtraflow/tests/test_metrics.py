from pathlib import Path

from sim.metrics import parse_tripinfo
from sim.util import ROOT, mg_to_litres


def test_parse_tripinfo_units(tmp_path):
    # 740000 mg fuel petrol => 1 L
    xml = """<?xml version="1.0"?>
<tripinfos>
  <tripinfo id="v0" vType="car" arrival="100" waitingTime="10" timeLoss="20" duration="100" waitingCount="2">
    <emissions fuel_abs="740000" CO2_abs="1000000"/>
  </tripinfo>
</tripinfos>
"""
    p = tmp_path / "t.xml"
    p.write_text(xml, encoding="utf-8")
    m = parse_tripinfo(p)
    assert m["n_completed"] == 1
    assert abs(m["total_fuel_L"] - 1.0) < 1e-9
    assert abs(m["total_CO2_kg"] - 1.0) < 1e-9
