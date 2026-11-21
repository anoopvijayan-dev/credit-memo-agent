"""T08: risk indicators (R14) from a packaged, validated thresholds file, compared in pence (R2).

R14: indicators are raised from thresholds in an editable file: a single credit that is large
against typical trading receipts; each cash deposit at or above the single-deposit limit; a
calendar month in which cash deposits exceed a set share of that month's trading receipts; a
cash deposit made within a set number of days after a cash withdrawal of an amount within a
set tolerance; payments to gambling operators; returned direct debits; runs of consecutive
overdrawn days; irregular credits from a named acquirer. Each lists its lines and the
threshold that fired, and a threshold still at its shipped value is labelled illustrative.
R2: every indicator comparison is in integer pence.

One fixture per kind, each built just either side of its threshold; then the file's
validation, which refuses a malformed file naming the line; then the evidence record `cma
run` writes.
"""

import json
from datetime import date

import pytest
from typer.testing import CliRunner

from cma import categorise, indicators
from cma.categorise import load_ruleset
from cma.cli import app
from cma.indicators import ThresholdsError, load_thresholds, raise_indicators
from cma.models import Statement, Transaction


def _statement(rows) -> Statement:
    """rows: (id, date, description, amount); balances are walked from zero."""
    txns, bal = [], 0
    for i, d, x, a in rows:
        bal += round(a * 100)
        txns.append(Transaction(id=i, date=d, description=x, amount=a, balance=bal / 100))
    return categorise.categorise(Statement(account_name="x", transactions=txns), load_ruleset("v2"))


def _of(kind, found):
    return [i for i in found if i.kind == kind]


def _thresholds(tmp_path, **changes):
    """The packaged file with some `section.key: text` lines replaced."""
    text = indicators.default_thresholds_text()
    for key, value in changes.items():
        section, field = key.split("__")
        lines = text.splitlines()
        inside = False
        for n, line in enumerate(lines):
            if not line.startswith(" ") and line.rstrip().endswith(":"):
                inside = line.rstrip()[:-1] == section
            elif inside and line.strip().startswith(f"{field}:"):
                lines[n] = f"  {field}: {value}"
        text = "\n".join(lines) + "\n"
    path = tmp_path / "indicators.yaml"
    path.write_text(text)
    return load_thresholds(path)


@pytest.mark.contracts
def test_the_packaged_thresholds_load_with_the_shipped_cash_limits():
    t = load_thresholds()
    assert t.source == "cma/thresholds/indicators.yaml" and len(t.sha256) == 64
    assert t.value("single_cash_deposit", "amount_at_least") == 250000  # £2,500 in pence (D35)
    assert str(t.value("cash_heavy_month", "share_above_pct")) == "50"  # D35
    assert t.value("cash_round_trip", "within_days") == 7
    assert str(t.value("cash_round_trip", "tolerance_pct")) == "10"
    assert all(t.illustrative(s, k) for s, k in t.fields())


@pytest.mark.contracts
def test_large_credit_against_typical_trading_receipts_fires_at_the_multiple_in_pence():
    # Trading receipts 100.00, 100.00, 120.00: the typical (median) one is 100.00, and the
    # shipped multiple is 5, so 500.00 fires and 499.99 does not, whatever the credit's bucket.
    s = _statement([("L1", date(2026, 1, 2), "STRIPE PAYOUT", 100.00),
                    ("L2", date(2026, 1, 3), "STRIPE PAYOUT", 100.00),
                    ("L3", date(2026, 1, 4), "STRIPE PAYOUT", 120.00),
                    ("L4", date(2026, 1, 5), "SOMETHING ELSE", 499.99),
                    ("L5", date(2026, 1, 6), "IWOCA LOAN", 500.00)])
    found = _of("large_credit", raise_indicators(s))
    assert [i.evidence for i in found] == [["L5"]]
    (th,) = found[0].thresholds
    assert (th.key, th.value, th.unit, th.illustrative) == ("large_credit.multiple_of_typical", "5", "times", True)
    assert found[0].illustrative


@pytest.mark.contracts
def test_each_cash_deposit_at_or_above_the_single_deposit_limit_is_flagged():
    s = _statement([("L1", date(2026, 1, 2), "CASH PAID IN", 2499.99),
                    ("L2", date(2026, 1, 3), "COUNTER DEPOSIT", 2500.00),
                    ("L3", date(2026, 1, 4), "CASH DEPOSIT", 4000.00),
                    ("L4", date(2026, 1, 5), "STRIPE PAYOUT", 9000.00)])
    found = _of("single_cash_deposit", raise_indicators(s))
    assert [i.evidence for i in found] == [["L2"], ["L3"]]
    assert found[0].thresholds[0].key == "single_cash_deposit.amount_at_least"
    assert found[0].thresholds[0].value == "2500.00" and found[0].thresholds[0].unit == "GBP"
    assert "£2,500.00" in found[0].reason


