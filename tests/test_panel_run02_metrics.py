"""Regression tests for the metric and categoriser findings of panel run 02 (D1, D4, D8-D11, D16, D17).

Each test is the seats' reproduction, shrunk to the smallest statement that shows it.
"""

from datetime import date, timedelta

import pytest

from cma import categorise, metrics, policy
from cma.models import REVENUE_CATEGORIES, RuleOutcome, Statement, Transaction


def statement(lines, opening=0.0):
    """lines: (date, description, amount); balances are walked from `opening`."""
    bal, txns = opening, []
    for i, (d, desc, amt) in enumerate(lines, start=1):
        bal = round(bal + amt, 2)
        txns.append(Transaction(id=f"T{i:03d}", date=d, description=desc, amount=amt, balance=bal))
    return categorise.categorise(Statement(account_name="x", transactions=txns))


def by_id(s, **kw):
    return {m.id: m for m in metrics.compute(s, **kw)}


def monthly(desc_amounts, months=12, year=2025, start_month=10, day=10):
    out = []
    for k in range(months):
        y, m = divmod(start_month - 1 + k, 12)
        for desc, amt in desc_amounts:
            out.append((date(year + y, m + 1, day), desc, amt))
    return out


# D1: no debt service gave M10 = 99.0, so a loss-maker passed the only fail-level cash rule.

def test_no_debt_means_no_cover_figure_and_a_loss_maker_fails():
    s = statement(monthly([("CARD ACQUIRER SETTLEMENT", 6000.0), ("BREW SUPPLY CO DD", -10000.0)]), opening=200000)
    m = by_id(s, vat_stagger=3)
    assert m["M8"].value == 0
    assert m["M10"].value is None and "no existing debt" in m["M10"].method
    assert m["M9"].value == -48000.0
    result = policy.evaluate(list(m.values()))
    outcomes = {r.rule_id: r.outcome for r in result.rules}
    assert outcomes["R2"] == RuleOutcome.REFER  # missing, not a pass
    assert outcomes["R8"] == RuleOutcome.FAIL
    assert result.overall == RuleOutcome.FAIL


def test_cover_is_after_owner_drawings():
    s = statement(monthly([
        ("CARD ACQUIRER SETTLEMENT", 10000.0), ("BREW SUPPLY CO DD", -6000.0),
        ("DD HOLLOWAY BRIDGE FINANCE LOAN", -1000.0), ("TFR OWNER DRAWINGS", -2500.0),
    ]))
    m = by_id(s)
    assert m["M10"].value == pytest.approx((4000 - 2500) / 1000)


# D4: every inbound transfer counted as revenue; a round trip lifted DSCR 1.43 -> 6.65.

@pytest.mark.parametrize("desc", [
    "FPS CREDIT A DIRECTOR LOAN ACCOUNT", "FPS CREDIT J SMITH", "BACS CREDIT HMRC VAT REPAYMENT",
    "FPS CREDIT CAPITAL INJECTION", "FPS CREDIT SAVINGS ACCOUNT",
])
def test_transfers_in_without_invoice_are_not_revenue(desc):
    cat = categorise.categorise_one(
        Transaction(id="T", date=date(2026, 1, 1), description=desc, amount=5000.0, balance=0)
    )
    assert cat not in REVENUE_CATEGORIES  # OTHER, or LOAN_DRAWDOWN for a director's loan


def test_round_trip_does_not_lift_cover_and_shows_as_unclassified():
    base = [("FPS CREDIT OAKFIELD PROPERTIES INV1001", 10000.0), ("PIPEWELL TRADE SUPPLIES DD", -7000.0),
            ("DD HOLLOWAY BRIDGE FINANCE LOAN", -1500.0)]
    before = by_id(statement(monthly(base)))
    gamed = by_id(statement(monthly([*base, ("FPS CREDIT J SMITH", 8000.0), ("FPS DEBIT J SMITH", -8000.0)])))
    assert gamed["M2"].value == before["M2"].value
    assert gamed["M10"].value == before["M10"].value
    assert gamed["M16"].value > 40
    assert {r.rule_id: r.outcome for r in policy.evaluate(list(gamed.values())).rules}["R9"] == RuleOutcome.REFER


# D8: the payer key kept invoice numbers, so one payer at 90% read as 10%.

def test_concentration_ignores_invoice_numbers():
    lines = [(date(2026, 1, 1) + timedelta(days=i), f"FPS CREDIT OAKFIELD PROPERTIES INV{1000 + i}", 900.0)
             for i in range(9)]
    lines.append((date(2026, 1, 20), "FPS CREDIT T WHITTAKER INV2000", 900.0))
    m = by_id(statement(lines))
    assert m["M15"].value == 90.0


def test_card_revenue_concentration_is_not_knowable_and_policy_says_so():
    m = by_id(statement(monthly([("CARD ACQUIRER SETTLEMENT", 5000.0)])))
    assert m["M15"].value is None
    r7 = {r.rule_id: r for r in policy.evaluate(list(m.values())).rules}["R7"]
    assert r7.outcome == RuleOutcome.PASS and "cannot be computed" in r7.detail


