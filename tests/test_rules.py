"""Rule tables as packaged data (R54, R10, P7).

The built-in table lives in src/cma/rules/<id>.yaml, is validated on load like the
policy, and carries an id and a hash. CMA_RULESET chooses the table. v1 is the table
as it was before it moved out of code, frozen: tests/golden/p7_v1_baseline.json.gz holds
M1-M16 and every category computed by that code on every committed synthetic case,
example and test fixture, and v1 must reproduce all of it exactly.

The baseline is rebuilt from the pre-change commit with the tools in
tests/golden/p7_tools/ (commands in make_p7_baseline.py's docstring); a rebuild must
equal the committed file on every key. tests/test_p7_history.py does that rebuild.
"""

import gzip
import hashlib
import json
import random
import re
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


@pytest.mark.contracts
def test_v1_is_packaged_with_an_id_and_the_hash_of_its_file():
    raw = resources.files("cma.rules").joinpath("v1.yaml").read_bytes()
    rs = load_ruleset("v1")
    assert rs.id == "v1"
    assert rs.sha256 == hashlib.sha256(raw).hexdigest()
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
    # These raise OverflowError and RecursionError, not re.error.
    ({"id": "v1", "frozen": True, "rules": [{**GOOD_RULE, "pattern": "A{4294967296}"}]}, "pattern does not compile"),
    ({"id": "v1", "frozen": True, "rules": [{**GOOD_RULE, "pattern": "(" * 2000 + ")" * 2000}]},
     "pattern does not compile"),
    ({"id": "v1", "frozen": True, "rules": [GOOD_RULE, GOOD_RULE]}, "duplicate id"),
    ({"id": "v1", "frozen": True, "rules": [{k: v for k, v in GOOD_RULE.items() if k != "pattern"}]}, "missing pattern"),
    ({"id": "v1", "frozen": True, "rules": [{**GOOD_RULE, "colour": "red"}]}, "unknown key"),
])
def test_a_malformed_table_is_refused_naming_the_problem(bad, match):
    with pytest.raises(RulesetError, match=match):
        validate_ruleset(bad, expected_id="v1", source="v1.yaml")


# A field of the wrong YAML type (a list, a mapping, a number, null) is refused as a
# malformed table, never raised as a TypeError that the CLI would blame on the model.
@pytest.mark.parametrize("field", ["id", "pattern", "direction", "category"])
@pytest.mark.parametrize("value", [["returned_dd"], {"a": 1}, 7, None, True])
def test_a_rule_field_of_the_wrong_type_is_refused(field, value):
    bad = {"id": "v1", "frozen": True, "rules": [{**GOOD_RULE, field: value}, GOOD_RULE | {"id": "b"}]}
    with pytest.raises(RulesetError, match=f"rule 1 .*{field}"):
        validate_ruleset(bad, expected_id="v1", source="v1.yaml")


@pytest.mark.parametrize("bad", [
    {"id": ["v1"], "frozen": True, "rules": [GOOD_RULE]},
    {"id": "v1", "frozen": True, "rules": [GOOD_RULE], ("a", "b"): 1},
    {"id": "v1", "frozen": True, "rules": {"card": GOOD_RULE}},
    {"id": "v1", "frozen": True, "rules": ["card"]},
])
def test_a_table_field_of_the_wrong_type_is_refused(bad):
    with pytest.raises(RulesetError):
        validate_ruleset(bad, expected_id="v1", source="v1.yaml")


@pytest.fixture
def packaged_table(monkeypatch):
    """Replace the packaged v1.yaml bytes, as a damaged install would."""
    def use(text: str | bytes):
        raw = text.encode() if isinstance(text, str) else text
        monkeypatch.setattr(categorise, "_table_bytes", lambda ruleset_id: raw)
        load_ruleset.cache_clear()
    yield use
    load_ruleset.cache_clear()


def test_a_damaged_packaged_table_is_refused_on_load(packaged_table):
    text = resources.files("cma.rules").joinpath("v1.yaml").read_text()
    packaged_table(text.replace("category: returned_dd", "category: [returned_dd]", 1))
    with pytest.raises(RulesetError, match=r"cma/rules/v1.yaml: rule 1 \(returned_dd\): category"):
        load_ruleset("v1")
    packaged_table("id: v1\nrules: [\n")
    with pytest.raises(RulesetError, match="not valid YAML"):
        load_ruleset("v1")