@pytest.mark.contracts
def test_a_month_whose_cash_exceeds_the_share_of_its_trading_receipts_is_flagged():
    # January: cash 50.01 of trading 100.01 is above 50%; February: cash 50.00 of 100.00 is not.
    s = _statement([("L1", date(2026, 1, 2), "CASH PAID IN", 50.01),
                    ("L2", date(2026, 1, 3), "STRIPE PAYOUT", 50.00),
                    ("L3", date(2026, 2, 2), "CASH PAID IN", 50.00),
                    ("L4", date(2026, 2, 3), "STRIPE PAYOUT", 50.00)])
    found = _of("cash_heavy_month", raise_indicators(s))
    assert [i.evidence for i in found] == [["L1"]]
    assert "2026-01" in found[0].reason
    assert found[0].thresholds[0].key == "cash_heavy_month.share_above_pct"


@pytest.mark.contracts
def test_a_cash_deposit_after_a_similar_cash_withdrawal_within_the_window_is_flagged():
    # Withdrawn 1,000.00; paid back in 1,100.00 seven days later (10% tolerance, 7 days): fires.
    # 1,100.01 is past the tolerance; eight days is past the window.
    s = _statement([("L0", date(2026, 1, 1), "STRIPE PAYOUT", 5000.00),
                    ("W1", date(2026, 1, 2), "CASH WITHDRAWAL", -1000.00),
                    ("D1", date(2026, 1, 9), "CASH PAID IN", 1100.00),
                    ("W2", date(2026, 2, 2), "ATM", -1000.00),
                    ("D2", date(2026, 2, 3), "CASH PAID IN", 1100.01),
                    ("W3", date(2026, 3, 2), "CASH WITHDRAWAL", -500.00),
                    ("D3", date(2026, 3, 10), "CASH PAID IN", 500.00)])
    found = _of("cash_round_trip", raise_indicators(s))
    assert [i.evidence for i in found] == [["W1", "D1"]]
    assert [t.key for t in found[0].thresholds] == ["cash_round_trip.within_days", "cash_round_trip.tolerance_pct"]


@pytest.mark.contracts
def test_payments_to_gambling_operators_are_flagged():
    s = _statement([("L1", date(2026, 1, 2), "STRIPE PAYOUT", 500.00),
                    ("L2", date(2026, 1, 3), "CARD BET365", -20.00),
                    ("L3", date(2026, 1, 4), "PADDY-POWER", -5.00),
                    ("L4", date(2026, 1, 5), "CARD TESCO", -5.00),
                    ("L5", date(2026, 1, 6), "BET365 WINNINGS", 30.00)])  # money in: not a payment
    found = _of("gambling", raise_indicators(s))
    assert [i.evidence for i in found] == [["L2"], ["L3"]]
    assert found[0].thresholds[0].key == "gambling.amount_at_least"


@pytest.mark.contracts
def test_returned_direct_debits_are_flagged():
    s = _statement([("L1", date(2026, 1, 2), "STRIPE PAYOUT", 500.00),
                    ("L2", date(2026, 1, 3), "DD IWOCA LTD", -100.00),
                    ("L3", date(2026, 1, 4), "UNPAID DD IWOCA LTD", 100.00)])
    found = _of("returned_dd", raise_indicators(s))
    assert [i.evidence for i in found] == [["L3"]]
    assert found[0].thresholds[0].key == "returned_dd.count_at_least"


@pytest.mark.contracts
def test_a_run_of_consecutive_overdrawn_days_at_the_limit_is_flagged():
    # Shipped run length is 7. Overdrawn 2026-01-03 to 2026-01-09 (seven closes, the carried
    # days citing the line that set them), then 2026-02-01 to 2026-02-06 (six): only the first.
    s = _statement([("L1", date(2026, 1, 2), "STRIPE PAYOUT", 10.00),
                    ("L2", date(2026, 1, 3), "RENT", -50.00),
                    ("L3", date(2026, 1, 5), "CARD TESCO", -1.00),
                    ("L4", date(2026, 1, 10), "STRIPE PAYOUT", 100.00),
                    ("L5", date(2026, 2, 1), "RENT", -100.00),
                    ("L6", date(2026, 2, 7), "STRIPE PAYOUT", 100.00)])
    found = _of("overdrawn_run", raise_indicators(s))
    assert [i.evidence for i in found] == [["L2", "L3"]]
    assert "2026-01-03" in found[0].reason and "2026-01-09" in found[0].reason
    assert found[0].thresholds[0].key == "overdrawn_run.days_at_least"


