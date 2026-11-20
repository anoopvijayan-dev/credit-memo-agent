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


@pytest.mark.contracts
@pytest.mark.parametrize("amount", ["10000000000000.00", "12345678901234.56", "0.123"])
def test_an_amount_no_float_carries_to_the_penny_is_refused_at_import(tmp_path, amount):
    """T04 review 3: the amount is read exactly; one that no float carries to the penny (or
    with more than two places) is refused at import, never rounded before it is counted."""
    from cma import ingest

    f = tmp_path / "s.csv"
    f.write_text(f"id,date,description,amount,balance\nL1,2026-01-05,ACME,{amount},{amount}\n")
    with pytest.raises(ingest.StatementError, match="amount"):
        ingest.load_csv(f)


@pytest.mark.contracts
def test_a_statement_whose_money_in_could_not_be_reported_is_refused_at_import(tmp_path):
    from cma import ingest

    f = tmp_path / "s.csv"
    f.write_text("id,date,description,amount,balance\nL1,2026-01-05,A,6000000000000.00,6000000000000.00\n"
                 "L2,2026-01-06,B,6000000000000.00,12000000000000.00\n")
    with pytest.raises(ingest.StatementError, match="money in totals"):
        ingest.load_csv(f)


@pytest.mark.contracts
def test_a_figure_shown_to_the_penny_is_the_counted_pence():
    """T04 review 3: the shown pence come from the decimal the float reads back as, never
    from rounding its binary value, up to the largest figure a statement can produce."""
    from cma import models

    for value, shown in [(9999999999999.99, "£9,999,999,999,999.99"), (0.29, "£0.29"), (-1234.57, "-£1,234.57")]:
        assert models.Metric(id="M17", name="x", value=value, unit="GBP", method="m").display_exact() == shown


@pytest.mark.contracts
def test_uncategorised_credits_go_through_the_full_reconciliation():
    """T04 review 4: credits with no category are named by the same reconciliation that
    names every other unplaced line, with the whole difference."""
    s = Statement(account_name="x", transactions=[
        _line("L1", "a", 12.34, Category.REVENUE_CARD), _line("L2", "b", 5.00, Category.OTHER),
        _line("L3", "c", 1.01, Category.OTHER)])
    s.transactions[1].category = None
    s.transactions[2].category = None
    with pytest.raises(metrics.ReconciliationError) as e:
        metrics.compute(s)
    assert e.value.lines == ["L2", "L3"] and "601 pence" in str(e.value)


@pytest.mark.dual_run
def test_m16_under_v1_rules_counts_every_credit_the_previous_release_counted():
    """T04 review 4: the previous release had no own-account category, so under the frozen v1
    rules no line is one, and M16 reads the same credits it always did: every credit but
    lending drawdowns and returned direct debits."""
    s = Statement(account_name="x", transactions=[
        _line("L1", "TO SAVINGS POT", 50.0, None), _line("L2", "CARD SETTLEMENT", 80.0, None),
        _line("L3", "LOAN DRAWDOWN ACME CAPITAL", 100.0, None)])
    s = categorise.categorise(s, load_ruleset("v1"))
    m = {x.id: x for x in metrics.compute(s)}
    old = [t.id for t in s.transactions if t.amount > 0 and t.category not in (Category.LOAN_DRAWDOWN, Category.RETURNED_DD)]
    assert m["M16"].evidence == old


