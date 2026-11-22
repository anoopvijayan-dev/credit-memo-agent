"""The OBIE Account and Transaction API v3.1 importer (R36, C4): `cma case import --format
openbanking` keeps only booked GBP transactions, keeps the provider's transaction ids,
rebuilds the running balance from the per-line balances or from a statement balance the
person supplies, refuses the file if the balance chain breaks anywhere, counts the pending
lines it leaves out, and keeps a transaction a paged export repeats once. The golden files
(tests/golden/openbanking/) were written by hand from the OBIE v3.1 transaction schema."""

import copy
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cma import ingest, store
from cma.cli import app
from cma.importers import contract, openbanking

runner = CliRunner()
GOLDEN = Path(__file__).parent / "golden" / "openbanking"
SENTINEL = "ZQXJ"  # a string no message the tool writes holds


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CMA_HOME", str(tmp_path / "cma-home"))
    return tmp_path / "cma-home"


def _flat(text: str) -> str:
    return " ".join(text.split())


def _txn(tid, amount, indicator="Credit", when="2026-05-01T10:00:00+00:00", *, balance=None,
         status="Booked", currency="GBP", info=None, account="ACC1"):
    t = {"AccountId": account, "TransactionId": tid,
         "Amount": {"Amount": amount, "Currency": currency},
         "CreditDebitIndicator": indicator, "Status": status, "BookingDateTime": when,
         "TransactionInformation": info if info is not None else f"FPS CREDIT {SENTINEL} {tid}"}
    if balance is not None:
        sign = "Debit" if balance.startswith("-") else "Credit"
        t["Balance"] = {"Amount": {"Amount": balance.lstrip("-"), "Currency": "GBP"},
                        "CreditDebitIndicator": sign, "Type": "InterimBooked"}
    return t


def _write(tmp_path, *pages, name="export.json") -> Path:
    body = [{"Data": {"Transaction": p}, "Links": {}, "Meta": {}} for p in pages]
    path = tmp_path / name
    path.write_text(json.dumps(body if len(body) > 1 else body[0], indent=2), encoding="utf-8")
    return path


def _line_of(path: Path, tid: str, occurrence: int = 1) -> int:
    """The line of the file the transaction object holding `tid` starts on."""
    text = path.read_text(encoding="utf-8")
    at = -1
    for _ in range(occurrence):
        at = text.index(f'"TransactionId": "{tid}"', at + 1)
    return text.count("\n", 0, text.rindex("{", 0, at)) + 1


def _convert(tmp_path, src, **kw):
    out = tmp_path / "statement.csv"
    return openbanking.convert(src, out, **kw), out


# --- golden files ------------------------------------------------------------------------


@pytest.mark.contracts
def test_per_line_balances_golden(tmp_path):
    r, out = _convert(tmp_path, GOLDEN / "per_line_balances.json")
    assert out.read_bytes() == (GOLDEN / "per_line_balances.statement.golden").read_bytes()
    assert (r.rows, r.pending, r.repeated) == (5, 1, 1)
    assert (r.start.isoformat(), r.end.isoformat()) == ("2026-03-01", "2026-03-03")
    assert (r.opening_balance, r.closing_balance) == (1000.0, 1174.5)
    assert r.balance_source == "per-line balances"
    ingest.load_csv(out)  # the output passes the statement loader


@pytest.mark.contracts
@pytest.mark.parametrize("balance, at", [("1389.75", "2026-04-03"), ("1379.75", "2026-04-02"),
                                         ("1500.00", "2026-04-01"), ("1000.00", "2026-03-31")])
def test_statement_balance_golden(tmp_path, balance, at):
    r, out = _convert(tmp_path, GOLDEN / "statement_balance.json", statement_balance=balance, at=at)
    assert out.read_bytes() == (GOLDEN / "statement_balance.statement.golden").read_bytes()
    assert (r.rows, r.pending, r.repeated) == (3, 1, 0)
    assert (r.opening_balance, r.closing_balance) == (1000.0, 1389.75)
    assert r.balance_source == f"statement balance supplied at {at}"


