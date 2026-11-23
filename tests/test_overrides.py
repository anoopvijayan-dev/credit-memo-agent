"""The override log (overrides.jsonl) and its application (R17, R18, R19; P3, P4).

An override changes one line's category, and nothing else; it carries a reason, the
typed name, the operating-system user and the UTC time, and is appended both to the
case's override log and to its case log. An override on a line that is not in the case,
or recorded against a statement whose hash has changed, is held as stale until the
underwriter confirms or drops it. Figures are recomputed from the statements and the
whole override log, and each figure that rests on an overridden line is marked."""

import json
import random
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cma import case, overrides, store
from cma.cli import app
from cma.models import Category

runner = CliRunner()
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "cafe_stretched.csv"
SMALL = ("id,date,description,amount,balance\n"
         "L1,2026-01-05,CARD SETTLEMENT STRIPE,500.00,1500.00\n"
         "L2,2026-01-06,FPS CREDIT J BLOGGS,300.00,1800.00\n"
         "L3,2026-01-07,DD COUNCIL,-100.00,1700.00\n"
         "L4,2026-02-02,FPS CREDIT ACME LTD,250.00,1950.00\n")


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    monkeypatch.delenv("CMA_RULESET", raising=False)
    return root


def _case(tmp_path, body: str | None = None) -> Path:
    case_id = store.new_case(label="t16")
    folder = store.case_folder(case_id)
    src = tmp_path / "statement.csv"
    if body is None:
        src.write_bytes(EXAMPLE.read_bytes())
    else:
        src.write_text(body)
    store.import_file(folder, src, "csv")
    return folder


def _snapshot(view) -> dict:
    """Everything a computation hands on, as plain data, for comparing two computations."""
    return {acc: {"metrics": [m.model_dump(mode="json") for m in f.metrics],
                  "monthly": [r.model_dump(mode="json") for r in f.monthly],
                  "lines": [(t.id, t.category.value, t.rule_id) for t in f.statement.transactions],
                  "marked": sorted(f.marked), "marked_cells": sorted(f.marked_cells),
                  "overridden": sorted(f.overridden), "stale": [s.seq for s in f.stale]}
            for acc, f in view.items()}


def _lines(view) -> set:
    return {(t.id, t.date.isoformat(), t.amount, t.balance, t.description)
            for f in view.values() for t in f.statement.transactions}


def _random_overrides(statement, rng: random.Random, n: int) -> list[tuple[str, Category]]:
    picks = []
    for t in rng.sample(statement.transactions, n):
        picks.append((t.id, rng.choice(sorted(overrides.valid_categories(t), key=lambda c: c.value))))
    return picks


# --- P3 ---------------------------------------------------------------------------------


@pytest.mark.invariants
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_p3_one_at_a_time_equals_fresh_computation_with_all(home, tmp_path, seed):
    folder = _case(tmp_path)
    rng = random.Random(seed)
    base = overrides.case_figures(folder)
    picks = _random_overrides(base["main"].statement, rng, 12)
    picks += [(picks[0][0], rng.choice(sorted(overrides.valid_categories(
        next(t for t in base["main"].statement.transactions if t.id == picks[0][0])), key=lambda c: c.value)))]
    stepwise = None
    for line, cat in picks:
        overrides.add_override(folder, line, cat.value, reason=f"seen on the paying-in slip {seed}",
                               name="Pat Underwriter")
        stepwise = overrides.case_figures(folder)  # recomputed after each one
    fresh = overrides.case_figures(folder)
    assert _snapshot(stepwise) == _snapshot(fresh)
    # A fresh computation with every override applied at once, outside the case store.
    from cma import metrics
    from cma.categorise import LineOverride, categorise
    from cma.ingest import load_csv

    final = {}
    for line, cat in picks:
        final[line] = cat
    st = load_csv(folder / "statements" / "main.csv", account_name="main")
    # A pick equal to the line's category at the time records nothing (an identical
    # override changes nothing), so only the recorded ones are applied here.
    assert set(fresh["main"].overridden) <= set(final)
    applied = {k: LineOverride(final[k], ref) for k, ref in fresh["main"].overridden.items()}
    profile = store.load_profile(folder)
    categorise(st, overrides=applied, profile=store.profile_names(profile))
    got = {t.id: t.category for t in fresh["main"].statement.transactions}
    for k, v in final.items():
        assert got[k] == v
    want = metrics.compute(st, profile["vat_stagger"], profile["proposed_monthly_repayment"])
    assert [m.model_dump() for m in want] == [m.model_dump() for m in fresh["main"].metrics]


