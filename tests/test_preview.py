"""T24: `cma case preview` (C6; R22, R24, R25, R26, R27, R28, R48, R56, P14, P20).

The preview builds the pack's memo-ready text from the memo field map and the figures layer,
the policy-check section while show_policy_verdict is on, the demo stamp, the grounding guard's
verdict over the text and the snapshot hash over every input R22 names, and writes nothing but
a preview entry in the case log."""

import hashlib
import re
from datetime import date

import pytest
from typer.testing import CliRunner

import cma
from cma import case as caselog
from cma import guard, overrides, pack, policy, store, synth
from cma.cli import app

STATEMENT = ("id,date,description,amount,balance\n"
             "L1,2026-01-05,CARD SETTLEMENT STRIPE,500.60,1500.60\n"
             "L2,2026-01-06,FPS CREDIT J BLOGGS,300.25,1800.85\n"
             "L3,2026-01-07,IWOCA LOAN REPAYMENT,-100.00,1700.85\n"
             "L4,2026-02-02,CASH PAID IN,250.10,1950.95\n"
             "L5,2026-02-20,FPS CREDIT JANE DOE,1000.00,2950.95\n")
OTHER = ("id,date,description,amount,balance\n"
         "S1,2026-01-03,CARD SETTLEMENT SUMUP,900.00,900.00\n"
         "S2,2026-01-20,IWOCA LOAN REPAYMENT,-120.00,780.00\n")
OUTCOME_WORD = re.compile(r"\b(pass|refer|fail)\b", re.IGNORECASE)
REPLY_FIELDS = ["business_details", "loan_request", "trading_history", "monthly_turnover", "balances",
                "existing_commitments", "returned_direct_debits", "vat_and_hmrc",
                "director_loans_and_transfers", "credit_history", "affordability", "key_risks", "recommendation"]
END = date(2026, 10, 31)


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    monkeypatch.setenv("CMA_RULESET", "v2")
    return root


def _case(tmp_path, accounts=(("main", STATEMENT),), demo=False):
    case_id = store.new_case(label="t24", demo=demo)
    folder = store.case_folder(case_id)
    for account, body in accounts:
        src = tmp_path / f"{account}.csv"
        src.write_text(body, encoding="utf-8")
        store.import_file(folder, src, "csv", account=account)
    return case_id, folder


def _sections(text: str) -> dict[str, str]:
    """Each ## section of the text, by heading, with its body."""
    out, title = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            title = line[3:]
            out[title] = ""
        elif title is not None:
            out[title] += line + "\n"
    return out


# --- the memo field map ------------------------------------------------------------------


@pytest.mark.contracts
def test_packaged_field_map_is_the_replys_13_fields_with_m18_next_to_m2():
    fm = pack.parse_field_map(pack.default_field_map_text(), pack.DEFAULT_FIELD_MAP_SOURCE)
    assert [f.id for f in fm.fields] == REPLY_FIELDS
    turnover = next(f for f in fm.fields if f.id == "monthly_turnover")
    assert turnover.figures[:2] == ("M2", "M18")
    assert re.search(r"\{M2\}[^{]*\{M18\}", turnover.template)  # M18 follows M2, nothing between
    unmapped = {f.id for f in fm.fields if f.for_underwriter}
    assert unmapped == {"business_details", "loan_request", "credit_history", "key_risks", "recommendation"}


