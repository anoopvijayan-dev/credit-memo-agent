"""CLI review flow (panel run 01, C6)."""

import json
from datetime import date

import pytest
from typer.testing import CliRunner

from cma import synth
from cma.cli import app

runner = CliRunner()


def _statement(tmp_path):
    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv_path, _ = synth.write(rows, truth, tmp_path)
    return csv_path


def test_yes_without_reviewer_is_refused(tmp_path):
    r = runner.invoke(app, ["run", str(_statement(tmp_path)), "--yes"])
    assert r.exit_code == 2
    assert "--reviewer" in r.output


def test_yes_with_reviewer_writes_named_approval(tmp_path):
    out = tmp_path / "memo.md"
    r = runner.invoke(app, ["run", str(_statement(tmp_path)), "--yes", "--reviewer", "A. Analyst", "--out", str(out)])
    assert r.exit_code == 0, r.output
    text = out.read_text()
    # Panel run 02, D12: a script approval says so.
    assert "APPROVED by A. Analyst (automated, no interactive review) at " in text


def test_written_memo_carries_its_evidence(tmp_path):
    # Panel run 02, D7: the memo file alone could not say where a figure came from.
    out = tmp_path / "memo.md"
    r = runner.invoke(app, ["run", str(_statement(tmp_path)), "--yes", "--reviewer", "A", "--vat-stagger", "3",
                            "--out", str(out)])
    assert r.exit_code == 0, r.output
    text = out.read_text()
    assert "## Appendix: the figures behind this memo" in text
    assert "| M10 | Debt service cover |" in text and "| R2 |" in text
    assert "sha256" in text and "Model: fake" in text
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    m2 = next(m for m in record["metrics"] if m["id"] == "M2")
    assert m2["evidence"] and record["policy"]["policy_sha256"]
    assert record["vat_stagger"] == 3 and record["guard"]["grounded"] is True


def test_whitespace_reviewer_is_refused(tmp_path):
    r = runner.invoke(app, ["run", str(_statement(tmp_path)), "--yes", "--reviewer", "   "])
    assert r.exit_code == 2


def test_pending_review_survives_a_restart(tmp_path):
    # Panel run 02, D15: a pending review lived only in memory.
    pytest.importorskip("langgraph.checkpoint.sqlite")
    db = tmp_path / "reviews.sqlite"
    csv = _statement(tmp_path)
    r = runner.invoke(app, ["run", str(csv), "--checkpoint-db", str(db), "--thread", "case-7"], input="\x04")
    assert "review id: case-7" in r.output
    out = tmp_path / "memo.md"
    r = runner.invoke(app, ["resume", "case-7", "--checkpoint-db", str(db), "--reviewer", "C. Later",
                            "--out", str(out)], input="y\n\n")
    assert r.exit_code == 0, r.output
    assert "APPROVED by C. Later" in out.read_text()


def test_interactive_approval_asks_for_a_name(tmp_path):
    out = tmp_path / "memo.md"
    r = runner.invoke(app, ["run", str(_statement(tmp_path)), "--out", str(out)], input="B. Credit\ny\n\n")
    assert r.exit_code == 0, r.output
    assert "APPROVED by B. Credit" in out.read_text()


def test_rejected_memo_is_kept_as_rejected_not_as_the_memo(tmp_path):
    # Panel run 02, D15: rejections were discarded.
    out = tmp_path / "memo.md"
    r = runner.invoke(app, ["run", str(_statement(tmp_path)), "--out", str(out)], input="B. Credit\nn\nneeds 24 months\n")
    assert r.exit_code == 1
    assert not out.exists()
    assert "NOT APPROVED by B. Credit" in (tmp_path / "memo.rejected.md").read_text()
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    assert record["review"]["approved"] is False and record["review"]["notes"] == "needs 24 months"


def test_bad_statement_is_refused_cleanly(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("id,date,description,amount,balance\nT1,2026-01-01,A,100,100\nT1,2026-01-02,B,100,200\n")
    r = runner.invoke(app, ["run", str(bad), "--yes", "--reviewer", "A"])
    assert r.exit_code == 2
    assert "duplicate id T1" in r.output
