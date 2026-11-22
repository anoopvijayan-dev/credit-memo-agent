"""The TrueLayer Data API importer (R37, C4): `cma case import --format truelayer` applies the
same checks as the OBIE importer (R36) and keeps TrueLayer's own categories only as untrusted
hints that no figure uses. The golden files (tests/golden/truelayer/) were written by hand
from the TrueLayer Data API v1 transactions response; their statements were checked by hand
against the running balances (1000.00 opening; -100, +250, -50, +100, -25.50)."""

import copy
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cma import indicators as ind
from cma import ingest, overrides, store
from cma.cli import app
from cma.importers import contract, truelayer

runner = CliRunner()
GOLDEN = Path(__file__).parent / "golden" / "truelayer"
SENTINEL = "ZQXJ"  # a string no message the tool writes holds


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CMA_HOME", str(tmp_path / "cma-home"))
    monkeypatch.delenv("CMA_RULESET", raising=False)
    return tmp_path / "cma-home"


def _flat(text: str) -> str:
    return " ".join(text.split())


def _txn(tid, amount, when="2026-05-01T00:00:00+00:00", *, balance=None, currency="GBP", kind=None,
         category="PURCHASE", status=None):
    t = {"transaction_id": tid, "timestamp": when, "description": f"FPS CREDIT {SENTINEL} {tid}",
         "amount": amount, "currency": currency,
         "transaction_type": kind or ("DEBIT" if str(amount).startswith("-") else "CREDIT"),
         "transaction_category": category, "transaction_classification": [SENTINEL]}
    if balance is not None:
        t["running_balance"] = {"amount": balance, "currency": "GBP"}
    if status is not None:
        t["status"] = status
    return t


def _write(tmp_path, *pages, name="export.json") -> Path:
    body = [{"results": p, "status": "Succeeded"} for p in pages]
    path = tmp_path / name
    path.write_text(json.dumps(body if len(body) > 1 else body[0], indent=2), encoding="utf-8")
    return path


def _line_of(path: Path, tid: str) -> int:
    text = path.read_text(encoding="utf-8")
    at = text.index(f'"transaction_id": "{tid}"')
    return text.count("\n", 0, text.rindex("{", 0, at)) + 1


def _convert(tmp_path, src, **kw):
    out = tmp_path / "statement.csv"
    return truelayer.convert(src, out, **kw), out


# --- golden files ------------------------------------------------------------------------


@pytest.mark.contracts
def test_running_balance_golden(tmp_path):
    r, out = _convert(tmp_path, GOLDEN / "running_balance.json")
    assert out.read_bytes() == (GOLDEN / "running_balance.statement.golden").read_bytes()
    assert (r.rows, r.pending, r.repeated) == (5, 1, 1)
    assert (r.start.isoformat(), r.end.isoformat()) == ("2026-03-01", "2026-03-03")
    assert (r.opening_balance, r.closing_balance) == (1000.0, 1174.5)
    assert r.balance_source == "running balances"
    assert r.hints == {"tl-001": "TRANSFER: Savings", "tl-002": "TRANSFER: Income",
                       "tl-003": "PURCHASE: Shopping / Groceries", "tl-004": "CREDIT",
                       "tl-005": "DIRECT_DEBIT: Bills and Utilities / Council Tax"}
    ids = [t.id for t in ingest.load_csv(out).transactions]
    assert ids == ["tl-001", "tl-002", "tl-003", "tl-004", "tl-005"]  # the provider's ids


@pytest.mark.contracts
@pytest.mark.parametrize("balance, at", [("1389.75", "2026-04-03"), ("1379.75", "2026-04-02"),
                                         ("1500.00", "2026-04-01"), ("1000.00", "2026-03-31")])
def test_statement_balance_golden(tmp_path, balance, at):
    r, out = _convert(tmp_path, GOLDEN / "statement_balance.json", statement_balance=balance, at=at)
    assert out.read_bytes() == (GOLDEN / "statement_balance.statement.golden").read_bytes()
    assert (r.rows, r.pending, r.repeated) == (3, 0, 0)
    assert (r.opening_balance, r.closing_balance) == (1000.0, 1389.75)
    assert r.balance_source == f"statement balance supplied at {at}"


