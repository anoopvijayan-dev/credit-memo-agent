"""The importer contract (C4, Interfaces): every importer writes the five-column statement
that ingest.load_csv validates, all importers share one step that walks and verifies the
balance, and refusals name lines, never a cell's value. Used by `cma import monzo` and
`cma case import`. The Monzo golden files were captured from the importer before the
shared step was extracted from it (tests/golden/monzo/)."""

import csv
import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cma import ingest, store
from cma.cli import app
from cma.importers import contract, monzo

runner = CliRunner()
GOLDEN = Path(__file__).parent / "golden" / "monzo"
RESULTS = json.loads((GOLDEN / "results.json").read_text())
SENTINEL = "ZQXJ"  # a string no message the tool writes holds


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CMA_HOME", str(tmp_path / "cma-home"))
    return tmp_path / "cma-home"


def _flat(text: str) -> str:
    return " ".join(text.split())


# --- golden output unchanged -------------------------------------------------------------


@pytest.mark.contracts
@pytest.mark.parametrize("name", sorted(RESULTS))
def test_monzo_golden_output_is_unchanged(tmp_path, name):
    out = tmp_path / "statement.csv"
    r = monzo.convert(GOLDEN / f"{name}_export.golden", out, RESULTS[name]["opening_balance"])
    assert out.read_bytes() == (GOLDEN / f"{name}_statement.golden").read_bytes()
    want = RESULTS[name]["result"]
    assert {**r.__dict__, "start": r.start.isoformat(), "end": r.end.isoformat()} == want
    ingest.load_csv(out)  # the output passes the statement loader


@pytest.mark.contracts
@pytest.mark.parametrize("name", sorted(RESULTS))
def test_cma_import_monzo_writes_the_golden_statement(tmp_path, name):
    out = tmp_path / "statement.csv"
    args = ["import", "monzo", str(GOLDEN / f"{name}_export.golden"), "-o", str(out)]
    if RESULTS[name]["opening_balance"] is not None:
        args += ["--opening-balance", RESULTS[name]["opening_balance"]]
    r = runner.invoke(app, args)
    assert r.exit_code == 0, r.output
    assert out.read_bytes() == (GOLDEN / f"{name}_statement.golden").read_bytes()


@pytest.mark.contracts
@pytest.mark.parametrize("name", sorted(RESULTS))
def test_cma_case_import_keeps_the_golden_statement(home, name):
    cid = store.new_case()
    args = ["case", "import", cid, str(GOLDEN / f"{name}_export.golden"), "--format", "monzo"]
    if RESULTS[name]["opening_balance"] is not None:
        args += ["--opening-balance", RESULTS[name]["opening_balance"]]
    r = runner.invoke(app, args)
    assert r.exit_code == 0, r.output
    kept = store.case_folder(cid) / "statements" / "main.csv"
    assert kept.read_bytes() == (GOLDEN / f"{name}_statement.golden").read_bytes()


# --- the shared balance walk -------------------------------------------------------------


def _line(line, tid, amount, balance=None, d="2026-03-01"):
    return contract.Line(line=line, id=tid, date=date.fromisoformat(d), description=f"FPS CREDIT {tid}",
                         amount=amount, balance=balance)


@pytest.mark.contracts
def test_the_walk_infers_the_opening_balance_and_checks_every_provider_balance():
    lines = [_line(2, "a", 10_00, 60_00), _line(3, "b", -5_00, 55_00), _line(4, "c", 1_00)]
    opening, source = contract.opening_balance(lines, None, column="Monzo's Balance column")
    assert (opening, source) == (50_00, "Monzo's Balance column")
    assert contract.walk(lines, opening, column="Monzo's Balance column") == [60_00, 55_00, 56_00]


@pytest.mark.contracts
def test_the_walk_refuses_a_disagreeing_balance_by_line_without_its_value():
    lines = [_line(2, "a", 10_00, 60_00), _line(7, "tx_ZQXJ", -5_00, 99_99)]
    with pytest.raises(contract.ImportRefused) as e:
        contract.walk(lines, 50_00, column="Monzo's Balance column")
    msg = str(e.value)
    assert msg.startswith("line 7:") and "disagrees with Monzo's Balance column" in msg
    assert "99.99" not in msg and "55.00" not in msg and SENTINEL not in msg


@pytest.mark.contracts
def test_the_walk_refuses_a_running_balance_at_the_limit_by_line():
    lines = [_line(2, "a", 9_999_999_999_999_99), _line(3, "b", 1)]
    with pytest.raises(contract.ImportRefused, match=r"^line 3: the running balance reaches £10,000,000,000,000"):
        contract.walk(lines, 0, column="Monzo's Balance column")


@pytest.mark.contracts
def test_a_supplied_opening_balance_must_agree_with_the_provider():
    lines = [_line(4, "a", 10_00, 60_00)]
    assert contract.opening_balance(lines, 50_00, column="Monzo's Balance column") == (50_00, "opening balance supplied")
    with pytest.raises(contract.ImportRefused, match=r"implies 50\.00 at line 4, the first transaction"):
        contract.opening_balance(lines, 40_00, column="Monzo's Balance column", option="--opening-balance")
    with pytest.raises(contract.ImportRefused, match=r"^line 4: the first transaction has no balance"):
        contract.opening_balance([_line(4, "a", 10_00)], None, column="Monzo's Balance column")


