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
import re
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
    assert [x.id for x in metrics.compute(s)] == [f"M{i}" for i in range(1, 29)]


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
def test_mcp_compute_metrics_carries_m17_to_m28(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from cma import mcp_server

    # MCP serves with the offline lock off only (R29); this checks what it then answers.
    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "home" / "firm.yaml").write_text("offline_lock: false\n")
    monkeypatch.setenv("CMA_HOME", str(tmp_path / "home"))

    rows, truth = synth.generate("cafe", "good", end=date(2026, 9, 30))
    synth.write(rows, truth, tmp_path / "data", stem="cafe")
    monkeypatch.setenv("CMA_DATA_DIR", str(tmp_path / "data"))
    got = {m["id"]: m for m in mcp_server.compute_metrics("cafe.csv", vat_stagger=3)}
    assert [f"M{i}" for i in range(1, 29)] == list(got)
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

    # MCP serves with the offline lock off only (R29); this checks what it then answers.
    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "home" / "firm.yaml").write_text("offline_lock: false\n")
    monkeypatch.setenv("CMA_HOME", str(tmp_path / "home"))

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

    monkeypatch.undo()  # the importer's own output is checked by the loader (T28)
    src = tmp_path / "m.csv"
    src.write_text("Transaction ID,Date,Time,Type,Name,Amount,Currency,Balance\n"
                   "tx_1,01/01/2026,09:00:00,Faster payment,ACME,-10.00,GBP,90.00\n")
    assert monzo.convert(src, tmp_path / "o.csv").closing_balance == 90.0
    monkeypatch.setattr(monzo, "flows_at_limit", lambda amounts: ["money in"])
    with pytest.raises(monzo.MonzoImportError, match="money in totals"):
        monzo.convert(src, tmp_path / "o.csv")
    monkeypatch.setattr(monzo, "flows_at_limit", lambda amounts: [])
    # The walk is the importer contract's, shared by every importer (T28).
    from cma.importers import contract
    monkeypatch.setattr(contract, "running_balances", lambda opening, amounts: [opening + 1 for _ in amounts])
    with pytest.raises(monzo.MonzoImportError, match="balance walked from the opening balance disagrees"):
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
    with pytest.raises(monzo.MonzoImportError, match="line 1: columns 6 and 9 have the same name"):
        monzo.convert(src, tmp_path / "o.csv")


def test_blank_trailing_columns_are_still_accepted(tmp_path):
    """Exports with several empty trailing header cells are not refused as duplicates."""
    csv = tmp_path / "s.csv"
    csv.write_text("id,date,description,amount,balance,,\nT1,2026-01-01,A,5.00,5.00,,\n")
    assert len(ingest.load_csv(csv, today=date(2026, 6, 1)).transactions) == 1


@pytest.mark.contracts
def test_a_readable_amount_at_the_line_limit_counts_toward_money_in_and_out(tmp_path):
    """T04 review 60 / R2: an amount at the line limit was named as such but left out of money in
    and money out, so the flow it took to the limit, a problem of its own, went unnamed."""
    csv = tmp_path / "s.csv"
    csv.write_text("id,date,description,amount,balance\nT1,2026-01-01,A,5.00,5.00\n"
                   "T2,2026-01-02,B,10000000000000.00,5.00\nT3,2026-01-03,C,-10000000000000.00,5.00\n")
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(csv, today=date(2026, 6, 1))
    shown = str(e.value)
    assert "line 3: amount '10000000000000.00' is £10,000,000,000,000 or more" in shown
    assert "money in totals £10,000,000,000,000 or more" in shown
    assert "money out totals £10,000,000,000,000 or more" in shown


@pytest.mark.contracts
def test_an_amount_with_thousands_of_digits_is_named_on_its_line(tmp_path):
    """T04 review 61 / R2: an amount of more than 4,300 digits, within the CSV reader's field
    limit, raised Python's integer conversion error before its line or the flows were named."""
    from cma.importers import monzo

    huge = "9" * 5000 + ".00"
    csv = tmp_path / "s.csv"
    csv.write_text(f"id,date,description,amount,balance\nT1,2026-01-01,A,{huge},5.00\n")
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(csv, today=date(2026, 6, 1))
    assert "line 2: amount" in str(e.value) and "money in totals £10,000,000,000,000 or more" in str(e.value)
    assert metrics.text_pence(huge) > 10**5000
    export = tmp_path / "m.csv"
    export.write_text("Transaction ID,Date,Time,Type,Name,Amount,Currency,Balance\n"
                      f"tx_1,01/01/2026,09:00:00,Faster payment,ACME,{huge},GBP,90.00\n")
    with pytest.raises(monzo.MonzoImportError, match="line 2"):
        monzo.convert(export, tmp_path / "o.csv")


@pytest.mark.contracts
def test_a_balance_at_the_line_limit_takes_part_in_the_running_balance(tmp_path):
    """T04 review 61 / R2: an amount or balance at the line limit was left out of the running
    balance, so a chain fault its exact value shows went unnamed."""
    csv = tmp_path / "s.csv"
    csv.write_text("id,date,description,amount,balance\nT1,2026-01-01,A,5.00,5.00\n"
                   "T2,2026-01-02,B,1.00,10000000000000.00\nT3,2026-01-03,C,1.00,10000000000001.00\n")
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(csv, today=date(2026, 6, 1))
    shown = str(e.value)
    assert "line 3: balance '10000000000000.00' is £10,000,000,000,000 or more" in shown
    assert "line 3: running balance 10000000000000.00 does not follow from previous 5.00 + 1.00" in shown
    assert "line 4: running balance" not in shown  # 10000000000000.00 + 1.00 is 10000000000001.00


@pytest.mark.contracts
def test_a_balance_of_thousands_of_digits_that_does_not_follow_is_named(tmp_path):
    """T04 review 62 / R2: writing a running-balance fault with a balance of thousands of digits
    raised Python's integer-to-string limit before the line or the flows were named."""
    huge = "9" * 5000 + ".00"
    csv = tmp_path / "s.csv"
    csv.write_text(f"id,date,description,amount,balance\nT1,2026-01-01,A,1.00,{huge}\nT2,2026-01-02,B,1.00,5.00\n")
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(csv, today=date(2026, 6, 1))
    shown = str(e.value)
    assert "line 3: running balance 5.00 does not follow from previous 9999999999" in shown
    assert "(5,003 characters) + 1.00" in shown and "line 2: balance" in shown
    assert metrics.pounds_text(-(10**6000)).startswith("-1000") and len(metrics.pounds_text(10**6000)) == 6002


@pytest.mark.contracts
def test_monzo_walks_rows_by_the_time_their_date_cell_carries(tmp_path):
    """T04 review 62 / R2: a Date cell holding a timestamp lost its time, so rows of one day were
    walked by Transaction ID and a consistent export was refused."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_text("Transaction ID,Date,Type,Name,Amount,Currency,Balance\n"
                   "tx_b,2026-01-01T15:00:00Z,Faster payment,ACME,-5.00,GBP,85.00\n"
                   "tx_z,2026-01-01T09:00:00Z,Faster payment,ACME,-10.00,GBP,90.00\n")
    r = monzo.convert(src, tmp_path / "o.csv")
    assert r.opening_balance == 100.0 and r.closing_balance == 85.0
    assert [line.split(",")[0] for line in (tmp_path / "o.csv").read_text().splitlines()[1:]] == ["tx_z", "tx_b"]


@pytest.mark.contracts
def test_monzo_walks_timestamps_with_different_offsets_in_the_order_they_happened(tmp_path):
    """T04 review 63 / R2: timestamps in Date lost their UTC offset, so rows written with
    different offsets were walked by their wall-clock times and a consistent export was refused."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    # 10:30+02:00 is 08:30 UTC, before 09:00+00:00, though its wall-clock time is later.
    src.write_text("Transaction ID,Date,Type,Name,Amount,Currency,Balance\n"
                   "tx_a,2026-01-01T09:00:00+00:00,Faster payment,ACME,-5.00,GBP,85.00\n"
                   "tx_b,2026-01-01T10:30:00+02:00,Faster payment,ACME,-10.00,GBP,90.00\n")
    r = monzo.convert(src, tmp_path / "o.csv")
    assert r.opening_balance == 100.0 and r.closing_balance == 85.0
    assert [line.split(",")[0] for line in (tmp_path / "o.csv").read_text().splitlines()[1:]] == ["tx_b", "tx_a"]


