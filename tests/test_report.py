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
