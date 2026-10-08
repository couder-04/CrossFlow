"""Parallel controllers regenerate the same routes file; it must never be seen half written."""
import multiprocessing as mp
import xml.etree.ElementTree as ET

import pytest

from sim.gen_demand import _mix_digest, generate
from sim.util import ROOT, load_config

SEED = 910002
ROUNDS = 150
DEMAND = ROOT / "results" / "demand"


def _hammer(barrier, rounds, queue):
    """Generate the same seed repeatedly, validating the file right after each write."""
    cfg = load_config()
    barrier.wait()
    bad = []
    texts = set()
    for _ in range(rounds):
        path = generate("balanced", SEED, cfg)
        try:
            text = path.read_text(encoding="utf-8")
            ET.fromstring(text)
            texts.add(text)
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{type(exc).__name__}: {exc}")
    queue.put((bad, texts))


def _watch(started, done, queue):
    """Poll the routes file while the writers run; every successful read must be valid XML."""
    path = DEMAND / f"balanced_seed{SEED}.rou.xml"
    started.wait()
    bad, reads = [], 0
    while not done.is_set():
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue  # not created yet
        reads += 1
        try:
            ET.fromstring(text)
        except ET.ParseError as exc:
            bad.append(f"partial file ({len(text)} bytes): {exc}")
    queue.put((bad, reads))


@pytest.fixture
def clean_seed():
    def wipe():
        for p in DEMAND.glob(f"balanced_seed{SEED}*"):
            p.unlink(missing_ok=True)

    wipe()
    yield
    wipe()


def test_two_processes_generating_the_same_seed_end_with_one_valid_file(clean_seed):
    ctx = mp.get_context("spawn")
    barrier, queue, watch_queue = ctx.Barrier(2), ctx.Queue(), ctx.Queue()
    started, done = ctx.Event(), ctx.Event()
    watcher = ctx.Process(target=_watch, args=(started, done, watch_queue))
    procs = [ctx.Process(target=_hammer, args=(barrier, ROUNDS, queue)) for _ in range(2)]
    watcher.start()
    for p in procs:
        p.start()
    started.set()
    results = [queue.get(timeout=120) for _ in procs]
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0
    done.set()
    watched_bad, watched_reads = watch_queue.get(timeout=30)
    watcher.join(timeout=30)
    assert watched_reads > 0
    assert watched_bad == [], f"a concurrent reader saw a partial file: {watched_bad[:3]}"

    bad = [b for r in results for b in r[0]]
    assert bad == [], f"a reader saw a partial or missing file: {bad[:3]}"
    texts = set().union(*(r[1] for r in results))
    assert len(texts) == 1, "processes produced different routes for the same seed"

    final = (DEMAND / f"balanced_seed{SEED}.rou.xml").read_text(encoding="utf-8")
    assert final == next(iter(texts))
    ET.fromstring(final)
    # No temp files left behind, and the metadata file is complete JSON.
    assert not list(DEMAND.glob(f"balanced_seed{SEED}*.tmp.*"))
    import json

    json.loads((DEMAND / f"balanced_seed{SEED}.rou.meta.json").read_text(encoding="utf-8"))


def test_mix_override_gets_its_own_file(clean_seed):
    cfg = load_config()
    mix_a = {"car": 0.7, "truck": 0.3}
    mix_b = {"car": 0.5, "truck": 0.5}
    a = generate("balanced", SEED, cfg, demand_scale={"N": 1.5}, mix_override=mix_a)
    b = generate("balanced", SEED, cfg, demand_scale={"N": 1.5}, mix_override=mix_b)
    a_again = generate("balanced", SEED, cfg, demand_scale={"N": 1.5}, mix_override=dict(mix_a))
    plain = generate("balanced", SEED, cfg, demand_scale={"N": 1.5})
    assert a != b and a == a_again and a != plain
    assert _mix_digest(mix_a) in a.name and _mix_digest(mix_b) in b.name
    assert _mix_digest({"truck": 0.3, "car": 0.7}) == _mix_digest(mix_a)  # order-independent
    assert a.read_text(encoding="utf-8") != b.read_text(encoding="utf-8")