@pytest.mark.contracts
@pytest.mark.parametrize("text, why", [
    ("fields:\n  - id: a\n    title: A\n    figures: [M99]\n    template: '{M99}'\n", "not a figure id"),
    ("fields:\n  - id: a\n    title: A\n    figures: [M2]\n    template: '{M3}'\n", "placeholder {M3}"),
    ("fields:\n  - id: a\n    title: A\n    figures: [M2, M3]\n    template: '{M2}'\n", "M3 is listed"),
    ("fields:\n  - id: a\n    title: A\n    figures: [M2]\n", "need a template"),
    ("fields:\n  - id: a\n    title: A\n    template: 'x'\n", "needs the figures"),
    ("fields:\n  - id: recommendation\n    title: R\n    figures: [M2]\n    template: '{M2}'\n", "judgement field"),
    ("fields:\n  - id: a\n    title: A\n  - id: a\n    title: B\n", "more than once"),
    ("fields:\n  - id: a\n    title: A\n    title: B\n", "more than once"),
    ("fields:\n  - id: a\n    title: A\n    figures: [M2]\n    template: '{M2:.2f}'\n", "takes no format"),
    ("fields: []\n", "no fields"),
    ("fields:\n  - id: a\n    title: A\n    colour: red\n", "unknown key"),
    ("fields: [\n", "not valid YAML"),
])
def test_malformed_field_map_is_refused_naming_the_problem(text, why):
    with pytest.raises(pack.FieldMapError, match=re.escape(why)):
        pack.parse_field_map(text, "memo_fields.yaml")


@pytest.mark.contracts
@pytest.mark.parametrize("placeholder", ["{M2.__class__}", "{M2[0]}", "{0}", "{}", "{M2!r}"])
def test_field_map_template_cannot_reach_attributes_indexes_or_positions(placeholder):
    """A firm's template is data: str.format must never get a field name that walks an
    attribute or an index, or a positional slot, so a template cannot read anything but the
    figure it names (security: input validated at the boundary)."""
    text = f"fields:\n  - id: a\n    title: A\n    figures: [M2]\n    template: '{placeholder}'\n"
    with pytest.raises(pack.FieldMapError):
        pack.parse_field_map(text, "memo_fields.yaml")


def test_firm_field_map_orders_the_sections(home, tmp_path):
    case_id, folder = _case(tmp_path)
    (home / "memo_fields.yaml").write_text(
        "fields:\n"
        "  - id: affordability\n    title: Can they pay\n    figures: [M10]\n    template: 'Cover is {M10}.'\n"
        "  - id: notes\n    title: Notes\n", encoding="utf-8")
    p = pack.build_preview(folder, case_id)
    heads = [line[3:] for line in p.text.splitlines() if line.startswith("## ")]
    assert heads == ["Can they pay", "Notes", "Policy check (illustrative)"]
    assert p.inputs["memo_field_map_sha256"] == hashlib.sha256((home / "memo_fields.yaml").read_bytes()).hexdigest()
    assert p.fields[1] == {"id": "notes", "title": "Notes", "figures": [], "for_underwriter": True, "text": {}}


def test_firm_names_a_field_map_that_is_not_there(home, tmp_path):
    case_id, folder = _case(tmp_path)
    (home / "firm.yaml").write_text("paths:\n  memo_fields: mine.yaml\n", encoding="utf-8")
    entries = len(caselog.for_case(folder).entries())
    with pytest.raises(pack.FieldMapError, match="no such file"):
        pack.build_preview(folder, case_id)
    assert len(caselog.for_case(folder).entries()) == entries  # nothing recorded


# --- R27: unmapped fields, R48: the demo stamp --------------------------------------------


def test_r27_unmapped_fields_are_marked_for_the_underwriter_and_left_empty(home, tmp_path):
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)
    sections = _sections(p.text)
    for f in p.fields:
        if f["for_underwriter"]:
            assert sections[f["title"]].strip() == f"_{pack.FOR_UNDERWRITER}_"
            assert f["text"] == {}
        else:
            assert f["text"]["main"] and "[M" in sections[f["title"]]
    # M18 sits right after M2 in the turnover field, each followed by its id
    assert re.search(r"£[\d,]+ \[M2\], including cash deposits of £[\d,]+ \[M18\]", sections["Monthly turnover and credits"])


def test_r48_demo_case_is_stamped(home, tmp_path):
    case_id, folder = _case(tmp_path, demo=True)
    assert pack.DEMO_STAMP in pack.build_preview(folder, case_id).text
    case_id, folder = _case(tmp_path)
    assert pack.DEMO_STAMP not in pack.build_preview(folder, case_id).text


