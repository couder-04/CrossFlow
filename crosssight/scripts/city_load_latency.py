#!/usr/bin/env python3
"""City-scale multi-camera load + latency proof.

Simulates N cameras publishing PlateReads concurrently into Kafka, while:
  - a ClickHouse poller records ingest latency (publish wall → first CH visibility)
  - an alerts.v1 Kafka consumer records alert latency (publish wall → alert message)

Writes reports/city_load.{json,md} and reports/alert_latency.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "anpr_common" / "src"))

from anpr_common.config import get_settings
from anpr_common.schemas import Direction, PlateFormat, PlateRead, VehicleClass

REPORTS = ROOT / "reports"


def _pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = min(len(sorted_vals) - 1, max(0, round(p * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


def _summary(vals: list[float]) -> dict[str, float]:
    if not vals:
        return {"n": 0, "min": 0, "p50": 0, "p95": 0, "p99": 0, "max": 0, "mean": 0}
    s = sorted(vals)
    return {
        "n": len(s),
        "min": round(s[0], 4),
        "p50": round(_pct(s, 0.50), 4),
        "p95": round(_pct(s, 0.95), 4),
        "p99": round(_pct(s, 0.99), 4),
        "max": round(s[-1], 4),
        "mean": round(statistics.fmean(s), 4),
    }


@dataclass
class Published:
    event_id: str
    camera_id: str
    plate_norm: str
    publish_wall: float
    is_watchlist: bool


@dataclass
class SharedState:
    published: dict[str, Published] = field(default_factory=dict)  # event_id → pub
    by_camera: Counter = field(default_factory=Counter)
    ingest_lat: dict[str, float] = field(default_factory=dict)  # event_id → s
    alert_lat: list[float] = field(default_factory=list)
    alert_by_plate: dict[str, float] = field(default_factory=dict)
    wl_publish_wall: dict[str, float] = field(default_factory=dict)  # plate → earliest
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    stop_poll: asyncio.Event = field(default_factory=asyncio.Event)


async def load_cameras_watchlist(settings) -> tuple[list[str], list[str]]:
    import asyncpg

    conn = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        database=settings.postgres_db,
    )
    try:
        cams = [r["id"] for r in await conn.fetch("SELECT id FROM cameras ORDER BY id")]
        # Fresh probe plates (unique per run) so 10-minute alert dedupe cannot suppress them
        run_tag = int(time.time()) % 100000
        probes = [f"LT{(run_tag + i) % 100:02d}PZ{1000 + i}" for i in range(10)]
        for plate in probes:
            await conn.execute(
                """
                INSERT INTO watchlist (plate_norm, reason, severity, added_by)
                VALUES ($1, 'city-load-probe', 'high', 'loadtest')
                ON CONFLICT (plate_norm) DO NOTHING
                """,
                plate,
            )
    finally:
        await conn.close()
    return cams, probes


def make_plate(cam_idx: int, seq: int) -> str:
    states = ["MH", "KA", "DL", "TN", "GJ", "UP", "WB", "TS", "RJ", "AP"]
    st = states[(cam_idx + seq) % len(states)]
    # Unique per (cam, seq) so ingest dedup (10s plate+camera) never collapses load traffic
    return f"{st}{(cam_idx % 90) + 1:02d}Z{(seq % 9000) + 1000:04d}"


async def camera_publisher(
    camera_id: str,
    cam_idx: int,
    duration_s: float,
    rate_per_cam: float,
    watchlist: list[str],
    watchlist_every: int,
    producer,
    topic: str,
    state: SharedState,
    start_mono: float,
) -> int:
    interval = 1.0 / max(rate_per_cam, 0.01)
    seq = 0
    emitted = 0
    await asyncio.sleep((cam_idx % 25) * 0.008)
    while time.monotonic() - start_mono < duration_s:
        now = datetime.now(UTC)
        wall = time.time()
        use_wl = (
            bool(watchlist) and watchlist_every > 0 and seq > 0 and (seq % watchlist_every == 0)
        )
        plate = watchlist[(cam_idx + seq) % len(watchlist)] if use_wl else make_plate(cam_idx, seq)
        eid = str(uuid.uuid4())
        read = PlateRead(
            event_id=uuid.UUID(eid),
            camera_id=camera_id,
            ts=now,
            plate_raw=plate,
            plate_norm=plate,
            plate_valid=True,
            plate_format=PlateFormat.standard,
            confidence=0.96,
            char_conf=[0.96] * len(plate),
            lane=(seq % 2) + 1,
            direction=Direction.N,
            vehicle_class=VehicleClass.car,
            color="white",
            make="LoadTest",
            speed_kmh=30.0,
            source="simulator",
        )
        await producer.send_and_wait(topic, read.model_dump_json().encode(), key=plate.encode())
        pub = Published(eid, camera_id, plate, wall, use_wl)
        async with state.lock:
            state.published[eid] = pub
            state.by_camera[camera_id] += 1
            if use_wl:
                prev = state.wl_publish_wall.get(plate)
                if prev is None or wall < prev:
                    state.wl_publish_wall[plate] = wall
        emitted += 1
        seq += 1
        jitter = interval * (0.85 + 0.3 * ((seq * 13 + cam_idx) % 10) / 10.0)
        await asyncio.sleep(jitter)
    return emitted


async def ingest_poller(settings, state: SharedState) -> None:
    """Continuously poll ClickHouse for newly published event_ids."""
    import clickhouse_connect

    ch = await asyncio.to_thread(
        clickhouse_connect.get_client,
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password or "",
        database=settings.clickhouse_db,
    )
    idle_rounds = 0
    while True:
        async with state.lock:
            pending = [eid for eid in state.published if eid not in state.ingest_lat]
        if not pending:
            if state.stop_poll.is_set():
                idle_rounds += 1
                if idle_rounds >= 3:
                    break
            await asyncio.sleep(0.25)
            continue
        idle_rounds = 0
        found: list[str] = []
        for i in range(0, len(pending), 300):
            chunk = pending[i : i + 300]
            in_list = ", ".join(f"'{e}'" for e in chunk)

            def _q(
                sql: str = (
                    "SELECT toString(event_id) FROM anpr_reads "
                    f"WHERE toString(event_id) IN ({in_list})"
                ),
            ):
                return ch.query(sql).result_rows

            try:
                rows = await asyncio.to_thread(_q)
            except Exception:  # noqa: BLE001
                rows = []
            for (eid,) in rows:
                found.append(eid)
        wall = time.time()
        async with state.lock:
            for eid in found:
                if eid in state.published and eid not in state.ingest_lat:
                    state.ingest_lat[eid] = wall - state.published[eid].publish_wall
        await asyncio.sleep(0.25)


async def alert_consumer(settings, state: SharedState) -> None:
    """Listen on alerts.v1 for watchlist alerts; latency = receive_wall - plate publish_wall."""
    import orjson
    from aiokafka import AIOKafkaConsumer

    consumer = AIOKafkaConsumer(
        settings.topic_alerts,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id=f"loadtest-alerts-{uuid.uuid4().hex[:8]}",
        auto_offset_reset="latest",
        enable_auto_commit=True,
    )
    await consumer.start()
    try:
        while not state.stop_poll.is_set():
            try:
                msg = await asyncio.wait_for(consumer.getone(), timeout=0.5)
            except TimeoutError:
                continue
            except Exception:  # noqa: BLE001, S112
                continue
            wall = time.time()
            try:
                data = orjson.loads(msg.value)
            except Exception:  # noqa: BLE001, S112
                continue
            if data.get("type") != "watchlist":
                continue
            plate = (data.get("plate_norm") or "").upper()
            # Prefer evidence event_id for precise publish→alert latency
            reads = (data.get("evidence") or {}).get("reads") or []
            eid = None
            if reads and isinstance(reads[0], dict):
                eid = reads[0].get("event_id")
            async with state.lock:
                pub_wall = None
                if eid and eid in state.published:
                    pub_wall = state.published[eid].publish_wall
                elif plate in state.wl_publish_wall:
                    # fallback: most recent publish for plate (not earliest)
                    pub_wall = state.wl_publish_wall.get(plate)
                if pub_wall is None:
                    continue
                key = eid or plate
                if key in state.alert_by_plate:
                    continue
                lat = max(0.0, wall - pub_wall)
                state.alert_by_plate[key] = lat
                state.alert_lat.append(lat)
    finally:
        await consumer.stop()


def write_reports(payload: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "city_load.json").write_text(json.dumps(payload, indent=2) + "\n")
    raw = payload["alerts"].get("latencies_raw") or []
    (REPORTS / "alert_latency.json").write_text(
        json.dumps(
            {
                "latencies_s": raw,
                "p50": payload["alerts"]["latency_s"]["p50"],
                "p95": payload["alerts"]["latency_s"]["p95"],
                "p99": payload["alerts"]["latency_s"]["p99"],
                "n": payload["alerts"]["latency_s"]["n"],
                "generated_at": payload["generated_at"],
            },
            indent=2,
        )
        + "\n"
    )
    cfg = payload["config"]
    pub = payload["publish"]
    ing = payload["ingest"]
    al = payload["alerts"]
    pc = payload["pass_criteria"]
    md = f"""# City multi-camera load & latency proof