@pytest.mark.contracts
def test_cma_case_import_openbanking_keeps_the_golden_statement_and_counts_pending(home):
    cid = store.new_case()
    src = GOLDEN / "per_line_balances.json"
    r = runner.invoke(app, ["case", "import", cid, str(src), "--format", "openbanking"])
    assert r.exit_code == 0, r.output
    out = _flat(r.output)
    assert "5 transactions" in out
    assert "left out 1 pending transaction" in out
    assert "1 transaction repeated across pages kept once" in out
    folder = store.case_folder(cid)
    kept = folder / "statements" / "main.csv"
    assert kept.read_bytes() == (GOLDEN / "per_line_balances.statement.golden").read_bytes()
    from cma import case as caselog
    entry = [e for e in caselog.for_case(folder).entries() if e.event == "import"][-1]
    assert entry.data["format"] == "openbanking"
    assert (entry.data["pending"], entry.data["repeated"]) == (1, 1)
    # Imported again: the case is unchanged (R39).
    again = runner.invoke(app, ["case", "import", cid, str(src), "--format", "openbanking"])
    assert again.exit_code == 0 and "already imported" in again.output


@pytest.mark.contracts
def test_cma_case_import_openbanking_with_a_statement_balance(home):
    cid = store.new_case()
    r = runner.invoke(app, ["case", "import", cid, str(GOLDEN / "statement_balance.json"), "--format",
                            "openbanking", "--statement-balance", "1389.75", "--at", "2026-04-03"])
    assert r.exit_code == 0, r.output
    kept = store.case_folder(cid) / "statements" / "main.csv"
    assert kept.read_bytes() == (GOLDEN / "statement_balance.statement.golden").read_bytes()


@pytest.mark.contracts
def test_provider_ids_are_the_line_ids(tmp_path):
    _, out = _convert(tmp_path, GOLDEN / "per_line_balances.json")
    ids = [t.id for t in ingest.load_csv(out).transactions]
    assert ids == ["T1", "T2", "T3", "T4", "T5"]


@pytest.mark.contracts
def test_equal_timestamps_are_ordered_so_the_chain_holds(tmp_path):
    when = "2026-05-02T00:00:00+00:00"
    src = _write(tmp_path, [
        _txn("Z3", "5.00", "Debit", when, balance="95.00"),
        _txn("Z2", "50.00", "Credit", when, balance="100.00"),
        _txn("Z1", "30.00", "Debit", when, balance="50.00"),
    ])
    _, out = _convert(tmp_path, src)
    assert [t.id for t in ingest.load_csv(out).transactions] == ["Z1", "Z2", "Z3"]


@pytest.mark.contracts
def test_lines_without_a_balance_are_walked_between_lines_with_one(tmp_path):
    src = _write(tmp_path, [
        _txn("B1", "10.00", "Credit", "2026-05-01T09:00:00Z"),
        _txn("B2", "4.00", "Debit", "2026-05-01T10:00:00Z", balance="106.00"),
        _txn("B3", "1.00", "Debit", "2026-05-02T10:00:00Z"),
    ])
    r, out = _convert(tmp_path, src)
    assert [t.balance for t in ingest.load_csv(out).transactions] == [110.0, 106.0, 105.0]
    assert r.opening_balance == 100.0


@pytest.mark.contracts
def test_an_overdrawn_per_line_balance_is_negative(tmp_path):
    src = _write(tmp_path, [
        _txn("O1", "30.00", "Debit", "2026-05-01T09:00:00Z", balance="-10.00"),
        _txn("O2", "15.00", "Credit", "2026-05-02T09:00:00Z", balance="5.00"),
    ])
    r, out = _convert(tmp_path, src)
    assert [t.balance for t in ingest.load_csv(out).transactions] == [-10.0, 5.0]
    assert r.opening_balance == 20.0


@pytest.mark.contracts
def test_pages_one_after_another_are_read_as_one_export(tmp_path):
    a = {"Data": {"Transaction": [_txn("J1", "1.00", balance="101.00")]}}
    b = {"Data": {"Transaction": [_txn("J2", "2.00", when="2026-05-02T10:00:00Z", balance="103.00")]}}
    src = tmp_path / "pages.json"
    src.write_text(json.dumps(a) + "\n" + json.dumps(b) + "\n", encoding="utf-8")
    r, _ = _convert(tmp_path, src)
    assert r.rows == 2


