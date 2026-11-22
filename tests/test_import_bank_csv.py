"""The bank CSV importer (R38, C4): `cma case import --format bank-csv:PROFILE`, or
`--format bank-csv --mapping FILE`, converts a bank's CSV export with paid-in and paid-out
columns, a non-ISO date format or newest-first order into the five-column statement, and
applies the same checks as the five-column loader (the output passes ingest.load_csv, and a
refusal names the source file's line, never a cell's value).

The golden files (tests/golden/bank_csv/) were written by hand: one export per named profile
and one for a custom mapping, each with its statement worked out by hand (oldest first, the
running balance walked from the bank's own balances or the opening balance given)."""

import hashlib
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cma import case as caselog
from cma import ingest, store
from cma.cli import app
from cma.importers import bankcsv, contract

runner = CliRunner()
GOLDEN = Path(__file__).parent / "golden" / "bank_csv"
SENTINEL = "ZQXJ"  # a string no message the tool writes holds

# profile -> the opening balance to pass (None: the export has a balance column)
PROFILES = {"barclays": "500.00", "lloyds": None, "natwest": None, "starling": None}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CMA_HOME", str(tmp_path / "cma-home"))
    monkeypatch.delenv("CMA_RULESET", raising=False)
    return tmp_path / "cma-home"


def _flat(text: str) -> str:
    return " ".join(text.split())


def _custom() -> dict:
    return bankcsv.load_mapping(GOLDEN / "custom.mapping.yaml")


MAP = {"columns": {"date": "Date", "description": "Details", "paid_in": "In", "paid_out": "Out",
                   "balance": "Balance"},
       "date_format": "%d/%m/%Y", "order": "auto"}


def _write(tmp_path, rows, header="Date,Details,In,Out,Balance", name="export.csv") -> Path:
    path = tmp_path / name
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


def _convert(tmp_path, rows, mapping=None, **kw):
    src = _write(tmp_path, rows, **{k: kw.pop(k) for k in ("header",) if k in kw})
    out = tmp_path / "out.csv"
    return bankcsv.convert(src, out, mapping=mapping or MAP, **kw), out


# --- golden files: one per profile, and a custom mapping ---------------------------------


@pytest.mark.contracts
@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_each_named_profile_converts_its_golden_export(tmp_path, profile):
    out = tmp_path / "out.csv"
    r = bankcsv.convert(GOLDEN / f"{profile}.export.golden", out, mapping=bankcsv.profile(profile),
                        opening_balance=PROFILES[profile])
    assert out.read_bytes() == (GOLDEN / f"{profile}.statement.golden").read_bytes()
    # the five-column checks: the output is what the statement loader accepts
    stmt = ingest.load_csv(out)
    assert len(stmt.transactions) == r.rows


@pytest.mark.contracts
def test_a_custom_mapping_converts_its_golden_export(tmp_path):
    out = tmp_path / "out.csv"
    r = bankcsv.convert(GOLDEN / "custom.export.golden", out, mapping=_custom(), opening_balance="200.00")
    assert out.read_bytes() == (GOLDEN / "custom.statement.golden").read_bytes()
    assert r.order == "newest first" and r.balance_source == "opening balance supplied"
    ingest.load_csv(out)


@pytest.mark.contracts
def test_newest_first_is_found_from_the_dates_and_turned_oldest_first(tmp_path):
    out = tmp_path / "out.csv"
    r = bankcsv.convert(GOLDEN / "lloyds.export.golden", out, mapping=bankcsv.profile("lloyds"))
    assert r.order == "newest first"
    assert (r.start.isoformat(), r.end.isoformat()) == ("2026-03-03", "2026-03-10")
    assert r.balance_source == "the bank's balance column"


@pytest.mark.contracts
def test_newest_first_rows_on_one_date_are_ordered_by_the_balances(tmp_path):
    rows = ["01/03/2026,B,5.00,,115.00", "01/03/2026,A,10.00,,110.00"]
    r, out = _convert(tmp_path, rows)
    assert r.order == "newest first"
    assert [t.description for t in ingest.load_csv(out).transactions] == ["A", "B"]


@pytest.mark.contracts
def test_the_profiles_are_packaged_and_listed():
    assert set(PROFILES) <= set(bankcsv.profile_names())
    with pytest.raises(contract.ImportRefused, match="unknown bank profile; use one of barclays"):
        bankcsv.profile(SENTINEL)


# --- the five-column checks and refusals: by line, never a cell's value --------------------


