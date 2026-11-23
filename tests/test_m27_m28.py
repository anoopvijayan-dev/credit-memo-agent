"""T07: average end-of-day balance (M27, R12) and debt service cover including the proposed
facility (M28, R13), on hand-computed fixtures with carried-over days.

M27 averages the close of every calendar day from the first line to the last, a day with no
lines carrying the close before it, and cites the line that set each of those closes (P2).
M28 adds the proposed monthly repayment, for the exact length of the period in months (as M3
counts months), to existing debt service. With no proposed repayment, as in `cma run`, which
has no case profile, it is n/a with the reason and cites nothing (R13).
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cma import metrics
from cma.cli import app
from cma.models import Category, Statement, Transaction

C = Category


def _st(rows) -> Statement:
    return Statement(account_name="x", transactions=[
        Transaction(id=i, date=d, description=i, amount=a, balance=b, category=c) for i, d, a, b, c in rows])


def _m(s: Statement, **kw) -> dict:
    return {m.id: m for m in metrics.compute(s, **kw)}


# Five days, 1 to 5 January. Day 1 closes on L2 (L1 is not a close); days 2 and 3 have no
# lines and carry L2's 70.00; day 4 closes on L3 at -30.00 and day 5 on L4 at 20.00.
# Closes: 70 + 70 + 70 - 30 + 20 = 200.00 over 5 days = 40.00.
CARRIED = [
    ("L1", date(2026, 1, 1), 100.00, 100.00, C.REVENUE_CARD),
    ("L2", date(2026, 1, 1), -30.00, 70.00, C.SUPPLIER),
    ("L3", date(2026, 1, 4), -100.00, -30.00, C.SUPPLIER),
    ("L4", date(2026, 1, 5), 50.00, 20.00, C.REVENUE_CARD),
]


@pytest.mark.invariants
def test_p2_m27_averages_every_close_carried_over_days_with_no_lines_citing_each_closes_line():
    m27 = _m(_st(CARRIED))["M27"]
    assert (m27.value, m27.unit, m27.evidence) == (40.00, "GBP", ["L2", "L3", "L4"])
    assert m27.name == "Average end-of-day balance"


@pytest.mark.invariants
def test_p2_m27_a_long_carried_gap_counts_every_carried_day():
    # 1 Jan closes at 10.00 (A); 2 to 30 Jan carry it (29 days); 31 Jan closes at 320.00 (B).
    # (30 * 10.00 + 320.00) / 31 = 620.00 / 31 = 20.00.
    s = _st([("A", date(2026, 1, 1), 10.00, 10.00, C.REVENUE_CARD),
             ("B", date(2026, 1, 31), 310.00, 320.00, C.REVENUE_CARD)])
    m27 = _m(s)["M27"]
    assert (m27.value, m27.evidence) == (20.00, ["A", "B"])


@pytest.mark.invariants
@pytest.mark.parametrize("closes, expected", [
    ((1, 1, 0), 0.01),   # 2/3 of a penny rounds to 1p
    ((1, 0), 0.00),      # half a penny rounds to even: 0p
    ((3, 0), 0.02),      # 1.5p rounds to even: 2p
    ((-1, 0), -0.00),    # minus half a penny: 0p
])
def test_p2_m27_is_the_exact_mean_of_the_closes_in_pence_rounded_half_to_even(closes, expected):
    rows, prev = [], 0
    for k, c in enumerate(closes):
        rows.append((f"D{k}", date(2026, 3, 1 + k), (c - prev) / 100, c / 100,
                     C.REVENUE_CARD if c > prev else C.SUPPLIER))
        prev = c
    m27 = _m(_st(rows))["M27"]
    assert m27.value == expected and m27.evidence == [r[0] for r in rows]


@pytest.mark.invariants
def test_p2_m27_agrees_with_the_closes_the_monthly_cells_read_across_months():
    # 30 Jan closes 100.00 (A), 31 Jan carried, 1 Feb closes 40.00 (B), 2 Feb carried.
    # (100 + 100 + 40 + 40) / 4 = 70.00; the two monthly averages are 100.00 and 40.00.
    s = _st([("A", date(2026, 1, 30), 100.00, 100.00, C.REVENUE_CARD),
             ("B", date(2026, 2, 1), -60.00, 40.00, C.SUPPLIER),
             ("C", date(2026, 2, 2), 0.00, 40.00, C.SUPPLIER)])
    m27 = _m(s)["M27"]
    assert (m27.value, m27.evidence) == (70.00, ["A", "B", "C"])
    rows = metrics.monthly_summary(s)
    assert [r.average_balance.value for r in rows] == [100.00, 40.00]
    assert sorted({i for r in rows for i in r.average_balance.evidence}) == m27.evidence


# 30 days, 1 to 30 January: 30 * 48 / 1461 = 1440/1461 of a month (M3's months).
# Net operating cash flow: 3,000.00 in, less supplier 500.00 and rent 200.00 = 2,300.00;
# after owner drawings of 300.00: 2,000.00. Existing debt service: 400.00.
COVER = [
    ("C1", date(2026, 1, 1), 3000.00, 3000.00, C.REVENUE_CARD),
    ("D1", date(2026, 1, 10), -500.00, 2500.00, C.SUPPLIER),
    ("D2", date(2026, 1, 15), -400.00, 2100.00, C.LOAN_REPAYMENT),
    ("D3", date(2026, 1, 20), -300.00, 1800.00, C.OWNER_DRAWINGS),
    ("D4", date(2026, 1, 30), -200.00, 1600.00, C.RENT),
]


@pytest.mark.invariants
@pytest.mark.parametrize("proposed", ["600.00", "600", Decimal("600.00"), 600, 600.0])
def test_p2_m28_adds_the_proposed_repayment_for_each_month_of_the_period(proposed):
    m = _m(_st(COVER), proposed_repayment=proposed)
    # 2,000.00 / (400.00 + 600.00 * 1440/1461) = 2922000 / 1448400 = 2.017398... -> 2.0174
    assert m["M10"].value == 5.0
    assert (m["M28"].value, m["M28"].unit) == (2.0174, "ratio")
    assert m["M28"].evidence == ["C1", "D1", "D2", "D3", "D4"]
    assert m["M28"].name == "Debt service cover including the proposed facility"


@pytest.mark.invariants
def test_p2_m28_with_no_existing_debt_covers_the_proposed_repayment_alone():
    rows = [r for r in COVER if r[0] != "D2"]
    m = _m(_st(rows), proposed_repayment="600.00")
    # 2,000.00 / (600.00 * 1440/1461) = 2922000 / 864000 = 3.38194... -> 3.3819; M10 is n/a.
    assert m["M10"].value is None
    assert (m["M28"].value, m["M28"].evidence) == (3.3819, ["C1", "D1", "D3", "D4"])


@pytest.mark.contracts
def test_m28_without_a_proposed_repayment_is_na_with_the_reason_and_cites_nothing():
    m28 = _m(_st(COVER))["M28"]
    assert m28.value is None and m28.evidence == []
    assert "No proposed monthly repayment" in m28.method and "case profile" in m28.method


@pytest.mark.contracts
@pytest.mark.parametrize("bad", ["600.005", "0", "-1.00", "0.00", "abc", 600.001, "10000000000000.00"])
def test_m28_refuses_a_proposed_repayment_that_is_not_a_positive_whole_number_of_pence(bad):
    with pytest.raises(ValueError, match="proposed"):
        metrics.compute(_st(COVER), proposed_repayment=bad)


@pytest.mark.contracts
def test_m27_and_m28_follow_m26_and_compute_leaves_the_statement_unchanged():
    s = _st(CARRIED)
    before = s.model_dump()
    ids = [m.id for m in metrics.compute(s, proposed_repayment="10.00")]
    assert ids == [f"M{i}" for i in range(1, 29)]
    assert s.model_dump() == before


@pytest.mark.contracts
def test_cma_run_has_no_profile_so_its_evidence_shows_m28_na_with_the_reason_and_m27(tmp_path):
    csv = tmp_path / "s.csv"
    csv.write_text("id,date,description,amount,balance\n"
                   "L1,2026-01-01,STRIPE PAYOUT,100.00,100.00\n"
                   "L2,2026-01-01,CARD TESCO,-30.00,70.00\n"
                   "L3,2026-01-04,CARD TESCO,-100.00,-30.00\n"
                   "L4,2026-01-05,STRIPE PAYOUT,50.00,20.00\n")
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", str(csv), "--yes", "--reviewer", "A", "--out", str(out)])
    assert r.exit_code == 0, r.output
    import json
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    got = {m["id"]: m for m in record["metrics"]}
    assert got["M27"]["value"] == 40.0 and got["M27"]["evidence"] == ["L2", "L3", "L4"]
    assert got["M28"]["value"] is None and got["M28"]["evidence"] == []
    assert "No proposed monthly repayment" in got["M28"]["method"]
    text = out.read_text()
    assert "| M27 | Average end-of-day balance | £40.00 |" in text
    assert "| M28 | Debt service cover including the proposed facility | n/a |" in text


@pytest.mark.contracts
def test_the_committed_example_carries_m27_and_m28_as_the_code_computes_them():
    import json

    from cma import categorise, ingest
    from cma.categorise import load_ruleset

    examples = Path(__file__).parent.parent / "examples"
    record = json.loads((examples / "cafe_stretched.memo.evidence.json").read_text())
    s = categorise.categorise(ingest.load_csv(examples / "cafe_stretched.csv"), load_ruleset(record["ruleset"]["id"]))
    fresh = {m.id: m.model_dump(mode="json") for m in metrics.compute(s, vat_stagger=record["vat_stagger"])}
    stored = {m["id"]: m for m in record["metrics"]}
    assert stored["M27"] == fresh["M27"] and stored["M28"] == fresh["M28"]