# --- refusals ----------------------------------------------------------------------------


def _refused(tmp_path, src, match, **kw) -> str:
    with pytest.raises(contract.ImportRefused) as e:
        _convert(tmp_path, src, **kw)
    msg = str(e.value)
    assert match in _flat(msg), msg
    assert SENTINEL not in msg  # a refusal never quotes a cell (R34, the importer contract)
    assert not (tmp_path / "statement.csv").exists()  # nothing written
    return msg


@pytest.mark.contracts
def test_no_balance_anywhere_is_refused_saying_which_to_supply(tmp_path):
    src = _write(tmp_path, [_txn("N1", "1.00"), _txn("N2", "2.00", when="2026-05-02T10:00:00Z")])
    _refused(tmp_path, src, "no per-line balances; pass --statement-balance AMOUNT --at DATE")


@pytest.mark.contracts
def test_a_broken_chain_is_refused_naming_the_line(tmp_path):
    src = _write(tmp_path, [
        _txn("C1", "10.00", when="2026-05-01T09:00:00Z", balance="110.00"),
        _txn("C2", "10.00", when="2026-05-02T09:00:00Z", balance="125.00"),
        _txn("C3", "10.00", when="2026-05-03T09:00:00Z", balance="135.00"),
    ])
    _refused(tmp_path, src, f"line {_line_of(src, 'C2')}: the balance walked")


@pytest.mark.contracts
def test_equal_timestamps_that_no_order_chains_are_refused_naming_the_first_break(tmp_path):
    when = "2026-05-02T00:00:00+00:00"
    src = _write(tmp_path, [
        _txn("E0", "1.00", "Credit", "2026-05-01T00:00:00Z", balance="101.00"),
        _txn("E1", "5.00", "Credit", when, balance="106.00"),
        _txn("E2", "5.00", "Credit", when, balance="200.00"),
    ])
    _refused(tmp_path, src, f"line {_line_of(src, 'E2')}: no order of the transactions booked at the same time "
                            "keeps the balance chain")


@pytest.mark.contracts
def test_a_statement_balance_that_disagrees_with_per_line_balances_is_refused(tmp_path):
    _refused(tmp_path, GOLDEN / "per_line_balances.json", "--statement-balance 1.00 at 2026-03-02 disagrees",
             statement_balance="1.00", at="2026-03-02")


@pytest.mark.contracts
def test_a_statement_balance_needs_its_date_and_a_date_in_the_period(tmp_path):
    src = GOLDEN / "statement_balance.json"
    _refused(tmp_path, src, "--statement-balance needs --at", statement_balance="1.00")
    _refused(tmp_path, src, "--at needs --statement-balance", at="2026-04-01")
    _refused(tmp_path, src, "--at 2026-05-01 is outside", statement_balance="1.00", at="2026-05-01")
    _refused(tmp_path, src, "--at is not a date like 2026-04-30", statement_balance="1.00", at="tomorrow")
    _refused(tmp_path, src, "--statement-balance is not a plain amount", statement_balance="1,000", at="2026-04-01")


@pytest.mark.contracts
@pytest.mark.parametrize("currency", ["EUR", "USD"])
def test_a_line_not_in_gbp_refuses_the_account(tmp_path, currency):
    src = _write(tmp_path, [_txn("G1", "1.00", balance="101.00"),
                            _txn("G2", "2.00", when="2026-05-02T10:00:00Z", currency=currency)])
    _refused(tmp_path, src, f"line {_line_of(src, 'G2')}: the amount is not in GBP; the account is refused; lines are never converted")


@pytest.mark.contracts
def test_a_balance_not_in_gbp_is_refused(tmp_path):
    t = _txn("G1", "1.00", balance="101.00")
    t["Balance"]["Amount"]["Currency"] = "EUR"
    src = _write(tmp_path, [t])
    _refused(tmp_path, src, f"line {_line_of(src, 'G1')}: the balance is not in GBP")


@pytest.mark.contracts
def test_a_repeated_id_with_different_content_is_refused_naming_both_lines(tmp_path):
    a = _txn("D1", "1.00", balance="101.00")
    b = copy.deepcopy(a)
    b["Amount"]["Amount"] = "2.00"
    src = _write(tmp_path, [a], [b])
    _refused(tmp_path, src, f"line {_line_of(src, 'D1', 2)}: transaction id repeated from line "
                            f"{_line_of(src, 'D1')} with different content")