@pytest.mark.contracts
@pytest.mark.parametrize("amount", ["10000000000000.00", "12.345"])
def test_the_monzo_import_reads_money_exactly_and_refuses_what_it_cannot_carry(tmp_path, amount):
    """T04 review 4: Monzo's amounts are read as exact pence, never a binary float; an amount
    with more than two places, or at the statement limit, is refused at import."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_text("Transaction ID,Date,Time,Type,Name,Amount,Currency,Balance\n"
                   f"tx_1,01/01/2026,09:00:00,Faster payment,ACME,{amount},GBP,{amount}\n")
    with pytest.raises(monzo.MonzoImportError):
        monzo.convert(src, tmp_path / "out.csv")


@pytest.mark.contracts
def test_an_uncategorised_debit_never_hides_the_credits_reconciliation():
    """T04 review 5: with an uncategorised debit and an unbucketed credit, the credit's
    reconciliation (difference and line) is reported; the debit alone is refused after."""
    lines = [_line("L1", "a", 12.34, Category.REVENUE_CARD), _line("L2", "b", 5.00, Category.OTHER),
             _line("L3", "c", -2.00, Category.SUPPLIER)]
    s = Statement(account_name="x", transactions=lines)
    s.transactions[1].category = None
    s.transactions[2].category = None
    with pytest.raises(metrics.ReconciliationError) as e:
        metrics.compute(s)
    assert e.value.lines == ["L2"] and "500 pence" in str(e.value)
    s.transactions[1].category = Category.OTHER
    with pytest.raises(metrics.AmountError, match="L3 has no category"):
        metrics.compute(s)


@pytest.mark.contracts
def test_a_dangling_reversal_never_hides_the_credits_reconciliation():
    """T04 review 6: a returned DD whose debit is not in the statement is refused after the
    credits' reconciliation, never as a KeyError before it."""
    lines = [_line("L1", "a", 12.34, Category.REVENUE_CARD), _line("L2", "b", 5.00, Category.OTHER),
             _line("L3", "UNPAID DD", 3.00, Category.RETURNED_DD)]
    s = Statement(account_name="x", transactions=lines)
    s.transactions[2].reverses = "NOT-HERE"
    s.transactions[1].category = None
    with pytest.raises(metrics.ReconciliationError) as e:
        metrics.compute(s)
    assert "L2" in e.value.lines
    s.transactions[1].category = Category.OTHER
    with pytest.raises(metrics.AmountError, match="reverses NOT-HERE"):
        metrics.compute(s)


@pytest.mark.contracts
def test_m9_names_only_the_credit_its_cap_splits():
    """T04 review 6: with £100 sent to ACME and £60 + £60 + £60 back, the cap is allocated in
    order: the first counts in full, the second in part, the third not at all; only the
    second is in the evidence note."""
    lines = [
        Transaction(id="O1", date=date(2026, 1, 1), description="FPS DEBIT ACME LTD", amount=-100.0, balance=900.0,
                    category=Category.SUPPLIER),
        Transaction(id="B1", date=date(2026, 1, 2), description="FPS CREDIT ACME LTD", amount=60.0, balance=960.0,
                    category=Category.OTHER),
        Transaction(id="B2", date=date(2026, 1, 3), description="FPS CREDIT ACME LTD", amount=60.0, balance=1020.0,
                    category=Category.OTHER),
        Transaction(id="B3", date=date(2026, 1, 4), description="FPS CREDIT ACME LTD", amount=60.0, balance=1080.0,
                    category=Category.OTHER),
    ]
    m = {x.id: x for x in metrics.compute(Statement(account_name="x", transactions=lines))}
    assert m["M9"].evidence_note == ["B2"]


@pytest.mark.contracts
@pytest.mark.parametrize("flow", ["in", "out"])
def test_an_import_refusal_says_which_flow_reached_the_limit(tmp_path, flow):
    from cma import ingest

    sign = "" if flow == "in" else "-"
    f = tmp_path / "s.csv"
    f.write_text("id,date,description,amount,balance\n"
                 f"L1,2026-01-05,A,{sign}6000000000000.00,{sign}6000000000000.00\n"
                 f"L2,2026-01-06,B,{sign}6000000000000.00,{sign}9999999999999.99\n")
    with pytest.raises(ingest.StatementError, match=f"money {flow} totals"):
        ingest.load_csv(f)


@pytest.mark.contracts
def test_a_statement_in_year_one_still_gets_its_reconciliation():
    """T04 review 7: a VAT quarter before year 1 is skipped, never a ValueError that would
    stop the reconciliation refusal."""
    lines = [Transaction(id="L1", date=date(1, 1, 5), description="a", amount=10.0, balance=10.0,
                         category=Category.REVENUE_CARD),
             Transaction(id="L2", date=date(1, 2, 5), description="b", amount=5.0, balance=15.0, category=None)]
    with pytest.raises(metrics.ReconciliationError) as e:
        metrics.compute(Statement(account_name="x", transactions=lines), vat_stagger=1)
    assert e.value.lines == ["L2"]


@pytest.mark.contracts
def test_a_statement_starting_at_the_first_date_python_has_still_gets_its_reconciliation():
    """T04 review 8: no VAT date arithmetic leaves the calendar."""
    lines = [Transaction(id="L1", date=date(1, 1, 1), description="a", amount=10.0, balance=10.0,
                         category=Category.REVENUE_CARD),
             Transaction(id="L2", date=date(1, 2, 5), description="b", amount=5.0, balance=15.0, category=None)]
    with pytest.raises(metrics.ReconciliationError) as e:
        metrics.compute(Statement(account_name="x", transactions=lines), vat_stagger=1)
    assert e.value.lines == ["L2"]


