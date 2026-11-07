"""CLI review flow (panel run 01, C6)."""

from datetime import date

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
    assert "APPROVED by A. Analyst at " in out.read_text()


def test_interactive_approval_asks_for_a_name(tmp_path):
    out = tmp_path / "memo.md"
    r = runner.invoke(app, ["run", str(_statement(tmp_path)), "--out", str(out)], input="B. Credit\ny\n\n")
    assert r.exit_code == 0, r.output
    assert "APPROVED by B. Credit" in out.read_text()


def test_rejected_memo_is_not_written(tmp_path):
    out = tmp_path / "memo.md"
    r = runner.invoke(app, ["run", str(_statement(tmp_path)), "--out", str(out)], input="B. Credit\nn\nneeds 24 months\n")
    assert r.exit_code == 1
    assert not out.exists()


def test_bad_statement_is_refused_cleanly(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("id,date,description,amount,balance\nT1,2026-01-01,A,100,100\nT1,2026-01-02,B,100,200\n")
    r = runner.invoke(app, ["run", str(bad), "--yes", "--reviewer", "A"])
    assert r.exit_code == 2
    assert "duplicate id T1" in r.output
