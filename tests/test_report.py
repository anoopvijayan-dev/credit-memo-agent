"""`cma report` (R50, C11): for each case, the judgement queue's lines, the lines each
indicator flagged, overrides by old and new category, the unexplained share of money in,
whether every figure was traced and whether the case was signed off; with no applicant
names, descriptions or amounts and no duration derived from the case log's timestamps. It
exits non-zero when a case is untraced or does not reconcile."""

import json
import re

import pytest
from typer.testing import CliRunner

from cma import metrics, overrides, pack, report, store
from cma.cli import app

runner = CliRunner()

# Sentinels: each would only reach the report through a name, a description, an amount,
# a label, a reason or a typed name.
NAME = "ZQXJANEDOEZQX"
BUSINESS = "ZQXACMEBAKERYZQX"
OWN = "ZQXOWNHOLDINGSZQX"
LABEL = "ZQXLABELZQX"
REASON = "ZQXREASONZQX"
TYPED = "ZQXTYPEDNAMEZQX"
DESC = "ZQXDESCZQX"
BODY = ("id,date,description,amount,balance\n"
        "L1,2026-01-05,CARD SETTLEMENT STRIPE,4321.87,5321.87\n"
        f"L2,2026-01-06,FPS CREDIT {DESC} ONE,1234.56,6556.43\n"
        "L3,2026-01-07,DD COUNCIL,-987.65,5568.78\n"
        "L4,2026-01-08,CASH DEPOSIT AT COUNTER,2765.43,8334.21\n"
        f"L5,2026-02-02,FPS CREDIT {NAME},1111.11,9445.32\n"
        f"L6,2026-02-03,FPS CREDIT {DESC} TWO,555.55,10000.87\n")
AMOUNTS = ("4321.87", "4,321.87", "1234.56", "1,234.56", "987.65", "2765.43", "2,765.43", "1111.11",
           "1,111.11", "555.55", "5321.87", "10000.87", "10,000.87", "9445.32")
SENTINELS = (NAME, BUSINESS, OWN, LABEL, REASON, TYPED, DESC)
# Anything that reads as a duration: a clock time, a time unit, an ISO timestamp.
DURATION = re.compile(r"\d+:\d{2}|\b(second|minute|hour|day|week)s?\b|\bmins?\b|\bhrs?\b|\d{4}-\d{2}-\d{2}T"
                      r"|duration|elapsed|took", re.IGNORECASE)


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    monkeypatch.delenv("CMA_RULESET", raising=False)
    return root


def _case(tmp_path, body: str = BODY) -> tuple[str, object]:
    case_id = store.new_case(label=LABEL)
    folder = store.case_folder(case_id)
    store.update_profile(folder, lambda p: {**p, "business_name": BUSINESS, "directors": [NAME],
                                            "own_accounts": [OWN]})
    src = tmp_path / f"{case_id}.csv"
    src.write_text(body)
    store.import_file(folder, src, "csv")
    return case_id, folder


def _signed_case(tmp_path):
    case_id, folder = _case(tmp_path)
    overrides.add_override(folder, "L2", "revenue_transfer", reason=REASON, name=TYPED)
    p = pack.build_preview(folder, case_id)
    pack.sign_off(folder, case_id, p.snapshot, TYPED)
    return case_id, folder


@pytest.mark.contracts
def test_r50_report_summarises_each_case(home, tmp_path):
    case_id, folder = _signed_case(tmp_path)
    other_id, _ = _case(tmp_path)
    rows = {r["case"]: r for r in report.report()}
    assert set(rows) == {case_id, other_id}
    r = rows[case_id]
    assert r["state"] == "shown" and r["reconciled"] is True and r["traced"] is True
    assert r["signed_off"] is True and rows[other_id]["signed_off"] is False
    assert r["overrides"] == [{"old": "other", "new": "revenue_transfer", "count": 1}]
    assert rows[other_id]["overrides"] == []
    # The queue and the indicators, as the workspace's summary counts them (R15, R14).
    from cma.review.summary import case_summary

    s = case_summary(folder, case_id)
    assert r["queue_lines"] == len(s["queue"]) > 0
    kinds = {}
    for a in s["accounts"]:
        for i in a["indicators"]:
            kinds.setdefault(i["kind"], set()).update(line["id"] for line in i["lines"])
    assert r["indicator_lines"] == {k: len(v) for k, v in sorted(kinds.items())}
    m26 = next(f for f in s["accounts"][0]["figures"] if f["id"] == "M26")
    assert r["unexplained_share"] == m26["display"] and r["unexplained_share"].endswith("%")


@pytest.mark.contracts
def test_r50_report_output_holds_no_sentinel_and_no_duration(home, tmp_path):
    _signed_case(tmp_path)
    _case(tmp_path)
    for args in (["report"], ["report", "--json"]):
        res = runner.invoke(app, args)
        assert res.exit_code == 0, res.output
        out = res.output
        for s in SENTINELS + AMOUNTS:
            assert s not in out, (s, out)
        assert "£" not in out
        assert not DURATION.search(out), (DURATION.search(out), out)
    rows = json.loads(runner.invoke(app, ["report", "--json"]).output)
    assert len(rows) == 2 and all(r["traced"] and r["reconciled"] for r in rows)


