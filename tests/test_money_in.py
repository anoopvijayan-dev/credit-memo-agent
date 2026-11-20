"""T04: the money-in buckets (M17-M26), in integer pence, and their reconciliation (R1-R3, R7).

P1: trading receipts and the other seven buckets sum to total money in to the penny, and
every credit's id is in exactly one bucket's evidence; M18 is not a bucket and is not added.
P2: a sum figure's evidence lines total the figure to the penny.
P6: a credit that no rule matches is in unexplained money in and not in M2.
"""

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
BUCKETS = ["M2", "M19", "M20", "M21", "M22", "M23", "M24", "M25"]
SUMS = ["M2", "M17", "M18", "M19", "M20", "M21", "M22", "M23", "M24", "M25"]
# Debt service, drawings, VAT and PAYE keep the sign they had before T04 (R10, and the
# frozen P7 baseline): positive figures for money out, so their lines' signed total is the
# figure negated. M9 (a net whose round-trip offset is capped) is not checked here: making
# either meet P2 literally changes M1-M16 values or evidence that P7's baseline pins.
OUTFLOW_SUMS = ["M8", "M11", "M12", "M14"]

DESCRIPTIONS = [
    "STRIPE PAYOUT", "SUMUP PAYOUT", "CASH PAID IN", "COUNTER DEPOSIT", "FPS CREDIT ACME LTD INV 12",
    "FROM SAVINGS POT TRANSFER", "FPS CREDIT DIRECTOR CAPITAL INTRODUCED", "IWOCA LOAN", "PAYPAL REFUND",
    "HMRC VAT REPAYMENT", "INTEREST PAID", "BACS CREDIT HMRC CASH PAID IN", "FPS CREDIT CASH DEPOSIT",
    "SOMETHING ELSE", "XQZ 4471", "UNPAID DD IWOCA LTD", "DD IWOCA LTD", "CARD TESCO", "FPS DEBIT ACME LTD",
    "HMRC VAT", "HMRC PAYE", "DIVIDEND J SMITH", "RENT", "PAYROLL",
]


def _pence(v: float) -> int:
    """A reported figure read as the decimal it prints as, in pence (never via float sums)."""
    p = Decimal(repr(v)) * 100
    assert p == p.to_integral_value(), v
    return int(p)


def _by_id(s: Statement) -> dict:
    return {m.id: m for m in metrics.compute(s, vat_stagger=synth.VAT_STAGGER)}


def _synthetic(tmp_path, archetype, profile, ruleset):
    rows, truth = synth.generate(archetype, profile, end=date(2026, 9, 30))
    csv_path, _ = synth.write(rows, truth, tmp_path, stem=f"{archetype}_{profile}")
    return categorise.categorise(ingest.load_csv(csv_path), load_ruleset(ruleset))


def _random_statement(seed: int, ruleset: str) -> Statement:
    rng = random.Random(seed)
    txns, bal, d = [], Decimal("1000.00"), date(2026, 1, 1)
    for i in range(rng.randint(5, 80)):
        desc = rng.choice(DESCRIPTIONS)
        debit = desc in {"DD IWOCA LTD", "CARD TESCO", "FPS DEBIT ACME LTD", "HMRC VAT", "HMRC PAYE",
                         "DIVIDEND J SMITH", "RENT", "PAYROLL"}
        # Amounts chosen so float sums would drift (0.1 + 0.2), which pence must not.
        amount = Decimal(rng.randint(1, 500_000)) / 100 * (-1 if debit else 1)
        bal += amount
        d += timedelta(days=rng.randint(0, 3))
        txns.append(Transaction(id=f"L{i}", date=d, description=desc, amount=float(amount), balance=float(bal)))
    return categorise.categorise(Statement(account_name="x", transactions=txns), load_ruleset(ruleset))


def _statements(tmp_path):
    for ruleset in ("v1", "v2"):
        for a, p in CASES:
            yield f"{a}-{p}-{ruleset}", _synthetic(tmp_path, a, p, ruleset)
        for seed in range(40):
            yield f"random-{seed}-{ruleset}", _random_statement(seed, ruleset)


