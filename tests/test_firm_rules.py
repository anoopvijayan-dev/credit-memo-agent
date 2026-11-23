"""firm_rules.yaml: its format and validation, its place in the categoriser's precedence, and
the case pipeline that applies it (R21, P16; design C1 precedence step 6).

A firm rule names a counterparty, a direction and a category, with who promoted it, when and
why (R20). It is applied below a protected verdict, an ambiguous verdict and a case-profile
name, and above the table's unprotected rules. A line that a protected built-in pattern
matches (director, capital, shareholder, lender, HMRC, returned payment and the other
protected verdicts) never takes a firm rule's category (R21)."""

import re
from importlib import resources
from pathlib import Path

import pytest
import yaml

from cma import firm_rules, overrides, store
from cma.categorise import (
    ProfileNames,
    Rule,
    Ruleset,
    _v1_protected,
    classify,
    load_ruleset,
    rule_matches,
)
from cma.models import CREDIT_BUCKET, Category, Transaction

C = Category


def _t(desc: str, amount: float, tid: str = "T1") -> Transaction:
    from datetime import date

    return Transaction(id=tid, date=date(2026, 1, 5), description=desc, amount=amount, balance=1000.0)


def _rule(rid="acme_trust", pattern="ACME TRUST", direction="in", category="other_explained_in", **extra):
    r = {"id": rid, "pattern": pattern, "direction": direction, "category": category,
         "by": "A Underwriter", "at": "2026-11-20T10:00:00Z", "reason": "trust distributions"}
    r.update(extra)
    return r


def _text(*rules) -> str:
    return yaml.safe_dump({"rules": list(rules)}, sort_keys=False)


# --- format and validation --------------------------------------------------------------


@pytest.mark.contracts
def test_a_valid_file_loads_each_rule_with_its_record():
    got = firm_rules.parse(_text(_rule(), _rule("s", "SPARES LTD", "out", "supplier")), "f.yaml")
    assert [r.id for r in got.rules] == ["acme_trust", "s"]
    assert got.rules[0].direction == "in" and got.rules[0].category == C.OTHER_EXPLAINED_IN
    assert got.records[0]["by"] == "A Underwriter" and got.records[0]["reason"] == "trust distributions"
    assert len(got.sha256) == 64


@pytest.mark.contracts
def test_an_empty_or_rule_free_file_has_no_rules():
    assert firm_rules.parse("rules: []\n", "f.yaml").rules == ()
    assert firm_rules.parse("", "f.yaml").rules == ()


@pytest.mark.contracts
@pytest.mark.parametrize("text, problem", [
    ("rules: [\n", "not valid YAML"),
    ("- a\n", "a mapping"),
    ("rules: []\nother: 1\n", "unknown key 'other'"),
    ("rules: []\nrules: []\n", "rules is given more than once"),
    ("rules: x\n", "rules must be a list"),
    ("rules:\n  - x\n", "must be a mapping"),
    (_text(_rule(colour="red")), "'colour'"),
    (_text({k: v for k, v in _rule().items() if k != "reason"}), "missing reason"),
    (_text(_rule(rid="a+b")), "id"),
    (_text(_rule(rid="conflict")), "reserved"),
    (_text(_rule(), _rule()), "acme_trust is given more than once"),
    (_text(_rule(pattern="---")), "letter or digit"),
    (_text(_rule(pattern=["ACME"])), "pattern must be text"),
    (_text(_rule(direction="sideways")), "direction"),
    (_text(_rule(category="rocket")), "unknown category"),
    (_text(_rule(direction="out", category="revenue_transfer")), "for money in"),
    (_text(_rule(direction="any", category="hmrc_repayment")), "for money in"),
    (_text(_rule(direction="in", category="payroll")), "for money out"),
    (_text(_rule(reason="")), "reason"),
])
def test_a_malformed_file_is_refused_naming_its_line(text, problem):
    with pytest.raises(firm_rules.FirmRulesError) as e:
        firm_rules.parse(text, "f.yaml")
    assert problem in str(e.value)
    assert re.search(r"^f\.yaml: line \d+: ", str(e.value))