@pytest.mark.contracts
def test_r50_report_exits_non_zero_on_an_unreconciled_case(home, tmp_path, monkeypatch):
    case_id, _ = _case(tmp_path)

    def refuse(folder, **kw):
        raise metrics.ReconciliationError(1, ["L2"])

    monkeypatch.setattr(overrides, "case_figures", refuse)
    res = runner.invoke(app, ["report"])
    assert res.exit_code == 1
    assert case_id in res.output and "not reconciled" in res.output
    for s in SENTINELS + AMOUNTS:
        assert s not in res.output
    r = report.report()[0]
    assert r["reconciled"] is False and r["traced"] is False


@pytest.mark.contracts
def test_r50_report_exits_non_zero_on_an_untraced_case(home, tmp_path, monkeypatch):
    _case(tmp_path)
    real = metrics.compute

    def untraced(*a, **kw):
        out = real(*a, **kw)
        m2 = next(m for m in out if m.id == "M2")
        m2.evidence = []  # a non-zero figure with no lines behind it
        return out

    monkeypatch.setattr(metrics, "compute", untraced)
    monkeypatch.setattr(overrides, "_BARE", overrides._Memo(8))
    res = runner.invoke(app, ["report"])
    assert res.exit_code == 1, res.output
    assert "untraced" in res.output
    r = report.report()[0]
    assert r["traced"] is False and r["reconciled"] is True and r["untraced"] == ["main:M2"]


@pytest.mark.contracts
def test_r50_report_with_no_cases(home):
    res = runner.invoke(app, ["report"])
    assert res.exit_code == 0 and "no cases" in res.output
    assert json.loads(runner.invoke(app, ["report", "--json"]).output) == []


@pytest.mark.contracts
def test_r50_report_one_failing_case_does_not_hide_the_others(home, tmp_path, monkeypatch):
    bad_id, _ = _case(tmp_path)
    good_id, _ = _case(tmp_path)
    import cma.review.summary as summary

    real = summary.case_summary

    def unforeseen(folder, case_id):
        if case_id == bad_id:
            raise RuntimeError(f"unforeseen {DESC}")
        return real(folder, case_id)

    monkeypatch.setattr(summary, "case_summary", unforeseen)
    res = runner.invoke(app, ["report"])
    assert res.exit_code == 1, res.output
    assert bad_id in res.output and "could not be read" in res.output
    assert good_id in res.output and DESC not in res.output
    rows = {r["case"]: r for r in report.report()}
    assert rows[bad_id]["state"] == "refused" and rows[bad_id]["traced"] is None
    assert rows[good_id]["traced"] is True and rows[good_id]["reconciled"] is True


# --- verify-273 -----------------------------------------------------------------------

VAT_FREE = ("id,date,description,amount,balance\n"
            "L1,2026-01-05,CARD SETTLEMENT STRIPE,100.00,1100.00\n"
            "L2,2026-06-15,CARD SETTLEMENT STRIPE,200.00,1300.00\n")


def _tree(root):
    """Every file's bytes and every path's mode under `root`."""
    out = {}
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[str(p.relative_to(root))] = (st.st_mode, p.read_bytes() if p.is_file() else None)
    out["."] = (root.lstat().st_mode, None)
    return out


@pytest.mark.contracts
def test_r50_a_missed_vat_payment_with_no_vat_line_is_traced(home, tmp_path):
    case_id, folder = _case(tmp_path, VAT_FREE)
    store.update_profile(folder, lambda p: {**p, "vat_stagger": 1})
    from cma.review.summary import case_summary

    m13 = next(f for f in case_summary(folder, case_id)["accounts"][0]["figures"] if f["id"] == "M13")
    assert m13["value"] and m13["value"] >= 1 and m13["lines"] == 0  # a missed payment has no line
    r = report.report()[0]
    assert r["traced"] is True and r["untraced"] == []
    assert runner.invoke(app, ["report"]).exit_code == 0


@pytest.mark.contracts
def test_r50_a_zero_valued_derived_figure_with_no_lines_is_untraced(home, tmp_path, monkeypatch):
    _case(tmp_path, VAT_FREE)  # no unexplained credit: M26 is 0%
    real = metrics.compute

    def bare(*a, **kw):
        out = real(*a, **kw)
        m26 = next(m for m in out if m.id == "M26")
        assert m26.value == 0
        m26.evidence = []
        return out

    monkeypatch.setattr(metrics, "compute", bare)
    monkeypatch.setattr(overrides, "_BARE", overrides._Memo(8))
    r = report.report()[0]
    assert r["traced"] is False and r["untraced"] == ["main:M26"]
    assert runner.invoke(app, ["report"]).exit_code == 1


