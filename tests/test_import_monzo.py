"""Monzo import, on a made-up export (no real account data)."""

import csv

import pytest

from cma import categorise, ingest, metrics
from cma.importers import monzo

HEADER = [
    "Transaction ID", "Date", "Time", "Type", "Name", "Emoji", "Category", "Amount", "Currency",
    "Local amount", "Local currency", "Notes and #tags", "Address", "Receipt", "Description",
    "Category split", "Money Out", "Money In",
]


def _row(tid, d, t, kind, name, amount, currency="GBP"):
    out = "" if amount >= 0 else f"{amount:.2f}"
    inn = f"{amount:.2f}" if amount > 0 else ""
    return [tid, d, t, kind, name, "", "general", f"{amount:.2f}", currency, f"{amount:.2f}",
            currency, "", "", "", "", "", out, inn]


def _write(tmp_path, rows, header=HEADER, bom=True):
    p = tmp_path / "monzo.csv"
    with p.open("w", newline="", encoding="utf-8-sig" if bom else "utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return p


ROWS = [  # deliberately newest first, as some exports are
    _row("tx_005", "28/02/2026", "09:00:00", "Faster payment", "Example Employer Ltd", 2500.00),
    _row("tx_004", "15/02/2026", "18:30:00", "Card payment", "Corner Shop", -12.40),
    _row("tx_003", "01/02/2026", "06:00:00", "Direct Debit", "Example Council", -150.00),
    _row("tx_002", "31/01/2026", "09:00:00", "Faster payment", "Example Employer Ltd", 2500.00),
    _row("tx_001", "05/01/2026", "12:00:00", "Pot transfer", "Holiday pot", -200.00),
    _row("tx_000", "03/01/2026", "11:00:00", "Card payment", "Cafe Abroad", -9.99, currency="EUR"),
]


def test_converts_orders_and_walks_balance(tmp_path):
    out = tmp_path / "mine.csv"
    r = monzo.convert(_write(tmp_path, ROWS), out, opening_balance=1000.00)
    assert r.rows == 5 and r.skipped_non_gbp == 1
    assert r.closing_balance == 1000 - 200 + 2500 - 150 - 12.40 + 2500
    s = ingest.load_csv(out)  # the strict loader accepts it
    assert [t.date.isoformat() for t in s.transactions] == sorted(t.date.isoformat() for t in s.transactions)
    descs = [t.description for t in s.transactions]
    assert descs[0] == "POT TRANSFER OUT HOLIDAY POT"
    assert "FPS CREDIT EXAMPLE EMPLOYER LTD" in descs
    assert "DD EXAMPLE COUNCIL" in descs
    assert "CARD CORNER SHOP" in descs


def test_end_to_end_metrics_run(tmp_path):
    out = tmp_path / "mine.csv"
    monzo.convert(_write(tmp_path, ROWS), out, opening_balance=1000.00)
    s = categorise.categorise(ingest.load_csv(out))
    m = {x.id: x for x in metrics.compute(s)}
    # A salary by Faster Payment has no invoice reference, so it is not business revenue:
    # it shows as unclassified money in (M16) for a person to explain.
    assert m["M2"].value == 0.0
    assert m["M16"].value == 100.0
    assert m["M5"].value == 800.00


def test_needs_opening_balance_without_balance_column(tmp_path):
    with pytest.raises(monzo.MonzoImportError, match="--opening-balance"):
        monzo.convert(_write(tmp_path, ROWS), tmp_path / "o.csv")


def test_uses_and_checks_monzo_balance_column(tmp_path):
    header = [*HEADER, "Balance", "Balance currency"]
    rows = [
        [*_row("tx_1", "01/03/2026", "09:00:00", "Faster payment", "Client A", 100.0), "600.00", "GBP"],
        [*_row("tx_2", "02/03/2026", "09:00:00", "Card payment", "Shop", -50.0), "550.00", "GBP"],
    ]
    r = monzo.convert(_write(tmp_path, rows, header=header), tmp_path / "o.csv")
    assert r.balance_source == "Monzo Balance column" and r.opening_balance == 500.0

    rows[1][-2] = "999.00"
    with pytest.raises(monzo.MonzoImportError, match="disagrees"):
        monzo.convert(_write(tmp_path, rows, header=header), tmp_path / "o2.csv")


def test_rejects_non_monzo_file(tmp_path):
    with pytest.raises(monzo.MonzoImportError, match="not a Monzo export"):
        monzo.convert(_write(tmp_path, [["1", "2"]], header=["a", "b"]), tmp_path / "o.csv")


def test_rejects_duplicate_transaction_ids(tmp_path):
    rows = [ROWS[1], ROWS[1]]
    with pytest.raises(monzo.MonzoImportError, match="duplicate"):
        monzo.convert(_write(tmp_path, rows), tmp_path / "o.csv", opening_balance=0)


@pytest.mark.parametrize(
    "kind, amount, prefix",
    [("Faster payment", 10, "FPS CREDIT"), ("Faster payment", -10, "FPS DEBIT"),
     ("Bacs (Direct Credit)", 10, "BACS CREDIT"), ("Direct Debit", -10, "DD"),
     ("Card payment", -10, "CARD"), ("Card payment", 10, "CARD REFUND"),
     ("Account interest", 1, "INTEREST"), ("Monzo-to-Monzo", 10, "FPS CREDIT"),
     ("Bank transfer", 10, "FPS CREDIT"), ("Bank transfer", -10, "FPS DEBIT"),
     ("Flex", -10, "LOAN REPAYMENT"), ("Flex", 10, "LOAN DRAWDOWN")],
)
def test_description_prefixes(kind, amount, prefix):
    assert monzo.describe(kind, "X", amount).startswith(prefix + " ")


def test_flex_is_debt_not_a_supplier(tmp_path):
    rows = [
        _row("tx_1", "01/03/2026", "09:00:00", "Card payment", "Laptop Shop", -900.0),
        _row("tx_2", "01/03/2026", "09:05:00", "Flex", "Monzo Flex", 900.0),
        _row("tx_3", "01/04/2026", "06:00:00", "Flex", "Monzo Flex", -300.0),
    ]
    out = tmp_path / "o.csv"
    monzo.convert(_write(tmp_path, rows), out, opening_balance=1000.0)
    s = categorise.categorise(ingest.load_csv(out))
    cats = {t.description: t.category.value for t in s.transactions}
    assert cats["LOAN DRAWDOWN MONZO FLEX"] == "loan_drawdown"
    assert cats["LOAN REPAYMENT MONZO FLEX"] == "loan_repayment"
    m = {x.id: x for x in metrics.compute(s)}
    assert m["M8"].value == 300.0


# --- panel run 03 ------------------------------------------------------------------

def _row_ref(tid, d, kind, name, amount, notes="", description=""):
    r = _row(tid, d, "09:00:00", kind, name, amount)
    r[11], r[14] = notes, description
    return r


def test_invoice_reference_survives_import_and_counts_as_revenue(tmp_path):
    # E3: the reference was dropped, so no Monzo transfer could ever be revenue.
    rows = [_row_ref("tx_a", "01/03/2026", "Faster payment", "Oakfield Properties", 4800.0, description="INV 1042"),
            _row_ref("tx_b", "02/03/2026", "Bacs (Direct Credit)", "Acme Ltd", 900.0, notes="invoice 2210 #work")]
    out = tmp_path / "o.csv"
    monzo.convert(_write(tmp_path, rows), out, opening_balance=0)
    s = categorise.categorise(ingest.load_csv(out))
    assert [t.description for t in s.transactions] == [
        "FPS CREDIT OAKFIELD PROPERTIES INV1042", "BACS CREDIT ACME LTD INVOICE2210"]
    assert {t.category.value for t in s.transactions} == {"revenue_transfer"}
    # ...and every line keeps Monzo's own id.
    assert [t.id for t in s.transactions] == ["tx_a", "tx_b"]


def test_supplied_opening_balance_is_checked_against_monzo(tmp_path):
    # E4: a wrong opening balance silently shifted every balance.
    header = [*HEADER, "Balance", "Balance currency"]
    rows = [[*_row("tx_1", "01/03/2026", "09:00:00", "Faster payment", "Client A", 100.0), "600.00", "GBP"],
            [*_row("tx_2", "02/03/2026", "09:00:00", "Card payment", "Shop", -50.0), "550.00", "GBP"]]
    src = _write(tmp_path, rows, header=header)
    with pytest.raises(monzo.MonzoImportError, match=r"implies 500\.00"):
        monzo.convert(src, tmp_path / "o.csv", opening_balance=5000)
    assert monzo.convert(src, tmp_path / "o2.csv", opening_balance=500).closing_balance == 550.0


@pytest.mark.parametrize("amount", ["1e5", "1,234.00", "£12.00"])
def test_monzo_amounts_must_be_plain(tmp_path, amount):
    row = _row("tx_1", "01/03/2026", "09:00:00", "Card payment", "Shop", -1.0)
    row[7] = amount
    with pytest.raises(monzo.MonzoImportError, match="plain amount"):
        monzo.convert(_write(tmp_path, [row]), tmp_path / "o.csv", opening_balance=0)


@pytest.mark.parametrize("kind", ["=1+1", "+cmd", "-2+3", "@SUM(A1)", "\t=1", "\r=1", "=HYPERLINK(x)"])
def test_formula_leading_description_is_neutralised(tmp_path, kind):
    # R34: an unknown provider Type becomes the description's prefix; it must not start a formula.
    out = tmp_path / "mine.csv"
    rows = [_row("tx_001", "05/01/2026", "12:00:00", kind, "Merchant", -10.00)]
    monzo.convert(_write(tmp_path, rows), out, opening_balance=100.00)
    with out.open(newline="") as f:
        cells = [r["description"] for r in csv.DictReader(f)]
    assert cells and all(c[0] not in "=+-@\t\r" for c in cells)
    assert "MERCHANT" in cells[0]
    assert ingest.load_csv(out).transactions[0].description == cells[0]