@pytest.mark.contracts
def test_a_refusal_never_quotes_the_rule_pattern_beyond_its_line():
    with pytest.raises(firm_rules.FirmRulesError) as e:
        firm_rules.parse(_text(_rule(rid="x y")), "f.yaml")
    assert "line 2" in str(e.value)


@pytest.mark.contracts
def test_a_firm_rule_matches_whole_words_whatever_separators(v2):
    rule = firm_rules.parse(_text(_rule(pattern="ACME TRUST")), "f").rules[0]
    assert classify(_t("FPS CREDIT ACME-TRUST REF 9", 50.0), v2, firm_rules=[rule]) == \
        (C.OTHER_EXPLAINED_IN, "firm:acme_trust")
    assert classify(_t("FPS CREDIT ACMETRUSTEES", 50.0), v2, firm_rules=[rule])[1] != "firm:acme_trust"


# --- loading for a case -----------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    monkeypatch.delenv("CMA_RULESET", raising=False)
    return root


@pytest.fixture
def v2():
    return load_ruleset("v2")


@pytest.mark.contracts
def test_no_file_means_no_firm_rules(home):
    assert firm_rules.for_case().rules == ()


@pytest.mark.contracts
def test_a_file_firm_yaml_names_must_exist(home):
    home.mkdir(parents=True)
    (home / "firm.yaml").write_text("paths:\n  firm_rules: mine.yaml\n")
    with pytest.raises(firm_rules.FirmRulesError, match="no such file"):
        firm_rules.for_case()


@pytest.mark.contracts
def test_a_firm_rules_error_is_a_ruleset_error_so_every_caller_refuses_it():
    from cma.categorise import RulesetError

    assert issubclass(firm_rules.FirmRulesError, RulesetError)


# --- the case pipeline ------------------------------------------------------------------


BODY = ("id,date,description,amount,balance\n"
        "L1,2026-01-05,FPS CREDIT ACME TRUST,500.00,1500.00\n"
        "L2,2026-01-06,HMRC VAT REPAYMENT ACME TRUST,300.00,1800.00\n"
        "L3,2026-01-07,FPS CREDIT DIRECTOR ACME TRUST,200.00,2000.00\n"
        "L4,2026-01-08,UNPAID DD ACME TRUST,100.00,2100.00\n"
        "L5,2026-01-09,HMRC PAYE ACME TRUST,-100.00,2000.00\n"
        "L6,2026-01-10,IWOCA ACME TRUST,-50.00,1950.00\n"
        "L7,2026-01-11,DD ACME TRUST,-25.00,1925.00\n")


def _case(tmp_path) -> Path:
    folder = store.case_folder(store.new_case(label="t36"))
    src = tmp_path / "statement.csv"
    src.write_text(BODY)
    store.import_file(folder, src, "csv")
    return folder


def _lines(folder) -> dict[str, tuple[str, str]]:
    view = overrides.case_figures(folder)
    return {t.id: (t.category.value, t.rule_id) for f in view.values() for t in f.statement.transactions}


@pytest.mark.invariants
def test_p16_the_case_pipeline_applies_firm_rules_but_never_to_protected_lines(home, tmp_path):
    folder = _case(tmp_path)
    before = _lines(folder)
    assert before["L1"] == ("other", "v2:default")
    (home / "firm_rules.yaml").write_text(_text(
        _rule("trust_in", "ACME TRUST", "in", "revenue_transfer"),
        _rule("trust_out", "ACME TRUST", "out", "supplier")))
    after = _lines(folder)
    assert after["L1"] == ("revenue_transfer", "firm:trust_in")  # applied to the case (R20)
    assert after["L7"] == ("supplier", "firm:trust_out")  # a debit too, where nothing protects it
    for line in ("L2", "L3", "L4", "L5", "L6"):  # HMRC, director, returned payment, lender (R21)
        assert after[line] == before[line], line
        assert not after[line][1].startswith("firm:"), line