@pytest.mark.contracts
def test_r50_a_zero_or_na_monthly_derived_cell_must_cite_or_say_why(home, tmp_path, monkeypatch):
    _case(tmp_path, VAT_FREE)
    real = metrics.monthly_summary

    def bare(*a, **kw):
        rows = real(*a, **kw)
        assert rows[0].days_overdrawn.value == 0
        rows[0].days_overdrawn.evidence = []
        rows[1].average_balance.value, rows[1].average_balance.note = None, None
        rows[1].average_balance.evidence = []
        return rows

    monkeypatch.setattr(metrics, "monthly_summary", bare)
    monkeypatch.setattr(overrides, "_BARE", overrides._Memo(8))
    r = report.report()[0]
    assert r["traced"] is False
    assert r["untraced"] == ["main:2026-01:days_overdrawn", "main:2026-02:average_balance"]
    assert runner.invoke(app, ["report"]).exit_code == 1


@pytest.mark.contracts
def test_r50_report_writes_nothing_and_changes_no_mode(home, tmp_path):
    _signed_case(tmp_path)
    _case(tmp_path)
    for p in [home, *home.rglob("*")]:
        if p.is_dir():
            p.chmod(0o755)
    before = _tree(home)
    for args in (["report"], ["report", "--json"]):
        assert runner.invoke(app, args).exit_code == 0
    assert _tree(home) == before


@pytest.mark.contracts
def test_r50_a_case_gone_while_listed_is_refused_and_the_others_reported(home, tmp_path, monkeypatch):
    gone_id, gone = _case(tmp_path)
    kept_id, _ = _case(tmp_path)
    listed = report.case_ids()
    (gone / store.PROFILE).unlink()  # purged between the listing and its report
    monkeypatch.setattr(report, "case_ids", lambda root=None: listed)
    rows = {r["case"]: r for r in report.report()}
    assert rows[gone_id]["state"] == "refused" and rows[gone_id]["traced"] is None
    assert rows[kept_id]["traced"] is True
    res = runner.invoke(app, ["report"])
    assert res.exit_code == 1 and "could not be read" in res.output and kept_id in res.output


@pytest.mark.skipif(__import__("shutil").which("git") is None, reason="git is not installed")
@pytest.mark.contracts
def test_r50_report_refuses_a_case_root_git_does_not_ignore(tmp_path, monkeypatch):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    root = repo / "cases-here"
    (root / store.CASES / "0123456789abcdef").mkdir(parents=True)
    (root / store.CASES / "0123456789abcdef" / store.PROFILE).write_text("label: x\n")
    monkeypatch.setenv("CMA_HOME", str(root))
    res = runner.invoke(app, ["report"])
    assert res.exit_code == 2, res.output
    assert "Refused" in res.output and "does not ignore" in " ".join(res.output.split())
    assert "Traceback" not in res.output


@pytest.mark.contracts
def test_r50_an_unreadable_statement_does_not_hide_the_sign_off(home, tmp_path):
    case_id, folder = _signed_case(tmp_path)
    (folder / "statements" / "main.csv").write_text("bad")
    r = report.case_report(folder, case_id)
    assert r["state"] == "refused" and r["traced"] is None
    assert r["signed_off"] is True


# --- every honest case reads as traced (T40 pass) ------------------------------------------


@pytest.mark.invariants
@pytest.mark.parametrize("seed", range(24))
def test_a_generated_case_is_reported_traced_whatever_its_features(tmp_path, monkeypatch, seed):
    """No false alarm: any generated case (one or two accounts, either ruleset, with or without
    a VAT stagger, a proposed repayment and an override) has every figure and cell traced. The
    reserve account of a two-account case, with no trading and no debt, has a proposed cover of
    0.00x resting on no line, which is traced."""
    import random
    from datetime import date

    from cma import overrides, report, store, synth

    rng = random.Random(seed)
    monkeypatch.setenv("CMA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CMA_RULESET", rng.choice(["v1", "v2"]))
    archetype, profile = rng.choice(list(synth.ARCHETYPES)), rng.choice(list(synth.PROFILES))
    features = tuple(f for f in synth.FEATURES if rng.random() < 0.5)
    rows, truth = synth.generate(archetype, profile, seed=seed, months=rng.choice([1, 3, 6, 12]),
                                 end=date(2026, 9, 30), features=features)
    if "two_accounts" in features:
        case_id, folder = synth.to_case(rows, truth, tmp_path / "work")
    else:
        case_id = store.new_case(label="t40")
        folder = store.case_folder(case_id)
        src, _ = synth.write(rows, truth, tmp_path / "work")
        store.import_file(folder, src, "csv")
    store.update_profile(folder, lambda old: {**old, "vat_stagger": rng.choice([None, 1, 2, 3]),
                                              "proposed_monthly_repayment": rng.choice([None, "750.00"])})
    if rng.random() < 0.5:
        figs = overrides.case_figures(folder)
        account = next(iter(figs))
        line = next(t for t in figs[account].statement.transactions if t.amount > 0)
        overrides.add_override(folder, line.id, "other_explained_in", reason="r", name="A", account=account)
    row = report.case_report(folder, case_id)
    assert row["state"] in ("shown", "empty") and row["traced"] is True, row["untraced"]