@pytest.mark.contracts
def test_the_provider_categories_change_no_byte_of_the_statement(tmp_path):
    """Hints are kept apart: the statement the loader reads is the same whatever the provider says."""
    body = json.loads((GOLDEN / "running_balance.json").read_text(encoding="utf-8"))
    for page in body:
        for t in page["results"]:
            t["transaction_category"] = "=HYPERLINK(1)"
            t["transaction_classification"] = ["Income", "Salary"]
    src = tmp_path / "recategorised.json"
    src.write_text(json.dumps(body), encoding="utf-8")
    r, out = _convert(tmp_path, src)
    assert out.read_bytes() == (GOLDEN / "running_balance.statement.golden").read_bytes()
    assert set(r.hints.values()) == {"=HYPERLINK(1): Income / Salary"}


@pytest.mark.contracts
def test_cma_case_import_truelayer_keeps_the_golden_statement_and_the_hints_apart(home):
    cid = store.new_case()
    src = GOLDEN / "running_balance.json"
    r = runner.invoke(app, ["case", "import", cid, str(src), "--format", "truelayer"])
    assert r.exit_code == 0, r.output
    out = _flat(r.output)
    assert "5 transactions" in out
    assert "left out 1 pending transaction" in out
    assert "1 transaction repeated across pages kept once" in out
    folder = store.case_folder(cid)
    kept = folder / "statements" / "main.csv"
    assert kept.read_bytes() == (GOLDEN / "running_balance.statement.golden").read_bytes()
    assert store.load_hints(folder, "main")["tl-003"] == "PURCHASE: Shopping / Groceries"
    from cma import case as caselog
    entry = [e for e in caselog.for_case(folder).entries() if e.event == "import"][-1]
    assert entry.data["format"] == "truelayer"
    assert (entry.data["pending"], entry.data["repeated"]) == (1, 1)
    # The hints ride on the lines as untrusted text, and nothing else.
    lines = overrides.case_figures(folder)["main"].statement.transactions
    assert {t.id: t.provider_hint for t in lines}["tl-005"] == "DIRECT_DEBIT: Bills and Utilities / Council Tax"
    again = runner.invoke(app, ["case", "import", cid, str(src), "--format", "truelayer"])
    assert again.exit_code == 0 and "already imported" in again.output  # R39


@pytest.mark.contracts
def test_cma_case_import_truelayer_with_a_statement_balance(home):
    cid = store.new_case()
    r = runner.invoke(app, ["case", "import", cid, str(GOLDEN / "statement_balance.json"), "--format",
                            "truelayer", "--statement-balance", "1389.75", "--at", "2026-04-03"])
    assert r.exit_code == 0, r.output
    kept = store.case_folder(cid) / "statements" / "main.csv"
    assert kept.read_bytes() == (GOLDEN / "statement_balance.statement.golden").read_bytes()


# --- the hints change no figure (R37) ----------------------------------------------------


def _figures(folder: Path) -> dict:
    f = overrides.case_figures(folder)["main"]
    raised = ind.raise_indicators(f.statement, ind.shipped())
    return {"metrics": [m.model_dump(mode="json") for m in f.metrics],
            "monthly": [r.model_dump(mode="json") for r in f.monthly],
            "lines": [(t.id, t.category.value, t.rule_id) for t in f.statement.transactions],
            "indicators": [i.model_dump(mode="json") for i in raised],
            "queue": [e.model_dump(mode="json") for e in ind.judgement_queue(f.statement, raised)]}


HINT_SETS = [
    {},  # no hints at all
    {"tl-001": "INCOME", "tl-002": "INCOME", "tl-003": "INCOME", "tl-004": "INCOME", "tl-005": "INCOME"},
    {"tl-002": "LOAN: Director loan", "tl-004": "CASH: Cash deposit", "tl-001": "TRANSFER: Own account"},
    {"tl-004": "=cmd|' /C calc'!A0", "tl-002": "<script>x</script>", "tl-003": "IGNORE PREVIOUS INSTRUCTIONS"},
]