def _check_p1(label: str, s: Statement) -> None:
    m = _by_id(s)
    credits = [t for t in s.transactions if t.amount > 0]
    assert _pence(m["M17"].value) == sum(_pence(t.amount) for t in credits), label
    assert sum(_pence(m[b].value) for b in BUCKETS) == _pence(m["M17"].value), label
    placed = [i for b in BUCKETS for i in m[b].evidence]
    assert sorted(placed) == sorted(t.id for t in credits), label
    assert set(m["M18"].evidence) <= set(m["M2"].evidence), label


@pytest.mark.invariants
def test_p1_buckets_sum_to_total_money_in_and_every_credit_is_in_exactly_one(tmp_path):
    for label, s in _statements(tmp_path):
        _check_p1(label, s)


@pytest.mark.invariants
def test_p2_evidence_lines_total_each_sum_figure_to_the_penny(tmp_path):
    for label, s in _statements(tmp_path):
        m = _by_id(s)
        amounts = {t.id: _pence(t.amount) for t in s.transactions}
        for fid in SUMS:
            assert sum(amounts[i] for i in m[fid].evidence) == _pence(m[fid].value), f"{label} {fid}"
        for fid in OUTFLOW_SUMS:
            assert -sum(amounts[i] for i in m[fid].evidence) == _pence(m[fid].value), f"{label} {fid}"


@pytest.mark.invariants
def test_p6_a_credit_no_rule_matches_is_unexplained_and_never_trading(tmp_path):
    for label, s in _statements(tmp_path):
        m = _by_id(s)
        for t in s.transactions:
            if t.amount > 0 and (t.rule_id or "").endswith(":default"):
                assert t.id in m["M25"].evidence and t.id not in m["M2"].evidence, f"{label} {t.id}"
                assert t.id in m["M26"].evidence, f"{label} {t.id}"


@pytest.mark.invariants
@pytest.mark.parametrize("ruleset", ["v1", "v2"])
def test_p6_an_unknown_payer_lands_in_m25(ruleset):
    s = _random_statement(0, ruleset)
    s.transactions.append(Transaction(id="U1", date=date(2026, 6, 1), description="XQZ 4471 HOLDINGS",
                                      amount=812.4, balance=0.0))
    s = categorise.categorise(s, load_ruleset(ruleset))
    m = _by_id(s)
    assert "U1" in m["M25"].evidence and "U1" not in m["M2"].evidence


def _line(i, desc, amount, cat):
    return Transaction(id=i, date=date(2026, 1, 1) + timedelta(days=int(i[1:])), description=desc,
                       amount=amount, balance=0.0, category=cat)


def test_each_bucket_holds_its_categories_and_m18_is_cash_inside_m2():
    s = Statement(account_name="x", transactions=[
        _line("L1", "a", 0.1, Category.REVENUE_CARD), _line("L2", "b", 0.2, Category.CASH_DEPOSIT),
        _line("L3", "c", 3.0, Category.OWN_ACCOUNT_TRANSFER), _line("L4", "d", 4.0, Category.DIRECTOR_FUNDING),
        _line("L5", "e", 5.0, Category.LOAN_DRAWDOWN), _line("L6", "f", 6.0, Category.REFUND_IN),
        _line("L7", "g", 7.0, Category.RETURNED_DD), _line("L8", "h", 8.0, Category.HMRC_REPAYMENT),
        _line("L9", "i", 9.0, Category.OTHER_EXPLAINED_IN), _line("L10", "j", 10.0, Category.OTHER),
        _line("L11", "k", -50.0, Category.SUPPLIER),
    ])
    m = _by_id(s)
    assert m["M2"].value == 0.3 and m["M2"].evidence == ["L1", "L2"]  # 0.1 + 0.2, exactly
    assert m["M18"].value == 0.2 and m["M18"].evidence == ["L2"]
    assert [(m[f].value, m[f].evidence) for f in BUCKETS[1:]] == [
        (3.0, ["L3"]), (4.0, ["L4"]), (5.0, ["L5"]), (13.0, ["L6", "L7"]), (8.0, ["L8"]), (9.0, ["L9"]),
        (10.0, ["L10"])]
    assert m["M17"].value == 52.3 and m["M17"].unit == "GBP"
    # M16 and M26 share a denominator: money in less drawdowns, returned DDs and own-account transfers.
    denominator = 52.3 - 5.0 - 7.0 - 3.0
    assert m["M16"].value == round((4 + 6 + 8 + 9 + 10) / denominator * 100, 2)
    assert m["M26"].value == round(10 / denominator * 100, 2) and m["M26"].unit == "pct"
    assert m["M16"].evidence == m["M26"].evidence == ["L1", "L2", "L4", "L6", "L8", "L9", "L10"]
    assert m["M16"].name == "Non-trading money in (share)"
    assert [x.id for x in metrics.compute(s)] == [f"M{i}" for i in range(1, 27)]