# --- P14: the text passes the grounding guard ----------------------------------------------


def _assert_grounded(p):
    assert p.grounded, p.findings()
    for a in p.accounts:
        # the account's whole text, as the pack holds it, against its figures and policy result
        g = guard.check(pack._text(p, a.account), a.metrics, a.policy)
        assert g.unmatched == [] and g.missing_citations == []
        assert g.numbers_checked > 0


@pytest.mark.invariants
@pytest.mark.parametrize("archetype", list(synth.ARCHETYPES))
@pytest.mark.parametrize("shown", [True, False])
def test_p14_preview_text_passes_the_guard_on_synthetic_cases(home, tmp_path, archetype, shown):
    home.mkdir(parents=True, exist_ok=True)
    (home / "firm.yaml").write_text(f"show_policy_verdict: {str(shown).lower()}\n", encoding="utf-8")
    for profile in synth.PROFILES:
        rows, truth = synth.generate(archetype, profile, seed=11, months=12, end=END, features=synth.FEATURES)
        out = tmp_path / f"{archetype}-{profile}"
        out.mkdir()
        csv_path, _ = synth.write(rows, truth, out, stem="case")
        case_id = store.new_case(label=f"{archetype}-{profile}")
        folder = store.case_folder(case_id)
        store.import_file(folder, csv_path, "csv")
        for a in truth.accounts:  # the second account, with two_accounts
            store.import_file(folder, out / f"case_{a['key']}.csv", "csv", account=a["key"])
        store.update_profile(folder, lambda cur, t=truth: {
            **cur, "directors": list(t.case_profile.get("directors", [])),
            "own_accounts": list(t.case_profile.get("own_accounts", [])), "proposed_monthly_repayment": "1250.50"})
        _assert_grounded(pack.build_preview(folder, case_id))


@pytest.mark.invariants
def test_p14_preview_text_passes_the_guard_with_overrides_negatives_and_missing_figures(home, tmp_path):
    overdrawn = ("id,date,description,amount,balance\n"
                 "D1,2026-03-01,CARD SETTLEMENT STRIPE,10.40,-50.00\n"
                 "D2,2026-03-02,DIRECT DEBIT HMRC VAT,-2000.49,-2050.49\n"
                 "D3,2026-03-09,FPS CREDIT ACME,0.60,-2049.89\n")
    case_id, folder = _case(tmp_path, accounts=(("main", STATEMENT), ("spare", overdrawn)), demo=True)
    overrides.add_override(folder, "L2", "revenue_transfer", reason="known customer", name="Ann")
    store.update_profile(folder, lambda cur: {**cur, "directors": ["Jane Doe"]})
    p = pack.build_preview(folder, case_id)
    _assert_grounded(p)
    assert "Account spare: " in p.text and "Account main: " in p.text  # several accounts, each named
    assert "n/a [M4]" in p.text  # a figure that cannot be computed, still with its id


@pytest.mark.invariants
def test_p14_a_literal_number_in_a_firm_template_fails_the_guard(home, tmp_path):
    case_id, folder = _case(tmp_path)
    (home / "memo_fields.yaml").write_text(
        "fields:\n  - id: a\n    title: A\n    figures: [M2]\n    template: 'Revenue {M2} over 12 months.'\n",
        encoding="utf-8")
    p = pack.build_preview(folder, case_id)
    assert not p.grounded
    assert any(number == "12" for _a, number, _why in p.findings())
    entry = caselog.for_case(folder).entries()[-1]
    assert entry.event == "preview" and entry.data["grounded"] is False


# --- P20: the policy check in the text ---------------------------------------------------