@pytest.mark.contracts
def test_the_monzo_opening_balance_is_read_exactly_from_the_command_line(tmp_path):
    """T04 review 8: --opening-balance is text read exactly; a third decimal is refused, never
    rounded through a float."""
    from typer.testing import CliRunner

    src = tmp_path / "m.csv"
    src.write_text("Transaction ID,Date,Time,Type,Name,Amount,Currency\n"
                   "tx_1,01/01/2026,09:00:00,Faster payment,ACME,10.00,GBP\n")
    r = CliRunner().invoke(app, ["import", "monzo", str(src), "-o", str(tmp_path / "o.csv"),
                                 "--opening-balance", "100.005"])
    assert r.exit_code != 0 and "--opening-balance '100.005'" in r.output and "line 0" not in r.output
    r = CliRunner().invoke(app, ["import", "monzo", str(src), "-o", str(tmp_path / "o.csv"),
                                 "--opening-balance", "100.05"])
    assert r.exit_code == 0 and "110.05" in (tmp_path / "o.csv").read_text()


@pytest.mark.contracts
def test_a_statement_ending_at_the_last_date_python_has_still_gets_its_reconciliation():
    """T04 review 9: no day-by-day loop steps past the statement's last day."""
    lines = [Transaction(id="L1", date=date(9999, 12, 30), description="a", amount=10.0, balance=10.0,
                         category=Category.REVENUE_CARD),
             Transaction(id="L2", date=date(9999, 12, 31), description="b", amount=5.0, balance=15.0, category=None)]
    with pytest.raises(metrics.ReconciliationError) as e:
        metrics.compute(Statement(account_name="x", transactions=lines), vat_stagger=1)
    assert e.value.lines == ["L2"]


@pytest.mark.contracts
def test_a_supplied_opening_balance_is_held_to_no_more_than_an_inferred_one(tmp_path):
    """T04 review 10: the opening balance is no line; a large one is accepted when every
    walked balance and amount is within R2's limits, whether supplied or inferred."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_text("Transaction ID,Date,Time,Type,Name,Amount,Currency\n"
                   "tx_1,01/01/2026,09:00:00,Faster payment,ACME,-10.00,GBP\n")
    r = monzo.convert(src, tmp_path / "o.csv", "10000000000000.00")
    assert r.rows == 1 and "9999999999990.00" in (tmp_path / "o.csv").read_text()


@pytest.mark.contracts
def test_returned_payment_matching_compares_exact_pence(monkeypatch):
    """T04 review 11: the match between a returned DD and the debit it undoes is decided in
    exact pence; float multiplication and a half-penny tolerance are not used."""
    lines = [
        Transaction(id="D1", date=date(2026, 1, 1), description="DD ACME", amount=-0.29, balance=-0.29,
                    category=Category.SUPPLIER),
        Transaction(id="R1", date=date(2026, 1, 2), description="UNPAID DD ACME", amount=0.29, balance=0.0,
                    category=Category.RETURNED_DD),
    ]
    s = categorise.link_reversals(Statement(account_name="x", transactions=lines))
    assert s.transactions[1].reverses == "D1"
    near = [lines[0].model_copy(update={"amount": -0.28, "balance": -0.28}),
            lines[1].model_copy(update={"reverses": None})]
    assert categorise.link_reversals(Statement(account_name="x", transactions=near)).transactions[1].reverses is None


@pytest.mark.contracts
def test_returned_payment_matching_takes_its_pence_from_the_figures_layer(monkeypatch):
    """T04 review 12: the categoriser does no money arithmetic of its own; the size it matches
    a returned DD to its debit by comes from metrics, the figures layer (R2)."""
    from cma import metrics

    lines = [
        Transaction(id="D1", date=date(2026, 1, 1), description="DD ACME", amount=-5.00, balance=-5.00,
                    category=Category.SUPPLIER),
        Transaction(id="R1", date=date(2026, 1, 2), description="UNPAID DD ACME", amount=7.00, balance=2.00,
                    category=Category.RETURNED_DD),
    ]
    st = Statement(account_name="x", transactions=lines)
    assert categorise.link_reversals(st).transactions[1].reverses is None
    monkeypatch.setattr(metrics, "size_pence", lambda t: 1)  # the figures layer decides the match
    assert categorise.link_reversals(st).transactions[1].reverses == "D1"


def _over_limit_csv(path, bad_lines: int, at_limit_line: bool = False):
    rows = ["id,date,description,amount,balance",
            "T1,2026-01-01,A,9999999999999.99,9999999999999.99",
            "T2,2026-01-02,B,9999999999999.99,9999999999999.99"]
    rows += [f"X{i},SECRET-DATE,C,1.00,1.00" for i in range(bad_lines)]
    if at_limit_line:
        rows.append("BIG,2026-01-03,D,10000000000000.00,1.00")
    path.write_text("\n".join(rows) + "\n")
    return path


@pytest.mark.contracts
def test_import_flow_totals_and_balance_chain_come_from_the_figures_layer(monkeypatch, tmp_path):
    """T04 review 13: the loader and the Monzo importer do no money arithmetic of their own;
    the flow totals, the balance chain and the walk are the figures layer's (R2)."""
    from cma.importers import monzo

    ok = tmp_path / "ok.csv"
    ok.write_text("id,date,description,amount,balance\nT1,2026-01-01,A,5.00,5.00\nT2,2026-01-02,B,1.00,6.00\n")
    ingest.load_csv(ok, today=date(2026, 6, 1))
    monkeypatch.setattr(ingest, "flows_at_limit", lambda amounts: ["money out"])
    with pytest.raises(ingest.StatementError, match="money out totals"):
        ingest.load_csv(ok, today=date(2026, 6, 1))
    monkeypatch.setattr(ingest, "follows", lambda *a: False)
    with pytest.raises(ingest.StatementError, match="line 3: running balance"):
        ingest.load_csv(ok, today=date(2026, 6, 1))

    src = tmp_path / "m.csv"
    src.write_text("Transaction ID,Date,Time,Type,Name,Amount,Currency,Balance\n"
                   "tx_1,01/01/2026,09:00:00,Faster payment,ACME,-10.00,GBP,90.00\n")
    assert monzo.convert(src, tmp_path / "o.csv").closing_balance == 90.0
    monkeypatch.setattr(monzo, "flows_at_limit", lambda amounts: ["money in"])
    with pytest.raises(monzo.MonzoImportError, match="money in totals"):
        monzo.convert(src, tmp_path / "o.csv")
    monkeypatch.setattr(monzo, "flows_at_limit", lambda amounts: [])
    monkeypatch.setattr(monzo, "running_balances", lambda opening, amounts: [opening + 1 for _ in amounts])
    with pytest.raises(monzo.MonzoImportError, match="walked balance"):
        monzo.convert(src, tmp_path / "o.csv")