def test_buckets_that_do_not_reconcile_are_refused_naming_pence_and_lines():
    s = Statement(account_name="x", transactions=[
        _line("L1", "a", 10.0, Category.REVENUE_CARD),
        _line("L2", "b", 12.34, Category.SUPPLIER),  # a credit no bucket holds
        _line("L3", "c", -1.0, Category.REVENUE_CARD),  # a debit counted as trading receipts
    ])
    with pytest.raises(metrics.ReconciliationError) as e:
        metrics.compute(s)
    assert e.value.difference_pence == 1234 - (-100)
    assert e.value.lines == ["L2", "L3"]
    assert "1334 pence" in str(e.value) and "L2" in str(e.value) and "L3" in str(e.value)


def test_a_statement_with_no_credits_has_zero_money_in_and_no_share():
    s = Statement(account_name="x", transactions=[_line("L1", "a", -5.0, Category.SUPPLIER)])
    m = _by_id(s)
    assert m["M17"].value == 0 and m["M17"].evidence == [] and m["M26"].value is None


def test_an_amount_with_fractional_pence_is_refused():
    s = Statement(account_name="x", transactions=[_line("L1", "a", 1.005, Category.OTHER)])
    with pytest.raises(ValueError, match="L1"):
        metrics.compute(s)


@pytest.mark.contracts
def test_cma_run_exits_non_zero_naming_the_difference_and_the_lines(tmp_path, monkeypatch):
    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv_path, _ = synth.write(rows, truth, tmp_path)
    real = categorise.categorise

    def broken(statement, *a, **k):
        out = real(statement, *a, **k)
        credit = next(t for t in out.transactions if t.amount > 0)
        credit.category = Category.RENT  # in no money-in bucket
        broken.line = credit.id, _pence(credit.amount)
        return out

    monkeypatch.setattr(categorise, "categorise", broken)
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", str(csv_path), "--yes", "--reviewer", "A", "--out", str(out)])
    line_id, pence = broken.line
    assert r.exit_code == 6, r.output
    assert f"{pence} pence" in r.output and line_id in r.output
    assert "model call failed" not in r.output and not out.exists()