@pytest.mark.contracts
@pytest.mark.parametrize("rows, match", [
    ([f"31/02/2026,{SENTINEL},1.00,,1.00"], r"line 2: Date is not a date in the form %d/%m/%Y"),
    ([f"01/03/2026,{SENTINEL},1.00,2.00,1.00"], r"line 2: both the paid-in and the paid-out column"),
    ([f"01/03/2026,{SENTINEL},,,1.00"], r"line 2: neither the paid-in nor the paid-out column"),
    ([f"01/03/2026,{SENTINEL},-1.00,,1.00"], r"line 2: In is not an unsigned amount"),
    ([f"01/03/2026,{SENTINEL},1.001,,1.00"], r"line 2: In is not an unsigned amount"),
    ([f"01/03/2026,{SENTINEL},1.00,,{SENTINEL}"], r"line 2: Balance is not a plain amount"),
    ([f"01/03/2026,{SENTINEL},1.00,,1.00,extra"], r"line 2: more fields than the header has columns"),
    (["01/03/2026,A,1.00,,1.00", f"02/03/2026,{SENTINEL},1.00,,5.00"],
     r"line 3: the balance walked from the opening balance disagrees with the bank's balance column"),
    (["01/03/2026,A,1.00,,1.00", f"01/01/2099,{SENTINEL},1.00,,2.00"], r"problem.*line 3"),  # the loader: a future date
    (["05/03/2026,A,1.00,,1.00", "01/03/2026,B,1.00,,2.00", f"09/03/2026,{SENTINEL},1.00,,3.00"],
     r"problem.*line 3"),  # the loader: a date out of order
    ([f"01/03/2026,{SENTINEL},10000000000000.00,,1.00"], r"line 2: In is £10,000,000,000,000 or more"),
    ([f"01/03/2026,{SENTINEL},9000000000000.00,,1.00", "02/03/2026,B,9000000000000.00,,2.00"],
     r"money in totals £10,000,000,000,000 or more"),
])
def test_a_bad_row_is_refused_naming_its_line_and_no_cell(tmp_path, rows, match):
    with pytest.raises(contract.ImportRefused) as e:
        _convert(tmp_path, rows)
    assert re.search(match, _flat(str(e.value))), str(e.value)
    assert SENTINEL not in str(e.value)
    assert not (tmp_path / "out.csv").exists()


@pytest.mark.contracts
def test_amounts_with_a_pound_sign_and_thousands_separators_are_read_exactly(tmp_path):
    rows = ['01/03/2026,A,"£1,234.56",,"1,234.56"', '02/03/2026,B,,0.01,"£1,234.55"',
            "03/03/2026,C,,2000.00,-765.45"]
    r, out = _convert(tmp_path, rows)
    assert [(t.amount, t.balance) for t in ingest.load_csv(out).transactions] == [
        (1234.56, 1234.56), (-0.01, 1234.55), (-2000.00, -765.45)]
    assert r.closing_balance == -765.45


@pytest.mark.contracts
def test_a_misgrouped_thousands_separator_is_refused(tmp_path):
    with pytest.raises(contract.ImportRefused, match="line 2: In is not an unsigned amount"):
        _convert(tmp_path, ['01/03/2026,A,"1,23.00",,1.00'])


@pytest.mark.contracts
def test_a_missing_column_is_refused_naming_the_mapping_not_the_file(tmp_path):
    with pytest.raises(contract.ImportRefused, match=r"missing column.*: Balance"):
        _convert(tmp_path, ["01/03/2026,A,1.00,"], header="Date,Details,In,Out")


@pytest.mark.contracts
def test_a_column_named_twice_is_refused(tmp_path):
    with pytest.raises(contract.ImportRefused, match="named more than once: In"):
        _convert(tmp_path, ["01/03/2026,A,1.00,,1.00,"], header="Date,Details,In,Out,Balance,In")


@pytest.mark.contracts
def test_no_balance_column_needs_an_opening_balance(tmp_path):
    mapping = {**MAP, "columns": {k: v for k, v in MAP["columns"].items() if k != "balance"}}
    with pytest.raises(contract.ImportRefused, match="no balance column; pass --opening-balance"):
        _convert(tmp_path, ["01/03/2026,A,1.00,,"], mapping=mapping)
    r, out = _convert(tmp_path, ["01/03/2026,A,1.00,,"], mapping=mapping, opening_balance="-3.50")
    assert ingest.load_csv(out).transactions[0].balance == -2.50 and r.opening_balance == -3.50