@pytest.mark.contracts
def test_monzo_dates_offset_timestamps_in_utc_so_dates_keep_to_the_row_order(tmp_path):
    """T04 review 64 / R2: rows were walked by their moment in UTC but written with their local
    dates, so across midnight in different offsets the statement's dates ran against its row
    order and the statement loader refused, or reversed, the file the import wrote."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    # 23:30-02:00 on 1 January is 01:30 UTC on 2 January, after 00:30+00:00 on 2 January.
    src.write_text("Transaction ID,Date,Type,Name,Amount,Currency,Balance\n"
                   "tx_a,2026-01-01T23:30:00-02:00,Faster payment,ACME,-5.00,GBP,85.00\n"
                   "tx_b,2026-01-02T00:30:00+00:00,Faster payment,ACME,-10.00,GBP,90.00\n"
                   "tx_c,2026-01-03T09:00:00+00:00,Faster payment,ACME,-1.00,GBP,84.00\n")
    out = tmp_path / "o.csv"
    r = monzo.convert(src, out)
    assert (r.start, r.end) == (date(2026, 1, 2), date(2026, 1, 3))
    st = ingest.load_csv(out, today=date(2026, 6, 1))
    assert [t.id for t in st.transactions] == ["tx_b", "tx_a", "tx_c"]
    assert [str(t.date) for t in st.transactions] == ["2026-01-02", "2026-01-02", "2026-01-03"]


@pytest.mark.contracts
def test_a_record_too_long_to_read_with_a_bad_byte_names_both(tmp_path):
    """T04 review 58 / R2: a record with a field too long to read and a separate cell that is not
    UTF-8 was refused as too long only; the bad byte was found and then dropped."""
    csv = tmp_path / "s.csv"
    csv.write_bytes(b"id,date,description,amount,balance\nT1,2026-01-01,A,5.00,5.00\n"
                    b"T2,2026-01-02,B\xff," + b"1" * 200_000 + b",5.00\n")
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(csv, today=date(2026, 6, 1))
    shown = str(e.value)
    assert "line 3: a field is too long to read (amount): longer than 131,072 characters" in shown
    assert "not UTF-8 (byte 76)" in shown


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
    assert "line 3: a field is too long to read (amount): longer than 131,072 characters" in str(e.value)
    assert "£10,000,000,000,000" not in str(e.value)  # its length says nothing of its value (T04 review 40)
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
    # The field too long to read is the amount itself: it is named as too long to read, never
    # read as an amount (revision 29; T04 review 37).
    with pytest.raises(monzo.MonzoImportError, match=r"line 4: a field is too long to read \(Amount\): longer than 131,072 characters$"):
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


@pytest.mark.contracts
def test_records_before_an_unreadable_one_are_still_checked(tmp_path):
    """T04 review 22: a field too long to read stopped the reader and discarded the records
    before it unchecked, so an amount at the limit among them, and money in at the limit,
    went unreported."""
    from cma.importers import monzo

    big = "9999999999999.99"
    csv = tmp_path / "s.csv"
    csv.write_text("id,date,description,amount,balance\n"
                   "T1,2026-01-01,A,10000000000000.00,1.00\n"
                   f"T2,2026-01-02,B,{big},{big}\nT3,2026-01-03,C,{big},{big}\n"
                   "T4,2026-01-04," + "D" * 200_000 + ",1.00,1.00\n")
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(csv, today=date(2026, 6, 1))
    shown = str(e.value)
    assert "line 2: amount '10000000000000.00' is £10,000,000,000,000 or more" in shown
    assert "money in totals £10,000,000,000,000 or more" in shown and "line 5: a field is too long to read" in shown

    src = tmp_path / "m.csv"
    src.write_text("Transaction ID,Date,Time,Type,Name,Amount,Currency,Balance\n"
                   "tx_1,01/01/2026,09:00:00,Faster payment,ACME,10000000000000.00,GBP,90.00\n"
                   "tx_2,02/01/2026,09:00:00,Faster payment," + "N" * 200_000 + ",-1.00,GBP,89.00\n")
    with pytest.raises(monzo.MonzoImportError, match=re.escape("line 2: Amount is £10,000,000,000,000")):
        monzo.convert(src, tmp_path / "o.csv")


@pytest.mark.contracts
def test_monzo_says_money_in_at_the_limit_before_an_unreadable_field(tmp_path):
    """T04 review 23: a later field too long to read hid that money in among the records
    already read had reached the limit."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_text("Transaction ID,Date,Time,Type,Name,Amount,Currency,Balance\n"
                   "tx_1,01/01/2026,09:00:00,Faster payment,ACME,9999999999999.99,GBP,9999999999999.99\n"
                   "tx_2,02/01/2026,09:00:00,Faster payment,ACME,9999999999999.99,GBP,9999999999999.99\n"
                   "tx_3,03/01/2026,09:00:00,Faster payment," + "N" * 200_000 + ",-1.00,GBP,1.00\n")
    with pytest.raises(monzo.MonzoImportError) as e:
        monzo.convert(src, tmp_path / "o.csv")
    assert "money in totals £10,000,000,000,000 or more" in str(e.value)
    assert "line 4: a field is too long to read" in str(e.value)