@pytest.mark.invariants
@pytest.mark.parametrize("accounts", [(("main", STATEMENT),), (("main", STATEMENT), ("other", OTHER))])
def test_p20_preview_text_with_show_policy_verdict_on(home, tmp_path, accounts):
    case_id, folder = _case(tmp_path, accounts)
    p = pack.build_preview(folder, case_id)
    sections = _sections(p.text)
    title = pack.POLICY_TITLE
    assert list(sections)[-1] == title  # after the memo fields, and no field maps to it
    assert sections[title].strip().splitlines()[0] == policy.ILLUSTRATIVE_LABEL  # the label first
    for heading, body in sections.items():
        if OUTCOME_WORD.search(body):  # every section that shows an outcome holds the label
            assert policy.ILLUSTRATIVE_LABEL in body, heading
    assert sections["Recommendation"].strip() == f"_{pack.FOR_UNDERWRITER}_"  # blank (R24)
    for a in p.accounts:
        assert f"Overall outcome: {a.policy.overall.value}." in sections[title]
        for r in a.policy.rules:
            assert f"- {r.rule_id} ({r.metric_id} " in sections[title]
    assert re.search(r"at least 1\.25x \[R2\]", p.text)  # a threshold after a limit word
    assert p.inputs["show_policy_verdict"] is True


@pytest.mark.invariants
def test_p20_preview_text_with_show_policy_verdict_off(home, tmp_path):
    home.mkdir(parents=True, exist_ok=True)
    (home / "firm.yaml").write_text("show_policy_verdict: false\n", encoding="utf-8")
    case_id, folder = _case(tmp_path, (("main", STATEMENT), ("other", OTHER)))
    p = pack.build_preview(folder, case_id)
    assert not OUTCOME_WORD.search(p.text)
    assert pack.POLICY_TITLE not in p.text and "[R" not in p.text
    assert all(a.policy is None and not a.policy_lines for a in p.accounts)
    assert _sections(p.text)["Recommendation"].strip() == f"_{pack.FOR_UNDERWRITER}_"
    assert caselog.for_case(folder).entries()[-1].data["policy_check"] == "hidden"


# --- the snapshot ----------------------------------------------------------------------


def _snapshot(folder, case_id):
    return pack.build_preview(folder, case_id).snapshot


@pytest.mark.invariants
def test_snapshot_changes_with_each_input_r22_names(home, tmp_path, monkeypatch):
    case_id, folder = _case(tmp_path)
    base = pack.build_preview(folder, case_id)
    assert set(base.inputs) == {"statements", "ruleset", "firm_rules_sha256", "profile_sha256", "override_log",
                                "thresholds_sha256", "memo_field_map_sha256", "policy_sha256",
                                "show_policy_verdict", "package_version"}
    assert _snapshot(folder, case_id) == base.snapshot  # nothing changed, same hash
    seen = {base.snapshot}

    def changed(what):
        h = _snapshot(folder, case_id)
        assert h not in seen, f"{what} did not change the snapshot"
        seen.add(h)

    # statement hashes: another account, then a corrected statement for it
    src = tmp_path / "o.csv"
    src.write_text(OTHER, encoding="utf-8")
    store.import_file(folder, src, "csv", account="other")
    changed("a new statement")
    # a statement whose bytes changed under the case (one account cannot be imported twice)
    stmt = folder / store.STATEMENTS / "other.csv"
    stmt.write_text(OTHER.replace("900.00,900.00", "900.01,900.01").replace("780.00", "780.01"), encoding="utf-8")
    changed("a changed statement")
    # the ruleset
    monkeypatch.setenv("CMA_RULESET", "v1")
    changed("the ruleset")
    monkeypatch.setenv("CMA_RULESET", "v2")
    seen.discard(_snapshot(folder, case_id))  # back where it was
    # the firm rules file
    (home / "firm_rules.yaml").write_text("rules: []\n", encoding="utf-8")
    changed("the firm rules")
    # the profile
    store.update_profile(folder, lambda cur: {**cur, "directors": ["Jane Doe"]})
    changed("the profile")
    # the override log position
    overrides.add_override(folder, "L2", "revenue_transfer", reason="customer", name="Ann")
    changed("an override")
    # the thresholds
    from cma import indicators
    (home / "indicators.yaml").write_text(indicators.default_thresholds_text() + "\n# the firm's copy\n",
                                          encoding="utf-8")
    changed("the thresholds")
    # the memo field map
    (home / "memo_fields.yaml").write_text(pack.default_field_map_text() + "\n# the firm's copy\n", encoding="utf-8")
    changed("the memo field map")
    # the policy hash
    shipped = policy.default_policy_text()
    monkeypatch.setattr(policy, "default_policy_text", lambda: shipped + "\n# edited in place\n")
    changed("the policy")
    # the show_policy_verdict setting
    (home / "firm.yaml").write_text("show_policy_verdict: false\n", encoding="utf-8")
    changed("show_policy_verdict")
    # the package version
    monkeypatch.setattr(cma, "__version__", "9.9.9")
    changed("the package version")


