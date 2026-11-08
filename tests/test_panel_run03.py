"""Regression tests for panel run 03 (E1, E7, E8, E11, E16, E18, E19, E23, E25, E26).

Each is the seats' reproduction, shrunk.
"""

from datetime import date, timedelta

import pytest

from cma import categorise, guard, metrics, policy, synth
from cma.graph import SYSTEM_PROMPT, build
from cma.models import Category, RuleOutcome, Statement, Transaction


def statement(lines, opening=0.0):
    bal, txns = opening, []
    for i, (d, desc, amt) in enumerate(lines, start=1):
        bal = round(bal + amt, 2)
        txns.append(Transaction(id=f"T{i:03d}", date=d, description=desc, amount=amt, balance=bal))
    return categorise.categorise(Statement(account_name="x", transactions=txns))


def by_id(s, **kw):
    return {m.id: m for m in metrics.compute(s, **kw)}


def year(lines_per_month, start=date(2025, 10, 10)):
    out = []
    for k in range(12):
        y, m = divmod(start.month - 1 + k, 12)
        d = date(start.year + y, m + 1, start.day)
        out += [(d, desc, amt) for desc, amt in lines_per_month]
    return out


BASE = [("CARD ACQUIRER SETTLEMENT", 20000.0), ("FRESHFIELD FOODS LTD DD", -17500.0),
        ("DD HOLLOWAY BRIDGE FINANCE LOAN", -2000.0), ("TFR OWNER DRAWINGS", -500.0)]


# E1 (blocker): money the owner puts in lifted cover from fail to pass.

@pytest.mark.parametrize("desc", [
    "FPS CREDIT J SMITH CAPITAL INTRODUCED", "FPS CREDIT DIRECTOR CAPITAL INJECTION",
    "FPS CREDIT SAVINGS ACCOUNT", "BACS CREDIT HMRC VAT REPAYMENT", "FPS CREDIT A PERSON",
])
def test_unexplained_money_in_cannot_lift_cover(desc):
    before = by_id(statement(year(BASE)))
    lines = sorted([*year(BASE), (date(2026, 3, 3), desc, 30000.0)], key=lambda x: x[0])
    after = by_id(statement(lines))
    assert after["M9"].value == before["M9"].value
    assert after["M10"].value == before["M10"].value
    result = policy.evaluate(list(after.values()))
    assert {r.rule_id: r.outcome for r in result.rules}["R2"] == RuleOutcome.FAIL


def test_money_sent_and_sent_back_nets_off_but_cannot_add_cash():
    sent = [*BASE, ("FPS DEBIT MY SAVINGS POT", -3000.0), ("FPS CREDIT MY SAVINGS POT", 3000.0)]
    assert by_id(statement(year(sent)))["M9"].value == by_id(statement(year(BASE)))["M9"].value
    more_back = [*BASE, ("FPS DEBIT MY SAVINGS POT", -3000.0), ("FPS CREDIT MY SAVINGS POT", 5000.0)]
    assert by_id(statement(year(more_back)))["M9"].value == by_id(statement(year(BASE)))["M9"].value


def test_unlinked_returned_dd_is_not_operating_cash():
    lines = [*year(BASE), (date(2026, 1, 20), "UNPAID DD SOMEONE ELSE", 4000.0)]
    lines.sort(key=lambda x: x[0])
    assert by_id(statement(lines))["M9"].value == by_id(statement(year(BASE)))["M9"].value


# E23: a "customer" the applicant also pays by transfer is not revenue.

def test_round_trip_with_invoice_reference_is_not_revenue():
    s = statement([(date(2026, 1, 2), "FPS DEBIT J SMITH", -5000.0),
                   (date(2026, 1, 3), "FPS CREDIT J SMITH INV 1001", 5000.0)])
    assert s.transactions[1].category == Category.OTHER


# E7: part-months distorted M1, M3 and M4.

def _daily(start, end, amount=1000.0):
    d, out = start, []
    while d <= end:
        out.append((d, "CARD ACQUIRER SETTLEMENT", amount))
        d += timedelta(days=1)
    return out


@pytest.mark.parametrize("start, end, months", [
    (date(2025, 10, 1), date(2026, 9, 30), 12),
    (date(2025, 10, 16), date(2026, 10, 15), 12),
    (date(2025, 10, 20), date(2026, 9, 30), 11),
    (date(2025, 10, 18), date(2026, 10, 14), 12),
])
def test_flat_business_reads_flat(start, end, months):
    m = by_id(statement(_daily(start, end)))
    assert m["M1"].value == months
    assert m["M3"].value == pytest.approx(1000 * 365.25 / 12, rel=0.002)
    assert abs(m["M4"].value) < 3.5  # month-length noise only, no fake decline


def test_flat_business_ending_mid_month_is_not_referred_for_decline():
    m = by_id(statement(_daily(date(2025, 10, 1), date(2026, 9, 16))))
    assert {r.rule_id: r.outcome for r in policy.evaluate(list(m.values())).rules}["R6"] == RuleOutcome.PASS


# E8: a VAT payment returned unpaid is not a payment.