@pytest.mark.contracts
def test_an_opening_balance_that_disagrees_with_the_balance_column_is_refused(tmp_path):
    with pytest.raises(contract.ImportRefused, match=r"--opening-balance 5\.00 disagrees"):
        _convert(tmp_path, ["01/03/2026,A,1.00,,1.00"], opening_balance="5.00")


@pytest.mark.contracts
def test_a_repeated_id_is_refused_naming_both_lines(tmp_path):
    mapping = {**MAP, "columns": {**MAP["columns"], "id": "Ref"}}
    with pytest.raises(contract.ImportRefused, match=r"line 3: the id repeats line 2's"):
        _convert(tmp_path, [f"01/03/2026,A,1.00,,1.00,{SENTINEL}", f"02/03/2026,B,1.00,,2.00,{SENTINEL}"],
                 mapping=mapping, header="Date,Details,In,Out,Balance,Ref")


@pytest.mark.contracts
def test_a_declared_order_the_dates_contradict_is_refused(tmp_path):
    with pytest.raises(contract.ImportRefused, match="says newest_first, but its dates run oldest first"):
        _convert(tmp_path, ["01/03/2026,A,1.00,,1.00", "02/03/2026,B,1.00,,2.00"],
                 mapping={**MAP, "order": "newest_first"})


@pytest.mark.contracts
def test_month_names_are_read_in_english_whatever_the_locale(tmp_path):
    mapping = {**MAP, "date_format": ["%d %b %Y", "%d %B %Y"]}
    _, out = _convert(tmp_path, ["1 Sep 2026,A,1.00,,1.00", "2 october 2026,B,1.00,,2.00"], mapping=mapping)
    assert [t.date.isoformat() for t in ingest.load_csv(out).transactions] == ["2026-09-01", "2026-10-02"]


@pytest.mark.contracts
def test_a_description_cell_a_spreadsheet_reads_as_a_formula_is_neutralised(tmp_path):
    _, out = _convert(tmp_path, ["01/03/2026,=HYPERLINK(1),1.00,,1.00"])
    assert ingest.load_csv(out).transactions[0].description.startswith("'=")


# --- the mapping a firm supplies -------------------------------------------------------------


@pytest.mark.contracts
@pytest.mark.parametrize("change, match", [
    ({"columns": {"date": "D", "description": "X", "amount": "A", "paid_in": "I", "paid_out": "O"}},
     "either amount or paid_in and paid_out"),
    ({"columns": {"date": "D", "description": "X", "paid_in": "I"}}, "either amount or paid_in and paid_out"),
    ({"columns": {"description": "X", "amount": "A"}}, "columns.date is required"),
    ({"columns": {"date": "D", "description": "X", "amount": "A", "colour": "C"}}, "unknown key columns.colour"),
    ({"columns": {"date": "D", "description": "D", "amount": "A"}}, "uses column D for more than one"),
    ({"date_format": "%Y"}, "date_format .* must name a day, a month and a year"),
    ({"date_format": None}, "date_format is required"),
    ({"order": "sideways"}, "order is oldest_first, newest_first or auto"),
    ({"extra": 1}, "unknown key extra"),
])
def test_a_bad_mapping_is_refused_naming_the_key(tmp_path, change, match):
    mapping = {**MAP, **change}
    if change.get("date_format", 0) is None:
        del mapping["date_format"]
    with pytest.raises(contract.ImportRefused, match=match):
        bankcsv.check_mapping(mapping)


@pytest.mark.contracts
def test_a_mapping_file_that_is_not_a_mapping_is_refused(tmp_path):
    bad = tmp_path / "m.yaml"
    bad.write_text("- just a list\n", encoding="utf-8")
    with pytest.raises(contract.ImportRefused, match="not a mapping"):
        bankcsv.load_mapping(bad)
    bad.write_text("columns: [\n", encoding="utf-8")
    with pytest.raises(contract.ImportRefused, match="not readable YAML"):
        bankcsv.load_mapping(bad)


@pytest.mark.contracts
def test_a_mapping_file_that_gives_a_key_twice_is_refused():
    # YAML keeps the last of two equal keys; here that would swap money in and money out.
    data = (b"columns:\n  date: Date\n  description: Details\n  paid_in: Out\n  paid_out: In\n"
            b"  paid_in: In\n  paid_out: Out\ndate_format: '%d/%m/%Y'\n")
    with pytest.raises(contract.ImportRefused, match="key paid_in is given more than once"):
        bankcsv.parse_mapping(data, "m.yaml")


