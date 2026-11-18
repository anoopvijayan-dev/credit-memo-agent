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


def test_unknown_cma_ruleset_is_refused_not_blamed_on_the_model(tmp_path, monkeypatch):
    # T01: exit 4 means the model failed; a bad CMA_RULESET is a refusal (exit 2).
    monkeypatch.setenv("CMA_RULESET", "v9")
    r = runner.invoke(app, ["run", str(_statement(tmp_path)), "--yes", "--reviewer", "A"])
    assert r.exit_code == 2, r.output
    assert "Refused." in r.output and "CMA_RULESET" in r.output and "model call failed" not in r.output


@pytest.mark.contracts
@pytest.mark.parametrize("env", [None, "v1"])
def test_written_evidence_records_the_ruleset(tmp_path, monkeypatch, env):
    """R54: `cma run` writes the ruleset id and the pinned hash of v1 into the evidence
    file, with CMA_RULESET unset (the default) and set to v1."""
    from cma.categorise import FROZEN_SHA256

    if env is None:
        monkeypatch.delenv("CMA_RULESET", raising=False)
    else:
        monkeypatch.setenv("CMA_RULESET", env)
    out = tmp_path / "memo.md"
    r = runner.invoke(app, ["run", str(_statement(tmp_path)), "--yes", "--reviewer", "A", "--out", str(out)])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    assert record["ruleset"] == {"id": "v1", "sha256": FROZEN_SHA256["v1"]}


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
    record = json.loads((tmp_path / "memo.rejected.evidence.json").read_text())
    assert record["review"]["approved"] is False and record["review"]["notes"] == "needs 24 months"
    assert not (tmp_path / "memo.evidence.json").exists()


def test_bad_statement_is_refused_cleanly(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("id,date,description,amount,balance\nT1,2026-01-01,A,100,100\nT1,2026-01-02,B,100,200\n")
    r = runner.invoke(app, ["run", str(bad), "--yes", "--reviewer", "A"])
    assert r.exit_code == 2
    assert "duplicate id T1" in r.output


# --- panel run 03 ------------------------------------------------------------------

def test_an_earlier_memo_and_its_evidence_are_never_overwritten(tmp_path):
    # E5: a rejected re-run replaced the approved memo's evidence.
    out = tmp_path / "memo.md"
    csv = _statement(tmp_path)
    assert runner.invoke(app, ["run", str(csv), "--yes", "--reviewer", "A", "--out", str(out)]).exit_code == 0
    before = (tmp_path / "memo.evidence.json").read_text()
    r = runner.invoke(app, ["run", str(csv), "--out", str(out)], input="B\nn\n\n")
    assert r.exit_code == 2 and "Refusing to overwrite" in r.output
    assert (tmp_path / "memo.evidence.json").read_text() == before


def _pending(tmp_path, thread="case-1"):
    pytest.importorskip("langgraph.checkpoint.sqlite")
    db = tmp_path / "reviews.sqlite"
    csv = _statement(tmp_path)
    runner.invoke(app, ["run", str(csv), "--checkpoint-db", str(db), "--thread", thread], input="\x04")
    return db, csv


def test_a_review_id_cannot_be_reused(tmp_path):
    # E9: a reused thread inherited redrafts, feedback and the old review.
    db, csv = _pending(tmp_path)
    r = runner.invoke(app, ["run", str(csv), "--checkpoint-db", str(db), "--thread", "case-1"])
    assert r.exit_code == 2 and "already used" in r.output


def test_second_reviewer_is_told_the_review_was_already_decided(tmp_path):
    # E10: the second decision was dropped and reported as the first reviewer's approval.
    from cma.cli import _decide
    from cma.graph import build, sqlite_checkpointer
    from cma.llm import FakeLLM

    db, _ = _pending(tmp_path)
    cfg = {"configurable": {"thread_id": "case-1"}}
    with sqlite_checkpointer(db) as a, sqlite_checkpointer(db) as b:
        ga, gb = build(FakeLLM(), checkpointer=a), build(FakeLLM(), checkpointer=b)
        assert ga.get_state(cfg).interrupts and gb.get_state(cfg).interrupts
        _decide(ga, cfg, {"approved": True, "reviewer": "A. First"})
        import typer
        with pytest.raises(typer.Exit) as e:
            _decide(gb, cfg, {"approved": False, "reviewer": "B. Second"})
        assert e.value.exit_code == 5


def test_resume_refuses_a_changed_statement(tmp_path):
    # E20: resume trusted the stored state.
    db, csv = _pending(tmp_path)
    csv.write_text(csv.read_text().replace("CARD ACQUIRER SETTLEMENT", "CARD ACQUIRER SETTLEMENT X", 1))
    r = runner.invoke(app, ["resume", "case-1", "--checkpoint-db", str(db), "--reviewer", "C"], input="y\n\n")
    assert r.exit_code == 2 and "has changed" in r.output


@pytest.mark.parametrize("body, expected", [
    (None, "no such file"),
    ("cp1252", "not UTF-8"),
])
def test_unreadable_statements_are_refused_cleanly(tmp_path, body, expected):
    # E24: tracebacks instead of a refusal.
    p = tmp_path / "s.csv"
    if body == "cp1252":
        p.write_bytes("id,date,description,amount,balance\nT1,2026-01-01,CAF\xc9 \xa3,1.00,1.00\n".encode("cp1252"))
    r = runner.invoke(app, ["run", str(p), "--yes", "--reviewer", "A"])
    assert r.exit_code == 2 and expected in r.output


def test_bad_policy_yaml_is_refused_cleanly(tmp_path):
    bad = tmp_path / "p.yaml"
    bad.write_text("rules: [")
    r = runner.invoke(app, ["run", str(_statement(tmp_path)), "--policy", str(bad), "--yes", "--reviewer", "A"])
    assert r.exit_code == 2 and "not valid YAML" in r.output
