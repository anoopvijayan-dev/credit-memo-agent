"""T11: v2 is the default ruleset (R10, R53, P7). The committed examples are regenerated
under it, policy rule R9 and the offline model's M16 sentence say what M16 now measures,
the appendix carries M17-M28, the MCP server has a read-only money-in buckets tool, and the
lines v2 newly recognises are listed where a person can read them."""

import json
import re
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cma import categorise, ingest, metrics, synth
from cma.categorise import DEFAULT_RULESET, active_ruleset, load_ruleset
from cma.cli import app
from cma.policy import load_policy

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
NEWLY_RECOGNISED_DOC = ROOT / "docs" / "v2-newly-recognised.md"


@pytest.mark.contracts
def test_v2_is_the_default_ruleset(monkeypatch):
    monkeypatch.delenv("CMA_RULESET", raising=False)
    assert DEFAULT_RULESET == "v2"
    assert active_ruleset().id == "v2"
    monkeypatch.setenv("CMA_RULESET", "v1")
    assert active_ruleset().id == "v1"  # the rollback still works


@pytest.mark.contracts
def test_policy_r9_is_worded_for_m16s_name():
    rules = {r["id"]: r for r in load_policy()["rules"]}
    assert rules["R9"]["metric"] == "M16"
    text = rules["R9"]["description"].lower()
    assert "unexplained" not in text and "not trading receipts" in text


@pytest.mark.contracts
def test_offline_models_m16_sentence_names_non_trading_money_in(tmp_path):
    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv, _ = synth.write(rows, truth, tmp_path)
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", str(csv), "--yes", "--reviewer", "A", "--out", str(out)])
    assert r.exit_code == 0, r.output
    text = out.read_text()
    assert "Unclassified" not in text
    assert re.search(r"Money in that is not trading receipts was \S+ \[M16\]", text)


# --- the committed examples, regenerated under v2 ------------------------------------------

def _committed_memo_body(text: str) -> str:
    # The approval line carries the time `cma run` ran; everything else is reproducible.
    return re.sub(r"(APPROVED by .*? at )\d{4}-\d\d-\d\d \d\d:\d\d UTC", r"\1<time>", text)


@pytest.mark.contracts
def test_committed_example_is_regenerated_under_v2(tmp_path, monkeypatch):
    """examples/cafe_stretched.memo.md and its evidence are what `cma run` writes today, with
    the default ruleset, on the committed CSV (README: the output of the offline model)."""
    monkeypatch.delenv("CMA_RULESET", raising=False)
    monkeypatch.chdir(ROOT)  # the evidence records the path as given, as the README runs it
    out = tmp_path / "cafe_stretched.memo.md"
    r = CliRunner().invoke(app, ["run", "examples/cafe_stretched.csv", "--vat-stagger", "3", "--yes",
                                 "--reviewer", "A. Analyst", "--out", str(out)])
    assert r.exit_code == 0, r.output
    committed = (EXAMPLES / "cafe_stretched.memo.md").read_text()
    assert _committed_memo_body(out.read_text()) == _committed_memo_body(committed)
    ev = json.loads((EXAMPLES / "cafe_stretched.memo.evidence.json").read_text())
    fresh = json.loads((tmp_path / "cafe_stretched.memo.evidence.json").read_text())
    v2 = load_ruleset("v2")
    assert ev["ruleset"] == {"id": "v2", "sha256": v2.sha256}
    for key in ("schema_version", "statement", "model", "vat_stagger", "ruleset", "lines", "metrics",
                "monthly_summary", "thresholds", "indicators", "policy", "guard", "redrafts", "memo"):
        assert ev[key] == fresh[key], key
    # The appendix carries every figure, M17-M28 included (design: Migration).
    for i in range(1, 29):
        assert re.search(rf"^\| M{i} \| ", committed, re.MULTILINE), f"M{i}"
    assert "| M16 | Non-trading money in (share) |" in committed