@pytest.mark.contracts
def test_the_cli_says_which_flow_and_which_lines_reached_the_limit_however_many_lines_are_wrong(tmp_path):
    """T04 review 13: `cma run` shows the first problems only; the flow at the limit, and every
    line whose amount or balance is at it, are always among them."""
    csv = _over_limit_csv(tmp_path / "big.csv", 25, at_limit_line=True)
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(csv, today=date(2026, 6, 1))
    shown = str(e.value)
    assert "money in totals £10,000,000,000,000 or more" in shown
    assert f"line {3 + 25 + 1}: amount '10000000000000.00' is £10,000,000,000,000 or more" in shown
    r = CliRunner().invoke(app, ["run", str(csv), "--yes", "--reviewer", "A"])
    assert r.exit_code != 0 and "money in totals" in r.output and "line 29: amount" in r.output


@pytest.mark.contracts
@pytest.mark.parametrize("column", ["amount", "balance"])
def test_a_money_column_named_twice_is_refused_not_overwritten(tmp_path, column):
    """T04 review 14: a second column of the same name would replace the first's value on
    every line before any check, hiding an amount at the limit; both readers refuse it."""
    from cma.importers import monzo

    header = ["id", "date", "description", "amount", "balance", column]
    values = {"amount": "10000000000000.00", "balance": "10000000000000.00"}
    row = ["T1", "2026-01-01", "A", "5.00", "5.00", "5.00"]
    row[3 if column == "amount" else 4] = values[column]
    csv = tmp_path / "s.csv"
    csv.write_text(",".join(header) + "\n" + ",".join(row) + "\n")
    with pytest.raises(ingest.StatementError, match=f"named more than once: {column}"):
        ingest.load_csv(csv, today=date(2026, 6, 1))

    src = tmp_path / "m.csv"
    src.write_text("Transaction ID,Date,Time,Type,Name,Amount,Currency,Balance,Amount\n"
                   "tx_1,01/01/2026,09:00:00,Faster payment,ACME,10000000000000.00,GBP,90.00,-10.00\n")
    with pytest.raises(monzo.MonzoImportError, match="named more than once: Amount"):
        monzo.convert(src, tmp_path / "o.csv")