@pytest.mark.invariants
def test_p3_computing_twice_gives_identical_results(home, tmp_path):
    folder = _case(tmp_path)
    t = overrides.case_figures(folder)["main"].statement.transactions
    overrides.add_override(folder, t[0].id, "other", reason="not takings", name="Pat")
    assert _snapshot(overrides.case_figures(folder)) == _snapshot(overrides.case_figures(folder))


# --- P4 ---------------------------------------------------------------------------------


@pytest.mark.invariants
@pytest.mark.parametrize("seed", [7, 8])
def test_p4_overrides_never_change_a_lines_id_date_amount_balance_or_description(home, tmp_path, seed):
    folder = _case(tmp_path)
    before = _lines(overrides.case_figures(folder))
    statement_bytes = (folder / "statements" / "main.csv").read_bytes()
    rng = random.Random(seed)
    for line, cat in _random_overrides(overrides.case_figures(folder)["main"].statement, rng, 15):
        overrides.add_override(folder, line, cat.value, reason="reason", name="Pat")
    after = overrides.case_figures(folder)
    assert _lines(after) == before
    assert (folder / "statements" / "main.csv").read_bytes() == statement_bytes
    assert len(after["main"].overridden) > 0


@pytest.mark.invariants
def test_p4_a_request_to_change_anything_but_the_category_is_refused(home, tmp_path):
    folder = _case(tmp_path, SMALL)
    for bad in ({"amount": "1.00"}, {"date": "2026-01-01"}, {"balance": "0"}, {"description": "X"}):
        with pytest.raises(store.CaseStoreError, match="category only"):
            overrides.add_override(folder, "L1", "other", reason="r", name="Pat", **bad)
    assert not (folder / overrides.OVERRIDES).exists()
    assert _lines(overrides.case_figures(folder)) == _lines(overrides.case_figures(folder))


# --- R18: categories only, valid for the direction -------------------------------------


@pytest.mark.contracts
def test_category_must_be_valid_for_the_lines_direction(home, tmp_path):
    folder = _case(tmp_path, SMALL)
    with pytest.raises(store.CaseStoreError, match="not valid for a debit"):
        overrides.add_override(folder, "L3", "revenue_transfer", reason="r", name="Pat")
    with pytest.raises(store.CaseStoreError, match="not valid for a credit"):
        overrides.add_override(folder, "L1", "payroll", reason="r", name="Pat")
    with pytest.raises(store.CaseStoreError, match="not a category"):
        overrides.add_override(folder, "L1", "takings", reason="r", name="Pat")
    with pytest.raises(store.CaseStoreError, match="no line L9"):
        overrides.add_override(folder, "L9", "other", reason="r", name="Pat")
    with pytest.raises(store.CaseStoreError, match="reason"):
        overrides.add_override(folder, "L1", "other", reason="  ", name="Pat")
    with pytest.raises(store.CaseStoreError, match="name"):
        overrides.add_override(folder, "L1", "other", reason="r", name="")
    assert not (folder / overrides.OVERRIDES).exists()
    assert [e.event for e in case.for_case(folder).entries()] == ["case_created", "import"]


