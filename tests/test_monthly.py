"""T06: the monthly summary (R11, R55), its sum cells against the period figures (P12) and
each cell's evidence by kind (P2).

R11: one row per calendar month in the period, part months marked as part, with money in,
money out, trading receipts (cash deposits included, R55), average and lowest end-of-day
balance and days overdrawn, each cell with the ids of the lines behind it.
P2: money in and trading receipts are sum figures, whose lines' signed amounts total them;
money out is an outflow figure, a positive amount whose lines total it negated; the balance
and overdrawn-day cells are derived and cite the line that set the close of every day of
the month in the period, a day with no lines taking the line that set the balance carried in.
P12: monthly trading receipts sum to M2, and monthly money in to total money in (M17).
"""

import json
import random
from datetime import date, timedelta
from decimal import Decimal

import pytest
from typer.testing import CliRunner

from cma import categorise, ingest, metrics, synth
from cma.categorise import load_ruleset
from cma.cli import app
from cma.models import Category, Statement, Transaction

CASES = [(a, p) for a in synth.ARCHETYPES for p in synth.PROFILES]

# Cash paid-in wording appears among the credits, so the fixtures hold cash deposits (R55).
CREDITS = ["STRIPE PAYOUT", "CASH PAID IN", "COUNTER DEPOSIT", "CASH DEPOSIT", "FPS CREDIT ACME LTD INV 12",
           "FROM SAVINGS POT TRANSFER", "IWOCA LOAN", "PAYPAL REFUND", "SOMETHING ELSE", "FPS CREDIT CASH DEPOSIT"]
DEBITS = ["CARD TESCO", "RENT", "PAYROLL", "HMRC VAT", "DD IWOCA LTD", "DIVIDEND J SMITH"]


def _pence(v: float) -> int:
    p = Decimal(repr(v)) * 100
    assert p == p.to_integral_value(), v
    return int(p)


def _random_statement(seed: int, ruleset: str) -> Statement:
    """Lines spread over several calendar months, with gaps of days and of whole months,
    overdrawn spells and cash deposits."""
    rng = random.Random(seed)
    txns, bal, d = [], Decimal("200.00"), date(2026, 1, 1) + timedelta(days=rng.randint(0, 27))
    for i in range(rng.randint(3, 70)):
        debit = rng.random() < 0.45
        desc = rng.choice(DEBITS if debit else CREDITS)
        amount = Decimal(rng.randint(1, 300_000)) / 100 * (-1 if debit else 1)
        bal += amount
        d += timedelta(days=rng.choice([0, 0, 1, 2, 5, 40]))
        txns.append(Transaction(id=f"L{i}", date=d, description=desc, amount=float(amount), balance=float(bal)))
    return categorise.categorise(Statement(account_name="x", transactions=txns), load_ruleset(ruleset))


def _statements(tmp_path):
    for ruleset in ("v1", "v2"):
        for a, p in CASES:
            rows, truth = synth.generate(a, p, end=date(2026, 9, 30))
            csv_path, _ = synth.write(rows, truth, tmp_path, stem=f"{a}_{p}")
            yield f"{a}-{p}-{ruleset}", categorise.categorise(ingest.load_csv(csv_path), load_ruleset(ruleset))
        for seed in range(60):
            yield f"random-{seed}-{ruleset}", _random_statement(seed, ruleset)


def _months(start: date, end: date) -> list[str]:
    out, (y, m) = [], (start.year, start.month)
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _closes(s: Statement) -> dict[date, Transaction]:
    """The line that set each day's close, from the first day to the last, carried over."""
    last = {}
    for t in s.transactions:
        last[t.date] = t
    out, d, cur = {}, s.start, None
    while d <= s.end:
        cur = last.get(d, cur)
        out[d] = cur
        d += timedelta(days=1)
    return out