@pytest.mark.contracts
def test_monzo_says_money_in_at_the_limit_before_any_later_bad_row(tmp_path):
    """T04 review 24: a later row with an invalid date hid that money in among the rows
    already read had reached the limit."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_text("Transaction ID,Date,Time,Type,Name,Amount,Currency,Balance\n"
                   "tx_1,01/01/2026,09:00:00,Faster payment,ACME,9999999999999.99,GBP,9999999999999.99\n"
                   "tx_2,02/01/2026,09:00:00,Faster payment,ACME,9999999999999.99,GBP,9999999999999.99\n"
                   "tx_3,not a date,09:00:00,Faster payment,ACME,-1.00,GBP,1.00\n")
    with pytest.raises(monzo.MonzoImportError) as e:
        monzo.convert(src, tmp_path / "o.csv")
    assert "money in totals £10,000,000,000,000 or more" in str(e.value) and "line 4: unrecognised date" in str(e.value)


@pytest.mark.contracts
@pytest.mark.parametrize("balance", ["first row blank", "no column"])
def test_monzo_says_money_in_at_the_limit_before_the_opening_balance(tmp_path, balance):
    """T04 review 25: a fully read export whose money in reached the limit was refused for
    its missing opening balance instead, without saying so."""
    from cma.importers import monzo

    big = "9999999999999.99"
    head = "Transaction ID,Date,Time,Type,Name,Amount,Currency" + (",Balance" if balance != "no column" else "")
    tail = "," if balance != "no column" else ""
    src = tmp_path / "m.csv"
    src.write_text(f"{head}\ntx_1,01/01/2026,09:00:00,Faster payment,ACME,{big},GBP{tail}\n"
                   f"tx_2,02/01/2026,09:00:00,Faster payment,ACME,{big},GBP{tail}\n")
    with pytest.raises(monzo.MonzoImportError, match="money in totals £10,000,000,000,000 or more"):
        monzo.convert(src, tmp_path / "o.csv")


@pytest.mark.contracts
def test_monzo_counts_the_row_that_reaches_the_limit_even_if_its_balance_is_bad(tmp_path):
    """T04 review 26: the row whose amount takes money in to the limit had a malformed
    Balance, and only rows already accepted were totalled, so the limit went unsaid."""
    from cma.importers import monzo

    big = "9999999999999.99"
    src = tmp_path / "m.csv"
    src.write_text("Transaction ID,Date,Time,Type,Name,Amount,Currency,Balance\n"
                   f"tx_1,01/01/2026,09:00:00,Faster payment,ACME,{big},GBP,{big}\n"
                   f"tx_2,02/01/2026,09:00:00,Faster payment,ACME,{big},GBP,not money\n")
    with pytest.raises(monzo.MonzoImportError) as e:
        monzo.convert(src, tmp_path / "o.csv")
    assert "money in totals £10,000,000,000,000 or more" in str(e.value) and "line 3: Balance is not a plain amount" in str(e.value)


@pytest.mark.contracts
def test_monzo_says_the_limit_when_the_crossing_row_has_a_bad_date(tmp_path):
    """T04 review 27: the date was read before the amount, so a bad date on the row that
    takes money in to the limit was reported instead of the limit."""
    from cma.importers import monzo

    big = "9999999999999.99"
    src = tmp_path / "m.csv"
    src.write_text("Transaction ID,Date,Time,Type,Name,Amount,Currency,Balance\n"
                   f"tx_1,01/01/2026,09:00:00,Faster payment,ACME,{big},GBP,{big}\n"
                   f"tx_2,someday,09:00:00,Faster payment,ACME,{big},GBP,{big}\n")
    with pytest.raises(monzo.MonzoImportError) as e:
        monzo.convert(src, tmp_path / "o.csv")
    assert "money in totals £10,000,000,000,000 or more" in str(e.value) and "line 3: unrecognised date" in str(e.value)


_MONZO_HEAD = "Transaction ID,Date,Time,Type,Name,Amount,Currency,Balance\n"


@pytest.mark.contracts
def test_monzo_skips_another_currency_before_checking_its_id(tmp_path):
    """T04 review 28: the Transaction ID was checked before the currency, so a row in another
    currency, which is skipped, could refuse the export."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_text(_MONZO_HEAD + "tx_1,01/01/2026,09:00:00,Faster payment,ACME,-10.00,GBP,90.00\n"
                   "bad id!,02/01/2026,09:00:00,Card payment,SHOP,-5.00,EUR,\n")
    assert monzo.convert(src, tmp_path / "o.csv").skipped_non_gbp == 1


@pytest.mark.contracts
def test_monzo_names_the_line_of_a_bad_date_and_a_walked_balance(tmp_path):
    """T04 review 28: a malformed date had no line, and a walked balance refusal gave only
    the transaction id."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_text(_MONZO_HEAD + "tx_1,01/01/2026,09:00:00,Faster payment,ACME,-10.00,GBP,90.00\n\n"
                   "tx_2,someday,09:00:00,Faster payment,ACME,-1.00,GBP,89.00\n")
    with pytest.raises(monzo.MonzoImportError, match="line 4: unrecognised date;"):
        monzo.convert(src, tmp_path / "o.csv")
    src.write_text(_MONZO_HEAD + "tx_1,01/01/2026,09:00:00,Faster payment,ACME,-10.00,GBP,90.00\n\n"
                   "tx_2,02/01/2026,09:00:00,Faster payment,ACME,-1.00,GBP,50.00\n")
    with pytest.raises(monzo.MonzoImportError, match="line 4: the balance walked from the opening balance disagrees"):
        monzo.convert(src, tmp_path / "o.csv")


@pytest.mark.contracts
def test_monzo_counts_an_amount_past_the_line_limit_toward_money_in(tmp_path):
    """T04 review 28: an amount itself past the line limit was refused before it was counted,
    so money in reaching the limit went unsaid."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_text(_MONZO_HEAD + "tx_1,01/01/2026,09:00:00,Faster payment,ACME,1.00,GBP,1.00\n"
                   "tx_2,02/01/2026,09:00:00,Faster payment,ACME,10000000000000.00,GBP,1.00\n")
    with pytest.raises(monzo.MonzoImportError) as e:
        monzo.convert(src, tmp_path / "o.csv")
    assert "money in totals £10,000,000,000,000 or more" in str(e.value) and "line 3: Amount" in str(e.value)


@pytest.mark.contracts
def test_monzo_refuses_a_row_missing_its_date_cell_naming_it(tmp_path):
    """T04 review 29: a row ending before its Date cell read the date as None and raised an
    AttributeError, hiding the first problem and money in at the limit."""
    from cma.importers import monzo

    big = "9999999999999.99"
    src = tmp_path / "m.csv"
    src.write_text("Transaction ID,Type,Name,Amount,Currency,Balance,Date\n"
                   f"tx_1,Faster payment,ACME,{big},GBP,{big},01/01/2026\n"
                   f"tx_2,Faster payment,ACME,{big},GBP,{big}\n")
    with pytest.raises(monzo.MonzoImportError) as e:
        monzo.convert(src, tmp_path / "o.csv")
    assert "money in totals £10,000,000,000,000 or more" in str(e.value) and "line 3: unrecognised date" in str(e.value)


@pytest.mark.contracts
def test_a_later_byte_that_is_not_utf8_does_not_hide_earlier_problems(tmp_path):
    """T04 review 29: the whole file was decoded first, so a bad byte late in it hid a
    problem, and money in at the limit, among the lines before it."""
    from cma.importers import monzo

    big = "9999999999999.99"
    src = tmp_path / "m.csv"
    src.write_bytes((_MONZO_HEAD + f"tx_1,01/01/2026,09:00:00,Faster payment,ACME,{big},GBP,{big}\n"
                     f"tx_2,02/01/2026,09:00:00,Faster payment,ACME,{big},GBP,{big}\n").encode()
                    + b"tx_3,03/01/2026,09:00:00,Faster payment,CAF\xe9,-1.00,GBP,1.00\n")
    with pytest.raises(monzo.MonzoImportError) as e:
        monzo.convert(src, tmp_path / "o.csv")
    assert "money in totals £10,000,000,000,000 or more" in str(e.value) and "line 4: not UTF-8" in str(e.value)

    csv = tmp_path / "s.csv"
    csv.write_bytes(b"id,date,description,amount,balance\nT1,2026-01-01,A,10000000000000.00,1.00\n"
                    b"T2,2026-01-02,CAF\xe9,1.00,2.00\n")
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(csv, today=date(2026, 6, 1))
    assert "line 2: amount '10000000000000.00' is £10,000,000,000,000 or more" in str(e.value)
    assert "line 3: not UTF-8" in str(e.value)