@pytest.mark.contracts
def test_an_override_records_line_old_new_reason_name_user_and_utc_time(home, tmp_path):
    folder = _case(tmp_path, SMALL)
    r = overrides.add_override(folder, "L2", "director_funding", reason="director's own transfer",
                               name="Pat Underwriter")
    assert r.recorded and r.seq == 1
    rec = json.loads((folder / overrides.OVERRIDES).read_text().splitlines()[0])
    assert rec["kind"] == "override" and rec["line"] == "L2" and rec["account"] == "main"
    assert rec["old"] == "other" and rec["new"] == "director_funding"
    assert rec["reason"] == "director's own transfer" and rec["name"] == "Pat Underwriter"
    assert rec["os_user"] == store._os_user()
    assert rec["at"].endswith("Z") and datetime.fromisoformat(rec["at"].replace("Z", "+00:00")).tzinfo
    assert len(rec["statement_sha256"]) == 64
    entry = case.for_case(folder).entries()[-1]
    assert entry.event == "override" and entry.data["seq"] == 1 and entry.data["new"] == "director_funding"
    assert (folder / overrides.OVERRIDES).stat().st_mode & 0o777 == 0o600
    view = overrides.case_figures(folder)["main"]
    assert next(t for t in view.statement.transactions if t.id == "L2").category is Category.DIRECTOR_FUNDING
    assert view.overridden == {"L2": "override:1"}
    assert next(t for t in view.statement.transactions if t.id == "L2").rule_id == "override:1"


@pytest.mark.contracts
def test_an_identical_override_changes_nothing(home, tmp_path):
    folder = _case(tmp_path, SMALL)
    r = overrides.add_override(folder, "L2", "other", reason="already other", name="Pat")
    assert not r.recorded
    assert not (folder / overrides.OVERRIDES).exists()


@pytest.mark.contracts
def test_a_stale_expected_log_position_is_a_conflict(home, tmp_path):
    folder = _case(tmp_path, SMALL)
    with pytest.raises(case.LogConflict):
        overrides.add_override(folder, "L2", "director_funding", reason="r", name="Pat", expected=1)
    overrides.add_override(folder, "L2", "director_funding", reason="r", name="Pat", expected=2)


# --- R17: figures marked when they rest on an overridden line --------------------------


@pytest.mark.contracts
def test_figures_that_rest_on_an_overridden_line_are_marked(home, tmp_path):
    folder = _case(tmp_path, SMALL)
    assert overrides.case_figures(folder)["main"].marked == set()
    overrides.add_override(folder, "L2", "revenue_transfer", reason="invoice 17", name="Pat")
    f = overrides.case_figures(folder)["main"]
    by_id = {m.id: m for m in f.metrics}
    assert "L2" in by_id["M2"].evidence and "M2" in f.marked and "M17" in f.marked
    assert "M25" in f.marked  # left unexplained money in: its value moved
    for mid in f.marked:
        assert mid in by_id
    untouched = [m.id for m in f.metrics if m.id not in f.marked]
    assert untouched and all("L2" not in by_id[m].evidence for m in untouched)
    assert ("2026-01", "trading_receipts") in f.marked_cells
    assert ("2026-02", "trading_receipts") not in f.marked_cells


# --- R19: stale overrides --------------------------------------------------------------


def _replace_statement(folder: Path, body: str) -> None:
    p = folder / "statements" / "main.csv"
    p.chmod(0o600)
    p.write_text(body)


@pytest.mark.contracts
def test_an_override_on_a_changed_statement_is_stale_until_confirmed(home, tmp_path):
    folder = _case(tmp_path, SMALL)
    overrides.add_override(folder, "L2", "director_funding", reason="r", name="Pat")
    _replace_statement(folder, SMALL.replace("J BLOGGS", "J BLOGGS LTD"))
    f = overrides.case_figures(folder)["main"]
    assert [s.seq for s in f.stale] == [1] and f.stale[0].why == "statement changed"
    assert f.overridden == {} and f.marked == set()
    assert next(t for t in f.statement.transactions if t.id == "L2").category is Category.OTHER
    overrides.resolve_stale(folder, 1, "confirm", name="Pat")
    f = overrides.case_figures(folder)["main"]
    assert f.stale == [] and f.overridden == {"L2": "override:1"}
    assert [e.event for e in case.for_case(folder).entries()][-1] == "override_confirmed"