@pytest.mark.invariants
def test_p12_monthly_trading_receipts_sum_to_m2_and_monthly_money_in_to_total_money_in(tmp_path):
    cash_seen = 0
    for label, s in _statements(tmp_path):
        m = {x.id: x for x in metrics.compute(s, vat_stagger=synth.VAT_STAGGER)}
        rows = metrics.monthly_summary(s)
        assert [r.month for r in rows] == _months(s.start, s.end), label  # one row per calendar month
        assert sum(_pence(r.trading_receipts.value) for r in rows) == _pence(m["M2"].value), label
        assert sum(_pence(r.money_in.value) for r in rows) == _pence(m["M17"].value), label
        assert sorted(i for r in rows for i in r.trading_receipts.evidence) == sorted(m["M2"].evidence), label
        assert sorted(i for r in rows for i in r.money_in.evidence) == sorted(m["M17"].evidence), label
        # R55: every cash deposit is in its month's trading receipts.
        cash = [t for t in s.transactions if t.category == Category.CASH_DEPOSIT]
        cash_seen += len(cash)
        for t in cash:
            row = next(r for r in rows if r.month == t.date.strftime("%Y-%m"))
            assert t.id in row.trading_receipts.evidence, label
    assert cash_seen > 0  # the fixtures hold cash deposits


@pytest.mark.invariants
def test_p2_monthly_cells_cite_their_lines_by_kind(tmp_path):
    for label, s in _statements(tmp_path):
        amounts = {t.id: _pence(t.amount) for t in s.transactions}
        by_id = s.by_id()
        closes = _closes(s)
        for r in metrics.monthly_summary(s):
            in_month = [t for t in s.transactions if t.date.strftime("%Y-%m") == r.month]
            # Sum figures: the lines' signed amounts total the cell, and the cell carries that total.
            for cell in (r.money_in, r.trading_receipts):
                assert cell.kind == "sum", label
                assert sum(amounts[i] for i in cell.evidence) == _pence(cell.value) == _pence(cell.total), label
            assert r.money_in.evidence == [t.id for t in in_month if amounts[t.id] > 0], label
            # Outflow figure: a positive amount whose lines total it negated.
            out = r.money_out
            assert out.kind == "outflow" and out.value >= 0, label
            assert -sum(amounts[i] for i in out.evidence) == _pence(out.value) == -_pence(out.total), label
            assert out.evidence == [t.id for t in in_month if amounts[t.id] < 0], label
            # Derived cells: no total; they cite the line that set every close of the month.
            days = [d for d in closes if d.strftime("%Y-%m") == r.month]
            assert r.start == days[0] and r.end == days[-1], label
            ids = list(dict.fromkeys(closes[d].id for d in days))
            pos = {t.id: i for i, t in enumerate(s.transactions)}
            ids.sort(key=pos.__getitem__)
            bal = [_pence(closes[d].balance) for d in days]
            for cell in (r.average_balance, r.lowest_balance, r.days_overdrawn):
                assert cell.kind == "derived" and cell.total is None, label
                assert cell.evidence == ids, label
                assert all(by_id[i].date <= days[-1] for i in cell.evidence), label
            assert _pence(r.lowest_balance.value) == min(bal), label
            assert r.days_overdrawn.value == sum(1 for b in bal if b < 0), label
            exact = Decimal(sum(bal)) / len(bal)
            assert abs(Decimal(_pence(r.average_balance.value)) - exact) <= Decimal("0.5"), label
            assert r.part_month == (days[0].day != 1 or (days[-1] + timedelta(days=1)).day != 1), label


def _hand_statement() -> Statement:
    """20 Jan to 3 Mar: January and March are part months, February is whole and has no
    lines after the 2nd, so its closes are carried."""
    rows = [
        ("L1", date(2026, 1, 20), "CASH PAID IN", 100.00, 100.00),
        ("L2", date(2026, 1, 20), "CARD TESCO", -150.00, -50.00),  # sets 20 Jan's close
        ("L3", date(2026, 1, 25), "STRIPE PAYOUT", 80.25, 30.25),
        ("L4", date(2026, 2, 2), "RENT", -40.00, -9.75),
        ("L5", date(2026, 3, 1), "COUNTER DEPOSIT", 20.00, 10.25),
        ("L6", date(2026, 3, 3), "FPS CREDIT ACME LTD INV 7", 5.10, 15.35),
    ]
    txns = [Transaction(id=i, date=d, description=x, amount=a, balance=b) for i, d, x, a, b in rows]
    return categorise.categorise(Statement(account_name="x", transactions=txns), load_ruleset("v2"))


