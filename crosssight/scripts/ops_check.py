"""Production ops check: worker health + Kafka consumer lag.

Exit 0 if healthy; 1 if workers down / lag above threshold.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.error
import urllib.request
from typing import Any


def _http_ok(url: str, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode()
            return resp.status == 200, body
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, str(exc)


def _rpk_lag(group: str, container: str = "crosssight-redpanda-1") -> dict[str, Any]:
    try:
        out = subprocess.check_output(
            ["docker", "exec", container, "rpk", "group", "describe", group],
            text=True,
            timeout=20,
        )
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as exc:
        return {"error": str(exc), "total_lag": None}
    total = None
    members = None
    state = None
    for line in out.splitlines():
        if line.startswith("TOTAL-LAG"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                total = int(parts[1])
        if line.startswith("MEMBERS"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                members = int(parts[1])
        if line.startswith("STATE"):
            parts = line.split()
            if len(parts) >= 2:
                state = parts[1]
    return {"total_lag": total, "members": members, "state": state, "raw": out}


def main() -> int:
    p = argparse.ArgumentParser(description="ANPR ops health + lag check")
    p.add_argument("--max-lag", type=int, default=5000, help="Fail if any group lag exceeds this")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    checks = {
        "ingest": "http://localhost:8081/healthz",
        "analytics": "http://localhost:8082/healthz",
        "alerts": "http://localhost:8083/healthz",
        "api": "http://localhost:8000/health",
    }
    ok_api, _ = _http_ok(checks["api"])
    if not ok_api:
        checks["api"] = "http://localhost:8002/health"

    health: dict[str, Any] = {}
    all_ok = True
    for name, url in checks.items():
        ok, body = _http_ok(url)
        health[name] = {"ok": ok, "url": url, "body": body[:200]}
        if name != "api" and not ok:
            all_ok = False

    groups = {}
    for g in ("anpr-ingest", "anpr-alerts", "anpr-analytics"):
        info = _rpk_lag(g)
        groups[g] = info
        lag = info.get("total_lag")
        if lag is not None and lag > args.max_lag:
            all_ok = False

    report = {"healthy": all_ok, "health": health, "lag": groups, "max_lag": args.max_lag}
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Overall: {'OK' if all_ok else 'DEGRADED'}")
        for name, h in health.items():
            print(f"  {name}: {'up' if h['ok'] else 'DOWN'} ({h['url']})")
        for g, info in groups.items():
            print(
                f"  {g}: lag={info.get('total_lag')} members={info.get('members')} "
                f"state={info.get('state')}"
            )
            if info.get("total_lag") is not None and info["total_lag"] > args.max_lag:
                print(f"    !! lag above --max-lag={args.max_lag}")
                print(
                    "    clear with: docker exec crosssight-redpanda-1 rpk group seek "
                    f"{g} --to end --topics anpr.reads.v1"
                )
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
