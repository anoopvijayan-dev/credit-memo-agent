"""T06 hardening: the monthly summary (R11) at the calendar's edges, against the committed
example, and on inputs the loader would refuse.

These check what tests/test_monthly.py leaves open: a period that starts on the calendar's
first day or ends on its last (T04 reviews 9 and 10 found figures stepping past them), that
the committed example's monthly summary is what the code computes from its own lines today,
that the summary leaves the statement it reads unchanged, and that a fraction of a penny in
a Statement built without the loader is refused, never reported.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from cma import categorise, ingest, metrics
from cma.categorise import load_ruleset
from cma.metrics import AmountError
from cma.models import Statement, Transaction

EXAMPLES = Path(__file__).parent.parent / "examples"


def _pence(v: float) -> int:
    return int(Decimal(repr(v)) * 100)


def _statement(rows) -> Statement:
    txns = [Transaction(id=i, date=d, description=x, amount=a, balance=b) for i, d, x, a, b in rows]
    return categorise.categorise(Statement(account_name="x", transactions=txns), load_ruleset("v2"))


@pytest.mark.invariants
def test_p12_and_p2_monthly_cells_at_the_calendars_first_and_last_days():
    for rows in (
        [("A", date(1, 1, 1), "CASH PAID IN", 10.00, 10.00), ("B", date(1, 2, 3), "RENT", -15.00, -5.00)],
        [("A", date(9999, 11, 30), "CASH DEPOSIT", 7.50, 7.50),
         ("B", date(9999, 12, 31), "CARD TESCO", -9.00, -1.50)],
    ):
        s = _statement(rows)
        m = {x.id: x for x in metrics.compute(s)}
        out = metrics.monthly_summary(s)
        assert sum(_pence(r.trading_receipts.value) for r in out) == _pence(m["M2"].value)
        assert sum(_pence(r.money_in.value) for r in out) == _pence(m["M17"].value)
        assert out[0].start == s.start and out[-1].end == s.end
        for r in out:
            assert _pence(r.money_out.value) == -_pence(r.money_out.total)
            assert r.days_overdrawn.evidence  # every month cites the lines that set its closes
    first = metrics.monthly_summary(_statement([("A", date(1, 1, 1), "CASH PAID IN", 10.00, 10.00),
                                                ("B", date(1, 2, 3), "RENT", -15.00, -5.00)]))
    assert [(r.month, r.part_month) for r in first] == [("0001-01", False), ("0001-02", True)]
    last = metrics.monthly_summary(_statement([("A", date(9999, 11, 30), "CASH DEPOSIT", 7.50, 7.50),
                                               ("B", date(9999, 12, 31), "CARD TESCO", -9.00, -1.50)]))
    assert [(r.month, r.part_month) for r in last] == [("9999-11", True), ("9999-12", False)]
    assert last[1].days_overdrawn.value == 1 and last[1].lowest_balance.value == -1.5


@pytest.mark.contracts
def test_the_committed_example_monthly_summary_is_what_the_code_computes_from_its_lines():
    record = json.loads((EXAMPLES / "cafe_stretched.memo.evidence.json").read_text())
    s = categorise.categorise(ingest.load_csv(EXAMPLES / "cafe_stretched.csv"),
                              load_ruleset(record["ruleset"]["id"]))
    assert [r.model_dump(mode="json") for r in metrics.monthly_summary(s)] == record["monthly_summary"]


@pytest.mark.contracts
def test_the_monthly_summary_leaves_its_statement_unchanged_and_gives_the_same_answer_twice():
    s = _statement([("A", date(2026, 1, 5), "CASH PAID IN", 10.00, 10.00),
                    ("B", date(2026, 3, 1), "RENT", -15.00, -5.00)])
    before = s.model_dump()
    assert metrics.monthly_summary(s) == metrics.monthly_summary(s)
    assert s.model_dump() == before


@pytest.mark.contracts
def test_a_fraction_of_a_penny_is_refused_not_reported_in_the_monthly_summary():
    s = Statement(account_name="x", transactions=[
        Transaction(id="A", date=date(2026, 1, 5), description="CASH PAID IN", amount=10.005, balance=10.005)])
    with pytest.raises(AmountError):
        metrics.monthly_summary(s)