**Overall: {"PASS" if payload["overall_pass"] else "FAIL"}**

- Cameras in parallel: **{cfg["cameras"]}**
- Duration: **{cfg["duration_s"]}s** @ **{cfg["rate_per_cam"]}/cam/s** (target ≈{cfg["target_aggregate_rps"]} reads/s)
- Published: **{pub["total"]}** reads (**{pub["throughput_rps"]}** rps)
- Ingest recovery: **{ing["recovered"]}/{pub["total"]}** ({ing["recovery_rate"] * 100:.1f}%)
- Cameras with ingested data: **{ing["cameras_with_data"]}/{cfg["cameras"]}**
- Camera fairness CV: **{pub["fairness_cv"]}** (0 = perfectly even)

## Latency (measured live during the run)

| Pipeline | n | p50 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|
| Ingest Kafka→ClickHouse | {ing["latency_s"]["n"]} | {ing["latency_s"]["p50"]}s | {ing["latency_s"]["p95"]}s | {ing["latency_s"]["p99"]}s | {ing["latency_s"]["max"]}s |
| Alert watchlist→alerts.v1 | {al["latency_s"]["n"]} | {al["latency_s"]["p50"]}s | {al["latency_s"]["p95"]}s | {al["latency_s"]["p99"]}s | {al["latency_s"]["max"]}s |