@pytest.mark.contracts
def test_pending_lines_are_left_out_and_counted(tmp_path):
    src = _write(tmp_path, [_txn("K1", "1.00", balance="101.00"),
                            _txn("K2", "50.00", "Debit", status="Pending"),
                            _txn("K3", "7.00", status="Pending", currency="EUR")])
    r, out = _convert(tmp_path, src)
    assert (r.rows, r.pending) == (1, 2)
    assert [t.id for t in ingest.load_csv(out).transactions] == ["K1"]


@pytest.mark.contracts
def test_only_pending_lines_is_refused(tmp_path):
    src = _write(tmp_path, [_txn("K2", "50.00", "Debit", status="Pending")])
    _refused(tmp_path, src, "no booked transactions (1 pending left out)")


@pytest.mark.contracts
@pytest.mark.parametrize("change, match", [
    (lambda t: t.update(Status="Rejected"), "Status is neither Booked nor Pending"),
    (lambda t: t.pop("TransactionId"), "a booked transaction has no TransactionId"),
    (lambda t: t.update(TransactionId=f"={SENTINEL}"), "TransactionId starts with a character"),
    (lambda t: t["Amount"].update(Amount="1.005"), "the amount is not a plain amount"),
    (lambda t: t["Amount"].update(Amount="-1.00"), "the amount is not a plain amount"),
    (lambda t: t.update(CreditDebitIndicator="Both"), "CreditDebitIndicator is neither Credit nor Debit"),
    (lambda t: t.update(BookingDateTime=SENTINEL), "BookingDateTime is not an ISO 8601 date"),
    (lambda t: t.update(BookingDateTime="2999-01-01T00:00:00Z"), "is in the future"),
    (lambda t: t["Amount"].update(Amount="10000000000000.00"), "£10,000,000,000,000 or more"),
])
def test_a_malformed_transaction_is_refused_naming_its_line(tmp_path, change, match):
    t = _txn("M1", "1.00", balance="101.00")
    change(t)
    src = _write(tmp_path, [t])
    msg = _refused(tmp_path, src, match)
    assert "line 4:" in msg  # the line the transaction's object starts on


@pytest.mark.contracts
def test_two_accounts_in_one_file_are_refused(tmp_path):
    src = _write(tmp_path, [_txn("A1", "1.00", balance="101.00"),
                            _txn("A2", "1.00", when="2026-05-02T10:00:00Z", balance="102.00", account="OTHER")])
    _refused(tmp_path, src, f"line {_line_of(src, 'A2')}: another account's transaction")


@pytest.mark.contracts
@pytest.mark.parametrize("body, match", [
    (b"not json", "line 1: not JSON"),
    (b'{"Data": {"Transaction": [\n  {"TransactionId": "x"\n', "not JSON"),
    (b'{"Data": {"Balance": []}}', "not an OBIE v3.1 transactions response"),
    (b'{"Data": {"Transaction": []}}', "no transactions"),
    (b'\xff\xfe', "not UTF-8"),
    (b'{"Data": {"Transaction": [{"TransactionId": "a", "TransactionId": "b"}]}}', "a key given more than once"),
])
def test_a_file_that_is_not_an_obie_transactions_response_is_refused(tmp_path, body, match):
    src = tmp_path / "export.json"
    src.write_bytes(body)
    _refused(tmp_path, src, match)


@pytest.mark.contracts
def test_case_import_refuses_balance_options_for_other_formats(home, tmp_path):
    cid = store.new_case()
    r = runner.invoke(app, ["case", "import", cid, str(GOLDEN / "per_line_balances.json"), "--format",
                            "openbanking", "--opening-balance", "1.00"])
    assert r.exit_code == 2 and "--statement-balance" in _flat(r.output)
    src = tmp_path / "s.csv"
    src.write_text("id,date,description,amount,balance\nx,2026-01-01,A,1.00,1.00\n")
    r = runner.invoke(app, ["case", "import", cid, str(src), "--format", "csv",
                            "--statement-balance", "1.00", "--at", "2026-01-01"])
    assert r.exit_code == 2 and "Open Banking" in _flat(r.output)


