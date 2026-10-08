import pytest

from sim.util import kmh_to_ms, mg_to_kg, mg_to_litres


def test_mg_to_litres_petrol():
    # 740000 mg = 0.74 kg = 1 L at 0.74 kg/L
    assert mg_to_litres(740_000, 0.74) == pytest.approx(1.0, rel=1e-9)


def test_mg_to_litres_diesel():
    assert mg_to_litres(840_000, 0.84) == pytest.approx(1.0, rel=1e-9)


def test_mg_to_kg():
    assert mg_to_kg(1_000_000) == pytest.approx(1.0)


def test_kmh_to_ms():
    assert kmh_to_ms(36) == pytest.approx(10.0)


def test_density_positive():
    with pytest.raises(ValueError):
        mg_to_litres(100, 0)