# D9: M6 counted dates with a transaction, not days that closed overdrawn.

def test_days_overdrawn_counts_calendar_days():
    s = statement([
        (date(2026, 1, 1), "CARD ACQUIRER SETTLEMENT", 100.0),
        (date(2026, 1, 2), "BREW SUPPLY CO DD", -500.0),      # overdrawn from the 2nd...
        (date(2026, 1, 28), "CARD ACQUIRER SETTLEMENT", 900.0),  # ...until the 28th
    ])
    assert by_id(s)["M6"].value == 26


# D10: the VAT window fitted one stagger group; no VAT at all passed.

def _vat_year(pay_day):
    """Stagger 1 business: quarters end Mar/Jun/Sep/Dec, due 7 May/Aug/Nov/Feb."""
    lines = monthly([("CARD ACQUIRER SETTLEMENT", 5000.0)], months=12, year=2025, start_month=10, day=1)
    for y, m in [(2025, 11), (2026, 2), (2026, 5), (2026, 8)]:
        lines.append((date(y, m, pay_day), "HMRC VAT", -1500.0))
    return sorted(lines, key=lambda x: x[0])


def test_on_time_stagger_1_payer_is_not_late():
    m = by_id(statement(_vat_year(7)), vat_stagger=1)
    assert m["M13"].value == 0


def test_late_payer_is_late_and_wrong_stagger_is_visible():
    assert by_id(statement(_vat_year(25)), vat_stagger=1)["M13"].value == 4
    assert by_id(statement(_vat_year(7)), vat_stagger=3)["M13"].value > 0


def test_unknown_stagger_or_no_vat_paid_is_not_a_pass():
    no_vat = statement(monthly([("CARD ACQUIRER SETTLEMENT", 5000.0)], day=1))
    assert by_id(no_vat)["M13"].value is None
    assert by_id(no_vat, vat_stagger=1)["M13"].value >= 3
    rules = {r.rule_id: r.outcome for r in policy.evaluate(list(by_id(no_vat).values())).rules}
    assert rules["R5"] == RuleOutcome.REFER


# D11: a bounced and re-presented DD counted twice in debt service.

def test_returned_and_represented_loan_payment_counts_once():
    s = statement([
        (date(2026, 1, 1), "CARD ACQUIRER SETTLEMENT", 5000.0),
        (date(2026, 1, 5), "DD HOLLOWAY BRIDGE FINANCE LOAN", -1150.0),
        (date(2026, 1, 7), "UNPAID DD HOLLOWAY BRIDGE FINANCE LOAN", 1150.0),
        (date(2026, 1, 7), "UNPAID ITEM FEE", -15.0),
        (date(2026, 1, 12), "DD HOLLOWAY BRIDGE FINANCE LOAN", -1150.0),
    ])
    m = by_id(s)
    assert m["M7"].value == 1
    assert m["M8"].value == 1150.0
    assert m["M9"].value == 5000.0 - 15.0  # the returned credit is not operating income
    assert s.transactions[2].reverses == "T002"


def test_returned_vat_payment_counts_once():
    s = statement([
        (date(2026, 1, 1), "CARD ACQUIRER SETTLEMENT", 9000.0),
        (date(2026, 1, 7), "HMRC VAT", -2000.0),
        (date(2026, 1, 9), "UNPAID DD HMRC VAT", 2000.0),
        (date(2026, 1, 14), "HMRC VAT", -2000.0),
    ])
    assert by_id(s)["M12"].value == 2000.0


# D16 / D17: lowest balance and months covered.

def test_months_covered_is_the_period_length_and_counts_dormant_months():
    lines = [(date(2025, 10, 25), "CARD ACQUIRER SETTLEMENT", 100.0)]
    lines += [(date(2025 + (m > 12), (m - 1) % 12 + 1, 10), "CARD ACQUIRER SETTLEMENT", 1000.0)
              for m in range(11, 21) if m != 15]  # March 2026 has no transactions
    lines.append((date(2026, 9, 30), "CARD ACQUIRER SETTLEMENT", 100.0))
    m = by_id(statement(lines))
    assert m["M1"].value == 11  # 25 Oct to 30 Sep is 341 days: 11 months to the nearest month
    days = (date(2026, 9, 30) - date(2025, 10, 25)).days + 1
    assert m["M3"].value == pytest.approx(m["M2"].value / (days / (365.25 / 12)), abs=0.01)


def test_lowest_balance_is_the_lowest_running_balance():
    s = statement([(date(2026, 1, 1), "BREW SUPPLY CO DD", -300.0),
                   (date(2026, 1, 2), "CARD ACQUIRER SETTLEMENT", 50.0)], opening=100.0)
    assert by_id(s)["M5"].value == -200.0
