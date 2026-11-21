"""T09: the judgement queue (R15) as a pure function in the figures layer, and P19.

R15: the queue lists first, largest amount first, the lines a person has to decide: the
unexplained lines (ambiguous credits among them, each with its candidates and the bucket each
would give), possible own-account transfers, and every line an indicator flagged, which
includes the cash deposits at or above the single-deposit limit.

P19: for any case, the evidence of the cash-deposits figure (M18) is exactly the credits
categorised as cash deposits, each of them is also in M2's evidence, the figure does not
exceed M2, and every cash deposit at or above the single-deposit limit is in the judgement
queue with the indicator that flagged it.
"""

import json
import random
from datetime import date, timedelta
from itertools import pairwise

import pytest
from typer.testing import CliRunner

from cma import categorise, indicators, metrics, synth
from cma.categorise import load_ruleset
from cma.cli import app
from cma.indicators import judgement_queue, load_thresholds, queue_candidates, raise_indicators
from cma.models import Bucket, Category, Statement, Transaction

# Credit and debit wording the generator draws from: cash in several spellings, trading names,
# wording no rule explains, wording that mixes lists (ambiguous), and debits that feed the
# indicators (a cash withdrawal for the round trip, a gambling operator, a returned DD).
CREDITS = [
    "CASH PAID IN", "CASH DEPOSIT HIGH ST", "COUNTER CREDIT", "CASH DEP 0423", "PAID IN AT BRANCH",
    "SUMUP PAYOUT", "STRIPE PAYMENTS UK", "SQUARE UK SETTLEMENT", "FPS CREDIT ACME LTD INV 42",
    "MYSTERY CREDIT", "J BLOGGS", "FPS CREDIT CASH DEPOSIT", "CHQ CASH DEPOSIT",
    "COUNTER DEPOSIT IWOCA", "HMRC REFUND", "IWOCA LOAN",
]
DEBITS = ["CASH WITHDRAWAL ATM", "CARD BET365", "RENT ACME PROPERTIES", "SUPPLIER WHOLESALE", "DD IWOCA LTD"]


def _statement(rows, ruleset="v2") -> Statement:
    """rows: (id, date, description, pence); balances are walked from zero."""
    txns, bal = [], 0
    for i, d, x, p in rows:
        bal += p
        txns.append(Transaction(id=i, date=d, description=x, amount=p / 100, balance=bal / 100))
    return categorise.categorise(Statement(account_name="x", transactions=txns), load_ruleset(ruleset))


def _random_case(seed: int) -> Statement:
    rng = random.Random(seed)
    day, rows = date(2026, 1, 1), []
    for n in range(rng.randint(1, 60)):
        day += timedelta(days=rng.choice([0, 0, 1, 2, 3]))
        if rng.random() < 0.65:
            desc = rng.choice(CREDITS)
            # Cash amounts cluster about the £2,500 limit, so both sides of it are drawn.
            p = rng.choice([249_999, 250_000, 250_001, rng.randint(1, 600_000)])
        else:
            desc = rng.choice(DEBITS)
            p = -rng.choice([250_000, rng.randint(1, 400_000)])
        rows.append((f"L{n + 1}", day, desc, p))
    return _statement(rows, rng.choice(["v2", "v2", "v1"]))


def _cases():
    yield from (_random_case(seed) for seed in range(300))
    for archetype in synth.ARCHETYPES:
        for profile in synth.PROFILES:
            rows, _ = synth.generate(archetype, profile, seed=3)
            txns = [Transaction(id=r["id"], date=date.fromisoformat(r["date"]), description=r["description"],
                                amount=float(r["amount"]), balance=float(r["balance"])) for r in rows]
            yield categorise.categorise(Statement(account_name="x", transactions=txns), load_ruleset("v2"))