@pytest.mark.contracts
def test_monzo_opening_balance_refusals_name_the_line(tmp_path):
    """T04 review 29: the opening-balance refusals named no line."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_text(_MONZO_HEAD + "\ntx_1,01/01/2026,09:00:00,Faster payment,ACME,-10.00,GBP,\n")
    with pytest.raises(monzo.MonzoImportError, match="line 3: the first transaction has no balance"):
        monzo.convert(src, tmp_path / "o.csv")
    src.write_text(_MONZO_HEAD + "\ntx_1,01/01/2026,09:00:00,Faster payment,ACME,-10.00,GBP,90.00\n")
    with pytest.raises(monzo.MonzoImportError, match=r"implies 100\.00 at line 3, the first transaction"):
        monzo.convert(src, tmp_path / "o.csv", "50.00")


@pytest.mark.contracts
def test_a_bad_byte_in_a_cr_only_export_still_reads_the_lines_before_it(tmp_path):
    """T04 review 30: the UTF-8 recovery cut at the last LF, so in a file with CR line
    endings every record before a bad byte was discarded."""
    from cma.importers import monzo

    big = "9999999999999.99"
    src = tmp_path / "m.csv"
    rows = [_MONZO_HEAD.strip(), f"tx_1,01/01/2026,09:00:00,Faster payment,ACME,{big},GBP,{big}",
            f"tx_2,02/01/2026,09:00:00,Faster payment,ACME,{big},GBP,{big}"]
    src.write_bytes("\r".join(rows).encode() + b"\rtx_3,03/01/2026,09:00:00,Faster payment,CAF\xe9,-1.00,GBP,1.00\r")
    with pytest.raises(monzo.MonzoImportError) as e:
        monzo.convert(src, tmp_path / "o.csv")
    assert "money in totals £10,000,000,000,000 or more" in str(e.value) and "line 4: not UTF-8" in str(e.value)


@pytest.mark.contracts
def test_a_bad_byte_in_a_multiline_record_names_the_record_start(tmp_path):
    """T04 review 30: a bad byte on the continuation of a quoted multi-line field was named by
    that continuation line, not the line its record starts on."""
    csv = tmp_path / "s.csv"
    csv.write_bytes(b'id,date,description,amount,balance\nT1,2026-01-01,A,1.00,1.00\n'
                    b'T2,2026-01-02,"first line\nCAF\xe9",1.00,2.00\n')
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(csv, today=date(2026, 6, 1))
    assert "line 3: not UTF-8" in str(e.value) and "line 4" not in str(e.value)


@pytest.mark.contracts
def test_a_bad_byte_after_a_byte_order_mark_still_reads_the_lines_before_it(tmp_path):
    """T04 review 31: the bad byte's offset was taken after the byte-order mark was dropped
    and used to slice bytes that still had it, cutting off the record before the bad one."""
    csv = tmp_path / "s.csv"
    csv.write_bytes(b"\xef\xbb\xbfid,date,description,amount,balance\n"
                    b"T1,2026-01-01,A,10000000000000.00,1.00\n"
                    b"T2,2026-01-02,CAF\xe9,1.00,2.00\n")
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(csv, today=date(2026, 6, 1))
    shown = str(e.value)
    assert "line 2: amount '10000000000000.00' is £10,000,000,000,000 or more" in shown
    assert "line 3: not UTF-8 (byte 94)" in shown


@pytest.mark.contracts
def test_cma_import_monzo_prints_a_refusal_quoting_markup_as_text(tmp_path):
    """T04 review 31: the refusal was printed as Rich markup, so a cell holding a closing tag
    raised a markup error and the refusal (its line, the flow limit) was lost."""
    src = tmp_path / "m.csv"
    src.write_text(_MONZO_HEAD + "tx_1,01/01/2026,09:00:00,Faster payment,ACME,[/red],GBP,1.00\n")
    r = CliRunner().invoke(app, ["import", "monzo", str(src), "--out", str(tmp_path / "o.csv")])
    flat = " ".join(r.output.split())
    # No cell is quoted (the importer contract, T28), so the refusal names the line alone.
    assert r.exit_code == 2 and "line 2: Amount is not a plain amount" in flat and "[/red]" not in flat, r.output


@pytest.mark.contracts
def test_a_quoted_field_left_open_at_the_end_is_refused_not_swallowing_lines(tmp_path):
    """T04 review 32: a quoted field never closed took every line after it, another
    transaction included, and the Monzo import still succeeded."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_text(_MONZO_HEAD + 'tx_1,01/01/2026,09:00:00,Faster payment,"ACME,-10.00,GBP,90.00\n'
                   "tx_2,02/01/2026,09:00:00,Faster payment,ACME,-1.00,GBP,89.00\n")
    with pytest.raises(monzo.MonzoImportError, match="line 2: a quoted field is not closed"):
        monzo.convert(src, tmp_path / "o.csv")
    csv = tmp_path / "s.csv"
    csv.write_text('id,date,description,amount,balance\nT1,2026-01-01,"A,1.00,1.00\nT2,2026-01-02,B,1.00,2.00\n')
    with pytest.raises(ingest.StatementError, match="line 2: a quoted field is not closed"):
        ingest.load_csv(csv, today=date(2026, 6, 1))


@pytest.mark.contracts
def test_a_record_the_reader_accepts_is_kept_before_a_bad_byte(tmp_path):
    """T04 review 32: the bad-byte recovery re-read the last record strictly and took any
    error as an open quote, dropping a record the reader had accepted ("b"x is read as bx)."""
    csv = tmp_path / "s.csv"
    csv.write_bytes(b'id,date,description,amount,balance\nT1,2026-01-01,"A"x,10000000000000.00,1.00\n'
                    b"\xe9T2,2026-01-02,CAFE,1.00,2.00\n")
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(csv, today=date(2026, 6, 1))
    assert "line 2: amount '10000000000000.00' is £10,000,000,000,000 or more" in str(e.value)
    assert "line 3: not UTF-8" in str(e.value)


@pytest.mark.contracts
def test_a_bad_byte_in_a_multiline_header_is_on_line_one(tmp_path):
    """T04 review 33: a bad byte on the second line of a header whose quoted name runs over
    two lines was reported as a new record on line 2."""
    csv = tmp_path / "s.csv"
    csv.write_bytes(b'id,date,"desc\nription\xe9",amount,balance\nT1,2026-01-01,A,1.00,1.00\n')
    with pytest.raises(ingest.StatementError, match="line 1: not UTF-8"):
        ingest.load_csv(csv, today=date(2026, 6, 1))


@pytest.mark.contracts
def test_monzo_refuses_a_row_with_more_cells_than_columns(tmp_path):
    """T04 review 33: surplus cells were ignored, so an unquoted 1,000.00 was imported as 1.00."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_text(_MONZO_HEAD + "tx_1,01/01/2026,09:00:00,Faster payment,ACME,1,000.00,GBP,1000.00\n")
    with pytest.raises(monzo.MonzoImportError, match=r"line 2: .*more fields than the header has columns"):
        monzo.convert(src, tmp_path / "o.csv")


@pytest.mark.contracts
def test_monzo_reads_currency_and_amount_before_the_surplus_cell_check(tmp_path):
    """T04 review 34: the surplus-cell check ran before the currency and the amount, so a row
    in another currency was refused instead of skipped, and a GBP amount with a trailing extra
    cell was not counted toward money in at the limit (revision 29)."""
    from cma.importers import monzo

    big = "9999999999999.99"
    src = tmp_path / "m.csv"
    src.write_text(_MONZO_HEAD + "tx_1,01/01/2026,09:00:00,Faster payment,ACME,-10.00,GBP,90.00\n"
                   "tx_2,02/01/2026,09:00:00,Card payment,SHOP,-5.00,EUR,,extra\n")
    assert monzo.convert(src, tmp_path / "o.csv").skipped_non_gbp == 1
    src.write_text(_MONZO_HEAD + f"tx_1,01/01/2026,09:00:00,Faster payment,ACME,{big},GBP,{big}\n"
                   f"tx_2,02/01/2026,09:00:00,Faster payment,ACME,{big},GBP,{big},extra\n")
    with pytest.raises(monzo.MonzoImportError) as e:
        monzo.convert(src, tmp_path / "o.csv")
    assert "money in totals £10,000,000,000,000 or more" in str(e.value)
    assert "line 3: more fields than the header has columns" in str(e.value)


@pytest.mark.contracts
def test_monzo_refuses_an_amount_past_the_line_limit_before_a_surplus_cell(tmp_path):
    """T04 review 35: the surplus-cell check came before the amount's own line limit, so an
    amount that must itself be refused was reported as a surplus field instead."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_text(_MONZO_HEAD + "tx_1,01/01/2026,09:00:00,Faster payment,ACME,10000000000000.00,GBP,1.00,extra\n")
    with pytest.raises(monzo.MonzoImportError, match=r"line 2: Amount is £10,000,000,000,000"):
        monzo.convert(src, tmp_path / "o.csv")