## Pass criteria (spec: alert p95 < 3s)

- alert p95 < 3s: **{pc["alert_p95_lt_3s"]}**
- ingest p95 < 5s: **{pc["ingest_p95_lt_5s"]}**
- ingest recovery ≥ 95%: **{pc["ingest_recovery_ge_95pct"]}**
- all cameras recovered: **{pc["all_cameras_recovered"]}**
- camera fairness CV < 0.35: **{pc["camera_fairness_cv_lt_0_35"]}**

## Method

Each of {cfg["cameras"]} cameras runs as its own asyncio task and publishes concurrently.
Ingest latency is first ClickHouse visibility during the run (not post-hoc).
Alert latency is Kafka `alerts.v1` receive time minus watchlist publish wall clock.
"""
    (REPORTS / "city_load.md").write_text(md)


async def reset_alerts_consumer_lag(settings) -> None:
    """Seek anpr-alerts group to tip so loadtest isn't queued behind a huge backlog."""
    import subprocess

    cmds = [
        [
            "docker",
            "exec",
            "crosssight-redpanda-1",
            "rpk",
            "group",
            "seek",
            "anpr-alerts",
            "--to",
            "end",
            "--topics",
            settings.topic_reads,
        ]
    ]
    for cmd in cmds:
        try:
            await asyncio.to_thread(
                subprocess.run, cmd, check=False, capture_output=True, text=True, timeout=30
            )
        except Exception as exc:  # noqa: BLE001
            print(f"warn: could not seek consumer group ({exc})")