def _check_p19(statement: Statement, thresholds) -> None:
    by = {m.id: m for m in metrics.compute(statement)}
    cash = [t for t in statement.transactions if t.amount > 0 and t.category == Category.CASH_DEPOSIT]
    m18, m2 = by["M18"], by["M2"]
    assert m18.evidence == [t.id for t in cash]
    assert set(m18.evidence) <= set(m2.evidence)
    assert metrics.text_pence(f"{m18.value:.2f}") <= metrics.text_pence(f"{m2.value:.2f}")
    found = raise_indicators(statement, thresholds)
    queue = {e.line_id: e for e in judgement_queue(statement, found)}
    limit = thresholds.value("single_cash_deposit", "amount_at_least")
    flagging = {i.evidence[0]: i for i in found if i.kind == "single_cash_deposit"}
    for t in cash:
        if metrics.pence(t) >= limit:
            assert t.id in queue, t.id
            ind = flagging[t.id]
            assert ind.id in queue[t.id].indicators
            assert "single_cash_deposit" in queue[t.id].indicator_kinds
            assert "indicator" in queue[t.id].reasons


@pytest.mark.invariants
def test_p19_cash_deposits_are_in_m2_and_every_large_one_is_queued_with_its_indicator():
    shipped = load_thresholds()
    assert shipped.value("single_cash_deposit", "amount_at_least") == 250_000
    checked_large = 0
    for statement in _cases():
        _check_p19(statement, shipped)
        checked_large += sum(1 for t in statement.transactions
                             if t.category == Category.CASH_DEPOSIT and metrics.pence(t) >= 250_000)
    assert checked_large > 50  # the generator does reach the limit, on both sides of it


@pytest.mark.invariants
def test_p19_holds_at_a_firm_single_deposit_limit(tmp_path):
    path = tmp_path / "indicators.yaml"
    path.write_text(indicators.default_thresholds_text().replace("amount_at_least: 2500.00", "amount_at_least: 100.01"))
    firm = load_thresholds(path)
    assert firm.value("single_cash_deposit", "amount_at_least") == 10_001
    for seed in range(100):
        _check_p19(_random_case(seed), firm)


@pytest.mark.invariants
def test_p19_the_queue_holds_every_unexplained_and_flagged_line_once_largest_first():
    for statement in _cases():
        found = raise_indicators(statement)
        queue = judgement_queue(statement, found)
        ids = [e.line_id for e in queue]
        assert len(ids) == len(set(ids))
        unexplained = {t.id for t in statement.transactions if t.amount > 0 and t.category == Category.OTHER}
        flagged = {i for ind in found for i in ind.evidence}
        assert set(ids) == unexplained | flagged
        sizes = [abs(metrics.text_pence(f"{e.amount:.2f}")) for e in queue]
        assert sizes == sorted(sizes, reverse=True)
        pos = {t.id: n for n, t in enumerate(statement.transactions)}
        for a, b in pairwise(queue):
            if abs(a.amount) == abs(b.amount):
                assert pos[a.line_id] < pos[b.line_id]
        for e in queue:
            assert ("unexplained" in e.reasons) == (e.line_id in unexplained)
            assert ("indicator" in e.reasons) == (e.line_id in flagged)
            assert e.indicators == [i.id for i in found if e.line_id in i.evidence]


@pytest.mark.contracts
def test_an_ambiguous_credit_is_queued_with_each_candidate_and_the_bucket_it_would_give():
    s = _statement([("L1", date(2026, 1, 2), "FPS CREDIT HMRC TO SAVINGS STRIPE", 50_000),
                    ("L2", date(2026, 1, 3), "MYSTERY CREDIT", 900_000),
                    ("L3", date(2026, 1, 4), "STRIPE PAYOUT", 10_000)])
    queue = judgement_queue(s, raise_indicators(s))
    assert [e.line_id for e in queue] == ["L2", "L1"]  # largest first; the gateway payout is not queued
    mystery, mixed = queue
    # The unexplained credit is also large against the typical trading receipt: one entry, both reasons.
    assert mystery.reasons == ["unexplained", "indicator"] and mystery.indicator_kinds == ["large_credit"]
    assert mystery.candidates == []
    assert mixed.reasons[:2] == ["unexplained", "ambiguous"]
    readings = {c.rule_id: (c.category, c.bucket) for c in mixed.candidates}
    assert readings["payment_rail"] == (None, None)  # a payment-method cue gives no bucket
    assert readings["payment_gateways"] == (Category.REVENUE_CARD, Bucket.TRADING_RECEIPTS)
    assert (Category.HMRC_REPAYMENT, Bucket.HMRC_REPAYMENTS) in readings.values()
    assert (Category.OWN_ACCOUNT_TRANSFER, Bucket.OWN_ACCOUNT_IN) in readings.values()
    assert [c.rule_id for c in mixed.candidates] == s.transactions[0].rule_id.split(":conflict:")[1].split("+")


