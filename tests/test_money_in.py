"""T04: the money-in buckets (M17-M26), in integer pence, and their reconciliation (R1-R3, R7).

P1: trading receipts and the other seven buckets sum to total money in to the penny, and
every credit's id is in exactly one bucket's evidence; M18 is not a bucket and is not added.
P2 (revision 24): a sum figure's evidence lines total the figure to the penny; an outflow
figure is reported as a positive amount and its lines total it negated; a derived figure
(M9 among them) is not asked to total its lines, and M9 names in its evidence note, by id,
each credit it counts only in part.
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
# Outflow figures (P2, revision 24): money paid out reported as a positive amount, as since
# before T04 (R10); their lines' signed total is the figure negated.
OUTFLOWS = ["M8", "M11", "M12", "M14"]

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
def test_p2_sum_figures_total_their_lines_and_outflow_figures_total_them_negated(tmp_path):
    for label, s in _statements(tmp_path):
        m = _by_id(s)
        amounts = {t.id: _pence(t.amount) for t in s.transactions}
        for fid in SUMS:
            assert sum(amounts[i] for i in m[fid].evidence) == _pence(m[fid].value), f"{label} {fid}"
        for fid in OUTFLOWS:
            assert m[fid].value >= 0, f"{label} {fid}"
            assert -sum(amounts[i] for i in m[fid].evidence) == _pence(m[fid].value), f"{label} {fid}"
        # M9 is derived: its note names only lines that are in its evidence.
        assert set(m["M9"].evidence_note or []) <= set(m["M9"].evidence), label


def _round_trip(back: float) -> Statement:
    return categorise.categorise(Statement(account_name="x", transactions=[
        Transaction(id="L1", date=date(2026, 1, 1), description="FPS DEBIT J BLOGGS", amount=-100.0, balance=-100.0),
        Transaction(id="L2", date=date(2026, 1, 2), description="FPS CREDIT J BLOGGS", amount=back,
                    balance=back - 100.0),
    ]), load_ruleset("v2"))


@pytest.mark.invariants
def test_p2_m9_notes_the_credit_it_counts_only_in_part():
    """verify-128: £100 sent and £250 sent back. M9 is £0 and cites both lines, which total
    £150; its evidence note names L2, the credit that offsets only what was sent."""
    m = _by_id(_round_trip(250.0))
    assert m["M9"].value == 0 and m["M9"].evidence == ["L1", "L2"]
    assert m["M9"].evidence_note == ["L2"]
    assert "L2" not in m["M9"].method  # the method sentence reaches the prompt (R30); the note does not
    assert "L2" not in metrics.as_table(list(m.values()))


@pytest.mark.invariants
def test_p2_m9_has_no_note_when_a_round_trip_counts_in_full():
    m = _by_id(_round_trip(60.0))
    assert m["M9"].value == -40.0 and m["M9"].evidence_note is None


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


def test_a_large_whole_penny_amount_is_counted_not_refused_by_a_ceiling():
    """T04 review 2: the old fixed ceiling (10**15 pence) refused £10,000,000,000,000.01,
    which the DTO carries exactly; it is now counted and reported to the penny."""
    line = _line("L1", "a", 10000000000000.01, Category.REVENUE_CARD)
    assert metrics.pence(line) == 1000000000000001
    assert metrics._gbp(1000000000000001) == 10000000000000.01


def test_a_figure_that_cannot_be_reported_to_the_penny_is_refused():
    """T04 review 2: a total whose pounds no float carries to the penny is refused, never
    reported a penny out."""
    s = Statement(account_name="x", transactions=[
        _line("L1", "a", 100000000000000.0, Category.REVENUE_CARD), _line("L2", "b", 0.01, Category.OTHER)])
    with pytest.raises(metrics.AmountError, match="cannot be reported to the penny"):
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


def test_m3_is_exact_or_not_reported_never_a_penny_out():
    """verify-128: £9,999,999,999,999.98 in one day gives an average monthly revenue of
    £304,374,999,999,999.39, which no float carries; M3 is n/a with the reason, and the
    sum figures are still reported."""
    s = Statement(account_name="x", transactions=[
        Transaction(id="L1", date=date(2026, 1, 1), description="a", amount=9999999999999.98,
                    balance=9999999999999.98, category=Category.REVENUE_CARD)])
    m = _by_id(s)
    assert m["M3"].value is None and "penny" in m["M3"].method
    assert m["M17"].value == 9999999999999.98 and m["M2"].evidence == ["L1"]
    small = Statement(account_name="x", transactions=[
        Transaction(id="L1", date=date(2026, 1, 1), description="a", amount=1000.0, balance=1000.0,
                    category=Category.REVENUE_CARD),
        Transaction(id="L2", date=date(2026, 1, 31), description="b", amount=0.01, balance=1000.01,
                    category=Category.REVENUE_CARD)])
    exact = Decimal(100001) * 1461 / (31 * 48)  # pence
    assert _pence(_by_id(small)["M3"].value) == int(exact.quantize(Decimal(1)))


@pytest.mark.contracts
def test_cma_run_refuses_an_uncategorised_credit_naming_pence_and_line(tmp_path, monkeypatch):
    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv_path, _ = synth.write(rows, truth, tmp_path)
    real = categorise.categorise

    def broken(statement, *a, **k):
        out = real(statement, *a, **k)
        credit = next(t for t in out.transactions if t.amount > 0)
        credit.category = None  # verify-128: in no bucket at all
        broken.line = credit.id, _pence(credit.amount)
        return out

    monkeypatch.setattr(categorise, "categorise", broken)
    r = CliRunner().invoke(app, ["run", str(csv_path), "--yes", "--reviewer", "A",
                                 "--out", str(tmp_path / "memo.md")])
    line_id, pence = broken.line
    assert r.exit_code == 6, r.output
    assert f"{pence} pence" in r.output and line_id in r.output and "model call failed" not in r.output


@pytest.mark.contracts
def test_an_uncategorised_credit_is_a_reconciliation_failure_naming_it():
    """T04 review 2 / R3: a credit with no category is in no bucket: refused with the
    difference and the line, never as a generic error the CLI reports as a model failure."""
    s = Statement(account_name="x", transactions=[
        _line("L1", "a", 12.34, Category.REVENUE_CARD), _line("L2", "b", 5.00, Category.OTHER)])
    s.transactions[1].category = None
    with pytest.raises(metrics.ReconciliationError) as e:
        metrics.compute(s)
    assert e.value.lines == ["L2"] and "500 pence" in str(e.value)


@pytest.mark.contracts
def test_the_money_in_figures_are_shown_to_the_penny_so_the_parts_add_up_to_the_whole():
    """T04 review 2: shown together, M2 and M19-M25 add up to the shown M17."""
    s = Statement(account_name="x", transactions=[
        _line("L1", "a", 0.6, Category.REVENUE_CARD), _line("L2", "b", 0.6, Category.REFUND_IN),
        _line("L3", "c", 0.6, Category.OTHER)])
    shown = {m.id: m.display_exact() for m in metrics.compute(s) if m.id in metrics.EXACT_IDS}
    assert shown["M17"] == "£1.80" and shown["M2"] == "£0.60"
    parts = sum(_pence(float(v.lstrip("£").replace(",", ""))) for k, v in shown.items() if k not in ("M17", "M18"))
    assert parts == 180