def test_bounced_vat_payment_is_late():
    lines = [(date(2025, 3, 1), "CARD ACQUIRER SETTLEMENT", 50000.0)]
    for d in (date(2025, 4, 7), date(2025, 7, 7), date(2025, 10, 7)):
        lines.append((d, "HMRC VAT", -6000.0))
    lines += [(date(2025, 7, 9), "UNPAID DD HMRC VAT", 6000.0), (date(2025, 8, 20), "HMRC VAT", -6000.0),
              (date(2025, 10, 31), "CARD ACQUIRER SETTLEMENT", 1.0)]
    lines.sort(key=lambda x: x[0])
    assert by_id(statement(lines), vat_stagger=3)["M13"].value == 1


# E16: policy decides on the unrounded figure.

def test_cover_just_below_the_line_fails():
    lines = year([("CARD ACQUIRER SETTLEMENT", 10000.0), ("FRESHFIELD FOODS LTD DD", -8250.4167),
                  ("DD HOLLOWAY BRIDGE FINANCE LOAN", -1000.0), ("TFR OWNER DRAWINGS", -500.0)])
    lines = [(d, desc, round(a, 2)) for d, desc, a in lines]
    m = by_id(statement(lines))
    assert m["M10"].value < 1.25 and m["M10"].display() == "1.25x"
    assert {r.rule_id: r.outcome for r in policy.evaluate(list(m.values())).rules}["R2"] == RuleOutcome.FAIL


# E18: M16 cites its denominator.

def test_unclassified_share_cites_all_money_in():
    s = statement([(date(2026, 1, 1), "CARD ACQUIRER SETTLEMENT", 900.0), (date(2026, 1, 2), "FPS CREDIT X", 100.0)])
    m = by_id(s)["M16"]
    assert m.value == 10.0 and set(m.evidence) == {"T001", "T002"}


# E19: short statements.

@pytest.mark.parametrize("profile, months", [("good", 3), ("poor", 4), ("poor", 2)])
def test_short_synthetic_statements_generate(profile, months):
    rows, _ = synth.generate("cafe", profile, months=months)
    assert rows


# E11 / E25: everything the model is told it may quote passes the guard.

def test_method_texts_and_rule_descriptions_are_quotable(tmp_path):
    cases = [
        statement(_daily(date(2026, 1, 1), date(2026, 3, 31))),  # short: M4 n/a
        statement(year(BASE)),
    ]
    for s in cases:
        ms = metrics.compute(s)  # no VAT stagger: M13 n/a
        pol = policy.evaluate(ms)
        for m in ms:
            text = f"{m.name} is {m.display()} [{m.id}]: {m.method}"
            g = guard.check(text, ms, pol)
            assert g.grounded, (m.id, g)
        for r in pol.rules:
            if not any(ch.isdigit() for ch in r.description):
                continue
            text = f"Rule {r.rule_id}: {r.description.replace(_limit(r), _limit(r) + f' [{r.rule_id}]')}."
            g = guard.check(text + " See [M1].", ms, pol)
            assert g.grounded, (r.rule_id, text, g)


def _limit(r):
    import re
    return re.search(r"-?\d+(?:\.\d+)?(?:x|%)?", r.description).group(0)


# E26: policy validation and R3 have tests.

@pytest.mark.parametrize("bad, match", [
    ({"name": "p", "rules": [{"id": "R1", "description": "d", "metric": "M1", "op": "=>", "threshold": 1, "on_fail": "refer"}]}, "op"),
    ({"name": "p", "rules": [{"id": "R1", "description": "d", "metric": "M1", "op": ">=", "threshold": 1, "on_fail": "pass"}]}, "on_fail"),
    ({"name": "p", "rules": [{"id": "R1", "description": "d", "metric": "M99", "op": ">=", "threshold": 1, "on_fail": "refer"}]}, "unknown metric"),
    ({"name": "p", "rules": [{"id": "R1", "description": "d", "metric": "M1", "op": ">=", "threshold": "12", "on_fail": "refer"}]}, "number"),
    ({"name": "p", "rules": [{"id": "R1", "description": "d", "metric": "M1", "op": ">=", "threshold": 1, "on_fail": "refer", "when_missing": "maybe"}]}, "when_missing"),
])
def test_bad_policies_are_refused(bad, match):
    ms = metrics.compute(statement(year(BASE)))
    with pytest.raises(policy.PolicyError, match=match):
        policy.evaluate(ms, bad)


def test_the_model_never_sees_transactions(tmp_path):
    rows, truth = synth.generate("trades", "good")
    csv, _ = synth.write(rows, truth, tmp_path)
    seen = []

    class Spy:
        name, model = "spy", "spy"

        def complete(self, system, user):
            seen.append(system + user)
            from cma.llm import FakeLLM
            return FakeLLM().complete(system, user)

    build(Spy()).invoke({"statement_path": str(csv)}, {"configurable": {"thread_id": "spy"}})
    prompt = seen[0]
    assert SYSTEM_PROMPT in prompt
    for r in rows:
        assert f"| {r['id']} " not in prompt and f"{r['id']}," not in prompt
        if "INV" in r["description"]:  # customer lines are unique; generic ones like "HMRC VAT" are not
            assert r["description"] not in prompt