@pytest.mark.contracts
def test_profile_candidates_give_their_kinds_bucket_whatever_the_ruleset():
    got = queue_candidates("v2:conflict:payment_rail+profile:director+profile:own_account")
    assert [(c.rule_id, c.category, c.bucket) for c in got] == [
        ("payment_rail", None, None),
        ("profile:director", Category.DIRECTOR_FUNDING, Bucket.DIRECTOR_FUNDING),
        ("profile:own_account", Category.OWN_ACCOUNT_TRANSFER, Bucket.OWN_ACCOUNT_IN)]
    assert queue_candidates("v2:cash_paid_in") == [] and queue_candidates(None) == []


@pytest.mark.contracts
def test_possible_own_account_transfers_are_queued_when_present_with_the_lines_they_could_pair_with():
    s = _statement([("L1", date(2026, 1, 2), "SUPPLIER WHOLESALE", -40_000),
                    ("L2", date(2026, 1, 3), "SUMUP PAYOUT", 40_000),
                    ("L3", date(2026, 1, 3), "STRIPE PAYMENTS UK", 40_000),
                    ("L4", date(2026, 1, 5), "STRIPE PAYMENTS UK", 1_000)])
    assert judgement_queue(s, []) == []  # none present: nothing queued
    queue = {e.line_id: e for e in judgement_queue(s, [], possible_own_account=[["L1", "L2", "L3"]])}
    assert set(queue) == {"L1", "L2", "L3"}
    assert queue["L1"].pairs_with == ["L2", "L3"] and queue["L2"].pairs_with == ["L1", "L3"]
    assert all(e.reasons == ["possible_own_account_transfer"] for e in queue.values())


@pytest.mark.contracts
def test_the_queue_leaves_the_statement_unchanged_and_gives_the_same_answer_twice():
    s = _random_case(11)
    before = s.model_dump()
    found = raise_indicators(s)
    first = judgement_queue(s, found)
    assert judgement_queue(s, found) == first and s.model_dump() == before


@pytest.mark.contracts
def test_cma_run_writes_the_judgement_queue_into_its_evidence_record(tmp_path, monkeypatch):
    monkeypatch.setenv("CMA_RULESET", "v2")
    csv = tmp_path / "s.csv"
    csv.write_text("date,description,amount,balance,id\n"
                   "2026-01-02,STRIPE PAYOUT,100.00,100.00,L1\n"
                   "2026-01-03,CASH PAID IN,3000.00,3100.00,L2\n"
                   "2026-01-04,MYSTERY CREDIT,40.00,3140.00,L3\n"
                   "2026-01-05,FPS CREDIT CASH DEPOSIT,60.00,3200.00,L4\n")
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", str(csv), "--yes", "--reviewer", "A", "--out", str(out)])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    queue = record["judgement_queue"]
    assert [e["line_id"] for e in queue] == ["L2", "L4", "L3"]
    cash_flag = next(i for i in record["indicators"] if i["kind"] == "single_cash_deposit")
    assert cash_flag["id"] in queue[0]["indicators"] and queue[0]["reasons"] == ["indicator"]
    assert queue[1]["reasons"][:2] == ["unexplained", "ambiguous"]
    assert {c["rule_id"]: c["bucket"] for c in queue[1]["candidates"]} == {
        "payment_rail": None, "cash_paid_in": "trading_receipts"}