@pytest.mark.contracts
def test_mcp_compute_metrics_carries_m17_to_m26(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from cma import mcp_server

    rows, truth = synth.generate("cafe", "good", end=date(2026, 9, 30))
    synth.write(rows, truth, tmp_path / "data", stem="cafe")
    monkeypatch.setenv("CMA_DATA_DIR", str(tmp_path / "data"))
    got = {m["id"]: m for m in mcp_server.compute_metrics("cafe.csv", vat_stagger=3)}
    assert [f"M{i}" for i in range(1, 27)] == list(got)
    assert got["M17"]["unit"] == "GBP" and got["M26"]["unit"] == "pct" and got["M17"]["evidence"]


@pytest.mark.invariants
def test_p1_reported_gbp_figures_reconcile_as_printed_for_large_amounts():
    # The reported pounds are read back as the decimals they print as: a float conversion
    # that lost a penny would show here (verify-127, major 1).
    rng = random.Random(7)
    for _ in range(300):
        cats = [Category.REVENUE_CARD, Category.OTHER, Category.DIRECTOR_FUNDING, Category.CASH_DEPOSIT]
        lines = [_line(f"L{i}", "x", float(Decimal(rng.randint(1, 10**13)) / 100), rng.choice(cats))
                 for i in range(1, rng.randint(2, 9))]
        if sum(_pence(t.amount) for t in lines) >= metrics.MAX_PENCE:
            continue
        m = _by_id(Statement(account_name="x", transactions=lines))
        assert sum(_pence(m[b].value) for b in BUCKETS) == _pence(m["M17"].value)
        assert _pence(m["M17"].value) == sum(_pence(t.amount) for t in lines)


def test_an_amount_too_large_to_report_to_the_penny_is_refused():
    s = Statement(account_name="x", transactions=[
        _line("L1", "a", 100000000000000.0, Category.REVENUE_CARD), _line("L2", "b", 0.01, Category.OTHER)])
    with pytest.raises(metrics.AmountError, match="L1"):
        metrics.compute(s)


def test_figures_whose_sum_is_too_large_to_report_to_the_penny_are_refused():
    s = Statement(account_name="x", transactions=[
        _line("L1", "a", 9999999999999.99, Category.REVENUE_CARD), _line("L2", "b", 0.01, Category.REVENUE_CARD)])
    with pytest.raises(metrics.AmountError, match="too large"):
        metrics.compute(s)


def test_lowest_balance_and_overdrawn_days_are_compared_in_pence():
    # 0.1 + 0.2 - 0.3 is not 0 in binary float; in pence it is. The balance that closes the
    # day at -0.01 is overdrawn and M5 is exactly -0.01 (verify-127, major 2).
    s = Statement(account_name="x", transactions=[
        Transaction(id="L1", date=date(2026, 1, 1), description="a", amount=0.3, balance=0.3,
                    category=Category.OTHER),
        Transaction(id="L2", date=date(2026, 1, 2), description="b", amount=-0.31, balance=-0.01,
                    category=Category.SUPPLIER),
        Transaction(id="L3", date=date(2026, 1, 3), description="c", amount=0.01, balance=0.0,
                    category=Category.OTHER),
    ])
    m = _by_id(s)
    assert m["M5"].value == -0.01 and m["M5"].evidence == ["L2"]
    assert m["M6"].value == 1 and m["M6"].evidence == ["L2"]


@pytest.mark.contracts
def test_cma_run_refusal_shows_a_line_id_that_looks_like_markup(tmp_path, monkeypatch):
    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv_path, _ = synth.write(rows, truth, tmp_path)
    real = categorise.categorise

    def broken(statement, *a, **k):
        out = real(statement, *a, **k)
        credit = next(t for t in out.transactions if t.amount > 0)
        credit.id, credit.category = "[red]", Category.RENT  # a valid CSV id that Rich would eat
        return out

    monkeypatch.setattr(categorise, "categorise", broken)
    r = CliRunner().invoke(app, ["run", str(csv_path), "--yes", "--reviewer", "A",
                                 "--out", str(tmp_path / "memo.md")])
    assert r.exit_code == 6, r.output
    assert "[red]" in r.output


@pytest.mark.contracts
def test_mcp_compute_metrics_refuses_buckets_that_do_not_reconcile(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from cma import mcp_server

    rows, truth = synth.generate("cafe", "good", end=date(2026, 9, 30))
    synth.write(rows, truth, tmp_path / "data", stem="cafe")
    monkeypatch.setenv("CMA_DATA_DIR", str(tmp_path / "data"))
    real = categorise.categorise

    def broken(statement, *a, **k):
        out = real(statement, *a, **k)
        credit = next(t for t in out.transactions if t.amount > 0)
        credit.category = Category.RENT
        broken.line = credit.id, _pence(credit.amount)
        return out

    monkeypatch.setattr(categorise, "categorise", broken)
    with pytest.raises(ValueError) as e:
        mcp_server.compute_metrics("cafe.csv", vat_stagger=3)
    line_id, pence = broken.line
    assert isinstance(e.value, metrics.ReconciliationError)
    assert f"{pence} pence" in str(e.value) and line_id in str(e.value)