@pytest.mark.contracts
def test_cma_case_import_bank_csv_twice_leaves_the_case_unchanged(home):
    cid = store.new_case()
    folder = store.case_folder(cid)
    args = ["case", "import", cid, str(GOLDEN / "lloyds.export.golden"), "--format", "bank-csv:lloyds"]
    assert runner.invoke(app, args).exit_code == 0
    before = [e.event for e in caselog.for_case(folder).entries()]
    again = runner.invoke(app, args)
    assert again.exit_code == 0 and "already imported" in again.output  # R39
    assert [e.event for e in caselog.for_case(folder).entries()] == before


@pytest.mark.contracts
def test_every_packaged_profile_is_a_valid_mapping():
    for name in bankcsv.profile_names():
        bankcsv.check_mapping(bankcsv.profile(name))


# --- cma case import ------------------------------------------------------------------------


@pytest.mark.contracts
@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_cma_case_import_bank_csv_keeps_the_golden_statement_and_logs_the_profile(home, profile):
    cid = store.new_case()
    folder = store.case_folder(cid)
    args = ["case", "import", cid, str(GOLDEN / f"{profile}.export.golden"), "--format", f"bank-csv:{profile}"]
    if PROFILES[profile]:
        args += ["--opening-balance", PROFILES[profile]]
    res = runner.invoke(app, args)
    assert res.exit_code == 0, res.output
    assert "imported account main" in res.output
    kept = folder / "statements" / "main.csv"
    assert kept.read_bytes() == (GOLDEN / f"{profile}.statement.golden").read_bytes()
    entry = [e for e in caselog.for_case(folder).entries() if e.event == "import"][-1]
    assert entry.data["format"] == f"bank-csv:{profile}"


@pytest.mark.contracts
def test_cma_case_import_with_a_custom_mapping_records_its_hash(home):
    cid = store.new_case()
    folder = store.case_folder(cid)
    mapping = GOLDEN / "custom.mapping.yaml"
    res = runner.invoke(app, ["case", "import", cid, str(GOLDEN / "custom.export.golden"), "--format", "bank-csv",
                              "--mapping", str(mapping), "--opening-balance", "200.00"])
    assert res.exit_code == 0, res.output
    assert (folder / "statements" / "main.csv").read_bytes() == (GOLDEN / "custom.statement.golden").read_bytes()
    entry = [e for e in caselog.for_case(folder).entries() if e.event == "import"][-1]
    assert entry.data["mapping_sha256"] == hashlib.sha256(mapping.read_bytes()).hexdigest()
    assert entry.data["format"] == "bank-csv"


@pytest.mark.contracts
@pytest.mark.parametrize("fmt, extra, match", [
    ("bank-csv", [], "pass --mapping FILE or name a profile"),
    ("bank-csv:lloyds", ["--mapping", "MAP"], "--mapping is for --format bank-csv without a profile"),
    ("bank-csv:nosuch", [], "unknown bank profile"),
    ("monzo", ["--mapping", "MAP"], "--mapping is for a bank CSV"),
    ("bank-csv:lloyds", ["--statement-balance", "1.00", "--at", "2026-03-01"], "--statement-balance and --at are for"),
])
def test_cma_case_import_refuses_options_that_do_not_fit(home, fmt, extra, match):
    cid = store.new_case()
    folder = store.case_folder(cid)
    extra = [str(GOLDEN / "custom.mapping.yaml") if x == "MAP" else x for x in extra]
    res = runner.invoke(app, ["case", "import", cid, str(GOLDEN / "lloyds.export.golden"), "--format", fmt, *extra])
    assert res.exit_code != 0
    assert match in _flat(res.output), res.output
    assert not (folder / "statements" / "main.csv").exists()


@pytest.mark.contracts
def test_cma_case_import_refusal_names_the_line_never_a_cell(home, tmp_path):
    cid = store.new_case()
    folder = store.case_folder(cid)
    src = tmp_path / "export.csv"
    src.write_text("Transaction Date,Transaction Type,Sort Code,Account Number,Transaction Description,"
                   "Debit Amount,Credit Amount,Balance\n"
                   f"01/03/2026,FPI,1,2,A,,1.00,1.00\n02/03/2026,FPI,1,2,{SENTINEL},,1.00,{SENTINEL}\n",
                   encoding="utf-8")
    res = runner.invoke(app, ["case", "import", cid, str(src), "--format", "bank-csv:lloyds"])
    assert res.exit_code != 0
    out = _flat(res.output)
    assert "import refused: line 3: Balance" in out, res.output
    assert SENTINEL not in res.output
    assert not (folder / "statements" / "main.csv").exists()
