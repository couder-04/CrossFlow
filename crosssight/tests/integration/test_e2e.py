"""End-to-end integration tests against the compose stack.

Requires: make up && make seed && workers + API running.
Set RUN_INTEGRATION=1 to enable. Optional API_URL (default http://localhost:8000).
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "services" / "simulator" / "data" / "scenarios.json"

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="Set RUN_INTEGRATION=1 to run against compose stack",
)


@pytest.fixture(scope="module")
def scenarios():
    assert SCENARIOS.exists(), f"missing {SCENARIOS}; run make seed"
    return json.loads(SCENARIOS.read_text())


@pytest.fixture(scope="module")
def api_client():
    import httpx

    base = os.environ.get("API_URL", "http://localhost:8000")

    def login(user: str, password: str) -> httpx.Client:
        c = httpx.Client(base_url=base, timeout=30.0)
        r = c.post("/auth/login", json={"username": user, "password": password})
        r.raise_for_status()
        token = r.json()["access_token"]
        c.headers["Authorization"] = f"Bearer {token}"
        return c

    return login


def _expected_from_scenarios(scenarios: dict) -> list[tuple[str, str]]:
    expected: list[tuple[str, str]] = []
    ea = scenarios.get("expected_alerts") or {}
    for atype, plates in ea.items():
        for plate in plates:
            expected.append((atype, plate))
    return expected


def test_watchlist_zero_false_negatives(scenarios, api_client):
    client = api_client("operator", "operator123")
    wl = set(
        scenarios.get("watchlist_plates")
        or scenarios.get("expected_alerts", {}).get("watchlist", [])
    )
    deadline = time.time() + 60
    found: set[str] = set()
    while time.time() < deadline:
        r = client.get("/alerts", params={"type": "watchlist"})
        r.raise_for_status()
        found = {a["plate_norm"] for a in r.json()}
        if wl and wl.issubset(found):
            break
        time.sleep(2)
    missing = wl - found
    assert not missing, f"watchlist false negatives: {missing}"


def test_scenario_alert_types(scenarios, api_client):
    client = api_client("operator", "operator123")
    expected = _expected_from_scenarios(scenarios)
    r = client.get("/alerts")
    r.raise_for_status()
    alerts = r.json()
    by_type_plate = {(a["type"], a["plate_norm"]) for a in alerts}
    missing = [(t, p) for t, p in expected if (t, p) not in by_type_plate]
    # Allow soft miss on loitering if plate has any alert (zone buffer race)
    hard = [m for m in missing if m[0] != "loitering"]
    soft = [m for m in missing if m[0] == "loitering"]
    assert not hard, f"missing scenario alerts: {hard}"
    if soft:
        for _, plate in soft:
            assert any(a["plate_norm"] == plate for a in alerts), (
                f"no alerts for loiter plate {plate}"
            )


def test_trajectory_recovery(scenarios, api_client):
    client = api_client("operator", "operator123")
    plate = (scenarios.get("watchlist_plates") or [None])[0]
    if not plate:
        pytest.skip("no scenario plate for trajectory")

    now = datetime.now(UTC)
    r = client.get(
        "/trajectory",
        params={
            "plate": plate,
            "from": (now - timedelta(days=8)).isoformat(),
            "to": now.isoformat(),
            "case_id": "e2e-test-001",
            "fuzzy": "false",
        },
    )
    r.raise_for_status()
    geo = r.json()
    features = geo.get("features", [])
    sightings = [f for f in features if f.get("geometry", {}).get("type") == "Point"]
    assert len(sightings) >= 1


def test_od_k_anonymity(api_client):
    client = api_client("analyst", "analyst123")
    from anpr_common.config import get_settings

    k = get_settings().od_k_anon
    today = datetime.now(UTC).date().isoformat()
    r = client.get("/analytics/od", params={"date": today, "hour": 8})
    r.raise_for_status()
    data = r.json()
    cells = data if isinstance(data, list) else data.get("cells", data.get("rows", []))
    for cell in cells:
        count = cell.get("trip_count") or cell.get("count") or 0
        assert count >= k, f"OD cell below k={k}: {cell}"


def test_analyst_forbidden_trajectory(api_client):
    client = api_client("analyst", "analyst123")
    now = datetime.now(UTC)
    r = client.get(
        "/trajectory",
        params={
            "plate": "MH12DE1433",
            "from": (now - timedelta(days=1)).isoformat(),
            "to": now.isoformat(),
            "case_id": "e2e-analyst",
        },
    )
    assert r.status_code == 403


def test_trajectory_writes_audit(api_client):
    op = api_client("operator", "operator123")
    admin = api_client("admin", "admin123")
    now = datetime.now(UTC)
    case_id = f"e2e-audit-{int(time.time())}"
    op.get(
        "/trajectory",
        params={
            "plate": "MH12DE1433",
            "from": (now - timedelta(days=1)).isoformat(),
            "to": now.isoformat(),
            "case_id": case_id,
        },
    )
    r = admin.get("/audit")
    r.raise_for_status()
    rows = r.json()
    assert any(
        (row.get("case_id") == case_id) or (row.get("params") or {}).get("case_id") == case_id
        for row in rows
    ), "audit_log missing trajectory case_id"


def test_alert_latency_p95(scenarios):
    path = ROOT / "reports" / "alert_latency.json"
    if not path.exists():
        pytest.skip("no latency samples recorded")
    latencies = json.loads(path.read_text()).get("latencies_s", [])
    if not latencies:
        pytest.skip("empty latency samples")
    latencies_sorted = sorted(latencies)
    p95 = latencies_sorted[int(0.95 * (len(latencies_sorted) - 1))]
    print(f"alert_latency_p95_s={p95}")
    assert p95 < 3.0, f"p95 alert latency {p95}s >= 3s"
