"""Direction-aware categorisation (panel run 01, C3: 4/6 seats)."""

from datetime import date

import pytest

from cma import categorise, metrics, policy
from cma.models import Category, Statement, Transaction


def _t(desc, amount, tid="T1"):
    return Transaction(id=tid, date=date(2026, 3, 5), description=desc, amount=amount, balance=0.0)


@pytest.mark.parametrize(
    "desc, amount, expected",
    [
        ("DD BRIDGEPOINT FINANCE LOAN", -1150.0, Category.LOAN_REPAYMENT),
        ("BRIDGEPOINT FINANCE LOAN DRAWDOWN", 20000.0, Category.LOAN_DRAWDOWN),
        ("HMRC VAT", -6200.0, Category.HMRC_VAT),
        # v2 (the default) newly recognises it (docs/v2-newly-recognised.md); v1 gave OTHER.
        ("HMRC VAT REPAYMENT", 900.0, Category.HMRC_REPAYMENT),
        ("CARD ACQUIRER SETTLEMENT", 812.4, Category.REVENUE_CARD),
        ("CARD ACQUIRER SETTLEMENT CHARGEBACK", -60.0, Category.SUPPLIER),
        ("FPS CREDIT T WHITTAKER INV4411", 1800.0, Category.REVENUE_TRANSFER),
        ("STOCKHAVEN WHOLESALE INV7781", -4300.0, Category.SUPPLIER),
        ("UNPAID DD BREW SUPPLY CO DD", 0.0, Category.RETURNED_DD),
    ],
)
def test_direction_decides_category(desc, amount, expected):
    assert categorise.categorise_one(_t(desc, amount)) == expected


def test_drawdown_does_not_corrupt_debt_service_or_policy():
    """Seats' repro: a drawdown credit made M8 and DSCR negative and flipped policy to fail."""
    txns = []
    bal = 0.0
    n = 0
    for m in range(1, 13):
        for desc, amt in [
            ("CARD ACQUIRER SETTLEMENT", 9000.0),
            ("DD BRIDGEPOINT FINANCE LOAN", -1000.0),
            ("BREW SUPPLY CO DD", -4000.0),
        ]:
            n += 1
            bal += amt
            txns.append(Transaction(id=f"T{n:03d}", date=date(2026, m, 10), description=desc, amount=amt, balance=bal))
    n += 1
    bal += 20000.0
    txns.append(Transaction(id=f"T{n:03d}", date=date(2026, 6, 15), description="BRIDGEPOINT FINANCE LOAN DRAWDOWN", amount=20000.0, balance=bal))
    s = categorise.categorise(Statement(account_name="x", transactions=txns))
    m = {x.id: x for x in metrics.compute(s)}
    assert m["M8"].value == 12_000.0
    assert m["M9"].value == 12 * 5000.0
    assert m["M10"].value == 5.0
    assert m["M2"].value == 12 * 9000.0
    assert policy.evaluate(list(m.values())).rules[1].outcome.value == "pass"