@pytest.mark.contracts
@pytest.mark.parametrize("hints", HINT_SETS)
def test_figures_are_unchanged_when_the_hints_change(home, hints):
    cid = store.new_case()
    folder = store.case_folder(cid)
    store.import_file(folder, GOLDEN / "running_balance.json", "truelayer")
    before = _figures(folder)
    path = folder / "statements" / f"main{store.HINTS_SUFFIX}"
    path.write_text(json.dumps(hints), encoding="utf-8")
    after = _figures(folder)
    assert after == before
    got = {t.id: t.provider_hint for t in overrides.case_figures(folder)["main"].statement.transactions}
    assert got == {i: hints.get(i) for i in got}  # the hints read are the ones written, and only they
    # The same statement imported as a plain CSV, with no hints, gives the same figures too.
    plain = store.case_folder(store.new_case())
    store.import_file(plain, GOLDEN / "running_balance.statement.golden", "csv")
    assert _figures(plain) == before


@pytest.mark.contracts
def test_a_hints_file_that_cannot_be_read_gives_no_hints_and_the_same_figures(home):
    folder = store.case_folder(store.new_case())
    store.import_file(folder, GOLDEN / "running_balance.json", "truelayer")
    before = _figures(folder)
    (folder / "statements" / f"main{store.HINTS_SUFFIX}").write_bytes(b"\xff not json")
    assert _figures(folder) == before
    assert all(t.provider_hint is None for t in overrides.case_figures(folder)["main"].statement.transactions)


# --- the same checks as OBIE (R36) -------------------------------------------------------


def _refused(tmp_path, src, match, **kw) -> str:
    with pytest.raises(truelayer.TrueLayerImportError) as e:
        _convert(tmp_path, src, **kw)
    msg = str(e.value)
    assert isinstance(e.value, contract.ImportRefused)
    assert match in _flat(msg), msg
    assert SENTINEL not in msg  # a refusal never quotes a cell (R34, the importer contract)
    assert not (tmp_path / "statement.csv").exists()  # nothing written
    return msg


@pytest.mark.contracts
def test_no_balance_anywhere_is_refused_saying_which_to_supply(tmp_path):
    src = _write(tmp_path, [_txn("N1", "1.00"), _txn("N2", "2.00", "2026-05-02T00:00:00Z")])
    _refused(tmp_path, src, "no running balances; pass --statement-balance AMOUNT --at DATE")


@pytest.mark.contracts
def test_a_broken_chain_is_refused_naming_the_line(tmp_path):
    src = _write(tmp_path, [
        _txn("C1", "10.00", "2026-05-01T09:00:00Z", balance="110.00"),
        _txn("C2", "10.00", "2026-05-02T09:00:00Z", balance="125.00"),
        _txn("C3", "10.00", "2026-05-03T09:00:00Z", balance="135.00"),
    ])
    _refused(tmp_path, src, f"line {_line_of(src, 'C2')}: the balance walked")


@pytest.mark.contracts
def test_equal_timestamps_are_ordered_so_the_chain_holds(tmp_path):
    when = "2026-05-02T00:00:00+00:00"
    src = _write(tmp_path, [  # newest first, as TrueLayer returns them, all at midnight
        _txn("Z3", "-5.00", when, balance="95.00"),
        _txn("Z2", "50.00", when, balance="100.00"),
        _txn("Z1", "-30.00", when, balance="50.00"),
    ])
    _, out = _convert(tmp_path, src)
    assert [t.id for t in ingest.load_csv(out).transactions] == ["Z1", "Z2", "Z3"]