@pytest.mark.contracts
def test_an_irregular_credit_from_a_named_acquirer_is_flagged_whatever_its_category():
    # SumUp pays 100.00 three times and then 300.00: three times its median, shipped multiple 3.
    # 299.99 from Zettle beside 100.00s is not. FPS CREDIT SUMUP CASH DEPOSIT is ambiguous, not
    # trading, and is still read from the acquirer list.
    s = _statement([("A1", date(2026, 1, 2), "SUMUP PAYOUT", 100.00),
                    ("A2", date(2026, 1, 3), "SUM UP PAYOUT", 100.00),
                    ("A3", date(2026, 1, 4), "SUMUP PAYOUT", 100.00),
                    ("A4", date(2026, 1, 5), "FPS CREDIT SUMUP CASH DEPOSIT", 300.00),
                    ("Z1", date(2026, 1, 2), "ZETTLE", 100.00),
                    ("Z2", date(2026, 1, 3), "ZETTLE", 100.00),
                    ("Z3", date(2026, 1, 4), "ZETTLE", 299.99)])
    assert next(t for t in s.transactions if t.id == "A4").category.value == "other"
    found = _of("irregular_acquirer", raise_indicators(s))
    assert [i.evidence for i in found] == [["A4"]]
    assert [t.key for t in found[0].thresholds] == ["irregular_acquirer.multiple_of_median",
                                                     "irregular_acquirer.min_credits"]


@pytest.mark.contracts
def test_a_threshold_moved_from_its_shipped_value_is_not_illustrative(tmp_path):
    t = _thresholds(tmp_path, single_cash_deposit__amount_at_least="1000")
    assert not t.illustrative("single_cash_deposit", "amount_at_least")
    assert t.illustrative("cash_heavy_month", "share_above_pct")
    s = _statement([("L1", date(2026, 1, 2), "CASH PAID IN", 1000.00)])
    (found,) = _of("single_cash_deposit", raise_indicators(s, t))
    assert found.thresholds[0].value == "1000.00" and not found.illustrative
    # Written another way but the same pence is still the shipped value.
    same = _thresholds(tmp_path, single_cash_deposit__amount_at_least="2500")
    assert same.illustrative("single_cash_deposit", "amount_at_least")


@pytest.mark.contracts
@pytest.mark.parametrize(("change", "words"), [
    ({"single_cash_deposit__amount_at_least": "2500.001"}, "whole number of pence"),
    ({"cash_heavy_month__share_above_pct": "fifty"}, "share_above_pct"),
    ({"cash_heavy_month__share_above_pct": "150"}, "share_above_pct"),
    ({"cash_round_trip__within_days": "-1"}, "within_days"),
    ({"overdrawn_run__days_at_least": "0"}, "days_at_least"),
    ({"large_credit__multiple_of_typical": ".nan"}, "multiple_of_typical"),
    ({"single_cash_deposit__amount_at_least": "true"}, "amount_at_least"),
])
def test_a_malformed_value_is_refused_naming_its_line(tmp_path, change, words):
    (key, value), = change.items()
    field = key.split("__")[1]
    with pytest.raises(ThresholdsError) as e:
        _thresholds(tmp_path, **change)
    text = (tmp_path / "indicators.yaml").read_text().splitlines()
    want = 1 + next(n for n, x in enumerate(text) if x == f"  {field}: {value}")
    assert f"line {want}" in str(e.value) and words in str(e.value)