@pytest.mark.contracts
def test_case_import_refusal_names_the_file_not_its_cells(home, tmp_path):
    cid = store.new_case()
    src = _write(tmp_path, [_txn("N1", "1.00")])
    r = runner.invoke(app, ["case", "import", cid, str(src), "--format", "openbanking"])
    assert r.exit_code == 2
    assert "--statement-balance" in _flat(r.output) and SENTINEL not in r.output
    assert not (store.case_folder(cid) / "statements" / "main.csv").exists()


@pytest.mark.contracts
def test_a_pending_transaction_a_page_repeats_is_counted_once(tmp_path):
    p = _txn("K2", "50.00", "Debit", status="Pending")
    src = _write(tmp_path, [_txn("K1", "1.00", balance="101.00"), p], [copy.deepcopy(p)])
    r, _ = _convert(tmp_path, src)
    assert (r.rows, r.pending, r.repeated) == (1, 1, 0)


@pytest.mark.contracts
def test_an_available_balance_is_not_read_as_the_booked_balance(tmp_path):
    t = _txn("V1", "1.00", balance="999.00")
    t["Balance"]["Type"] = "InterimAvailable"  # may hold pending lines or an overdraft limit
    src = _write(tmp_path, [t])
    _refused(tmp_path, src, "no per-line balances")
    r, _ = _convert(tmp_path, src, statement_balance="101.00", at="2026-05-01")
    assert r.opening_balance == 100.0


def _chain(n, when="2026-01-01"):
    return [_txn(f"S{i}", "1.00", when=when, balance=f"{101 + i}.00") for i in range(n)]


@pytest.mark.contracts
@pytest.mark.parametrize("n, reverse", [(1500, False), (1500, True), (500, True)])
def test_a_large_group_booked_at_one_moment_is_ordered_whatever_order_the_file_wrote(tmp_path, n, reverse):
    # A date-only export puts a whole day at midnight; such a group chains in one order.
    lines = _chain(n)
    src = _write(tmp_path, lines[::-1] if reverse else lines)
    r, out = _convert(tmp_path, src)
    assert r.rows == n
    assert [t.id for t in ingest.load_csv(out).transactions] == [f"S{i}" for i in range(n)]


@pytest.mark.contracts
def test_a_large_group_that_no_order_chains_is_refused_not_crashed(tmp_path):
    lines = _chain(1500)
    lines[700]["Balance"]["Amount"]["Amount"] = "99999.00"
    src = _write(tmp_path, lines)
    _refused(tmp_path, src, "no order of the transactions booked at the same time keeps the balance chain")


@pytest.mark.contracts
@pytest.mark.parametrize("loose", [8, 12, 400])
def test_balance_less_lines_ahead_of_the_balanced_line_that_must_come_first_are_ordered(tmp_path, loose):
    # verify-256: lines with no balance are interchangeable, so the search must not walk their
    # orderings before it reaches the balanced line that has to come first.
    when = "2026-05-02T00:00:00+00:00"
    group = [_txn(f"L{i}", "1.00", when=when) for i in range(loose)] + [
        _txn("A", "1.00", when=when, balance="101.00")]
    src = _write(tmp_path, [_txn("E0", "1.00", when="2026-05-01T00:00:00+00:00", balance="100.00"), *group])
    r, out = _convert(tmp_path, src)
    ids = [t.id for t in ingest.load_csv(out).transactions]
    assert r.rows == loose + 2 and ids[:2] == ["E0", "A"]
    assert r.closing_balance == 101.0 + loose


@pytest.mark.contracts
def test_mixed_loose_amounts_and_balanced_lines_at_one_moment_are_ordered(tmp_path):
    when = "2026-05-02T00:00:00+00:00"
    loose = [_txn(f"L{i}", f"{1 + i % 3}.00", when=when) for i in range(15)]  # 1, 2 or 3 pounds
    # The balanced lines need 2 and then 5 pounds of loose lines before them.
    balanced = [_txn("B2", "1.00", when=when, balance="108.00"), _txn("B1", "1.00", when=when, balance="103.00")]
    src = _write(tmp_path, [_txn("E0", "1.00", when="2026-05-01T00:00:00+00:00", balance="100.00"),
                            *loose, *balanced])
    r, out = _convert(tmp_path, src)
    assert r.rows == 18 and r.closing_balance == 100.0 + 2 + sum(1 + i % 3 for i in range(15))
    ids = [t.id for t in ingest.load_csv(out).transactions]
    assert ids.index("B1") < ids.index("B2")