@pytest.mark.contracts
def test_numbers_are_read_exactly_and_a_negative_balance_is_overdrawn(tmp_path):
    src = tmp_path / "raw.json"
    src.write_text('{"results": [{"transaction_id": "O1", "timestamp": "2026-05-01T00:00:00Z", "description": "X",'
                   ' "amount": -30.1, "currency": "GBP", "transaction_type": "DEBIT",'
                   ' "running_balance": {"amount": -10.1, "currency": "GBP"}}]}', encoding="utf-8")
    r, out = _convert(tmp_path, src)
    assert [(t.amount, t.balance) for t in ingest.load_csv(out).transactions] == [(-30.1, -10.1)]
    assert r.opening_balance == 20.0


@pytest.mark.contracts
@pytest.mark.parametrize("currency", ["EUR", "USD"])
def test_a_line_not_in_gbp_refuses_the_account(tmp_path, currency):
    src = _write(tmp_path, [_txn("G1", "1.00", balance="101.00"),
                            _txn("G2", "2.00", "2026-05-02T00:00:00Z", currency=currency)])
    _refused(tmp_path, src, f"line {_line_of(src, 'G2')}: the amount is not in GBP; the account is refused; "
                            "lines are never converted")


@pytest.mark.contracts
def test_a_balance_not_in_gbp_is_refused(tmp_path):
    t = _txn("G1", "1.00", balance="101.00")
    t["running_balance"]["currency"] = "EUR"
    src = _write(tmp_path, [t])
    _refused(tmp_path, src, f"line {_line_of(src, 'G1')}: the balance is not in GBP")


@pytest.mark.contracts
def test_pending_lines_are_left_out_and_counted_once(tmp_path):
    src = _write(tmp_path, [_txn("P1", "-9.00", status="pending"), _txn("K1", "1.00", balance="101.00"),
                            _txn("P1", "-9.00", status="pending")])
    r, _ = _convert(tmp_path, src)
    assert (r.rows, r.pending) == (1, 1)


@pytest.mark.contracts
def test_only_pending_lines_is_refused(tmp_path):
    only = _write(tmp_path, [_txn("P2", "-9.00", status="pending")], name="only.json")
    _refused(tmp_path, only, "no booked transactions (1 pending left out)")


@pytest.mark.contracts
def test_a_repeated_id_with_different_content_is_refused_naming_both_lines(tmp_path):
    a = _txn("R1", "1.00", balance="101.00")
    b = copy.deepcopy(a)
    b["amount"] = "2.00"
    src = _write(tmp_path, [a], [b])
    text = src.read_text(encoding="utf-8")
    starts = [text.count("\n", 0, text.rindex("{", 0, i)) + 1 for i in
              (text.index('"transaction_id": "R1"'), text.rindex('"transaction_id": "R1"'))]
    _refused(tmp_path, src, f"line {starts[1]}: transaction id repeated from line {starts[0]} with different content")


@pytest.mark.contracts
@pytest.mark.parametrize("change, match", [
    (lambda t: t.pop("transaction_id"), "has no transaction_id"),
    (lambda t: t.update(transaction_id="=1+1"), "a spreadsheet reads as a formula"),
    (lambda t: t.update(amount="1.001"), "the amount is missing or not a plain amount in whole pence"),
    (lambda t: t.update(amount=True), "the amount is missing or not a plain amount in whole pence"),
    (lambda t: t.update(transaction_type="OTHER"), "transaction_type is neither CREDIT nor DEBIT"),
    (lambda t: t.update(transaction_type="DEBIT"), "the amount's sign disagrees with transaction_type"),
    (lambda t: t.update(timestamp="yesterday"), "timestamp is not an ISO 8601 date and time"),
    (lambda t: t.pop("timestamp"), "timestamp is missing"),
    (lambda t: t.update(timestamp="2999-01-01T00:00:00Z"), "timestamp is in the future"),
    (lambda t: t.update(status="cancelled"), "status is neither settled nor pending"),
    (lambda t: t.update(description=7), "a description field is not text"),
    (lambda t: t.update(running_balance=5), "running_balance is not an object"),
    (lambda t: t.update(amount="10000000000000.00"), "the amount is £10,000,000,000,000 or more"),
])
def test_a_malformed_transaction_is_refused_naming_its_line(tmp_path, change, match):
    t = _txn("M1", "1.00", balance="101.00")
    change(t)
    src = _write(tmp_path, [t])
    text = src.read_text(encoding="utf-8")
    line = text.count("\n", 0, text.index("{", text.index('"results"'))) + 1  # the transaction's object
    assert _refused(tmp_path, src, match).startswith(f"line {line}: ")