@pytest.mark.invariants
def test_p16_an_override_still_changes_a_protected_line(home, tmp_path):
    folder = _case(tmp_path)
    (home / "firm_rules.yaml").write_text(_text(_rule("trust_in", "ACME TRUST", "in", "revenue_transfer")))
    assert _lines(folder)["L2"] == ("hmrc_repayment", "v2:hmrc_repayment")
    overrides.add_override(folder, "L2", "revenue_transfer", reason="checked the remittance", name="Pat")
    assert _lines(folder)["L2"] == ("revenue_transfer", "override:1")  # only a per-line override (R21)


@pytest.mark.contracts
def test_a_malformed_firm_rules_file_stops_the_case_figures(home, tmp_path):
    folder = _case(tmp_path)
    (home / "firm_rules.yaml").write_text("rules:\n  - id: x\n")
    with pytest.raises(firm_rules.FirmRulesError, match="line 2"):
        overrides.case_figures(folder)


@pytest.mark.contracts
def test_editing_the_firm_rules_file_recategorises_the_case(home, tmp_path):
    folder = _case(tmp_path)
    (home / "firm_rules.yaml").write_text(_text(_rule("t", "ACME TRUST", "in", "revenue_transfer")))
    assert _lines(folder)["L1"] == ("revenue_transfer", "firm:t")
    (home / "firm_rules.yaml").write_text(_text(_rule("t", "ACME TRUST", "in", "other_explained_in")))
    assert _lines(folder)["L1"] == ("other_explained_in", "firm:t")
    (home / "firm_rules.yaml").unlink()
    assert _lines(folder)["L1"] == ("other", "v2:default")


# --- P16, the property ------------------------------------------------------------------


def _protected(ruleset: Ruleset, desc: str, amount: float) -> bool:
    """Whether a protected built-in pattern matches the line (R21's patterns and the cues)."""
    if ruleset.frozen:
        return any(_v1_protected(r, desc, amount) for r in ruleset.rules)
    return any(r.protected and r.category is not None and rule_matches(r, desc, amount) for r in ruleset.rules)


def _listed_names(table: str) -> list[str]:
    data = yaml.safe_load(resources.files("cma.rules").joinpath(f"{table}.yaml").read_bytes())
    return sorted({n for r in data["rules"] for n in r.get("names", ())})


WORDING = ["HMRC VAT", "HMRC REFUND", "UNPAID DD", "RETURNED DD", "DIRECTOR LOAN", "DIRECTOR", "SHAREHOLDER",
           "CAPITAL INTRODUCED", "CAPITAL INJECTED", "IWOCA", "FUNDING CIRCLE", "LOAN", "FINANCE", "REFUND",
           "REVERSAL", "TO SAVINGS", "FROM POT", "POT TRANSFER", "OWNER DRAWINGS", "DIVIDEND", "HMRC PAYE",
           "STRIPE CAPITAL", "SUMUP CASH ADVANCE"]


def _firm_rules_for(word: str) -> list[Rule]:
    """A firm rules file that claims `word` for every category each direction can take."""
    text = _text(*(
        _rule(f"r{i}", word, direction, cat.value)
        for i, (direction, cat) in enumerate(
            [("in", c) for c in CREDIT_BUCKET] + [("out", c) for c in Category if c not in CREDIT_BUCKET]
            + [("any", C.OWN_ACCOUNT_TRANSFER), ("any", C.OTHER)])))
    return list(firm_rules.parse(text, "p16").rules)


@pytest.mark.invariants
@pytest.mark.parametrize("table", ["v1", "v2"])
def test_p16_no_protected_line_changes_category_through_any_firm_rule(table):
    """For any firm rules file (here: every category in every direction, first in the file),
    no line a protected built-in pattern matches takes a firm rule's category or its rule,
    with or without a case profile, in either direction. Positive control: unprotected lines
    the same rules match do take them, so the guard is not a rule that never applies."""
    ruleset = load_ruleset(table)
    words = sorted(set(WORDING) | set(_listed_names("v2")))
    profiles = (None, ProfileNames(directors=("Jane Doe",), own_accounts=("ACME HOLDINGS",)))
    checked = applied = 0
    for word in words:
        firm = _firm_rules_for(word)
        for amount in (75.0, -75.0):
            for desc in (word, f"FPS CREDIT {word} REF 42", f"{word} JANE DOE", f"CARD {word} LTD"):
                for profile in profiles:
                    t = _t(desc, amount)
                    folded = desc.upper()
                    with_firm = classify(t, ruleset, profile=profile, firm_rules=firm)
                    if _protected(ruleset, folded, amount):
                        checked += 1
                        assert with_firm == classify(t, ruleset, profile=profile), (table, desc, amount)
                        assert not with_firm[1].startswith("firm:"), (table, desc, amount)
                    elif with_firm[1].startswith("firm:"):
                        applied += 1
    assert checked > 200 and applied > 50, (checked, applied)