async def main() -> int:
    parser = argparse.ArgumentParser(description="City multi-cam load + latency")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument(
        "--rate",
        type=float,
        default=0.5,
        help="reads/cam/sec (0.5 ≈ busy city intersection; 60 cams → ~30 rps)",
    )
    parser.add_argument("--cameras", type=int, default=None)
    parser.add_argument("--watchlist-every", type=int, default=8)
    parser.add_argument("--drain", type=float, default=30.0, help="seconds to drain after publish")
    parser.add_argument(
        "--no-seek",
        action="store_true",
        help="Do not seek alerts consumer group to tip before the run",
    )
    args = parser.parse_args()

    from aiokafka import AIOKafkaProducer

    settings = get_settings()
    cameras, watchlist = await load_cameras_watchlist(settings)
    if not cameras:
        print("No cameras — run make seed first", file=sys.stderr)
        return 2
    if args.cameras:
        cameras = cameras[: args.cameras]

    if not args.no_seek:
        print("Seeking anpr-alerts consumer group to tip (clear backlog)…")
        # Alerts must be stopped briefly for a reliable seek; try online seek first.
        await reset_alerts_consumer_lag(settings)
        await asyncio.sleep(2.0)

    print(f"Watchlist probes for this run: {watchlist}")
    # Allow watchlist reload loop (5s) to pick up probes
    await asyncio.sleep(6.0)

    state = SharedState()
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap,
        linger_ms=5,
        acks="all",
    )
    await producer.start()

    poll_task = asyncio.create_task(ingest_poller(settings, state))
    alert_task = asyncio.create_task(alert_consumer(settings, state))
    await asyncio.sleep(1.0)  # let alert consumer join

    start_mono = time.monotonic()
    wall0 = time.time()
    print(
        f"Publishing: cameras={len(cameras)} rate/cam={args.rate}/s "
        f"duration={args.duration}s ≈{len(cameras) * args.rate:.0f} rps aggregate"
    )
    try:
        counts = await asyncio.gather(
            *[
                camera_publisher(
                    cam_id,
                    idx,
                    args.duration,
                    args.rate,
                    watchlist,
                    args.watchlist_every,
                    producer,
                    settings.topic_reads,
                    state,
                    start_mono,
                )
                for idx, cam_id in enumerate(cameras)
            ]
        )
    finally:
        await producer.stop()

    publish_elapsed = time.time() - wall0
    total = sum(counts)
    throughput = total / publish_elapsed if publish_elapsed else 0.0
    print(
        f"Published {total} in {publish_elapsed:.2f}s ({throughput:.1f}/s) — draining {args.drain}s…"
    )
    await asyncio.sleep(args.drain)
    state.stop_poll.set()
    # allow poller to finish remaining
    try:
        await asyncio.wait_for(poll_task, timeout=30)
    except TimeoutError:
        poll_task.cancel()
    alert_task.cancel()
    try:
        await alert_task
    except asyncio.CancelledError:
        pass

    async with state.lock:
        ingest_vals = list(state.ingest_lat.values())
        alert_lats = list(state.alert_lat)
        cam_counts = dict(state.by_camera)
        cams_recovered = len({state.published[e].camera_id for e in state.ingest_lat})

    counts_list = [cam_counts.get(c, 0) for c in cameras]
    mean_c = statistics.fmean(counts_list) if counts_list else 0
    fairness_cv = (
        (statistics.pstdev(counts_list) / mean_c) if mean_c and len(counts_list) > 1 else 0.0
    )
    recovery = len(ingest_vals) / total if total else 0.0
    ingest_summary = _summary(ingest_vals)
    alert_summary = _summary(alert_lats)

    alert_ok = alert_summary["n"] > 0 and alert_summary["p95"] < 3.0
    ingest_ok = ingest_summary["n"] > 0 and ingest_summary["p95"] < 5.0
    recovery_ok = recovery >= 0.95
    all_cams_ok = cams_recovered >= len(cameras)
    fairness_ok = fairness_cv < 0.35

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "config": {
            "cameras": len(cameras),
            "duration_s": args.duration,
            "rate_per_cam": args.rate,
            "target_aggregate_rps": round(len(cameras) * args.rate, 2),
            "watchlist_every": args.watchlist_every,
            "drain_s": args.drain,
        },
        "publish": {
            "total": total,
            "elapsed_s": round(publish_elapsed, 3),
            "throughput_rps": round(throughput, 2),
            "per_camera_min": min(counts_list) if counts_list else 0,
            "per_camera_max": max(counts_list) if counts_list else 0,
            "per_camera_mean": round(mean_c, 2),
            "fairness_cv": round(fairness_cv, 4),
        },
        "ingest": {
            "recovered": len(ingest_vals),
            "recovery_rate": round(recovery, 4),
            "latency_s": ingest_summary,
            "cameras_with_data": cams_recovered,
        },
        "alerts": {
            "samples": alert_summary["n"],
            "latency_s": alert_summary,
            "latencies_raw": [round(x, 4) for x in alert_lats],
        },
        "pass_criteria": {
            "alert_p95_lt_3s": alert_ok,
            "ingest_p95_lt_5s": ingest_ok,
            "ingest_recovery_ge_95pct": recovery_ok,
            "all_cameras_recovered": all_cams_ok,
            "camera_fairness_cv_lt_0_35": fairness_ok,
        },
        "overall_pass": alert_ok and ingest_ok and recovery_ok and all_cams_ok and fairness_ok,
    }
    write_reports(payload)
    print(
        f"ingest p95={ingest_summary['p95']}s recovery={recovery:.1%} cams={cams_recovered}/{len(cameras)} | "
        f"alert p95={alert_summary['p95']}s n={alert_summary['n']} | fairness_cv={fairness_cv:.3f}"
    )
    print(f"Overall: {'PASS' if payload['overall_pass'] else 'FAIL'}")
    print(f"Wrote {REPORTS / 'city_load.md'}")
    return 0 if payload["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