@pytest.mark.contracts
def test_an_output_the_loader_would_refuse_is_never_written(tmp_path):
    """The contract's output passes ingest.load_csv, or nothing is written: a row dated after
    today is refused by the line of the source file it came from."""
    lines = [_line(2, "a", 1_00, d="2026-01-01"), _line(5, f"{SENTINEL}", 1_00, d="2099-01-01")]
    dst = tmp_path / "out.csv"
    with pytest.raises(contract.ImportRefused) as e:
        contract.write_statement(dst, lines, contract.walk(lines, 0, column="x"), label="export.csv")
    assert "line 5" in str(e.value) and SENTINEL not in str(e.value) and "future" not in str(e.value)
    assert not dst.exists()


# --- refusals name lines, never cell values ------------------------------------------------

HEADER = ["Transaction ID", "Date", "Time", "Type", "Name", "Amount", "Currency", "Balance"]


def _export(tmp_path, rows):
    p = tmp_path / "monzo.csv"
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    return p


OK = ["tx_1", "01/03/2026", "09:00:00", "Faster payment", "Client", "10.00", "GBP", "60.00"]
BAD_ROWS = {
    "date": [OK, ["tx_2", f"{SENTINEL}", "", "Card payment", "Shop", "-1.00", "GBP", "59.00"]],
    "amount": [OK, ["tx_2", "02/03/2026", "", "Card payment", "Shop", f"{SENTINEL}", "GBP", "59.00"]],
    "decimals": [OK, ["tx_2", "02/03/2026", "", "Card payment", "Shop", "-1.0007", "GBP", "59.00"]],
    "amount at limit": [OK, ["tx_2", "02/03/2026", "", "Card payment", "Shop", "10000000000000.00", "GBP", ""]],
    "balance": [OK, ["tx_2", "02/03/2026", "", "Card payment", "Shop", "-1.00", "GBP", f"{SENTINEL}"]],
    "currency": [OK, ["tx_2", "02/03/2026", "", "Card payment", "Shop", "-1.00", f"{SENTINEL}9", "59.00"]],
    "duplicate id": [[f"tx_{SENTINEL}", *OK[1:]], [f"tx_{SENTINEL}", "02/03/2026", "", "Card", "Shop", "-1.00",
                                                     "GBP", "59.00"]],
    "disagreeing balance": [OK, ["tx_2", "02/03/2026", "", "Card payment", "Shop", "-1.00", "GBP", "1234.56"]],
    "first balance missing": [[*OK[:7], ""], ["tx_2", "02/03/2026", "", "Card payment", "Shop", "-1.00", "GBP", ""]],
}
CELLS = {"date": SENTINEL, "amount": SENTINEL, "decimals": "1.0007", "amount at limit": "10000000000000.00",
         "balance": SENTINEL, "currency": SENTINEL, "duplicate id": SENTINEL, "disagreeing balance": "1234.56",
         "first balance missing": "tx_1"}
LINE = {"date": 3, "amount": 3, "decimals": 3, "amount at limit": 3, "balance": 3, "currency": 3,
        "duplicate id": 3, "disagreeing balance": 3, "first balance missing": 2}


@pytest.mark.contracts
@pytest.mark.parametrize("case", sorted(BAD_ROWS))
def test_a_monzo_refusal_names_the_line_never_the_cell(tmp_path, case):
    with pytest.raises(contract.ImportRefused) as e:
        monzo.convert(_export(tmp_path, BAD_ROWS[case]), tmp_path / "o.csv")
    msg = str(e.value)
    assert f"line {LINE[case]}" in msg, msg
    assert CELLS[case] not in msg, msg
    assert not (tmp_path / "o.csv").exists()


@pytest.mark.contracts
@pytest.mark.parametrize("case", sorted(BAD_ROWS))
def test_cma_import_monzo_and_cma_case_import_refuse_by_line(home, tmp_path, case):
    src = _export(tmp_path, BAD_ROWS[case])
    r = runner.invoke(app, ["import", "monzo", str(src), "-o", str(tmp_path / "o.csv")])
    assert r.exit_code == 2 and f"line {LINE[case]}" in _flat(r.output) and CELLS[case] not in r.output, r.output
    cid = store.new_case()
    r = runner.invoke(app, ["case", "import", cid, str(src), "--format", "monzo"])
    assert r.exit_code == 2 and f"line {LINE[case]}" in _flat(r.output) and CELLS[case] not in r.output, r.output


@pytest.mark.contracts
def test_cma_case_import_of_a_statement_csv_refuses_by_line_never_the_cell(home, tmp_path):
    src = tmp_path / "statement.csv"
    src.write_text("id,date,description,amount,balance\n"
                   "L1,2026-03-01,FPS CREDIT CLIENT,10.00,10.00\n"
                   f"L2,2026-03-02,CARD SHOP,{SENTINEL},9.00\n"
                   f"{SENTINEL},2026-03-0{SENTINEL[0]},CARD SHOP,-1.00,8.00\n")
    cid = store.new_case()
    with pytest.raises(store.CaseStoreError) as e:
        store.import_file(store.case_folder(cid), src, "csv")
    msg = str(e.value)
    assert "line 3" in msg and "line 4" in msg and SENTINEL not in msg, msg
    r = runner.invoke(app, ["case", "import", cid, str(src), "--format", "csv"])
    assert r.exit_code == 2 and SENTINEL not in r.output, r.output


@pytest.mark.contracts
def test_monzo_import_error_is_an_import_refusal():
    assert issubclass(monzo.MonzoImportError, contract.ImportRefused)