@pytest.mark.invariants
def test_p2_monthly_cells_hand_computed_with_carried_days_and_cash():
    s = _hand_statement()
    assert [t.category for t in s.transactions if t.id in ("L1", "L5")] == [Category.CASH_DEPOSIT] * 2
    jan, feb, mar = metrics.monthly_summary(s)
    assert (jan.month, jan.part_month, jan.start, jan.end) == ("2026-01", True, date(2026, 1, 20), date(2026, 1, 31))
    assert (feb.part_month, feb.start, feb.end) == (False, date(2026, 2, 1), date(2026, 2, 28))
    assert (mar.part_month, mar.end) == (True, date(2026, 3, 3))

    assert (jan.money_in.value, jan.money_in.evidence) == (180.25, ["L1", "L3"])
    assert (jan.trading_receipts.value, jan.trading_receipts.evidence) == (180.25, ["L1", "L3"])  # cash included
    assert (jan.money_out.value, jan.money_out.total, jan.money_out.evidence) == (150.0, -150.0, ["L2"])
    # Jan: 20-24 close at -50.00 (L2), 25-31 at 30.25 (L3): 5 x -5000 + 7 x 3025 = -3825 over 12 days.
    assert jan.lowest_balance.value == -50.0 and jan.days_overdrawn.value == 5
    assert jan.average_balance.value == -3.19  # -318.75p rounds half to even to -319p
    assert jan.lowest_balance.evidence == ["L2", "L3"]  # L1 never closes a day

    # Feb: the 1st carries L3's 30.25 in; 2-28 close at -9.75 (L4).
    assert feb.money_in.value == 0.0 and feb.money_in.evidence == [] and feb.trading_receipts.total == 0.0
    assert (feb.money_out.value, feb.money_out.evidence) == (40.0, ["L4"])
    assert feb.days_overdrawn.value == 27 and feb.lowest_balance.value == -9.75
    assert feb.average_balance.value == round((3025 - 975 * 27) / 28) / 100
    assert feb.days_overdrawn.evidence == ["L3", "L4"]

    # Mar: 1-2 close at 10.25 (L5), 3 at 15.35 (L6).
    assert mar.money_in.value == 25.1 and mar.trading_receipts.evidence == ["L5", "L6"]
    assert mar.money_out.value == 0.0 and mar.money_out.evidence == []
    assert mar.days_overdrawn.value == 0 and mar.lowest_balance.value == 10.25
    assert mar.average_balance.value == 11.95  # (1025 + 1025 + 1535) / 3
    assert mar.average_balance.evidence == ["L5", "L6"]


@pytest.mark.contracts
def test_cma_run_writes_the_monthly_summary_into_its_evidence_record(tmp_path, monkeypatch):
    monkeypatch.setenv("CMA_RULESET", "v2")
    csv = tmp_path / "s.csv"
    csv.write_text("date,description,amount,balance,id\n"
                   "2026-01-20,CASH PAID IN,100.00,100.00,L1\n"
                   "2026-01-20,CARD TESCO,-150.00,-50.00,L2\n"
                   "2026-01-25,STRIPE PAYOUT,80.25,30.25,L3\n"
                   "2026-02-02,RENT,-40.00,-9.75,L4\n"
                   "2026-03-01,COUNTER DEPOSIT,20.00,10.25,L5\n"
                   "2026-03-03,FPS CREDIT ACME LTD INV 7,5.10,15.35,L6\n")
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", str(csv), "--yes", "--reviewer", "A", "--out", str(out)])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    rows = record["monthly_summary"]
    assert [(x["month"], x["part_month"]) for x in rows] == [("2026-01", True), ("2026-02", False),
                                                            ("2026-03", True)]
    jan = rows[0]
    assert jan["start"] == "2026-01-20" and jan["end"] == "2026-01-31"
    assert jan["trading_receipts"] == {"value": 180.25, "unit": "GBP", "kind": "sum", "evidence": ["L1", "L3"],
                                       "total": 180.25, "note": None}
    assert jan["money_out"]["total"] == -150.0 and jan["money_out"]["value"] == 150.0
    assert jan["days_overdrawn"]["unit"] == "days" and jan["days_overdrawn"]["evidence"] == ["L2", "L3"]
    m = {x["id"]: x for x in record["metrics"]}
    assert sum(_pence(x["trading_receipts"]["value"]) for x in rows) == _pence(m["M2"]["value"])
    assert sum(_pence(x["money_in"]["value"]) for x in rows) == _pence(m["M17"]["value"])
