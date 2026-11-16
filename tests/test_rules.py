"""Rule tables as packaged data (R54, R10, P7).

The built-in table lives in src/cma/rules/<id>.yaml, is validated on load like the
policy, and carries an id and a hash. CMA_RULESET chooses the table. v1 is the table
as it was before it moved out of code, frozen: tests/golden/p7_v1_baseline.json.gz holds
M1-M16 and every category computed by that code on every committed synthetic case,
example and test fixture, and v1 must reproduce all of it exactly.
"""

import gzip
import hashlib
import json
from datetime import date
from importlib import resources
from pathlib import Path

import pytest
import yaml

from cma import categorise, ingest, metrics, synth
from cma.categorise import RulesetError, active_ruleset, load_ruleset, validate_ruleset
from cma.models import Category, Statement, Transaction

GOLDEN = Path(__file__).parent / "golden" / "p7_v1_baseline.json.gz"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# --- the table as data -------------------------------------------------------------


def test_v1_is_packaged_with_an_id_and_the_hash_of_its_file():
    text = resources.files("cma.rules").joinpath("v1.yaml").read_text()
    rs = load_ruleset("v1")
    assert rs.id == "v1"
    assert rs.sha256 == _sha(text)
    assert rs.rules and all(r.id for r in rs.rules)


def test_v1_is_marked_frozen():
    assert yaml.safe_load(resources.files("cma.rules").joinpath("v1.yaml").read_text())["frozen"] is True


GOOD_RULE = {"id": "card", "pattern": "CARD SETTLEMENT", "direction": "in", "category": "revenue_card_settlement"}


@pytest.mark.parametrize("bad, match", [
    ("- a list", "not a mapping"),
    ({"id": "v1", "frozen": True, "rules": []}, "no rules"),
    ({"frozen": True, "rules": [GOOD_RULE]}, "missing id"),
    ({"id": "v2", "frozen": True, "rules": [GOOD_RULE]}, "id 'v2' does not match"),
    ({"id": "v1", "rules": [GOOD_RULE]}, "frozen"),
    ({"id": "v1", "frozen": True, "rules": [{**GOOD_RULE, "direction": "sideways"}]}, "direction"),
    ({"id": "v1", "frozen": True, "rules": [{**GOOD_RULE, "category": "lottery"}]}, "category 'lottery'"),
    ({"id": "v1", "frozen": True, "rules": [{**GOOD_RULE, "pattern": "("}]}, "pattern does not compile"),
    ({"id": "v1", "frozen": True, "rules": [GOOD_RULE, GOOD_RULE]}, "duplicate id"),
    ({"id": "v1", "frozen": True, "rules": [{k: v for k, v in GOOD_RULE.items() if k != "pattern"}]}, "missing pattern"),
    ({"id": "v1", "frozen": True, "rules": [{**GOOD_RULE, "colour": "red"}]}, "unknown key"),
])
def test_a_malformed_table_is_refused_naming_the_problem(bad, match):
    with pytest.raises(RulesetError, match=match):
        validate_ruleset(bad, expected_id="v1", source="v1.yaml")


def test_an_unknown_ruleset_is_refused_naming_the_known_ones():
    with pytest.raises(RulesetError, match=r"v9.*v1"):
        load_ruleset("v9")


def test_a_ruleset_id_cannot_name_a_path():
    with pytest.raises(RulesetError):
        load_ruleset("../policies/uk_sme_default")


# --- CMA_RULESET chooses the table -----------------------------------------------------


def test_cma_ruleset_chooses_the_table(monkeypatch):
    monkeypatch.setenv("CMA_RULESET", "v1")
    assert active_ruleset().id == "v1"
    monkeypatch.setenv("CMA_RULESET", "v9")
    with pytest.raises(RulesetError, match="CMA_RULESET"):
        active_ruleset()
    with pytest.raises(RulesetError):
        categorise.categorise_one(Transaction(id="T", date=date(2026, 1, 1), description="X", amount=1.0, balance=1.0))


def test_unset_cma_ruleset_uses_the_default(monkeypatch):
    monkeypatch.delenv("CMA_RULESET", raising=False)
    assert active_ruleset().id == categorise.DEFAULT_RULESET


def test_the_categoriser_uses_the_table_it_is_given():
    """A rule added to a table changes the category; the code holds no table of its own."""
    v1 = load_ruleset("v1")
    data = yaml.safe_load(resources.files("cma.rules").joinpath("v1.yaml").read_text())
    data["rules"].insert(0, {"id": "test_cash", "pattern": "CASH PAID IN", "direction": "in", "category": "loan_drawdown"})
    other = categorise.ruleset_from_data(data, expected_id="v1", source="test", sha256="x")
    t = Transaction(id="T", date=date(2026, 1, 1), description="CASH PAID IN", amount=50.0, balance=50.0)
    assert categorise.categorise_one(t, v1) == Category.OTHER
    assert categorise.categorise_one(t, other) == Category.LOAN_DRAWDOWN