@pytest.mark.contracts
def test_monzo_checks_the_amount_of_a_row_with_a_later_field_too_long_to_read(tmp_path):
    """T04 review 36: the CSV reader stopped on a description too long to read before the
    row's currency and amount were seen, hiding the amount's own limit and money in at it."""
    from cma.importers import monzo

    big = "9999999999999.99"
    src = tmp_path / "m.csv"
    src.write_text("Transaction ID,Date,Time,Type,Amount,Currency,Balance,Name\n"
                   f"tx_1,01/01/2026,09:00:00,Faster payment,{big},GBP,{big},ACME\n"
                   f"tx_2,02/01/2026,09:00:00,Faster payment,10000000000000.00,GBP,1.00," + "N" * 200_000 + "\n")
    with pytest.raises(monzo.MonzoImportError) as e:
        monzo.convert(src, tmp_path / "o.csv")
    msg = str(e.value)
    assert "money in totals £10,000,000,000,000 or more" in msg
    assert "line 3: Amount is £10,000,000,000,000 or more" in msg and "NNNN" not in msg

    csv = tmp_path / "s.csv"
    csv.write_text("id,date,amount,balance,description\n"
                   "T1,2026-01-01,10000000000000.00,1.00," + "D" * 200_000 + "\n")
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(csv, today=date(2026, 6, 1))
    assert "line 2: amount '10000000000000.00' is £10,000,000,000,000 or more" in str(e.value)
    assert "line 2: a field is too long to read" in str(e.value)


@pytest.mark.contracts
def test_a_readable_amount_before_a_bad_byte_in_its_row_still_counts(tmp_path):
    """T04 review 37: the UTF-8 recovery dropped the whole record a bad byte fell in, so a
    readable amount before the bad byte never counted toward money in at the limit."""
    from cma.importers import monzo

    big = "9999999999999.99"
    src = tmp_path / "m.csv"
    src.write_bytes(("Transaction ID,Date,Time,Type,Amount,Currency,Balance,Name\n"
                     f"tx_1,01/01/2026,09:00:00,Faster payment,{big},GBP,{big},ACME\n"
                     f"tx_2,02/01/2026,09:00:00,Faster payment,{big},GBP,{big},").encode() + b"CAF\xe9\n")
    with pytest.raises(monzo.MonzoImportError) as e:
        monzo.convert(src, tmp_path / "o.csv")
    assert "money in totals £10,000,000,000,000 or more" in str(e.value) and "line 3: not UTF-8" in str(e.value)

    csv = tmp_path / "s.csv"
    csv.write_bytes(f"id,date,amount,balance,description\nT1,2026-01-01,{big},{big},A\nT2,2026-01-02,{big},1.00,"
                    .encode() + b"CAF\xe9\n")
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(csv, today=date(2026, 6, 1))
    assert "money in totals £10,000,000,000,000 or more" in str(e.value) and "line 3: not UTF-8" in str(e.value)


@pytest.mark.contracts
@pytest.mark.parametrize("damage", ["bad byte", "open quote"])
def test_monzo_counts_a_readable_amount_in_a_row_that_cannot_be_read_whole(tmp_path, damage):
    """T04 review 38: a row that cannot be read whole lost its readable amount when the export
    has no Currency column (every row is GBP), or when its last field is a quote never closed."""
    from cma.importers import monzo

    big = "9999999999999.99"
    head = "Transaction ID,Date,Time,Type,Amount,Balance,Name\n"
    first = f"tx_1,01/01/2026,09:00:00,Faster payment,{big},{big},ACME\n"
    second = f"tx_2,02/01/2026,09:00:00,Faster payment,{big},{big},".encode()
    tail = b"CAF\xe9\n" if damage == "bad byte" else b'"CAFE\n'
    src = tmp_path / "m.csv"
    src.write_bytes((head + first).encode() + second + tail)
    with pytest.raises(monzo.MonzoImportError) as e:
        monzo.convert(src, tmp_path / "o.csv")
    assert "money in totals £10,000,000,000,000 or more" in str(e.value) and "line 3:" in str(e.value)


@pytest.mark.contracts
def test_monzo_checks_a_partial_rows_currency_before_needing_its_amount(tmp_path):
    """T04 review 39: a row whose Amount was too long to read reported that before its
    readable Currency, hiding an invalid currency (revision 29: currency first)."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_text(_MONZO_HEAD + "tx_1,01/01/2026,09:00:00,Faster payment,ACME," + "1" * 200_000 + ",X1,1.00\n")
    with pytest.raises(monzo.MonzoImportError, match="line 2: Currency is not a currency code"):
        monzo.convert(src, tmp_path / "o.csv")


@pytest.mark.contracts
def test_reading_a_long_record_never_changes_the_process_wide_csv_limit(tmp_path, monkeypatch):
    """T04 review 39: the recovery raised csv.field_size_limit for the whole process, so a
    load running at the same moment could accept a field it must refuse."""
    import csv as csv_module

    from cma.importers import monzo

    real = csv_module.field_size_limit
    changes = []

    def spy(*args):
        if args:
            changes.append(args[0])
        return real(*args)

    monkeypatch.setattr(csv_module, "field_size_limit", spy)
    src = tmp_path / "m.csv"
    src.write_text(_MONZO_HEAD + "tx_1,01/01/2026,09:00:00,Faster payment," + "N" * 200_000 + ",-1.00,GBP,1.00\n")
    with pytest.raises(monzo.MonzoImportError, match="line 2: a field is too long to read"):
        monzo.convert(src, tmp_path / "o.csv")
    assert changes == []


@pytest.mark.contracts
def test_a_long_field_cut_by_a_bad_byte_never_makes_an_empty_amount_readable(tmp_path):
    """T04 review 41: a bad byte cut the text inside a record whose earlier field was too long
    to read; the columns after the cut were filled in as empty and taken as readable, so Monzo
    refused a made-up empty Amount instead of naming the field too long to read."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_bytes(("Transaction ID,Date,Time,Type,Name,Amount,Currency,Balance\n"
                     "tx_1,01/01/2026,09:00:00,Faster payment," + "N" * 200_000).encode() + b"\xe9,-1.00,GBP,1.00\n")
    with pytest.raises(monzo.MonzoImportError, match=r"line 2: a field is too long to read \(Name\)"):
        monzo.convert(src, tmp_path / "o.csv")


@pytest.mark.contracts
def test_a_whole_long_record_keeps_its_last_cell_when_a_later_record_has_a_bad_byte(tmp_path):
    """T04 review 42: a bad byte in a later record made the last cell of an earlier, whole
    record with a field too long to read count as cut, dropping its readable amount."""
    from cma.importers import monzo

    big = "9999999999999.99"
    head = "Transaction ID,Date,Time,Type,Name,Currency,Balance,Amount\n"
    src = tmp_path / "m.csv"
    src.write_bytes((head + f"tx_1,01/01/2026,09:00:00,Faster payment,ACME,GBP,{big},{big}\n"
                     f"tx_2,02/01/2026,09:00:00,Faster payment," + "N" * 200_000 + f",GBP,{big},{big}\n").encode()
                    + b"tx_3,03/01/2026,09:00:00,Faster payment,CAF\xe9,GBP,1.00,-1.00\n")
    with pytest.raises(monzo.MonzoImportError) as e:
        monzo.convert(src, tmp_path / "o.csv")
    assert "money in totals £10,000,000,000,000 or more" in str(e.value)
    assert "line 3: a field is too long to read (Name)" in str(e.value)


