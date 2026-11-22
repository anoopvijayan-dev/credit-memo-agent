"""T38: cma verify (C8; R44, R45, P3, P13).

Verify recomputes every figure, bucket and monthly cell an evidence record holds from the
inputs it recorded (the statement file for a `cma run` record; the case's statements, profile,
firm rules and override log up to the recorded position for a signed pack), and reports each
mismatch, or that there is none. A record made with a ruleset other than the one installed is
refused, naming the package version and ruleset that made it. A record with no schema_version
(v0, the previous release's) is checked for M1 to M16 only, and verify says so."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cma import overrides, pack, store, verify
from cma.categorise import load_ruleset
from cma.cli import app

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "cafe_stretched.memo.evidence.json"

STATEMENT = ("id,date,description,amount,balance\n"
             "L1,2026-01-05,CARD SETTLEMENT STRIPE,500.60,1500.60\n"
             "L2,2026-01-06,FPS CREDIT J BLOGGS,300.25,1800.85\n"
             "L3,2026-01-07,IWOCA LOAN REPAYMENT,-100.00,1700.85\n"
             "L4,2026-02-02,CASH PAID IN,250.10,1950.95\n"
             "L5,2026-02-20,FPS CREDIT A SMITH,75.00,2025.95\n")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CMA_HOME", str(tmp_path / "cma-home"))
    monkeypatch.setenv("CMA_RULESET", "v2")
    return tmp_path / "cma-home"


def _signed(tmp_path, with_override=True):
    case_id = store.new_case(label="t38")
    folder = store.case_folder(case_id)
    src = tmp_path / "main.csv"
    src.write_text(STATEMENT, encoding="utf-8")
    store.import_file(folder, src, "csv")
    if with_override:
        overrides.add_override(folder, "L2", "revenue_transfer", reason="customer", name="Ann")
    p = pack.build_preview(folder, case_id)
    s = pack.sign_off(folder, case_id, p.snapshot, "Ann Smith")
    return case_id, folder, json.loads((s.pack / "evidence.json").read_text(encoding="utf-8"))


def _run_record(tmp_path, monkeypatch, ruleset="v2"):
    monkeypatch.setenv("CMA_RULESET", ruleset)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "s.csv").write_text(STATEMENT, encoding="utf-8")
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", "s.csv", "--vat-stagger", "1", "--yes", "--reviewer", "A. Analyst",
                                 "--out", str(out)])
    assert r.exit_code == 0, r.output
    return tmp_path / "memo.evidence.json"


# --- P13: a signed pack's evidence reproduces exactly ---------------------------------------


@pytest.mark.invariants
def test_p13_signed_pack_evidence_reproduces_every_figure_bucket_and_cell(home, tmp_path):
    _case_id, folder, ev = _signed(tmp_path)
    result = verify.verify(ev, case_folder=folder)
    assert result.mismatches == []
    assert result.kind == "pack" and result.scope == "full"
    assert result.checked["figures"] == len(ev["accounts"][0]["metrics"]) == 28
    assert result.checked["buckets"] == 1 + len(ev["accounts"][0]["money_in"]["buckets"]) + 1
    assert result.checked["monthly_cells"] == 6 * len(ev["accounts"][0]["monthly_summary"]) == 12


@pytest.mark.invariants
def test_p13_later_overrides_do_not_change_what_a_signed_pack_verifies_against(home, tmp_path):
    """The recorded inputs are the override log up to the position the pack recorded: an
    override made after the sign-off is not part of them."""
    _case_id, folder, ev = _signed(tmp_path)
    overrides.add_override(folder, "L5", "revenue_transfer", reason="customer", name="Ann")
    assert verify.verify(ev, case_folder=folder).mismatches == []


@pytest.mark.invariants
def test_p13_a_changed_figure_bucket_or_cell_is_reported(home, tmp_path):
    _case_id, folder, ev = _signed(tmp_path)
    acct = ev["accounts"][0]
    m2 = next(m for m in acct["metrics"] if m["id"] == "M2")
    m2["value"] = m2["value"] + 1
    acct["money_in"]["buckets"][0]["evidence"] = ["L9"]
    acct["monthly_summary"][1]["money_in"]["value"] = 0.01
    found = verify.verify(ev, case_folder=folder).mismatches
    where = [m.where for m in found]
    assert "account main: figure M2 value" in where
    assert any(w.startswith("account main: bucket ") and w.endswith(" evidence") for w in where)
    assert "account main: monthly 2026-02 money_in value" in where
    assert len(found) == 3


@pytest.mark.contracts
def test_pack_whose_case_inputs_changed_is_refused_naming_the_input(home, tmp_path):
    _case_id, folder, ev = _signed(tmp_path)
    store.update_profile(folder, lambda cur: {**cur, "directors": ["A Smith"]})
    with pytest.raises(verify.VerifyRefused, match="the case profile"):
        verify.verify(ev, case_folder=folder)


@pytest.mark.contracts
def test_pack_against_another_case_is_refused(home, tmp_path):
    case_id, _folder, ev = _signed(tmp_path)
    _other, other_folder, _ = _signed(tmp_path)
    with pytest.raises(verify.VerifyRefused, match=f"made for case {case_id}"):
        verify.verify(ev, case_folder=other_folder)


@pytest.mark.invariants
def test_p3_figures_twice_and_overrides_one_at_a_time_equal_a_fresh_computation(home, tmp_path):
    """Overrides applied one at a time give the figures a fresh computation with all of them
    gives, and verifying (a recomputation) twice gives the same answer."""
    _case_id, folder, ev = _signed(tmp_path, with_override=False)
    for line in ("L2", "L5"):
        overrides.add_override(folder, line, "revenue_transfer", reason="customer", name="Ann")
        overrides.case_figures(folder)
    stepwise = overrides.case_figures(folder)["main"]
    for memo in (overrides._PARSED, overrides._BARE, overrides._CLASSES):
        memo._items.clear()
    fresh = overrides.case_figures(folder)["main"]
    dump = lambda f: ([m.model_dump(mode="json") for m in f.metrics],  # noqa: E731
                      [r.model_dump(mode="json") for r in f.monthly])
    assert dump(stepwise) == dump(fresh)
    first, second = verify.verify(ev, case_folder=folder), verify.verify(ev, case_folder=folder)
    assert first == second and first.mismatches == []


# --- R44 on a `cma run` record --------------------------------------------------------------


@pytest.mark.contracts
def test_run_record_reproduces_and_reports_a_changed_cell(tmp_path, monkeypatch):
    path = _run_record(tmp_path, monkeypatch)
    ev = json.loads(path.read_text())
    assert verify.verify(ev, base=path.parent).mismatches == []
    ev["monthly_summary"][0]["days_overdrawn"]["evidence"] = ["L1"]
    found = verify.verify(ev, base=path.parent).mismatches
    assert [m.where for m in found] == ["monthly 2026-01 days_overdrawn evidence"]


@pytest.mark.contracts
def test_committed_example_verifies_with_no_mismatch(monkeypatch):
    monkeypatch.delenv("CMA_RULESET", raising=False)
    monkeypatch.chdir(ROOT)
    ev = json.loads(EXAMPLE.read_text())
    assert verify.verify(ev, base=EXAMPLE.parent).mismatches == []


@pytest.mark.contracts
def test_run_record_whose_statement_changed_is_refused(tmp_path, monkeypatch):
    path = _run_record(tmp_path, monkeypatch)
    (tmp_path / "s.csv").write_text(STATEMENT.replace("75.00,2025.95", "76.00,2026.95"), encoding="utf-8")
    with pytest.raises(verify.VerifyRefused, match="not the one the record was made from"):
        verify.verify(json.loads(path.read_text()), base=path.parent)


@pytest.mark.contracts
def test_run_record_under_v1_is_verified_under_v1_whatever_cma_ruleset_says(tmp_path, monkeypatch):
    path = _run_record(tmp_path, monkeypatch, ruleset="v1")
    monkeypatch.setenv("CMA_RULESET", "v2")
    ev = json.loads(path.read_text())
    assert ev["ruleset"]["id"] == "v1"
    assert verify.verify(ev, base=path.parent).mismatches == []


# --- R45: another ruleset is refused, naming the version and ruleset ------------------------


@pytest.mark.contracts
@pytest.mark.parametrize("ruleset", [{"id": "v2", "sha256": "0" * 64}, {"id": "v9", "sha256": "1" * 64}])
def test_r45_record_from_another_ruleset_is_refused_naming_version_and_ruleset(tmp_path, monkeypatch, ruleset):
    path = _run_record(tmp_path, monkeypatch)
    ev = json.loads(path.read_text())
    ev["ruleset"], ev["package_version"] = ruleset, "0.0.9"
    with pytest.raises(verify.VerifyRefused) as e:
        verify.verify(ev, base=path.parent)
    msg = str(e.value)
    assert "0.0.9" in msg and ruleset["id"] in msg and ruleset["sha256"] in msg


@pytest.mark.contracts
def test_r45_cli_refuses_with_exit_2(tmp_path, monkeypatch):
    path = _run_record(tmp_path, monkeypatch)
    ev = json.loads(path.read_text())
    ev["ruleset"] = {"id": "v2", "sha256": "0" * 64}
    path.write_text(json.dumps(ev))
    r = CliRunner().invoke(app, ["verify", str(path)])
    assert r.exit_code == 2
    assert "0" * 64 in r.output and "package version" in r.output


# --- v0 records: M1 to M16 only ---------------------------------------------------------------


@pytest.mark.contracts
def test_v0_record_is_checked_for_m1_to_m16_only_and_says_so(tmp_path, monkeypatch):
    path = _run_record(tmp_path, monkeypatch, ruleset="v1")
    ev = json.loads(path.read_text())
    v0 = {"statement": ev["statement"], "model": ev["model"], "vat_stagger": ev["vat_stagger"],
          "metrics": [m for m in ev["metrics"] if int(m["id"][1:]) <= 16], "policy": ev["policy"]}
    monkeypatch.setenv("CMA_RULESET", "v2")
    result = verify.verify(v0, base=path.parent)
    assert result.scope == "v0" and result.mismatches == []
    assert result.checked == {"figures": 16, "buckets": 0, "monthly_cells": 0}
    assert "M1 to M16" in result.note
    v0["metrics"][3]["value"] = 12345.0
    assert [m.where for m in verify.verify(v0, base=path.parent).mismatches] == [f"figure {v0['metrics'][3]['id']} value"]


# --- the CLI ---------------------------------------------------------------------------------


@pytest.mark.contracts
def test_cli_verify_exit_0_when_reproduced_and_1_on_a_mismatch(home, tmp_path):
    case_id, _folder, ev = _signed(tmp_path)
    path = tmp_path / "ev.json"
    path.write_text(json.dumps(ev))
    r = CliRunner().invoke(app, ["verify", str(path), "--case", case_id])
    assert r.exit_code == 0, r.output
    assert "no mismatch" in r.output
    ev["accounts"][0]["metrics"][0]["value"] = -1.0
    path.write_text(json.dumps(ev))
    r = CliRunner().invoke(app, ["verify", str(path)])  # the case is found by the record's id
    assert r.exit_code == 1, r.output
    assert "figure M1 value" in r.output


@pytest.mark.contracts
def test_cli_verify_unreadable_record_exits_2(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    assert CliRunner().invoke(app, ["verify", str(path)]).exit_code == 2


@pytest.mark.contracts
def test_installed_ruleset_check_names_the_installed_hash(tmp_path, monkeypatch):
    path = _run_record(tmp_path, monkeypatch)
    ev = json.loads(path.read_text())
    ev["ruleset"] = {"id": "v2", "sha256": "0" * 64}
    with pytest.raises(verify.VerifyRefused) as e:
        verify.verify(ev, base=path.parent)
    assert load_ruleset("v2").sha256 in str(e.value)


# --- the boundary: a malformed record is refused, never raised or called a mismatch ----------


@pytest.mark.contracts
@pytest.mark.parametrize("change", [
    {"metrics": [1, 2]}, {"metrics": "x"}, {"monthly_summary": ["x"]}, {"vat_stagger": "zz"},
    {"vat_stagger": True}, {"ruleset": {"id": ["v2"], "sha256": "x"}}, {"statement": "s.csv"},
    {"inputs": [], "accounts": [], "case": "abc"}, {"accounts": [{"account": "main", "metrics": [3]}], "case": "abc"},
])
def test_cli_verify_malformed_record_is_refused_with_exit_2(tmp_path, change):
    ev = {**json.loads(EXAMPLE.read_text()), **change}
    path = tmp_path / "ev.json"
    path.write_text(json.dumps(ev))
    r = CliRunner().invoke(app, ["verify", str(path)])
    assert r.exit_code == 2, r.output
    assert r.exception is None or isinstance(r.exception, SystemExit)
    assert "Refused." in r.output


@pytest.mark.contracts
def test_verify_writes_nothing_in_the_case_folder(home, tmp_path):
    _case_id, folder, ev = _signed(tmp_path)

    def snapshot():
        return {p.relative_to(folder): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}

    before = snapshot()
    verify.verify(ev, case_folder=folder)
    verify.verify(ev, case_folder=folder)
    assert snapshot() == before