def test_a_scalar_yaml_cannot_construct_is_refused_as_invalid_yaml(packaged_table):
    """PyYAML raises ValueError, not YAMLError, building the impossible date 2026-02-30
    (verify-11); that is a damaged table, not a model failure. Since T03 review 30 the value
    stays text, so the refusal names the rule and field it sits in."""
    packaged_table(_v1_text().replace("category: returned_dd", "category: 2026-02-30", 1))
    with pytest.raises(RulesetError, match=r"cma/rules/v1\.yaml: frozen table v1 has been changed.*"
                                           r"\(returned_dd\): category holds a timestamp value '2026-02-30' that YAML cannot build"):
        load_ruleset("v1")


def _v1_text() -> str:
    return resources.files("cma.rules").joinpath("v1.yaml").read_text()


@pytest.mark.parametrize("old, new, key", [
    # A second category inside a rule: safe_load would keep the last one.
    ("    category: revenue_card_settlement\n",
     "    category: revenue_card_settlement\n    category: supplier\n", "category"),
    # A second rules list at the top level would replace the whole table.
    ("frozen: true\n", "frozen: true\nrules: []\n", "rules"),
])
def test_a_repeated_key_in_the_table_is_refused(packaged_table, monkeypatch, old, new, key):
    text = _v1_text()
    assert old in text
    packaged_table(text.replace(old, new, 1))
    # Since T03 review 35 neither value is kept: the key is named, with the table's other
    # faults, after the changed frozen table.
    with pytest.raises(RulesetError, match=rf"cma/rules/v1\.yaml: frozen table v1 has been changed.*"
                                           rf"{key} holds a repeated key value '{key} given twice'"):
        load_ruleset("v1")
    monkeypatch.setenv("CMA_RULESET", "v1")
    line = Transaction(id="T1", date=date(2026, 1, 5), description="CARD SETTLEMENT ACME", amount=120.0, balance=120.0)
    with pytest.raises(RulesetError):
        categorise.categorise_one(line)


def test_a_missing_packaged_table_is_refused_not_raised_as_an_os_error(monkeypatch):
    def missing(ruleset_id):
        raise FileNotFoundError(f"cma/rules/{ruleset_id}.yaml")
    monkeypatch.setattr(categorise, "_table_bytes", missing)
    load_ruleset.cache_clear()
    try:
        with pytest.raises(RulesetError, match=r"cma/rules/v1\.yaml: cannot be read from the installed package"):
            load_ruleset("v1")
    finally:
        load_ruleset.cache_clear()