@pytest.mark.contracts
def test_an_override_on_a_missing_line_is_stale_and_can_only_be_dropped(home, tmp_path):
    folder = _case(tmp_path, SMALL)
    overrides.add_override(folder, "L4", "revenue_transfer", reason="r", name="Pat")
    _replace_statement(folder, "\n".join(SMALL.splitlines()[:4]) + "\n")
    f = overrides.case_figures(folder)["main"]
    assert [(s.seq, s.why) for s in f.stale] == [(1, "line not in the case")]
    with pytest.raises(store.CaseStoreError, match="drop"):
        overrides.resolve_stale(folder, 1, "confirm", name="Pat")
    overrides.resolve_stale(folder, 1, "drop", name="Pat")
    assert overrides.case_figures(folder)["main"].stale == []
    with pytest.raises(store.CaseStoreError, match="not stale"):
        overrides.resolve_stale(folder, 1, "drop", name="Pat")


@pytest.mark.contracts
def test_an_applied_override_is_not_stale_and_cannot_be_dropped_as_one(home, tmp_path):
    folder = _case(tmp_path, SMALL)
    overrides.add_override(folder, "L2", "director_funding", reason="r", name="Pat")
    with pytest.raises(store.CaseStoreError, match="not stale"):
        overrides.resolve_stale(folder, 1, "drop", name="Pat")


# --- the override log is the case log's ------------------------------------------------


@pytest.mark.contracts
def test_a_hand_edited_override_log_is_refused(home, tmp_path):
    folder = _case(tmp_path, SMALL)
    overrides.add_override(folder, "L2", "director_funding", reason="r", name="Pat")
    p = folder / overrides.OVERRIDES
    p.write_text(p.read_text().replace("director_funding", "revenue_transfer"))
    with pytest.raises(store.CaseStoreError, match="record 1"):
        overrides.case_figures(folder)


@pytest.mark.contracts
def test_a_record_the_case_log_never_recorded_is_a_leftover_and_ignored(home, tmp_path):
    folder = _case(tmp_path, SMALL)
    overrides.add_override(folder, "L2", "director_funding", reason="r", name="Pat")
    p = folder / overrides.OVERRIDES
    good = p.read_text()
    rec = json.loads(good.splitlines()[0]) | {"seq": 2, "line": "L1", "new": "other", "old": "revenue_card_settlement"}
    p.write_text(good + json.dumps(rec) + "\n")
    assert overrides.case_figures(folder)["main"].overridden == {"L2": "override:1"}
    overrides.add_override(folder, "L4", "revenue_transfer", reason="r", name="Pat")
    lines = p.read_text().splitlines()
    assert [json.loads(x)["line"] for x in lines] == ["L2", "L4"]


@pytest.mark.contracts
def test_a_refused_case_log_append_leaves_no_override_record(home, tmp_path, monkeypatch):
    folder = _case(tmp_path, SMALL)

    def refuse(*a, **k):
        raise case.LogConflict(2, 3)

    monkeypatch.setattr(case.CaseLog, "append", refuse)
    with pytest.raises(case.LogConflict):
        overrides.add_override(folder, "L2", "director_funding", reason="r", name="Pat")
    assert not (folder / overrides.OVERRIDES).exists()


# --- CLI -------------------------------------------------------------------------------


