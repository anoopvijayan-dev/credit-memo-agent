from datetime import date

from cma import synth


def test_generation_is_deterministic():
    a, ta = synth.generate("cafe", "stretched", seed=3, end=date(2026, 9, 30))
    b, tb = synth.generate("cafe", "stretched", seed=3, end=date(2026, 9, 30))
    assert a == b
    assert ta.total_revenue == tb.total_revenue


def test_profiles_order_risk():
    _, good = synth.generate("trades", "good", end=date(2026, 9, 30))
    _, poor = synth.generate("trades", "poor", end=date(2026, 9, 30))
    assert good.total_revenue > poor.total_revenue
    assert good.returned_dd_count < poor.returned_dd_count
    assert good.lowest_balance > poor.lowest_balance


def test_balance_walk_is_consistent():
    rows, truth = synth.generate("ecommerce", "stretched", end=date(2026, 9, 30))
    bal = synth.PROFILES["stretched"]["opening_balance"]
    for r in rows:
        bal = round(bal + float(r["amount"]), 2)
        assert abs(bal - float(r["balance"])) < 0.01
    assert len(truth.months) == 12