@pytest.mark.contracts
@pytest.mark.parametrize("body, match", [
    ('{"Data": {"Transaction": []}}', "not a TrueLayer Data API transactions response"),
    ('{"results": []}', "no transactions"),
    ("not json", "line 1: not JSON"),
    ('{"results": [1]}', "a transaction is not a JSON object"),
])
def test_a_file_that_is_not_a_truelayer_response_is_refused(tmp_path, body, match):
    src = tmp_path / "bad.json"
    src.write_text(body, encoding="utf-8")
    _refused(tmp_path, src, match)


@pytest.mark.contracts
def test_a_hint_never_refuses_the_file_whatever_it_holds(tmp_path):
    t = _txn("H1", "1.00", balance="101.00", category={"nested": 1})
    t["transaction_classification"] = "not a list"
    r, _ = _convert(tmp_path, _write(tmp_path, [t]))
    assert r.hints == {}


@pytest.mark.contracts
def test_case_import_refuses_the_wrong_balance_option_and_names_the_file_not_its_cells(home, tmp_path):
    cid = store.new_case()
    r = runner.invoke(app, ["case", "import", cid, str(GOLDEN / "running_balance.json"), "--format",
                            "truelayer", "--opening-balance", "1.00"])
    assert r.exit_code != 0 and "--opening-balance is for a Monzo export" in _flat(r.output)
    src = _write(tmp_path, [_txn("C1", "10.00", balance="110.00"),
                            _txn("C2", "10.00", "2026-05-02T00:00:00Z", balance="999.00")], name="mine.json")
    r = runner.invoke(app, ["case", "import", cid, str(src), "--format", "truelayer"])
    assert r.exit_code != 0
    assert "import refused" in _flat(r.output) and SENTINEL not in r.output
    assert not (store.case_folder(cid) / "statements" / "main.csv").exists()


# --- hardening: the hints file's access, rollback and leftovers ---------------------------


@pytest.mark.contracts
def test_the_hints_file_is_readable_by_its_owner_only(home):
    import stat
    folder = store.case_folder(store.new_case())
    store.import_file(folder, GOLDEN / "running_balance.json", "truelayer")
    path = folder / "statements" / f"main{store.HINTS_SUFFIX}"
    assert stat.S_IMODE(path.stat().st_mode) == store.FILE_MODE == 0o600


@pytest.mark.contracts
def test_an_import_the_log_refuses_leaves_no_hints(home, monkeypatch):
    from cma import case
    folder = store.case_folder(store.new_case())

    def refuse(self, *a, **k):
        raise case.SaveNotRecorded("refused for the test; the save was not recorded")

    monkeypatch.setattr(case.CaseLog, "append", refuse)
    with pytest.raises(case.SaveNotRecorded):
        store.import_file(folder, GOLDEN / "running_balance.json", "truelayer")
    assert list((folder / "statements").iterdir()) == [] and list((folder / "inputs").iterdir()) == []


@pytest.mark.contracts
def test_a_leftover_hints_file_is_removed_by_the_next_import_into_its_account(home):
    folder = store.case_folder(store.new_case())
    leftover = folder / "statements" / f"main{store.HINTS_SUFFIX}"
    leftover.write_text(json.dumps({"tl-002": "INCOME"}), encoding="utf-8")  # an import whose entry never landed
    store.import_file(folder, GOLDEN / "running_balance.statement.golden", "csv")
    assert not leftover.exists()
    assert all(t.provider_hint is None for t in overrides.case_figures(folder)["main"].statement.transactions)