@pytest.mark.contracts
def test_cli_refuses_a_malformed_firm_rules_file_naming_its_line_without_a_traceback(home, tmp_path):
    from typer.testing import CliRunner

    from cma.cli import app

    folder = _case(tmp_path)
    (home / "firm_rules.yaml").write_text("rules:\n  - id: x\n")
    got = CliRunner().invoke(app, ["case", "figures", folder.name], terminal_width=400)
    assert got.exit_code == 2, got.output
    assert "Refused." in got.output and "line 2" in got.output and "missing pattern" in got.output
    assert "Traceback" not in got.output
    assert got.exception is None or isinstance(got.exception, SystemExit)


# One line of each kind R21 names, with the category the built-in table gives it, written out
# here rather than read from the table's protected flags, so the guard is checked against R21's
# own list and not against itself.
R21_LINES = [
    ("FPS CREDIT DIRECTOR LOAN J DOE", 400.0, Category.DIRECTOR_FUNDING),      # director
    ("CAPITAL INTRODUCED J DOE", 400.0, Category.DIRECTOR_FUNDING),            # capital
    ("SHAREHOLDER LOAN J DOE", 400.0, None),                                   # shareholder
    ("IWOCA LOAN ADVANCE", 400.0, Category.LOAN_DRAWDOWN),                     # lender, money in
    ("FUNDING CIRCLE", -400.0, Category.LOAN_REPAYMENT),                       # lender, money out
    ("HMRC VAT REPAYMENT", 400.0, Category.HMRC_REPAYMENT),                    # HMRC, money in
    ("HMRC PAYE", -400.0, Category.HMRC_PAYE),                                 # HMRC, money out
    ("UNPAID DD", 400.0, Category.RETURNED_DD),                                # returned payment
]


@pytest.mark.invariants
@pytest.mark.parametrize("table", ["v1", "v2"])
@pytest.mark.parametrize("desc,amount,category", R21_LINES)
def test_r21_each_named_kind_keeps_its_category_under_a_firm_rule_claiming_it(table, desc, amount, category):
    ruleset = load_ruleset(table)
    t = _t(desc, amount)
    bare = classify(t, ruleset)
    if table == "v2" and category is not None:
        assert bare[0] == category, bare  # the line is the kind it is meant to be
    if bare[1].endswith(":default"):
        # v1, the previous release's table, has no pattern for some of these (FUNDING CIRCLE
        # out): no built-in pattern matches, so R21 protects nothing; v2 has one for each.
        assert table == "v1", bare
        return
    claim = Category.REVENUE_TRANSFER if amount > 0 else Category.SUPPLIER
    rules = firm_rules.parse(_text(_rule("claim", desc, "in" if amount > 0 else "out", claim.value)), "r21").rules
    assert classify(t, ruleset, firm_rules=list(rules)) == bare, (table, desc)


@pytest.mark.contracts
@pytest.mark.parametrize("wording", ["CAPITAL INJECTION", "CAPITAL CONTRIBUTION", "CAPITAL INJECTED"])
def test_v1_protects_capital_wording_in_both_forms_from_a_profile_name(wording):
    # Antigravity re-review of T05: under v1, CAPITAL INJECTION (v2's own wording) was not
    # protected, so an own account named in the profile moved a capital injection out of it.
    from cma.categorise import ProfileNames

    t = _t(f"FPS CREDIT {wording} ACME HOLDINGS", 500.0)
    got = classify(t, load_ruleset("v1"), profile=ProfileNames(own_accounts=("ACME HOLDINGS",)))
    assert got[1] != "profile:own_account", got