@pytest.mark.contracts
def test_cli_override_list_figures_and_resolve(home, tmp_path):
    folder = _case(tmp_path, SMALL)
    cid = folder.name
    r = runner.invoke(app, ["case", "override", cid, "L2", "director_funding", "--reason", "loan from Jo",
                            "--name", "Pat"])
    assert r.exit_code == 0, r.output
    assert "override 1" in r.output
    r = runner.invoke(app, ["case", "override", cid, "L3", "revenue_transfer", "--reason", "x", "--name", "Pat"])
    assert r.exit_code == 2 and "not valid for a debit" in r.output
    r = runner.invoke(app, ["case", "overrides", cid])
    assert r.exit_code == 0 and "L2" in r.output and "applied" in r.output
    r = runner.invoke(app, ["case", "figures", cid])
    assert r.exit_code == 0 and "M20" in r.output and "*" in r.output
    _replace_statement(folder, SMALL.replace("J BLOGGS", "J BLOGGS LTD"))
    r = runner.invoke(app, ["case", "overrides", cid])
    assert "stale" in r.output and "statement changed" in r.output
    r = runner.invoke(app, ["case", "figures", cid])
    assert r.exit_code == 0 and "stale" in r.output
    r = runner.invoke(app, ["case", "resolve-override", cid, "1", "--drop", "--name", "Pat"])
    assert r.exit_code == 0, r.output
    assert overrides.case_figures(folder)["main"].stale == []


@pytest.mark.invariants
@pytest.mark.parametrize("seed", [11, 12, 13])
def test_p3_p4_hold_across_stale_confirmed_and_dropped_overrides(home, tmp_path, seed):
    """P3 and P4 for an override log that holds stale overrides and their confirmations and
    drops: a fresh computation equals the one made after the last step, computing twice is
    identical, and no line's id, date, amount, balance or description ever changes."""
    rng = random.Random(seed)
    folder = _case(tmp_path, SMALL)
    first = overrides.case_figures(folder)["main"].statement
    for line, cat in _random_overrides(first, rng, 3):
        overrides.add_override(folder, line, cat.value, reason="r", name="Pat")
    # The statement changes (one line gone, one added): overrides go stale.
    changed = SMALL.replace("L2,2026-01-06,FPS CREDIT J BLOGGS,300.00,1800.00\n", "") \
                   .replace("L3,2026-01-07,DD COUNCIL,-100.00,1700.00\n", "L3,2026-01-07,DD COUNCIL,-100.00,1400.00\n") \
                   .replace("L4,2026-02-02,FPS CREDIT ACME LTD,250.00,1950.00\n",
                            "L4,2026-02-02,FPS CREDIT ACME LTD,250.00,1650.00\nL5,2026-02-03,FPS CREDIT X,10.00,1660.00\n")
    _replace_statement(folder, changed)
    before = _lines(overrides.case_figures(folder))
    step = None
    for s in overrides.stale_overrides(folder):
        action = "drop" if s.why == "line not in the case" or rng.random() < 0.5 else "confirm"
        try:
            overrides.resolve_stale(folder, s.seq, action, name="Pat")
        except store.CaseStoreError:
            overrides.resolve_stale(folder, s.seq, "drop", name="Pat")
        step = overrides.case_figures(folder)
    t = overrides.case_figures(folder)["main"].statement.transactions
    line = rng.choice(t)
    overrides.add_override(folder, line.id, rng.choice(sorted(overrides.valid_categories(line),
                                                             key=lambda c: c.value)).value, reason="r", name="Pat")
    step = overrides.case_figures(folder)
    fresh = overrides.case_figures(folder)
    assert _snapshot(step) == _snapshot(fresh) == _snapshot(overrides.case_figures(folder))
    assert _lines(fresh) == before and not fresh["main"].stale


@pytest.mark.contracts
def test_case_figures_that_do_not_reconcile_are_refused_without_a_traceback(home, tmp_path, monkeypatch):
    # Antigravity re-review of T16: a ReconciliationError from the figures layer reached the
    # user as a traceback; it is a refusal with exit 6, as `cma run` gives (R3).
    from typer.testing import CliRunner

    from cma.cli import app
    from cma.metrics import ReconciliationError

    case_id = store.new_case(label="t16")

    def refuse(folder, **kw):
        raise ReconciliationError(1, ["L1"])

    monkeypatch.setattr(overrides, "case_figures", refuse)
    r = CliRunner().invoke(app, ["case", "figures", case_id])
    assert r.exit_code == 6 and "no figures shown" in r.output and "Traceback" not in r.output