# --- R54: the evidence records the ruleset --------------------------------------------------


def test_evidence_records_the_ruleset(tmp_path, monkeypatch):
    from langgraph.types import Command

    from cma.graph import build, evidence_record
    from cma.llm import FakeLLM

    monkeypatch.setenv("CMA_RULESET", "v1")
    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv_path, _ = synth.write(rows, truth, tmp_path)
    graph = build(FakeLLM())
    cfg = {"configurable": {"thread_id": "rules-evidence"}}
    graph.invoke({"statement_path": str(csv_path)}, cfg)
    graph.invoke(Command(resume={"approved": False, "reviewer": "A. Analyst", "notes": "test"}), cfg)
    record = evidence_record(graph.get_state(cfg).values)
    assert record["ruleset"] == {"id": "v1", "sha256": load_ruleset("v1").sha256}


# --- P7 under v1: identical to the code before the move, no exceptions ---------------------


def _golden() -> dict:
    return json.loads(gzip.decompress(GOLDEN.read_bytes()))


def _categories_sha(s: Statement) -> str:
    return _sha("\n".join(f"{t.id}|{t.category.value}|{t.reverses or ''}" for t in s.transactions))


def _metric_rows(s: Statement, vat_stagger):
    return [
        {"id": m.id, "unit": m.unit, "value": m.value, "evidence_n": len(m.evidence),
         "evidence_sha256": _sha("\n".join(m.evidence))}
        for m in metrics.compute(s, vat_stagger=vat_stagger)
        if int(m.id[1:]) <= 16
    ]


def _stagger(key: str):
    return None if key == "None" else int(key)


@pytest.fixture
def v1(monkeypatch):
    monkeypatch.setenv("CMA_RULESET", "v1")


def test_p7_golden_covers_every_case():
    g = _golden()
    combos = {(c["archetype"], c["profile"]) for c in g["synthetic"]}
    assert combos >= {(a, p) for a in synth.ARCHETYPES for p in synth.PROFILES}
    assert [e["path"] for e in g["examples"]] == ["examples/cafe_stretched.csv"]
    assert len(g["fixtures"]) >= 30 and g["lines"]


@pytest.mark.parametrize("case", _golden()["synthetic"], ids=lambda c: f"{c['archetype']}-{c['profile']}-s{c['seed']}-m{c['months']}")
def test_p7_v1_synthetic_cases_match_the_baseline(case, v1, tmp_path):
    rows, truth = synth.generate(case["archetype"], case["profile"], seed=case["seed"], months=case["months"],
                                 end=date.fromisoformat(case["end"]))
    csv_path, _ = synth.write(rows, truth, tmp_path, stem="s")
    assert hashlib.sha256(csv_path.read_bytes()).hexdigest() == case["csv_sha256"], "generator drifted"
    s = categorise.categorise(ingest.load_csv(csv_path))
    assert _categories_sha(s) == case["categories_sha256"]
    for key, expected in case["metrics"].items():
        assert _metric_rows(s, _stagger(key)) == expected


@pytest.mark.parametrize("case", _golden()["examples"], ids=lambda c: c["path"])
def test_p7_v1_committed_examples_match_the_baseline(case, v1):
    path = Path(__file__).parents[1] / case["path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == case["file_sha256"]
    s = categorise.categorise(ingest.load_csv(path))
    assert _categories_sha(s) == case["categories_sha256"]
    for key, expected in case["metrics"].items():
        assert _metric_rows(s, _stagger(key)) == expected


@pytest.mark.parametrize("case", _golden()["fixtures"], ids=lambda c: f"fixture-{len(c['lines'])}-lines")
def test_p7_v1_test_fixtures_match_the_baseline(case, v1):
    s = Statement(account_name=case["account_name"], transactions=[
        Transaction(id=i, date=date.fromisoformat(d), description=desc, amount=a, balance=b)
        for i, d, desc, a, b, _, _ in case["lines"]
    ])
    s = categorise.categorise(s)
    assert [[t.category.value, t.reverses] for t in s.transactions] == [[c, r] for *_, c, r in case["lines"]]
    assert _metric_rows(s, case["vat_stagger"]) == case["metrics"]


def test_p7_v1_single_lines_match_the_baseline(v1):
    got = [
        [d, a, categorise.categorise_one(Transaction(id="T", date=date(2026, 1, 1), description=d, amount=a, balance=0.0)).value]
        for d, a, _ in _golden()["lines"]
    ]
    assert got == _golden()["lines"]