@pytest.mark.contracts
def test_a_malformed_file_is_refused_naming_the_line(tmp_path):
    p = tmp_path / "t.yaml"
    good = indicators.default_thresholds_text()
    cases = {
        "a: [1\n": "line 1",  # not YAML
        good + "single_cash_deposit:\n  amount_at_least: 1\n": "line",  # a section given twice
        good.replace("cash_round_trip:\n", "cash_round_trip:\n  surprise: 1\n", 1): "surprise",
        "- 1\n": "line 1",  # not a mapping
    }
    for text, words in cases.items():
        p.write_text(text)
        with pytest.raises(ThresholdsError) as e:
            load_thresholds(p)
        assert words in str(e.value) and "line " in str(e.value), (text[:40], str(e.value))
    # A section given twice names the line of the second one.
    p.write_text(good + "single_cash_deposit:\n  amount_at_least: 1\n")
    with pytest.raises(ThresholdsError, match=f"line {len(good.splitlines()) + 1}"):
        load_thresholds(p)
    # A missing section is named, with the line of the mapping it belongs in.
    p.write_text(good.replace("gambling:", "gamblin:", 1))
    with pytest.raises(ThresholdsError, match="gamblin") as e:
        load_thresholds(p)
    assert "line" in str(e.value)
    # An empty operator list, and an operator of separators only, are refused by line.
    for bad in ("  operators: []", "  operators: ['---']"):
        lines = good.splitlines()
        start = next(n for n, x in enumerate(lines) if x.startswith("  operators:"))
        end = start + 1
        while end < len(lines) and lines[end].startswith("    "):
            end += 1
        p.write_text("\n".join([*lines[:start], bad, *lines[end:]]) + "\n")
        with pytest.raises(ThresholdsError, match=f"line {start + 1}"):
            load_thresholds(p)
    p.write_text("")
    with pytest.raises(ThresholdsError, match="line 1"):
        load_thresholds(p)
    with pytest.raises(ThresholdsError, match="no such file"):
        load_thresholds(tmp_path / "missing.yaml")


@pytest.mark.contracts
def test_indicators_leave_the_statement_unchanged_and_give_the_same_answer_twice():
    s = _statement([("L1", date(2026, 1, 2), "CASH PAID IN", 3000.00),
                    ("L2", date(2026, 1, 3), "CARD BET365", -20.00)])
    before = s.model_dump()
    assert raise_indicators(s) == raise_indicators(s)
    assert s.model_dump() == before
    found = raise_indicators(s)
    assert [i.id for i in found] == [f"I{n}" for n in range(1, len(found) + 1)]
    assert {"single_cash_deposit", "gambling", "cash_heavy_month"} <= {i.kind for i in found}


@pytest.mark.contracts
def test_cma_run_writes_indicators_and_their_thresholds_into_its_evidence_record(tmp_path, monkeypatch):
    monkeypatch.setenv("CMA_RULESET", "v2")
    csv = tmp_path / "s.csv"
    csv.write_text("date,description,amount,balance,id\n"
                   "2026-01-02,STRIPE PAYOUT,100.00,100.00,L1\n"
                   "2026-01-03,CASH PAID IN,3000.00,3100.00,L2\n"
                   "2026-01-04,CARD BET365,-20.00,3080.00,L3\n")
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", str(csv), "--yes", "--reviewer", "A", "--out", str(out)])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    assert record["thresholds"]["source"] == "cma/thresholds/indicators.yaml"
    assert record["thresholds"]["sha256"] == load_thresholds().sha256
    by_kind = {i["kind"]: i for i in record["indicators"]}
    assert by_kind["single_cash_deposit"]["evidence"] == ["L2"]
    assert by_kind["single_cash_deposit"]["thresholds"] == [
        {"key": "single_cash_deposit.amount_at_least", "value": "2500.00", "unit": "GBP", "illustrative": True}]
    assert by_kind["single_cash_deposit"]["illustrative"] is True
    assert by_kind["gambling"]["evidence"] == ["L3"]
    # A firm's own file: its hash is recorded, and a moved value is not illustrative.
    firm = tmp_path / "firm.yaml"
    firm.write_text(indicators.default_thresholds_text().replace("amount_at_least: 2500.00", "amount_at_least: 2000"))
    out2 = tmp_path / "memo2.md"
    r = CliRunner().invoke(app, ["run", str(csv), "--yes", "--reviewer", "A", "--out", str(out2),
                                 "--thresholds", str(firm)])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "memo2.evidence.json").read_text())
    assert record["thresholds"]["source"] == str(firm)
    (cash,) = [i for i in record["indicators"] if i["kind"] == "single_cash_deposit"]
    assert cash["thresholds"][0]["value"] == "2000.00" and cash["illustrative"] is False


