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
    """Replace the packaged v1.yaml text, as a damaged install would."""
    def use(text: str):
        monkeypatch.setattr(categorise, "_table_text", lambda ruleset_id: text)
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


@pytest.mark.parametrize("old, new", [
    ("category: returned_dd", "category: [returned_dd]"),
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
    legacy = {k: v for k, v in graph.get_state(cfg).values.items() if k != "ruleset"}
    monkeypatch.setenv("CMA_RULESET", "v9")
    assert evidence_record(legacy)["ruleset"] == {
        "id": "v1", "sha256": load_ruleset("v1").sha256, "inferred": "pre-ruleset checkpoint"}
    assert evidence_record({**legacy, "ruleset": None})["ruleset"]["id"] == "v1"


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
def test_p7_golden_covers_every_case():
    g = _golden()
    combos = {(c["archetype"], c["profile"]) for c in g["synthetic"]}
    assert combos >= {(a, p) for a in synth.ARCHETYPES for p in synth.PROFILES}
    assert [e["path"] for e in g["examples"]] == ["examples/cafe_stretched.csv"]
    assert len(g["fixtures"]) >= 37 and g["lines"]
    # The same lines under another VAT stagger are another case (M13 differs); the
    # stagger-3 run of the on-time stagger-1 VAT year is the regression case for that.
    by_lines: dict[str, set] = {}
    for f in g["fixtures"]:
        by_lines.setdefault(json.dumps([line[:5] for line in f["lines"]]), set()).add(f["vat_stagger"])
    assert any({1, 3} <= staggers for staggers in by_lines.values())


@pytest.mark.dual_run
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
@pytest.mark.parametrize("case", _golden()["examples"], ids=lambda c: c["path"])
def test_p7_v1_committed_examples_match_the_baseline(case, v1):
    path = Path(__file__).parents[1] / case["path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == case["file_sha256"]
    s = categorise.categorise(ingest.load_csv(path))
    assert _categories_sha(s) == case["categories_sha256"]
    for key, expected in case["metrics"].items():
        assert _metric_rows(s, _stagger(key)) == expected


@pytest.mark.dual_run
@pytest.mark.parametrize("case", _golden()["fixtures"], ids=lambda c: f"fixture-{len(c['lines'])}-lines")
def test_p7_v1_test_fixtures_match_the_baseline(case, v1):
    s = Statement(account_name=case["account_name"], transactions=[
        Transaction(id=i, date=date.fromisoformat(d), description=desc, amount=a, balance=b)
        for i, d, desc, a, b, _, _ in case["lines"]
    ])
    s = categorise.categorise(s)
    assert [[t.category.value, t.reverses] for t in s.transactions] == [[c, r] for *_, c, r in case["lines"]]
    assert _metric_rows(s, case["vat_stagger"]) == case["metrics"]


@pytest.mark.dual_run
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