@pytest.mark.contracts
def test_cma_run_refuses_a_missing_table_rather_than_blaming_the_model(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from cma.cli import app

    def missing(ruleset_id):
        raise FileNotFoundError(ruleset_id)
    monkeypatch.setattr(categorise, "_table_bytes", missing)
    load_ruleset.cache_clear()
    monkeypatch.setenv("CMA_RULESET", "v1")
    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv_path, _ = synth.write(rows, truth, tmp_path)
    try:
        r = CliRunner().invoke(app, ["run", str(csv_path), "--yes", "--reviewer", "A"])
    finally:
        load_ruleset.cache_clear()
    assert r.exit_code == 2, r.output
    assert "Refused." in r.output and "cannot be read" in r.output and "model call failed" not in r.output


def test_the_hash_is_of_the_file_bytes_not_of_normalised_text(packaged_table):
    """The pin is compared with the file's bytes: a CRLF copy with the same rules is a
    different file and is refused, naming both hashes."""
    lf = _v1_text().encode()
    crlf = lf.replace(b"\n", b"\r\n")
    assert load_ruleset("v1").sha256 == hashlib.sha256(lf).hexdigest()
    packaged_table(crlf)
    with pytest.raises(RulesetError, match=hashlib.sha256(crlf).hexdigest()):
        load_ruleset("v1")


@pytest.mark.contracts
def test_v1_hash_is_pinned_in_code():
    assert categorise.FROZEN_SHA256["v1"] == hashlib.sha256(_v1_text().encode()).hexdigest()
    assert load_ruleset("v1").sha256 == categorise.FROZEN_SHA256["v1"]


@pytest.mark.contracts
@pytest.mark.parametrize("old, new", [
    ("category: revenue_card_settlement", "category: other"),  # verify-12: schema-valid, still v1
    # Was `direction: in` -> `any` on loan_drawdown, which since T03 review 50 the schema itself
    # refuses (a money-in-only category on a rule that also matches debits); this edit still
    # passes the schema and changes v1.
    ("direction: any\n", "direction: in\n"),
])
def test_a_schema_valid_change_to_frozen_v1_is_refused(packaged_table, old, new):
    text = _v1_text()
    assert old in text
    changed = text.replace(old, new, 1)
    validate_ruleset(yaml.safe_load(changed), "v1")  # the schema alone accepts it
    packaged_table(changed)
    with pytest.raises(RulesetError, match=r"cma/rules/v1\.yaml: frozen table v1 has been changed"):
        load_ruleset("v1")


@pytest.mark.contracts
def test_switching_v1_frozen_off_is_refused(packaged_table):
    # A table that is not frozen must stage every rule, so v1 with frozen: false fails the
    # schema before the pin is even checked; either way v1 cannot load.
    changed = _v1_text().replace("frozen: true", "frozen: false", 1)
    with pytest.raises(RulesetError, match="missing stage"):
        validate_ruleset(yaml.safe_load(changed), "v1")
    packaged_table(changed)
    with pytest.raises(RulesetError):
        load_ruleset("v1")


@pytest.mark.contracts
def test_cma_run_refuses_a_changed_v1_and_writes_no_evidence(tmp_path, monkeypatch, packaged_table):
    from typer.testing import CliRunner

    from cma.cli import app

    packaged_table(_v1_text().replace("category: revenue_card_settlement", "category: other", 1))
    monkeypatch.setenv("CMA_RULESET", "v1")
    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv_path, _ = synth.write(rows, truth, tmp_path)
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", str(csv_path), "--yes", "--reviewer", "A", "--out", str(out)])
    assert r.exit_code == 2, r.output
    assert "Refused." in r.output and "frozen table v1 has been changed" in r.output
    assert not out.exists() and not (tmp_path / "memo.evidence.json").exists()


@pytest.mark.parametrize("old, new", [
    ("category: returned_dd", "category: [returned_dd]"),
    ("pattern: '^UNPAID DD\\b'", "pattern: 'A{4294967296}'"),
    ("- id: returned_dd", "- id: [returned_dd]"),
    ("direction: any", "direction: {any: 1}"),
])
def test_cma_run_refuses_a_damaged_table_rather_than_blaming_the_model(tmp_path, monkeypatch, packaged_table, old, new):
    from typer.testing import CliRunner

    from cma.cli import app

    text = resources.files("cma.rules").joinpath("v1.yaml").read_text()
    packaged_table(text.replace(old, new, 1))
    monkeypatch.setenv("CMA_RULESET", "v1")
    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv_path, _ = synth.write(rows, truth, tmp_path)
    r = CliRunner().invoke(app, ["run", str(csv_path), "--yes", "--reviewer", "A"])
    assert r.exit_code == 2, r.output
    assert "Refused." in r.output and "rule 1" in r.output and "model call failed" not in r.output


@pytest.mark.contracts
def test_an_unknown_ruleset_is_refused_naming_the_known_ones():
    with pytest.raises(RulesetError, match=r"v9.*v1"):
        load_ruleset("v9")


@pytest.mark.contracts
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


@pytest.mark.contracts
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


@pytest.mark.contracts
def test_a_review_drafted_before_the_ruleset_was_recorded_still_names_v1(tmp_path, monkeypatch):
    """A review left pending in a checkpoint written before T01 has no ruleset in its state.
    Its lines were categorised by the table that is now v1 (P7), so the evidence written on
    resume names v1 and its hash, and says it was inferred, never null. CMA_RULESET at
    resume time does not change it: the categories were fixed when the memo was drafted."""
    from langgraph.types import Command

    from cma.graph import build, evidence_record
    from cma.llm import FakeLLM

    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv_path, _ = synth.write(rows, truth, tmp_path)
    graph = build(FakeLLM())
    cfg = {"configurable": {"thread_id": "legacy"}}
    graph.invoke({"statement_path": str(csv_path)}, cfg)
    graph.invoke(Command(resume={"approved": True, "reviewer": "A. Analyst"}), cfg)
    state = graph.get_state(cfg).values
    recorded = [(t.category, t.rule_id) for t in state["statement"].transactions]
    # A checkpoint from before provenance: no ruleset in the state, no rule id on any line.
    legacy = {k: v for k, v in state.items() if k != "ruleset"}
    legacy["statement"] = state["statement"].model_copy(deep=True)
    for t in legacy["statement"].transactions:
        t.rule_id = None
    monkeypatch.setenv("CMA_RULESET", "v9")
    record = evidence_record(legacy)
    assert record["ruleset"] == {
        "id": "v1", "sha256": load_ruleset("v1").sha256, "inferred": "pre-ruleset checkpoint"}
    assert evidence_record({**legacy, "ruleset": None})["ruleset"]["id"] == "v1"
    # Each line's provenance is inferred from v1 too, and equals what a fresh run records.
    assert [(t.category, t.rule_id) for t in legacy["statement"].transactions] == [(c, None) for c, _ in recorded]
    assert [(line["category"], line["rule_id"]) for line in record["lines"]] == \
        [(c.value, r) for c, r in recorded]
    assert all(r.startswith("v1:") for _, r in recorded)
    # A line whose saved category v1 would not give keeps no rule id rather than a wrong one.
    legacy["statement"].transactions[0].category = Category.LOAN_DRAWDOWN
    assert evidence_record(legacy)["lines"][0]["rule_id"] is None


@pytest.mark.contracts
def test_a_t02_checkpoint_with_a_recorded_v1_ruleset_but_no_line_rule_ids_replays_v1(tmp_path):
    """The release before T03 recorded the ruleset (v1 and its hash) but no rule id on any
    line. On resume each line's rule id is replayed from that recorded table, not left
    null; a recorded hash the installed table does not have replays nothing."""
    from langgraph.types import Command

    from cma.graph import build, evidence_record
    from cma.llm import FakeLLM

    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv_path, _ = synth.write(rows, truth, tmp_path)
    graph = build(FakeLLM())
    cfg = {"configurable": {"thread_id": "t02"}}
    graph.invoke({"statement_path": str(csv_path)}, cfg)
    graph.invoke(Command(resume={"approved": True, "reviewer": "A. Analyst"}), cfg)
    state = graph.get_state(cfg).values
    recorded = [(t.category.value, t.rule_id) for t in state["statement"].transactions]
    assert any(r == "v1:card_settlement" for _, r in recorded)
    t02 = {**state, "ruleset": {"id": "v1", "sha256": load_ruleset("v1").sha256},
           "statement": state["statement"].model_copy(deep=True)}
    for t in t02["statement"].transactions:
        t.rule_id = None
    record = evidence_record(t02)
    assert record["ruleset"] == {"id": "v1", "sha256": load_ruleset("v1").sha256}
    assert [(line["category"], line["rule_id"]) for line in record["lines"]] == recorded
    stale = {**t02, "ruleset": {"id": "v1", "sha256": "0" * 64}}
    assert all(line["rule_id"] is None for line in evidence_record(stale)["lines"])


# --- R10: M16 keeps its id, unit and value, and its name says what it measures ------------


def test_m16_is_named_as_non_trading_money_in(v1):
    path = Path(__file__).parents[1] / "examples" / "cafe_stretched.csv"
    m16 = next(m for m in metrics.compute(categorise.categorise(ingest.load_csv(path))) if m.id == "M16")
    assert m16.name == "Non-trading money in (share)"
    assert m16.unit == "pct"


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


@pytest.mark.dual_run
@pytest.mark.invariants
def test_p7_golden_covers_every_case():
    g = _golden()
    combos = {(c["archetype"], c["profile"]) for c in g["synthetic"]}
    assert combos >= {(a, p) for a in synth.ARCHETYPES for p in synth.PROFILES}
    # Every committed statement file, found by globbing tests/ and examples/, has a baseline.
    root = Path(__file__).parents[1]
    committed = sorted(p.relative_to(root).as_posix() for d in ("tests", "examples") for p in (root / d).rglob("*.csv"))
    assert committed == ["examples/cafe_stretched.csv"]
    assert [e["path"] for e in g["examples"]] == committed
    # 48 statements the pre-change suite wrote, loaded, categorised or computed on; a
    # rebuild that finds fewer has lost one (tests/test_p7_history.py rebuilds it).
    assert len(g["fixtures"]) == 48 and len(g["lines"]) == 13
    # verify-12: the Monzo balance-column conversion writes a statement no test loads; the
    # capture finds it in the suite's temporary files.
    assert [["tx_1", "2026-03-01", "FPS CREDIT CLIENT A", 100.0, 600.0],
            ["tx_2", "2026-03-02", "CARD SHOP", -50.0, 550.0]] in [[line[:5] for line in f["lines"]] for f in g["fixtures"]]
    # The same lines under another VAT stagger are another case (M13 differs); the
    # stagger-3 run of the on-time stagger-1 VAT year is the regression case for that.
    by_lines: dict[str, set] = {}
    for f in g["fixtures"]:
        by_lines.setdefault(json.dumps([line[:5] for line in f["lines"]]), set()).add(f["vat_stagger"])
    assert any({1, 3} <= staggers for staggers in by_lines.values())
    # A whole statement the pre-change tests categorised without computing metrics still
    # gets an M1-M16 baseline (verify-10: the two-credit Monzo invoice fixture in
    # tests/test_import_monzo.py was captured only as single lines).
    descriptions = [{line[2] for line in f["lines"]} for f in g["fixtures"]]
    assert {"FPS CREDIT OAKFIELD PROPERTIES INV1042", "BACS CREDIT ACME LTD INVOICE2210"} in descriptions
    # Statements the pre-change tests only loaded, never categorised or computed on, are
    # in too (verify-11: the newest-first and byte-order-mark fixtures in tests/test_ingest.py).
    rows = [[line[:5] for line in f["lines"]] for f in g["fixtures"]]
    assert [["T1", "2026-01-01", "A", 100.0, 100.0], ["T2", "2026-01-02", "B", 100.0, 200.0],
            ["T3", "2026-01-03", "C", -50.0, 150.0]] in rows
    assert [["T1", "2026-01-01", "A", 100.0, 100.0]] in rows
    assert [["T1", "2026-01-01", "CARD ACQUIRER SETTLEMENT", 100.0, 100.0]] in rows


@pytest.mark.dual_run
@pytest.mark.invariants
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


@pytest.mark.dual_run
@pytest.mark.invariants
@pytest.mark.parametrize("case", _golden()["examples"], ids=lambda c: c["path"])
def test_p7_v1_committed_examples_match_the_baseline(case, v1):
    path = Path(__file__).parents[1] / case["path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == case["file_sha256"]
    s = categorise.categorise(ingest.load_csv(path))
    assert _categories_sha(s) == case["categories_sha256"]
    for key, expected in case["metrics"].items():
        assert _metric_rows(s, _stagger(key)) == expected


@pytest.mark.dual_run
@pytest.mark.invariants
@pytest.mark.parametrize("case", _golden()["fixtures"], ids=lambda c: f"fixture-{len(c['lines'])}-lines-{c['lines'][0][2][:24]}")
def test_p7_v1_test_fixtures_match_the_baseline(case, v1):
    s = Statement(account_name=case["account_name"], transactions=[
        Transaction(id=i, date=date.fromisoformat(d), description=desc, amount=a, balance=b)
        for i, d, desc, a, b, _, _ in case["lines"]
    ])
    s = categorise.categorise(s)
    assert [[t.category.value, t.reverses] for t in s.transactions] == [[c, r] for *_, c, r in case["lines"]]
    assert _metric_rows(s, case["vat_stagger"]) == case["metrics"]


@pytest.mark.dual_run
@pytest.mark.invariants
def test_p7_v1_single_lines_match_the_baseline(v1):
    got = [
        [d, a, categorise.categorise_one(Transaction(id="T", date=date(2026, 1, 1), description=d, amount=a, balance=0.0)).value]
        for d, a, _ in _golden()["lines"]
    ]
    assert got == _golden()["lines"]


# --- Dual run: the table as code before the move vs v1.yaml ----------------------------
# Copied verbatim from src/cma/categorise.py at fd7b64c (the last commit before T01) as an
# independent oracle. Never edit it: it is the "before" side of P7.

_OLD_NON_TRADING_IN = re.compile(
    r"\bDIRECTOR\b|\bOWNER\b|\bCAPITAL\b|\bSHAREHOLDER\b|\bHMRC\b|\bREFUND\b|\bREVERSAL\b"
    r"|\bSAVINGS\b|\bPOT TRANSFER\b|\bINTEREST\b|\bGRANT\b|\bINSURANCE CLAIM\b"
)
_OLD_RULES = [
    (re.compile(r"^UNPAID DD\b"), "any", Category.RETURNED_DD),
    (re.compile(r"UNPAID ITEM FEE|ACCOUNT FEES|BANK CHARGES"), "out", Category.BANK_FEES),
    (re.compile(r"HMRC VAT"), "out", Category.HMRC_VAT),
    (re.compile(r"HMRC PAYE"), "out", Category.HMRC_PAYE),
    (re.compile(r"PAYROLL|SALARIES"), "out", Category.PAYROLL),
    (re.compile(r"\bRENT\b"), "out", Category.RENT),
    (re.compile(r"\bLOAN\b|FINANCE\b"), "out", Category.LOAN_REPAYMENT),
    (re.compile(r"\bLOAN\b|FINANCE\b"), "in", Category.LOAN_DRAWDOWN),
    (re.compile(r"OWNER DRAWINGS|DIRECTOR DRAWINGS|DIVIDEND"), "out", Category.OWNER_DRAWINGS),
    (_OLD_NON_TRADING_IN, "in", Category.OTHER),
    (re.compile(r"ACQUIRER SETTLEMENT|PAYGATE PAYOUT|CARD SETTLEMENT"), "in", Category.REVENUE_CARD),
    (re.compile(r"\bINV(?:OICE)?\s*[-#:]?\s*\d+"), "in", Category.REVENUE_TRANSFER),
]


def _old_categorise_one(description: str, amount: float) -> Category:
    desc = description.upper()
    for pattern, direction, cat in _OLD_RULES:
        ok = True if direction == "any" else (amount > 0 if direction == "in" else amount < 0)
        if pattern.search(desc) and ok:
            return cat
    return Category.SUPPLIER if amount < 0 else Category.OTHER


@pytest.mark.dual_run
def test_dual_run_v1_yaml_is_the_old_rule_list():
    v1 = load_ruleset("v1")
    assert [(r.pattern.pattern, r.direction, r.category) for r in v1.rules] == \
        [(p.pattern, d, c) for p, d, c in _OLD_RULES]


def _descriptions() -> list[str]:
    g = _golden()
    out = {d for d, _, _ in g["lines"]}
    out |= {line[2] for f in g["fixtures"] for line in f["lines"]}
    examples = Path(__file__).parents[1] / "examples" / "cafe_stretched.csv"
    out |= {t.description for t in ingest.load_csv(examples).transactions}
    return sorted(out)


@pytest.mark.dual_run
def test_dual_run_every_known_and_fuzzed_description_categorises_the_same(v1):
    """Every description in the baseline and the example, plus random descriptions built
    from every word the rules mention (and near misses), in both directions and at zero."""
    words = sorted({w for p, _, _ in _OLD_RULES for w in re.findall(r"[A-Z]{2,}", p.pattern)})
    words += ["INV1234", "INVOICE 99", "INV-7", "INV#12", "UNPAID", "DD", "CO", "LTD", "PAYMENT",
              "TFR", "FROM", "TO", "XFINANCE", "LOANS", "RENTAL", "INVOICES", "INV", "#", "-", ":"]
    rng = random.Random(7)
    samples = [(d, a) for d in _descriptions() for a in (-12.5, 0.0, 12.5)]
    for _ in range(20_000):
        d = " ".join(rng.choice(words) for _ in range(rng.randint(1, 5)))
        samples.append((d.lower() if rng.random() < 0.1 else d, rng.choice((-1.0, 0.0, 1.0)) * rng.randint(1, 9999) / 100))
    ruleset = load_ruleset("v1")
    differ = [
        (d, a) for d, a in samples
        if categorise.categorise_one(Transaction(id="T", date=date(2026, 1, 1), description=d, amount=a, balance=0.0), ruleset)
        != _old_categorise_one(d, a)
    ]
    assert not differ, differ[:5]
    assert len(samples) > 20_000


# --- a review drafted before T01, resumed with a damaged table -------------------------


@pytest.fixture
def legacy_pending(tmp_path, monkeypatch):
    """A review waiting for a decision whose state has no ruleset, as a checkpoint written
    before T01 has. `cma resume` is driven through one shared in-memory saver, standing in
    for the --checkpoint-db file (the sqlite extra is optional)."""
    from contextlib import contextmanager

    from cma import cli
    from cma.graph import build, default_checkpointer
    from cma.llm import FakeLLM

    saver = default_checkpointer()

    @contextmanager
    def shared(path):
        yield saver
    monkeypatch.setattr(cli, "_checkpointer", shared)
    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv_path, _ = synth.write(rows, truth, tmp_path)
    graph = build(FakeLLM(), checkpointer=saver)
    cfg = {"configurable": {"thread_id": "legacy"}}
    graph.invoke({"statement_path": str(csv_path)}, cfg)
    graph.update_state(cfg, {"ruleset": None})
    graph.invoke(None, cfg)  # back to the review interrupt, as the stored review would be
    snap = graph.get_state(cfg)
    assert snap.interrupts and not snap.values.get("ruleset")
    db = tmp_path / "reviews.db"
    db.touch()
    return graph, cfg, db


@pytest.mark.contracts
def test_a_legacy_resume_with_a_damaged_table_is_refused_before_anything_is_recorded(
        tmp_path, legacy_pending, packaged_table):
    from typer.testing import CliRunner

    from cma.cli import app

    graph, cfg, db = legacy_pending
    packaged_table(_v1_text().replace("category: returned_dd", "category: [returned_dd]", 1))
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["resume", "legacy", "--checkpoint-db", str(db), "--reviewer", "C", "--out", str(out)],
                           input="y\n\n")
    assert r.exit_code == 2, r.output
    assert "Refused, nothing recorded." in r.output and "rule 1" in r.output
    assert not out.exists() and not (tmp_path / "memo.evidence.json").exists()
    snap = graph.get_state(cfg)
    assert snap.interrupts and snap.values.get("review") is None, "the review must still be pending"

    # With the table repaired the same review resumes and its evidence names v1, inferred.
    packaged_table(_v1_text())
    r = CliRunner().invoke(app, ["resume", "legacy", "--checkpoint-db", str(db), "--reviewer", "C", "--out", str(out)],
                           input="y\n\n")
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    assert record["ruleset"] == {"id": "v1", "sha256": load_ruleset("v1").sha256, "inferred": "pre-ruleset checkpoint"}
    assert "APPROVED by C" in out.read_text()


@pytest.mark.contracts
def test_rule_tables_keep_lf_line_endings_on_every_checkout():
    """The v1 pin hashes the file's bytes, so a CRLF checkout (Windows, core.autocrlf=true) must not
    rewrite it: .gitattributes fixes eol=lf for the rule tables, and the shipped file has no CR."""
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    table = root / "src" / "cma" / "rules" / "v1.yaml"
    assert b"\r" not in table.read_bytes()
    out = subprocess.run(
        ["git", "-C", str(root), "check-attr", "eol", "--", "src/cma/rules/v1.yaml"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    assert out.stdout.strip().endswith("eol: lf"), out.stdout


def test_m16_is_renamed_from_the_previous_releases_name():
    # R10: M16's name says it measures money in that is not trading receipts. The previous
    # release, the source the dual run compares against (tests/previous_release.py BASE),
    # called it "Unclassified inflows"; T01 renamed it. T04 review 43 read a branch that
    # already holds T01 as the previous release; this pins the release the spec means.
    import subprocess

    from tests.previous_release import BASE, REPO

    old = subprocess.run(["git", "-C", str(REPO), "show", f"{BASE}:src/cma/metrics.py"],
                         check=True, capture_output=True, text=True).stdout
    assert 'id="M16", name="Unclassified inflows"' in old
    path = Path(__file__).parents[1] / "examples" / "cafe_stretched.csv"
    m16 = next(m for m in metrics.compute(categorise.categorise(ingest.load_csv(path))) if m.id == "M16")
    assert m16.name != "Unclassified inflows" and "non-trading" in m16.name.lower()