@pytest.mark.contracts
def test_committed_truth_names_the_director_capital_line_director_funding():
    truth = json.loads((EXAMPLES / "cafe_stretched.truth.json").read_text())
    s = categorise.categorise(ingest.load_csv(EXAMPLES / "cafe_stretched.csv"), load_ruleset("v2"))
    capital = [t for t in s.transactions if t.description == "FPS CREDIT DIRECTOR CAPITAL INTRODUCED"]
    assert capital
    for t in capital:
        assert truth["category_by_id"][t.id] == "director_funding" == t.category.value
    assert all(truth["category_by_id"][t.id] == t.category.value for t in s.transactions)


@pytest.mark.parametrize("archetype", synth.ARCHETYPES)
@pytest.mark.parametrize("profile", synth.PROFILES)
def test_generated_truth_matches_v2_categories_without_features(tmp_path, archetype, profile):
    rows, truth = synth.generate(archetype, profile, end=date(2026, 9, 30))
    csv, _ = synth.write(rows, truth, tmp_path)
    s = categorise.categorise(ingest.load_csv(csv), load_ruleset("v2"))
    assert [t.id for t in s.transactions if t.category.value != truth.category_by_id[t.id]] == []


# --- the lines v2 newly recognises, listed for a person (R10) -------------------------------

def _listed_lines() -> set[tuple[str, str, str, str]]:
    rows = set()
    for line in NEWLY_RECOGNISED_DOC.read_text().splitlines():
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[1] in ("in", "out"):
            rows.add(tuple(cells))
    return rows


@pytest.mark.contracts
def test_the_newly_recognised_lines_are_listed_in_the_docs():
    from test_rules_v2 import NEWLY_RECOGNISED

    assert _listed_lines() == NEWLY_RECOGNISED
    readme = (ROOT / "README.md").read_text()
    assert "docs/v2-newly-recognised.md" in readme


# --- the read-only money-in buckets MCP tool -------------------------------------------------

@pytest.fixture
def mcp_data(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    home = tmp_path / "home"
    home.mkdir()
    (home / "firm.yaml").write_text("offline_lock: false\n")
    monkeypatch.setenv("CMA_HOME", str(home))
    data = tmp_path / "data"
    data.mkdir()
    (data / "ex.csv").write_bytes((EXAMPLES / "cafe_stretched.csv").read_bytes())
    monkeypatch.setenv("CMA_DATA_DIR", str(data))
    return data


@pytest.mark.contracts
def test_mcp_money_in_buckets_tool_returns_the_buckets_with_evidence_ids(mcp_data, monkeypatch):
    from cma import mcp_server

    monkeypatch.delenv("CMA_RULESET", raising=False)
    out = mcp_server.money_in_buckets("ex.csv")
    s = categorise.categorise(ingest.load_csv(mcp_data / "ex.csv"))
    by_id = {m.id: m for m in metrics.compute(s)}
    assert out["total_money_in"]["id"] == "M17"
    assert [b["id"] for b in out["buckets"]] == ["M2", "M19", "M20", "M21", "M22", "M23", "M24", "M25"]
    assert out["cash_deposits_within_trading_receipts"]["id"] == "M18"
    for d in [out["total_money_in"], out["cash_deposits_within_trading_receipts"], *out["buckets"]]:
        m = by_id[d["id"]]
        assert d == {"id": m.id, "name": m.name, "value": m.value, "unit": m.unit, "evidence": m.evidence}
    # The buckets add up to total money in, in pence (R1, P1).
    assert sum(round(b["value"] * 100) for b in out["buckets"]) == round(out["total_money_in"]["value"] * 100)
    assert next(b for b in out["buckets"] if b["id"] == "M20")["value"] == 8000.0  # v2: director capital
    assert "STRIPE" not in json.dumps(out) and "DIRECTOR" not in json.dumps(out)  # ids only (R34)


@pytest.mark.contracts
def test_mcp_money_in_buckets_tool_is_registered_and_refuses_under_the_lock(mcp_data, tmp_path):
    from cma import firm, mcp_server

    assert "money_in_buckets" in {t.name for t in mcp_server.server._tool_manager.list_tools()}
    with pytest.raises(ValueError):
        mcp_server.money_in_buckets("../outside.csv")
    (tmp_path / "home" / "firm.yaml").write_text("offline_lock: true\n")
    with pytest.raises(firm.OfflineLockError):
        mcp_server.money_in_buckets("ex.csv")
