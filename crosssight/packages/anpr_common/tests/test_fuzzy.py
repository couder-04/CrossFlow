"""Fuzzy matching tests."""

from anpr_common.fuzzy import candidates, weighted_edit_distance


def test_identical_zero_cost():
    assert weighted_edit_distance("MH12DE1433", "MH12DE1433") == 0.0


def test_confusion_sub_cheap():
    # O vs 0
    d = weighted_edit_distance("MH12DO1433", "MH12D01433")
    assert abs(d - 0.3) < 1e-9


def test_normal_sub_full_cost():
    d = weighted_edit_distance("MH12DE1433", "MH12DE1434")
    assert abs(d - 1.0) < 1e-9


def test_candidates_within_budget():
    pool = ["MH12DE1433", "MH12DE1434", "KA03MP1234", "MH12D01433"]
    hits = candidates("MH12DO1433", pool=pool, max_cost=1.0)
    norms = [h[0] for h in hits]
    assert "MH12DE1433" in norms or "MH12D01433" in norms
    assert all(c <= 1.0 for _, c in hits)


def test_candidates_sorted_by_cost():
    pool = ["AAAA0001", "AAAA0002", "AAAA0O01"]
    hits = candidates("AAAA0001", pool=pool, max_cost=1.0)
    costs = [c for _, c in hits]
    assert costs == sorted(costs)