@pytest.mark.contracts
def test_a_readable_balance_at_the_limit_is_named_in_a_record_with_a_long_field(tmp_path):
    """T04 review 43: in a record with a description too long to read, only the readable
    amount was checked; a readable balance at the limit went unnamed."""
    csv = tmp_path / "s.csv"
    csv.write_text("id,date,amount,balance,description\n"
                   "T1,2026-01-01,1.00,10000000000000.00," + "D" * 200_000 + "\n")
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(csv, today=date(2026, 6, 1))
    assert "line 2: balance '10000000000000.00' is £10,000,000,000,000 or more" in str(e.value)


@pytest.mark.contracts
@pytest.mark.parametrize("ending", ["\n", "\r", "\r\n"])
def test_a_bad_byte_in_the_first_data_record_is_on_its_line_with_its_amount_read(tmp_path, ending):
    """T04 review 44: a bad byte in the first data record, with no whole data record before
    it, is that record's line (2), never the header's (1), and its readable amount is still
    checked (here past the line limit), in the loader and the Monzo importer."""
    from cma.importers import monzo

    csv = tmp_path / "s.csv"
    csv.write_bytes(f"id,date,amount,balance,description{ending}T1,2026-01-01,10000000000000.00,1.00,".encode()
                    + b"CAF\xe9" + ending.encode())
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(csv, today=date(2026, 6, 1))
    assert "line 2: not UTF-8" in str(e.value) and "line 1" not in str(e.value)
    assert "line 2: amount '10000000000000.00' is £10,000,000,000,000 or more" in str(e.value)

    src = tmp_path / "m.csv"
    src.write_bytes(f"Transaction ID,Amount,Currency,Date,Time,Type,Name,Balance{ending}".encode()
                    + b"tx_1,10000000000000.00,GBP,01/01/2026,09:00:00,Faster payment,CAF\xe9,1.00" + ending.encode())
    with pytest.raises(monzo.MonzoImportError, match=r"line 2: Amount is £10,000,000,000,000"):
        monzo.convert(src, tmp_path / "o.csv")


@pytest.mark.contracts
def test_a_header_field_too_long_is_named_before_a_later_bad_byte_in_it(tmp_path):
    """T04 review 45: a bad byte later in the header hid a header field before it already too
    long for the CSV reader, which the reader names first."""
    from cma.importers import monzo

    csv = tmp_path / "s.csv"
    csv.write_bytes(b"id," + b"H" * 200_000 + b",amount,CAF\xe9\nT1,1,2,3\n")
    with pytest.raises(ingest.StatementError, match="line 1: a field is too long to read"):
        ingest.load_csv(csv, today=date(2026, 6, 1))
    with pytest.raises(monzo.MonzoImportError, match="line 1: a field is too long to read"):
        monzo.convert(csv, tmp_path / "o.csv")


@pytest.mark.contracts
def test_monzo_skips_a_foreign_row_with_a_bad_byte_and_reads_on(tmp_path):
    """T04 review 46: a row in another currency, which the importer skips, refused the export
    when it also held a byte that is not UTF-8; reading now goes on past it."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_bytes((_MONZO_HEAD + "tx_1,01/01/2026,09:00:00,Faster payment,ACME,-10.00,GBP,90.00\n").encode()
                    + b"tx_2,02/01/2026,09:00:00,Card payment,CAF\xe9,-5.00,EUR,\n"
                    + b"tx_3,03/01/2026,09:00:00,Faster payment,ACME,-1.00,GBP,89.00\n")
    r = monzo.convert(src, tmp_path / "o.csv")
    assert r.rows == 2 and r.skipped_non_gbp == 1
    src.write_bytes((_MONZO_HEAD + "tx_1,01/01/2026,09:00:00,Faster payment,ACME,-10.00,GBP,90.00\n").encode()
                    + b"tx_2,02/01/2026,09:00:00,Card payment,CAF\xe9,-5.00,EUR,\n"
                    + b"tx_3,03/01/2026,09:00:00,Faster payment,B\xe9,-1.00,GBP,89.00\n")
    with pytest.raises(monzo.MonzoImportError, match="line 4: not UTF-8"):
        monzo.convert(src, tmp_path / "o.csv")


@pytest.mark.contracts
@pytest.mark.parametrize("ending", ["\n", "\r", "\r\n"])
def test_monzo_skips_a_foreign_row_with_a_field_too_long_to_read_and_reads_on(tmp_path, ending):
    """T04 review 47: a row in another currency with a field too long for the CSV reader
    stopped reading and refused the export; reading now goes on past it."""
    from cma.importers import monzo

    head = "Transaction ID,Date,Time,Type,Name,Amount,Currency,Balance"
    rows = [head, "tx_1,01/01/2026,09:00:00,Faster payment,ACME,-10.00,GBP,90.00",
            "tx_2,02/01/2026,09:00:00,Card payment," + "N" * 200_000 + ",-5.00,EUR,",
            "tx_3,03/01/2026,09:00:00,Faster payment,ACME,-1.00,GBP,89.00"]
    src = tmp_path / "m.csv"
    src.write_text(ending.join(rows) + ending, newline="")
    r = monzo.convert(src, tmp_path / "o.csv")
    assert r.rows == 2 and r.skipped_non_gbp == 1
    rows[2] = rows[2].replace("EUR", "GBP").replace("-5.00,GBP,", "-5.00,GBP,85.00")
    src.write_text(ending.join(rows) + ending, newline="")
    with pytest.raises(monzo.MonzoImportError, match=r"line 3: a field is too long to read \(Name\)"):
        monzo.convert(src, tmp_path / "o.csv")


@pytest.mark.contracts
def test_a_bad_byte_in_a_long_record_after_a_skipped_foreign_row_is_never_read_as_money(tmp_path):
    """T04 review 48: after a skipped foreign row with a bad byte, a later record with a field
    too long to read kept a cell with a bad byte as readable, and its amount was read."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_bytes((_MONZO_HEAD + "tx_1,01/01/2026,09:00:00,Faster payment,ACME,-10.00,GBP,90.00\n").encode()
                    + b"tx_2,02/01/2026,09:00:00,Card payment,CAF\xe9,-5.00,EUR,\n"
                    + b"tx_3,03/01/2026,09:00:00,Faster payment," + b"N" * 200_000 + b",-1.\xe900,GBP,89.00\n")
    with pytest.raises(monzo.MonzoImportError) as e:
        monzo.convert(src, tmp_path / "o.csv")
    assert "line 4: a field is too long to read (Name)" in str(e.value) and "Amount" not in str(e.value)


@pytest.mark.contracts
def test_a_foreign_long_record_open_to_the_end_still_refuses_the_export(tmp_path):
    """T04 review 49: a foreign row whose too-long quoted field was never closed swallowed the
    lines after it, and skipping it as another currency exported a truncated statement."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_text(_MONZO_HEAD + "tx_1,01/01/2026,09:00:00,Faster payment,ACME,-10.00,GBP,90.00\n"
                   'tx_2,02/01/2026,09:00:00,Card payment,SHOP,-5.00,EUR,"' + "N" * 200_000 + "\n"
                   "tx_3,03/01/2026,09:00:00,Faster payment,ACME,-1.00,GBP,89.00\n")
    with pytest.raises(monzo.MonzoImportError, match=r"line 3: a field is too long to read.*not closed"):
        monzo.convert(src, tmp_path / "o.csv")


@pytest.fixture
def small_field_limit():
    """The CSV reader's field limit lowered for one test, and restored."""
    import csv as csv_module

    old = csv_module.field_size_limit()
    csv_module.field_size_limit(20)
    yield 20
    csv_module.field_size_limit(old)


