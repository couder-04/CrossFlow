from sim.gen_demand import generate
from sim.util import load_config


def test_same_seed_identical_demand(tmp_path):
    cfg = load_config()
    a = generate("balanced", 42, cfg)
    text_a = a.read_text(encoding="utf-8")
    b = generate("balanced", 42, cfg)
    text_b = b.read_text(encoding="utf-8")
    assert text_a == text_b


def test_different_seed_different_demand():
    cfg = load_config()
    a = generate("balanced", 1, cfg).read_text(encoding="utf-8")
    b = generate("balanced", 2, cfg).read_text(encoding="utf-8")
    assert a != b