def test_blank_trailing_columns_are_still_accepted(tmp_path):
    """Exports with several empty trailing header cells are not refused as duplicates."""
    csv = tmp_path / "s.csv"
    csv.write_text("id,date,description,amount,balance,,\nT1,2026-01-01,A,5.00,5.00,,\n")
    assert len(ingest.load_csv(csv, today=date(2026, 6, 1)).transactions) == 1


@pytest.mark.contracts
def test_an_amount_longer_than_the_csv_reader_reads_is_refused_naming_its_line(tmp_path):
    """T04 review 19: the CSV reader stopped on a field past its size limit before any check,
    and `cma run` reported it as a model call failure."""
    huge = "1" + "0" * 200_000 + ".00"
    csv = tmp_path / "s.csv"
    csv.write_text("id,date,description,amount,balance\nT1,2026-01-01,A,5.00,5.00\n"
                   f"T2,2026-01-02,B,{huge},5.00\n")
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(csv, today=date(2026, 6, 1))
    assert "line 3: a field is too long to read" in str(e.value) and "£10,000,000,000,000 limit" in str(e.value)
    r = CliRunner().invoke(app, ["run", str(csv), "--yes", "--reviewer", "A"])
    assert r.exit_code != 0 and "line 3:" in r.output and "model call failed" not in r.output


@pytest.mark.contracts
def test_problems_name_the_line_a_person_sees_blank_lines_counted(tmp_path):
    """T04 review 20: the loader counted parsed records, so after a blank line every problem,
    and the refusal of an oversized amount, named the wrong line."""
    huge = "1" + "0" * 200_000 + ".00"
    csv = tmp_path / "s.csv"
    csv.write_text("id,date,description,amount,balance\n\nT1,2026-01-01,A,5.00,5.00\n\n"
                   "T2,2026-01-02,B,x,5.00\n")
    with pytest.raises(ingest.StatementError, match="line 5: amount 'x'"):
        ingest.load_csv(csv, today=date(2026, 6, 1))
    csv.write_text(f"id,date,description,amount,balance\n\nT1,2026-01-01,A,5.00,5.00\n\nT2,2026-01-02,B,{huge},5.00\n")
    with pytest.raises(ingest.StatementError, match="line 5: a field is too long to read"):
        ingest.load_csv(csv, today=date(2026, 6, 1))


@pytest.mark.contracts
def test_the_monzo_importer_refuses_an_oversized_field_naming_its_line(tmp_path):
    """T04 review 20: the Monzo importer raised the CSV reader's own error, which `cma import
    monzo` does not handle, before it could name the line."""
    from cma.importers import monzo

    huge = "1" + "0" * 200_000 + ".00"
    src = tmp_path / "m.csv"
    src.write_text("Transaction ID,Date,Time,Type,Name,Amount,Currency,Balance\n"
                   "tx_1,01/01/2026,09:00:00,Faster payment,ACME,-10.00,GBP,90.00\n\n"
                   f"tx_2,02/01/2026,09:00:00,Faster payment,ACME,{huge},GBP,90.00\n")
    with pytest.raises(monzo.MonzoImportError, match=r"line 4: a field is too long to read.*£10,000,000,000,000 limit"):
        monzo.convert(src, tmp_path / "o.csv")
    r = CliRunner().invoke(app, ["import", "monzo", str(src), "--out", str(tmp_path / "o.csv")])
    assert r.exit_code != 0 and "line 4: a field is too long to read" in " ".join(r.output.split())



@pytest.mark.contracts
def test_a_multiline_record_is_named_by_its_first_line(tmp_path):
    """T04 review 21: a record whose description runs over several lines was named by its
    last line, a continuation of the description, not the line the record starts on."""
    huge = "1" + "0" * 200_000 + ".00"
    csv = tmp_path / "s.csv"
    csv.write_text('id,date,description,amount,balance\nT1,2026-01-01,"A\nB\nC",x,5.00\n')
    with pytest.raises(ingest.StatementError, match="line 2: amount 'x'"):
        ingest.load_csv(csv, today=date(2026, 6, 1))
    csv.write_text(f'id,date,description,amount,balance\nT1,2026-01-01,A,5.00,5.00\n\n'
                   f'T2,2026-01-02,"two\nlines",{huge},5.00\n')
    with pytest.raises(ingest.StatementError, match="line 4: a field is too long to read"):
        ingest.load_csv(csv, today=date(2026, 6, 1))