@pytest.mark.contracts
def test_a_pending_transaction_booked_on_another_page_is_not_counted_as_left_out(tmp_path):
    # verify-256 minor: a page fetched while it settled shows it pending, another booked.
    src = _write(tmp_path, [_txn("K1", "1.00", balance="101.00"), _txn("X", "5.00", status="Pending")],
                 [_txn("X", "5.00", balance="106.00", when="2026-05-01T11:00:00+00:00")])
    r, _ = _convert(tmp_path, src)
    assert (r.rows, r.pending) == (2, 0)


@pytest.mark.contracts
def test_distinct_loose_amounts_whose_subset_a_balanced_line_needs_are_ordered(tmp_path):
    when = "2026-05-02T00:00:00+00:00"
    pence = [2 ** i for i in range(16)]
    target = sum(pence[::2])  # the loose lines that must come before A
    loose = [_txn(f"L{i}", f"{p / 100:.2f}", when=when) for i, p in enumerate(pence)]
    src = _write(tmp_path, [_txn("E0", "0.01", when="2026-05-01T00:00:00+00:00", balance="0.01"), *loose,
                            _txn("A", "0.01", when=when, balance=f"{(target + 2) / 100:.2f}")])
    r, _ = _convert(tmp_path, src)
    assert r.rows == 18


# --- the order of lines booked at one moment, over generated exports ----------------------
#
# Lines with no balance can go anywhere among lines with one, so finding an order is, in
# general, a subset-sum search: an export mixing lines with and without a balance at one
# moment may be refused at the search limit, saying so, never as a broken chain. A balance on
# every line, on none or on each day's last line is always ordered, and these generated
# exports with a balance on all but a few lines are too; an order is always found or refused
# within bounded time.

