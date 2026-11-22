"""cma pseudonymise (R46, P10): names and account numbers become consistent tokens, the
institutions and every id, date, amount and balance are kept, the case profile is tokenised the
same way, the output says it is pseudonymised personal data, and the copy gives the original's
figures, buckets, monthly cells and indicators."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cma import pseudonymise as ps
from cma import store, synth
from cma.cli import app
from cma.importers import contract
from cma.synth import ARCHETYPES, PROFILES

HEADER = "id,date,description,amount,balance\n"


def _csv(rows: list[tuple[str, str, str, str, str]]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(["id", "date", "description", "amount", "balance"])
    w.writerows(rows)
    return buf.getvalue().encode()


def _statement(lines: list[tuple[str, str, str]], opening: int = 100_000) -> bytes:
    """(date, description, amount in pence) lines, balances walked from `opening` pence."""
    rows, bal = [], opening
    for i, (d, desc, amount) in enumerate(lines, 1):
        bal += amount
        rows.append((f"L{i}", d, desc, f"{amount / 100:.2f}", f"{bal / 100:.2f}"))
    return _csv(rows)


def _rows(data: bytes) -> list[dict]:
    return list(csv.DictReader(io.StringIO(data.decode(), newline="")))


def _profile(**kw) -> dict:
    return store.validate_profile(kw)


def _figures_equal(original: bytes, profile, result: ps.Pseudonymised) -> None:
    a, b = contract.checked(original, "a"), contract.checked(result.statement, "b")
    for rid in ("v1", "v2"):
        from cma.categorise import load_ruleset
        rs = load_ruleset(rid)
        fa, fb = ps.figures(a, profile, ruleset=rs), ps.figures(b, result.profile, ruleset=rs)
        assert fa.lines == fb.lines
        assert fa.metrics == fb.metrics
        assert fa.monthly == fb.monthly
        assert fa.indicators == fb.indicators


def _synth_case(archetype: str, profile: str, features) -> tuple[bytes, dict]:
    rows, truth = synth.generate(archetype, profile, seed=11, months=6, features=list(features))
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=["id", "date", "description", "amount", "balance"])
    w.writeheader()
    w.writerows(rows)
    names = truth.case_profile or {"directors": [], "own_accounts": []}
    prof = _profile(directors=list(names.get("directors", [])), own_accounts=list(names.get("own_accounts", [])),
                    vat_stagger=truth.vat_stagger, business_name="PARKGATE TRADING LTD", label="synthetic case",
                    proposed_monthly_repayment="1500.00")
    return buf.getvalue().encode(), prof


@pytest.mark.invariants
@pytest.mark.parametrize("archetype", sorted(ARCHETYPES))
@pytest.mark.parametrize("risk", sorted(PROFILES))
@pytest.mark.parametrize("features", [(), tuple(synth.FEATURES)], ids=["plain", "all_features"])
def test_p10_pseudonymised_statement_and_profile_give_identical_figures(archetype, risk, features):
    data, prof = _synth_case(archetype, risk, features)
    result = ps.pseudonymise_statement(data, prof)
    _figures_equal(data, prof, result)
    # Every id, date, amount and balance is kept exactly.
    before, after = _rows(data), _rows(result.statement)
    assert [(r["id"], r["date"], r["amount"], r["balance"]) for r in before] == \
           [(r["id"], r["date"], r["amount"], r["balance"]) for r in after]
    if features:
        assert synth.DIRECTOR_NAME not in result.statement.decode()
        assert synth.DIRECTOR_NAME not in json.dumps(result.profile)
        assert result.words_replaced > 0


@pytest.mark.invariants
def test_p10_names_and_account_numbers_become_consistent_tokens_institutions_kept():
    prof = _profile(directors=["JANE DOE"], own_accounts=["ACME HOLDINGS"], business_name="ACME LTD")
    data = _statement([
        ("2026-01-02", "STRIPE PAYMENTS UK", 50_000),
        ("2026-01-03", "FPS CREDIT JANE DOE", 20_000),
        ("2026-01-04", "FPS DEBIT ACME HOLDINGS 12345678 20-11-33", -10_000),
        ("2026-01-05", "FPS CREDIT JOHN SMITH INV 4471", 7_500),
        ("2026-01-06", "FPS CREDIT JOHN SMITH INV 4472", 7_500),
        ("2026-01-07", "IWOCA LOAN DRAWDOWN", 300_000),
        ("2026-01-08", "HMRC VAT REPAYMENT", 12_000),
        ("2026-01-09", "DD BRITISH GAS 99887766", -9_000),
        ("2026-01-10", "UNPAID DD BRITISH GAS 99887766", 9_000),
        ("2026-01-11", "CARD PAYMENT TO BET365", -2_000),
        ("2026-01-12", "FPS CREDIT JANE DOE ACME HOLDINGS", 4_000),
    ])
    result = ps.pseudonymise_statement(data, prof)
    out = result.statement.decode()
    for secret in ("JANE", "DOE", "ACME", "HOLDINGS", "JOHN", "SMITH", "12345678", "99887766", "BRITISH"):
        assert secret not in out, secret
    for kept in ("STRIPE", "IWOCA", "HMRC", "UNPAID DD", "BET365", "INV 4471", "FPS CREDIT"):
        assert kept in out, kept
    rows = _rows(result.statement)
    # The same name gives the same token on every line; another name another token.
    assert rows[3]["description"].split("INV")[0] == rows[4]["description"].split("INV")[0]
    assert rows[7]["description"][3:].split()[0] == rows[8]["description"][10:].split()[0]
    # The profile holds the tokens the descriptions hold.
    jane, acme = result.profile["directors"][0], result.profile["own_accounts"][0]
    assert jane != acme and jane in rows[1]["description"] and acme in rows[2]["description"]
    assert jane in rows[10]["description"] and acme in rows[10]["description"]
    assert result.profile["business_name"] not in ("ACME LTD",)
    _figures_equal(data, prof, result)


@pytest.mark.invariants
def test_p10_a_director_inside_an_own_account_name_and_two_spellings():
    prof = _profile(directors=["JANE DOE"], own_accounts=["JANE DOE LTD"])
    data = _statement([
        ("2026-02-02", "FPS CREDIT JANE DOE LTD", 10_000),
        ("2026-02-03", "FPS CREDIT JANE-DOE LTD", 10_000),
        ("2026-02-04", "FPS CREDIT JANE DOE", 5_000),
        ("2026-02-05", "FPS DEBIT JANE DOE LTD", -3_000),
        ("2026-02-06", "TFR JANE DOE LTD", -2_000),
        ("2026-02-07", "FPS CREDIT CASH DEPOSIT REF JANE DOE", 6_000),
        ("2026-02-08", "CASH PAID IN JANE DOE", 6_000),
    ])
    result = ps.pseudonymise_statement(data, prof)
    assert "JANE" not in result.statement.decode()
    rows = _rows(result.statement)
    assert rows[0]["description"] != rows[1]["description"]  # two spellings stay two counterparties
    _figures_equal(data, prof, result)


@pytest.mark.invariants
def test_p10_a_profile_name_holding_recognised_wording_is_kept():
    prof = _profile(directors=["IWOCA"])  # a name the lender list recognises
    data = _statement([("2026-03-02", "IWOCA LOAN", 10_000), ("2026-03-03", "FPS CREDIT IWOCA", 5_000)])
    result = ps.pseudonymise_statement(data, prof)
    assert result.profile["directors"] == ["IWOCA"] and result.names_kept == 1
    _figures_equal(data, prof, result)


@pytest.mark.invariants
def test_p10_round_trips_and_concentration_keep_their_counterparties():
    data = _statement([
        ("2026-04-01", "FPS DEBIT PETER PAN", -10_000),
        ("2026-04-02", "FPS CREDIT PETER PAN", 15_000),
        ("2026-04-03", "FPS CREDIT WENDY DARLING INV 12", 20_000),
        ("2026-04-04", "FPS CREDIT WENDY DARLING INV 13", 20_000),
        ("2026-04-05", "FPS CREDIT CAPTAIN HOOK INV 14", 1_000),
        ("2026-04-06", "Card Payment to Tesco Stores 4411", -1_500),
    ])
    result = ps.pseudonymise_statement(data, None)
    assert result.profile is None
    _figures_equal(data, None, result)


@pytest.mark.contracts
def test_refused_statement_writes_nothing(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_bytes(HEADER.encode() + b"L1,2026-01-01,X,abc,1.00\n")
    with pytest.raises(ps.PseudonymiseError):
        ps.pseudonymise_file(bad, "csv", tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.contracts
def test_cli_writes_a_labelled_copy_and_a_tokenised_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("CMA_HOME", str(tmp_path / "root"))
    case_id = store.new_case(label="Jane's café", root=tmp_path / "root")
    folder = store.case_folder(case_id, tmp_path / "root")
    store.set_profile(folder, _profile(directors=["JANE DOE"], label="Jane's café", vat_stagger=2))
    src = tmp_path / "s.csv"
    src.write_bytes(_statement([("2026-01-02", "FPS CREDIT JANE DOE", 20_000),
                                ("2026-01-03", "SUMUP PAYMENTS", 5_000)]))
    out = tmp_path / "pseudo"
    r = CliRunner().invoke(app, ["pseudonymise", str(src), "--format", "csv", "--out", str(out),
                                 "--profile", case_id])
    assert r.exit_code == 0, r.output
    assert "pseudonymised personal data" in r.output.lower() and "not anonymous" in r.output.lower()
    assert sorted(p.name for p in out.iterdir()) == sorted(
        [ps.STATEMENT_FILE, ps.PROFILE_FILE, ps.LABEL_FILE, ps.MANIFEST_FILE])
    for name in (ps.STATEMENT_FILE, ps.PROFILE_FILE, ps.LABEL_FILE, ps.MANIFEST_FILE):
        text = (out / name).read_text()
        assert "JANE" not in text.upper() or name == ps.LABEL_FILE
    assert ps.LABEL in (out / ps.LABEL_FILE).read_text()
    assert ps.LABEL in (out / ps.PROFILE_FILE).read_text()
    manifest = json.loads((out / ps.MANIFEST_FILE).read_text())
    assert manifest["pseudonymised_personal_data"] is True and manifest["anonymous"] is False
    prof = store.parse_profile((out / ps.PROFILE_FILE).read_bytes())
    assert prof["vat_stagger"] == 2 and prof["directors"][0] in (out / ps.STATEMENT_FILE).read_text()
    assert "SUMUP" in (out / ps.STATEMENT_FILE).read_text()
    # The copy loads as a statement, and a second run refuses to overwrite it.
    contract.checked((out / ps.STATEMENT_FILE).read_bytes(), "copy")
    r2 = CliRunner().invoke(app, ["pseudonymise", str(src), "--format", "csv", "--out", str(out)])
    assert r2.exit_code == 2


@pytest.mark.contracts
def test_cli_monzo_export_goes_through_its_importer(tmp_path):
    src = tmp_path / "export.csv"
    src.write_bytes((Path(__file__).parent / "golden" / "monzo" / "business_export.golden").read_bytes())
    out = tmp_path / "m"
    r = CliRunner().invoke(app, ["pseudonymise", str(src), "--format", "monzo", "--out", str(out)])
    assert r.exit_code == 0, r.output
    copy = (out / ps.STATEMENT_FILE).read_bytes()
    assert "CLIENT A" not in copy.decode().upper()
    golden = (Path(__file__).parent / "golden" / "monzo" / "business_statement.golden").read_bytes()
    assert [(r["id"], r["date"], r["amount"], r["balance"]) for r in _rows(golden)] == \
           [(r["id"], r["date"], r["amount"], r["balance"]) for r in _rows(copy)]