@pytest.mark.contracts
def test_cma_run_refuses_a_malformed_thresholds_file_naming_the_line(tmp_path):
    csv = tmp_path / "s.csv"
    csv.write_text("date,description,amount,balance,id\n2026-01-02,STRIPE PAYOUT,100.00,100.00,L1\n")
    bad = tmp_path / "bad.yaml"
    text = indicators.default_thresholds_text().replace("amount_at_least: 2500.00", "amount_at_least: 2500.001")
    bad.write_text(text)
    want = 1 + text.splitlines().index("  amount_at_least: 2500.001")
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", str(csv), "--yes", "--reviewer", "A", "--out", str(out),
                                 "--thresholds", str(bad)])
    assert r.exit_code == 2, r.output
    assert f"line {want}:" in " ".join(r.output.split())  # Rich wraps long lines
    assert not out.exists() and not (tmp_path / "memo.evidence.json").exists()


@pytest.mark.contracts
def test_the_committed_example_indicators_are_what_the_code_raises_from_its_lines():
    from pathlib import Path

    from cma import ingest

    examples = Path(__file__).parent.parent / "examples"
    record = json.loads((examples / "cafe_stretched.memo.evidence.json").read_text())
    s = categorise.categorise(ingest.load_csv(examples / "cafe_stretched.csv"), load_ruleset(record["ruleset"]["id"]))
    assert [i.model_dump(mode="json") for i in raise_indicators(s)] == record["indicators"]
    assert record["thresholds"]["sha256"] == load_thresholds().sha256


@pytest.mark.contracts
def test_an_integer_threshold_with_too_many_digits_is_refused_naming_its_line(tmp_path):
    # Python refuses to convert an integer string of more than 4,300 digits; that must reach
    # the caller as the file's own refusal, naming the line, not as a bare ValueError.
    huge = "9" * 5000
    with pytest.raises(ThresholdsError) as e:
        _thresholds(tmp_path, overdrawn_run__days_at_least=huge)
    text = (tmp_path / "indicators.yaml").read_text().splitlines()
    want = 1 + next(n for n, x in enumerate(text) if x == f"  days_at_least: {huge}")
    assert f"line {want}" in str(e.value) and "days_at_least" in str(e.value)


@pytest.mark.contracts
def test_the_cash_round_trip_window_is_found_by_date_and_position_at_scale():
    # 10,000 lines: 5,000 withdrawals of £100.00 and 5,000 deposits of £100.00, twenty a day.
    # Every deposit follows a same-day withdrawal, so every deposit fires; a deposit's lines are
    # the withdrawals within the window before it, never one after it.
    import time
    from datetime import timedelta

    rows, d0 = [], date(2026, 1, 1)
    for i in range(5000):
        d = d0 + timedelta(days=i // 20)
        rows += [(f"W{i}", d, "CASH WITHDRAWAL", -100.00), (f"D{i}", d, "CASH PAID IN", 100.00)]
    s = _statement(rows)
    start = time.perf_counter()
    found = raise_indicators(s)
    elapsed = time.perf_counter() - start
    trips = _of("cash_round_trip", found)
    assert len(trips) == 5000
    pos = {t.id: n for n, t in enumerate(s.transactions)}
    for trip in trips:
        dep = trip.evidence[-1]
        assert dep.startswith("D") and all(pos[w] < pos[dep] for w in trip.evidence[:-1])
    # The first deposit's window is the one withdrawal before it; the last day's last deposit
    # sees every withdrawal of the 7 days before it and of its own day.
    assert trips[0].evidence == ["W0", "D0"]
    assert len(trips[-1].evidence) == 1 + 20 * 8
    assert elapsed < 5, elapsed  # was over 7 s before the window was bisected (R52 target 2 s)


@pytest.mark.contracts
def test_the_evidence_reads_the_thresholds_the_run_kept_not_the_file_as_it_is_now(tmp_path):
    # A resumed review writes its evidence from the text its run read at the start, so a file
    # edited or removed while the review waits changes nothing it records; a checkpoint from
    # before indicators existed holds no text and reads the packaged file.
    from cma.graph import run_thresholds

    firm = tmp_path / "firm.yaml"
    kept = indicators.default_thresholds_text().replace("amount_at_least: 2500.00", "amount_at_least: 2000")
    firm.write_text("not: [valid")  # the file as it is now: malformed
    t = run_thresholds({"thresholds_path": str(firm), "thresholds_text": kept})
    assert t.value("single_cash_deposit", "amount_at_least") == 200000 and t.source == str(firm)
    firm.unlink()
    assert run_thresholds({"thresholds_path": str(firm), "thresholds_text": kept}).sha256 == t.sha256
    old = run_thresholds({"statement_path": "x.csv"})
    assert old.sha256 == load_thresholds().sha256 and old.source == "cma/thresholds/indicators.yaml"