@pytest.mark.invariants
def test_preview_writes_nothing_but_a_preview_entry(home, tmp_path):
    case_id, folder = _case(tmp_path)
    overrides.add_override(folder, "L2", "revenue_transfer", reason="known customer J BLOGGS", name="Ann")
    log_files = {caselog.LOG_NAME, caselog.LOG_NAME + ".head", caselog.LOG_NAME + ".gate", caselog.LOG_NAME + ".lock"}

    def files():
        return {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file() and p.name not in log_files}

    before, entries = files(), caselog.for_case(folder).entries()
    p = pack.build_preview(folder, case_id)
    assert files() == before
    after = caselog.for_case(folder).entries()
    assert len(after) == len(entries) + 1 and p.position == len(after)
    e = after[-1]
    assert e.event == "preview"
    assert e.data == {"snapshot": p.snapshot, "inputs": p.inputs, "grounded": True, "policy_check": "shown"}
    # hashes and counts only: no description, name or reason reaches the log
    assert "BLOGGS" not in str(e.data) and "Ann" not in str(e.data)
    assert caselog.for_case(folder).verify().ok


def test_preview_is_deterministic(home, tmp_path):
    case_id, folder = _case(tmp_path)
    a, b = pack.build_preview(folder, case_id), pack.build_preview(folder, case_id)
    assert a.text == b.text and a.snapshot == b.snapshot and a.fields == b.fields


def test_preview_of_a_case_with_no_statement_is_refused(home, tmp_path):
    case_id = store.new_case(label="empty")
    folder = store.case_folder(case_id)
    n = len(caselog.for_case(folder).entries())
    with pytest.raises(pack.PreviewError):
        pack.build_preview(folder, case_id)
    assert len(caselog.for_case(folder).entries()) == n


# --- the command ----------------------------------------------------------------------


@pytest.mark.contracts
def test_cli_case_preview(home, tmp_path):
    case_id, folder = _case(tmp_path, demo=True)
    r = CliRunner().invoke(app, ["case", "preview", case_id])
    assert r.exit_code == 0, r.output
    entry = caselog.for_case(folder).entries()[-1]
    assert f"snapshot {entry.data['snapshot']}" in r.output
    assert "## Monthly turnover and credits" in r.output and pack.DEMO_STAMP in r.output.replace("\n", " ")
    assert policy.ILLUSTRATIVE_LABEL.split(".")[0] in r.output


@pytest.mark.contracts
def test_cli_case_preview_ungrounded_and_refused(home, tmp_path):
    case_id, _folder = _case(tmp_path)
    (home / "memo_fields.yaml").write_text(
        "fields:\n  - id: a\n    title: A\n    figures: [M2]\n    template: 'Revenue {M2} over 12 months.'\n",
        encoding="utf-8")
    r = CliRunner().invoke(app, ["case", "preview", case_id])
    assert r.exit_code == 1 and "fails the grounding guard" in r.output
    empty = store.new_case(label="empty")
    r = CliRunner().invoke(app, ["case", "preview", empty])
    assert r.exit_code == 2 and "Refused" in r.output
    (home / "memo_fields.yaml").write_text("fields: [\n", encoding="utf-8")
    r = CliRunner().invoke(app, ["case", "preview", case_id])
    assert r.exit_code == 2 and "not valid YAML" in r.output