@pytest.mark.contracts
def test_reading_on_past_many_long_records_never_recurses(tmp_path, small_field_limit):
    """T04 review 50: reading on past each record with a field too long to read recursed once
    per record, so enough of them exhausted the stack before any line was named."""
    from cma.importers import monzo

    rows = [f"tx_{k},02/01/2026,09:00:00,Card payment,{'N' * 30},-5.00,EUR," for k in range(1500)]
    src = tmp_path / "m.csv"
    src.write_text(_MONZO_HEAD + "tx_a,01/01/2026,09:00:00,Faster,ACME,-10.00,GBP,90.00\n" + "\n".join(rows) + "\n")
    r = monzo.convert(src, tmp_path / "o.csv")
    assert r.rows == 1 and r.skipped_non_gbp == 1500


@pytest.mark.contracts
def test_monzo_never_writes_a_cell_the_statement_loader_cannot_read(tmp_path, small_field_limit):
    """T04 review 50: two cells each under the reader's limit made a description over it, so
    the import succeeded and wrote a statement the loader refuses."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_text("Transaction ID,Date,Time,Type,Name,Amount,Currency,Balance,Notes and #tags\n"
                   "tx_1,01/01/2026,09:00,Faster,ACMEACMEACMEACME,-10.00,GBP,90.00,INV-12345678901\n")
    with pytest.raises(monzo.MonzoImportError, match="line 2: the description would be"):
        monzo.convert(src, tmp_path / "o.csv")


@pytest.mark.contracts
def test_reading_past_many_long_records_reads_each_line_a_bounded_number_of_times(tmp_path, monkeypatch,
                                                                                    small_field_limit):
    """T04 review 51: each record with a field too long to read made the reader rebuild the
    lines and the reader over the rest of the file, so the work grew with the square of the
    file. One pass now: the lines read stay proportional to the file's lines."""
    from cma.importers import monzo

    reads = [0]
    real_next = ingest._Feed.__next__
    real_split = ingest._split_lines

    def counted_next(self):
        reads[0] += 1
        return real_next(self)

    def counted_split(lines):
        def each():
            for line in lines:
                reads[0] += 1
                yield line
        return real_split(each())

    monkeypatch.setattr(ingest._Feed, "__next__", counted_next)
    monkeypatch.setattr(ingest, "_split_lines", counted_split)
    n = 2000
    rows = [f"tx_{k},02/01/2026,09:00:00,Card payment,{'N' * 30},-5.00,EUR," for k in range(n)]
    src = tmp_path / "m.csv"
    src.write_text(_MONZO_HEAD + "tx_a,01/01/2026,09:00:00,Faster,ACME,-10.00,GBP,90.00\n" + "\n".join(rows) + "\n")
    assert monzo.convert(src, tmp_path / "o.csv").skipped_non_gbp == n
    assert reads[0] <= 4 * (n + 2)


@pytest.mark.contracts
def test_monzo_checks_the_header_before_reading_any_record(tmp_path, monkeypatch):
    """T04 review 51: the whole export was read before its header was checked."""
    from cma.importers import monzo

    def never(data):
        raise AssertionError("records were read before the header was checked")

    monkeypatch.setattr(monzo, "iter_data", never)
    src = tmp_path / "m.csv"
    src.write_text("Transaction ID,Date,Amount\ntx_1,01/01/2026,-1.00\n")
    with pytest.raises(monzo.MonzoImportError, match="missing column"):
        monzo.convert(src, tmp_path / "o.csv")


@pytest.mark.contracts
def test_monzo_reads_nothing_past_the_first_problem(tmp_path, monkeypatch):
    """T04 review 52: the importer parsed and kept every row before checking the first one, so
    a long tail could delay or prevent the refusal; it now reads one record at a time."""
    from cma.importers import monzo

    reads = [0]
    real_next = ingest._Feed.__next__

    def counted(self):
        reads[0] += 1
        return real_next(self)

    monkeypatch.setattr(ingest._Feed, "__next__", counted)
    rows = [f"tx_{k},02/01/2026,09:00:00,Faster payment,ACME,-1.00,GBP,1.00" for k in range(5000)]
    src = tmp_path / "m.csv"
    src.write_text(_MONZO_HEAD + ",01/01/2026,09:00:00,Faster payment,ACME,-10.00,GBP,90.00\n" + "\n".join(rows) + "\n")
    with pytest.raises(monzo.MonzoImportError, match="line 2: empty Transaction ID"):
        monzo.convert(src, tmp_path / "o.csv")
    assert reads[0] <= 5


@pytest.mark.contracts
def test_monzo_refuses_an_unreadable_description_on_its_own_row(tmp_path, small_field_limit):
    """T04 review 52: the check that a description can be read back ran after every row, so a
    later row's problem hid an earlier row whose description was too long."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_text("Transaction ID,Date,Time,Type,Name,Amount,Currency,Balance,Notes and #tags\n"
                   "tx_1,01/01/2026,09:00,Faster,ACMEACMEACMEACME,-10.00,GBP,90.00,INV-12345678901\n"
                   "bad id!,02/01/2026,09:00,Faster,ACME,-1.00,GBP,89.00,\n")
    with pytest.raises(monzo.MonzoImportError, match="line 2: the description would be"):
        monzo.convert(src, tmp_path / "o.csv")


@pytest.mark.contracts
def test_the_loader_checks_each_record_as_it_is_read(tmp_path, monkeypatch):
    """T04 review 53: the statement loader parsed every record before checking any record's
    values; each is now checked as it is read, one record ahead at most (revision 30)."""
    reads = [0]
    at_first_check = []
    real_next, real_number = ingest._Feed.__next__, ingest._number

    def counted(self):
        reads[0] += 1
        return real_next(self)

    def watched(raw):
        if not at_first_check:
            at_first_check.append(reads[0])
        return real_number(raw)

    monkeypatch.setattr(ingest._Feed, "__next__", counted)
    monkeypatch.setattr(ingest, "_number", watched)
    rows = [f"T{k},2026-01-01,A,1.00,{k + 1}.00" for k in range(5000)]
    csv = tmp_path / "s.csv"
    csv.write_text("id,date,description,amount,balance\n" + "\n".join(rows) + "\n")
    ingest.load_csv(csv, today=date(2026, 6, 1))
    assert at_first_check and at_first_check[0] <= 3


@pytest.mark.contracts
def test_a_header_with_a_quote_never_closed_is_refused_on_line_one(tmp_path):
    """T04 review 53: a quoted field never closed in the header swallowed the file, which was
    then reported as an export with no transactions."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_text('Transaction ID,"Date,Time,Type,Name,Amount,Currency,Balance\n'
                   "tx_1,01/01/2026,09:00:00,Faster payment,ACME,-10.00,GBP,90.00\n")
    with pytest.raises(monzo.MonzoImportError, match="line 1: a quoted field in the header is not closed"):
        monzo.convert(src, tmp_path / "o.csv")
    csv = tmp_path / "s.csv"
    csv.write_text('id,"date,description,amount,balance\nT1,2026-01-01,A,1.00,1.00\n')
    with pytest.raises(ingest.StatementError, match="line 1: a quoted field in the header is not closed"):
        ingest.load_csv(csv, today=date(2026, 6, 1))


def _old_order_and_chain(rows):
    """The date-order and running-balance checks as they ran before T04 review 54, after
    reading: the reference the checks made as records are read must match."""
    parsed = list(rows)
    out = []
    dates = [d for _, d, _, _ in parsed if d]
    if dates and dates[0] > dates[-1]:
        parsed.reverse()
    prev = None
    for line, d, _, _ in parsed:
        if d and prev and d < prev:
            out.append(f"line {line}: date {d} is out of order (after {prev})")
        prev = d or prev
    prev_balance = None
    for line, _, a, b in parsed:
        if None not in (a, b, prev_balance) and round(prev_balance * 100) + round(a * 100) != round(b * 100):
            out.append(f"line {line}: running balance {b:.2f} does not follow from previous {prev_balance:.2f} "
                       f"{'+' if a >= 0 else '-'} {abs(a):.2f}")
        if b is not None:
            prev_balance = b
    return out