def _generated(n, has_balance, seed, days=1, distinct=1000, drop=False):
    """An export of `n` lines over `days` date-only days, every line booked at midnight, in a
    shuffled order; `has_balance(i, last_of_day, rng)` says which lines carry their balance."""
    import random

    rng = random.Random(seed)
    amounts = [rng.randint(-20000, 30000) or 1 for _ in range(distinct)]
    bal, lines = 100_000, []
    for i in range(n):
        a = rng.choice(amounts)
        bal += a
        day = i * days // n
        last = (i + 1) * days // n != day or i == n - 1
        balance = f"{bal / 100:.2f}" if has_balance(i, last, rng) else None
        lines.append(_txn(f"G{i}", f"{abs(a) / 100:.2f}", "Credit" if a > 0 else "Debit",
                          f"2026-04-{1 + day:02d}T00:00:00+00:00", balance=balance))
    if drop:  # a line missing between the first and last line with a balance
        withb = [k for k, t in enumerate(lines) if "Balance" in t]
        del lines[(withb[0] + withb[-1]) // 2]
    rng.shuffle(lines)
    return lines


def _kept_balances(out, lines):
    given = {t["TransactionId"]: t["Balance"] for t in lines if "Balance" in t}
    for t in ingest.load_csv(out).transactions:
        if t.id in given:
            b = given[t.id]
            want = float(b["Amount"]["Amount"]) * (-1 if b["CreditDebitIndicator"] == "Debit" else 1)
            assert t.balance == want, t.id


REAL_SHAPES = {
    "each day's last line": lambda i, last, rng: last,
    "every line": lambda i, last, rng: True,
    "all but a few": lambda i, last, rng: rng.random() > 0.05,
}


@pytest.mark.contracts
@pytest.mark.parametrize("shape", list(REAL_SHAPES))
@pytest.mark.parametrize("n, days, distinct", [(20, 1, 1000), (200, 1, 1000), (200, 5, 3), (100, 4, 10)])
@pytest.mark.parametrize("seed", [0, 1])
def test_real_balance_shapes_are_always_ordered_whatever_order_the_file_wrote(tmp_path, shape, n, days, distinct,
                                                                             seed):
    import time

    lines = _generated(n, REAL_SHAPES[shape], seed, days, distinct)
    src = _write(tmp_path, lines)
    start = time.perf_counter()
    r, out = _convert(tmp_path, src)
    assert time.perf_counter() - start < 10
    assert r.rows == n
    _kept_balances(out, lines)  # every per-line balance kept, and the loader's chain check passed


@pytest.mark.contracts
@pytest.mark.parametrize("shape", ["every line", "all but a few"])
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_a_missing_line_among_balanced_lines_at_one_moment_is_refused(tmp_path, shape, seed):
    lines = _generated(60, REAL_SHAPES[shape], seed, days=1, distinct=1000, drop=True)
    src = _write(tmp_path, lines)
    with pytest.raises(openbanking.OpenBankingImportError, match="keeps the balance chain"):
        openbanking.convert(src, tmp_path / "out.csv")


@pytest.mark.contracts
@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("p", [0.2, 0.5])
def test_a_heavy_mix_at_one_moment_is_ordered_or_refused_at_the_limit_never_as_a_break(tmp_path, seed, p):
    """A valid export mixing lines with and without a balance at one moment is either ordered
    or refused saying the search limit was reached; never as a broken chain, and in bounded time."""
    import time

    lines = _generated(40, lambda i, last, rng: rng.random() < p, seed, days=1, distinct=1000)
    if not any("Balance" in t for t in lines):
        pytest.skip("no line carries a balance: nothing to order")
    src = _write(tmp_path, lines)
    start = time.perf_counter()
    try:
        _r, out = _convert(tmp_path, src)
        _kept_balances(out, lines)
    except openbanking.OpenBankingImportError as e:
        assert "search limit" in str(e) and "it may be valid" in str(e), str(e)
    assert time.perf_counter() - start < 30  # bounded: the search ends at its limit


@pytest.mark.contracts
def test_a_mixed_first_group_followed_by_single_balanced_lines_starts_where_they_say(tmp_path):
    # verify-257: a later group of one balanced line must chain from the start tried; a start
    # that put A before the balance-less line was accepted and the valid file refused.
    src = _write(tmp_path, [
        _txn("L0", "5.00", when="2026-05-01T00:00:00Z"),
        _txn("A", "1.00", when="2026-05-01T00:00:00Z", balance="106.00"),
        _txn("B", "1.00", when="2026-05-02T00:00:00Z", balance="107.00"),
    ])
    r, out = _convert(tmp_path, src)
    assert [t.id for t in ingest.load_csv(out).transactions] == ["L0", "A", "B"]
    assert (r.opening_balance, r.closing_balance) == (100.0, 107.0)


@pytest.mark.contracts
def test_a_later_fully_balanced_group_pins_the_start_past_the_start_search(tmp_path):
    # verify-257 minor: 17 distinct loose amounts in the first group (past START_SEARCH), of
    # which the even-indexed ones must come before A; a later group with a balance on every
    # line fixes where the walk starts, so the order is found, not refused at the limit.
    when = "2026-05-01T00:00:00Z"
    pence = [2 ** i for i in range(17)]
    before_a = sum(pence[::2])
    loose = [_txn(f"L{i}", f"{p / 100:.2f}", when=when) for i, p in enumerate(pence)]
    end = sum(pence) + 1  # opening 0.00, A is 0.01
    later = "2026-05-02T00:00:00Z"
    src = _write(tmp_path, [*loose, _txn("A", "0.01", when=when, balance=f"{(before_a + 1) / 100:.2f}"),
                            _txn("C2", "0.01", when=later, balance=f"{(end + 2) / 100:.2f}"),
                            _txn("C1", "0.01", when=later, balance=f"{(end + 1) / 100:.2f}")])
    r, out = _convert(tmp_path, src)
    ids = [t.id for t in ingest.load_csv(out).transactions]
    assert r.rows == 20 and r.opening_balance == 0.0 and ids[-2:] == ["C1", "C2"]
    assert {f"L{i}" for i in range(0, 17, 2)} == set(ids[:ids.index("A")])