@pytest.mark.invariants
@pytest.mark.parametrize("seed", range(300))
def test_order_and_balance_checked_as_read_report_what_the_after_reading_checks_did(tmp_path, seed):
    """T04 review 54: date order and the running balance are now checked as each record is
    read, both ways at once; on random statements, newest or oldest first, with gaps and
    breaks, they report exactly what the after-reading checks reported."""
    rng = random.Random(seed)
    n = rng.randint(1, 12)
    rows, bal, day = [], 100.0, date(2026, 1, 1)
    for k in range(n):
        a = round(rng.uniform(-50, 50), 2)
        bal = round(bal + a + (rng.choice([0, 0, 0, 1.0]) if k else 0), 2)
        day = day + timedelta(days=rng.choice([0, 1, 2, -1]))
        rows.append([f"T{k}", day if rng.random() > 0.1 else None, a, bal if rng.random() > 0.1 else None])
    if rng.random() < 0.5:
        rows.reverse()
    text = "id,date,description,amount,balance\n" + "".join(
        f"{t},{d.isoformat() if d else 'x'},A,{a:.2f},{f'{b:.2f}' if b is not None else 'x'}\n" for t, d, a, b in rows)
    csv = tmp_path / "s.csv"
    csv.write_text(text)
    want = _old_order_and_chain([(k + 2, d, a, b) for k, (_, d, a, b) in enumerate(rows)])
    try:
        ingest.load_csv(csv, today=date(2027, 1, 1))
        got = []
    except ingest.StatementError as e:
        got = [p for p in e.problems if "out of order" in p or "does not follow" in p]
    assert got == want


@pytest.mark.contracts
def test_a_foreign_row_with_a_bad_byte_left_open_stops_the_import(tmp_path):
    """T04 review 54: a row in another currency with a bad byte and a quoted field never closed
    was skipped, and the GBP rows its open quote had swallowed were silently lost."""
    from cma.importers import monzo

    src = tmp_path / "m.csv"
    src.write_bytes((_MONZO_HEAD + "tx_1,01/01/2026,09:00:00,Faster payment,ACME,-10.00,GBP,90.00\n").encode()
                    + b'tx_2,02/01/2026,09:00:00,Card payment,CAF\xe9,-5.00,EUR,"\n'
                    + b"tx_3,03/01/2026,09:00:00,Faster payment,ACME,-1.00,GBP,89.00\n")
    with pytest.raises(monzo.MonzoImportError, match=r"line 3: not UTF-8.*not closed"):
        monzo.convert(src, tmp_path / "o.csv")


@pytest.mark.contracts
def test_a_bad_byte_under_a_repeated_blank_column_is_still_found(tmp_path):
    """T04 review 55: two blank header names collapse in a row's dict, so a clean cell under the
    second hid a bad byte under the first; bad bytes are found by the record's own cells."""
    csv = tmp_path / "s.csv"
    csv.write_bytes(b"id,date,description,amount,balance,,\nT1,2026-01-01,A,1.00,1.00,CAF\xe9,clean\n")
    with pytest.raises(ingest.StatementError, match="line 2: not UTF-8"):
        ingest.load_csv(csv, today=date(2026, 6, 1))



@pytest.mark.contracts
def test_records_are_related_to_one_another_once_reading_has_stopped(tmp_path, monkeypatch):
    """Revision 31 (T04 review 56): date order and the running balance relate records to one
    another, and are checked from the records read once reading has stopped, never during
    it; each record's own values are checked as it is read."""
    reads = [0]
    at_balance, at_value = [], []
    real_next, real_follows, real_number = ingest._Feed.__next__, ingest.follows, ingest._number

    def counted(self):
        reads[0] += 1
        return real_next(self)

    monkeypatch.setattr(ingest._Feed, "__next__", counted)
    monkeypatch.setattr(ingest, "follows", lambda *a: at_balance.append(reads[0]) or real_follows(*a))
    monkeypatch.setattr(ingest, "_number", lambda raw: at_value.append(reads[0]) or real_number(raw))
    rows = [f"T{k},2026-01-01,A,1.00,{k + 1}.00" for k in range(50)]
    csv = tmp_path / "s.csv"
    csv.write_text("id,date,description,amount,balance\n" + "\n".join(rows) + "\n")
    ingest.load_csv(csv, today=date(2026, 6, 1))
    assert at_value[0] <= 2 and at_balance and min(at_balance) == 51


@pytest.mark.contracts
def test_every_readable_field_of_a_record_that_cannot_be_read_whole_is_checked(tmp_path):
    """T04 review 56: a record with a field too long to read had only its money cells checked;
    its readable id, date, amount and balance are each checked as in a whole record."""
    csv = tmp_path / "s.csv"
    csv.write_text("id,date,amount,balance,description\nT1,2026-01-01,1.00,1.00,A\n"
                   "T1,2026-13-01,x,y," + "D" * 200_000 + "\n")
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(csv, today=date(2026, 6, 1))
    shown = str(e.value)
    assert "line 3: a field is too long to read (description)" in shown
    assert "line 3: duplicate id T1" in shown and "line 3: date '2026-13-01' is not YYYY-MM-DD" in shown
    assert "line 3: amount 'x' is not a plain number" in shown and "line 3: balance 'y' is not a plain number" in shown


@pytest.mark.contracts
def test_a_partial_last_record_takes_part_in_the_checks_across_records(tmp_path):
    """T04 review 57: the last record, with a field too long to read, gave its readable amount to
    the flows but dropped its readable date and balance from the checks across records."""
    csv = tmp_path / "s.csv"
    csv.write_text("id,date,amount,balance,description\nT1,2026-01-01,1.00,1.00,A\nT2,2026-01-03,1.00,2.00,B\n"
                   "T3,2026-01-02,1.00,9.00," + "D" * 200_000 + "\n")
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(csv, today=date(2026, 6, 1))
    shown = str(e.value)
    assert "line 4: date 2026-01-02 is out of order (after 2026-01-03)" in shown
    assert "line 4: running balance 9.00 does not follow from previous 2.00 + 1.00" in shown


@pytest.mark.contracts
def test_every_problem_found_is_named(tmp_path):
    """T04 review 57 / revision 31: the refusal named the first twenty problems only."""
    rows = [f"T{k},not-a-date,A,1.00,1.00" for k in range(40)]
    csv = tmp_path / "s.csv"
    csv.write_text("id,date,description,amount,balance\n" + "\n".join(rows) + "\n")
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(csv, today=date(2026, 6, 1))
    assert all(f"line {k + 2}: date 'not-a-date' is not YYYY-MM-DD" in str(e.value) for k in range(40))
    assert "more" not in str(e.value)


@pytest.mark.contracts
def test_policy_compares_money_figures_in_the_figures_layer(tmp_path, monkeypatch):
    """T04 review 57 / R2: policy rules compared GBP figures with their limits as raw floats in
    policy.py; every comparison of a figure with a limit goes through metrics.compare."""
    from cma import policy

    seen = []
    real = metrics.compare
    monkeypatch.setattr(policy, "compare", lambda *a: seen.append(a) or real(*a))
    st = _synthetic(tmp_path, "cafe", "good", "v1")
    result = policy.evaluate(metrics.compute(st))
    gbp = [a for a in seen if a[3] == "GBP"]
    assert seen and result.rules and len(seen) >= len(result.rules)
    assert all(isinstance(a[0], float) for a in gbp)
    assert metrics.compare(0.3, ">=", 0.3, "GBP") and not metrics.compare(0.29, ">=", 0.3, "GBP")
