"""Convert a Monzo CSV export into the five-column statement format.

Monzo's export (personal: Transaction ID, Date, Time, Type, Name, Emoji, Category,
Amount, Currency, Local amount, Local currency, Notes and #tags, Address, Receipt,
Description, Category split, Money Out, Money In; business exports may add Balance)
has no running balance on personal accounts and names the counterparty without the
payment-rail prefix a bank statement carries.

So this module:
- uses the signed `Amount` column (account currency), never Money In/Out as well;
- orders rows oldest first by date and time, then Transaction ID;
- walks a running balance forward from an opening balance the owner supplies, or uses
  Monzo's own Balance column when the export has one; whenever that column is there,
  every walked balance is checked against it, including a supplied opening balance;
- keeps Monzo's Transaction ID as the line id, so every figure traces back to Monzo;
- carries invoice references from Notes and Description into the description, so a
  customer payment marked "INV 1234" is recognised as revenue;
- builds a bank-style description from Type, direction and Name (for example
  "FPS CREDIT ACME LTD", "DD COUNCIL TAX", "CARD TESCO"), so the categoriser sees
  the payment rail the way it would on a bank statement;
- treats Monzo Flex as the credit line it is: money in is "LOAN DRAWDOWN MONZO FLEX",
  money out is "LOAN REPAYMENT MONZO FLEX", so repayments count as debt service.

It reads the file locally and writes a new file. Nothing is sent anywhere.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ..categorise import INVOICE_REF
from ..ingest import CsvRecordError, iter_data, read_header, shown
from ..metrics import (
    AT_LIMIT,
    at_limit,
    flows_at_limit,
    opening_before,
    pounds,
    pounds_text,
    running_balances,
    text_pence,
)

REQUIRED = ("Transaction ID", "Date", "Type", "Name", "Amount")
PLAIN_MONEY = re.compile(r"^[-+]?\d+(?:\.\d{1,2})?$")
CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")


class MonzoImportError(ValueError):
    pass


@dataclass
class ImportResult:
    rows: int
    start: date
    end: date
    opening_balance: float
    closing_balance: float
    balance_source: str  # "opening balance supplied" or "Monzo Balance column"
    skipped_non_gbp: int


def _parse_when(raw: str | None) -> tuple[date, str | None]:
    """The row's date, and the time of day its Date cell carries, if it is a timestamp
    ("2026-01-01T10:00:00Z"), as the wall-clock time written, so rows are walked in the order
    they happened (T04 review 62)."""
    raw = (raw or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date(), None
        except ValueError:
            pass
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as e:
        raise MonzoImportError(f"unrecognised date {shown(raw)}") from e
    return when.date(), when.time().isoformat()



def _money(raw: str | None, field: str, line: int, *, limit: bool = True) -> int:
    """The amount in whole pence, read exactly from Monzo's text, never through a binary
    float; one the statement could not carry to the penny is refused (R2, T04 review 4)."""
    text = (raw or "").strip()
    at = f"line {line}: " if line else ""  # line 0: the command line, not the export
    if not PLAIN_MONEY.match(text):
        raise MonzoImportError(f"{at}{field} {shown(raw)} is not a plain amount like -12.40")
    p = text_pence(text)  # exact, in the figures layer (R2, T04 review 13)
    if p is None:
        raise MonzoImportError(f"{at}{field} {shown(raw)} has more than two decimal places")
    if limit and at_limit(p):
        raise MonzoImportError(f"{at}{field} {shown(raw)} is {AT_LIMIT}; no figure could be reported to the penny")
    return p


def _gbp(p: int) -> str:
    """Whole pence written as pounds with two decimals, by the figures layer."""
    return pounds_text(p)


def references(*fields: str | None) -> str:
    """Invoice references found in Monzo's free-text fields, e.g. "INV1234"."""
    found = []
    for f in fields:
        for m in INVOICE_REF.finditer((f or "").upper()):
            ref = re.sub(r"\s+", "", m.group(0))
            if ref not in found:
                found.append(ref)
    return " ".join(found)


def describe(kind: str, name: str, amount: float, extra: str = "") -> str:
    """A bank-statement style description from Monzo's Type, Name and direction."""
    k = kind.strip().lower()
    who = " ".join(f"{name} {extra}".split()).upper() or "UNKNOWN"
    incoming = amount > 0
    if "flex" in k or "MONZO FLEX" in who:
        # Monzo Flex is credit: money in is a drawdown, money out is a repayment.
        prefix = "LOAN DRAWDOWN" if incoming else "LOAN REPAYMENT"
        if "FLEX" not in who:
            who = f"MONZO FLEX {who}"
    elif ("faster payment" in k or "monzo-to-monzo" in k or k == "p2p_payment"
          or "bank transfer" in k):
        prefix = "FPS CREDIT" if incoming else "FPS DEBIT"
    elif "bacs" in k:
        prefix = "BACS CREDIT" if incoming else "BACS DEBIT"
    elif "direct debit" in k:
        prefix = "DD REFUND" if incoming else "DD"
    elif "standing order" in k:
        prefix = "SO"
    elif "card" in k:
        prefix = "CARD REFUND" if incoming else "CARD"
    elif "pot" in k:
        prefix = "POT TRANSFER IN" if incoming else "POT TRANSFER OUT"
    elif "interest" in k:
        prefix = "INTEREST"
    else:
        prefix = k.upper() or "OTHER"
    return f"{prefix} {who}".strip()


def convert(src: Path, dst: Path, opening_balance: str | float | None = None) -> ImportResult:
    """`opening_balance` is the text the person gave (read exactly), or a float from code."""
    # The opening balance is no line of the statement: like one inferred from the export, it
    # is not held to the line limit; every walked balance is (R2, T04 review 10).
    opening_pence = None if opening_balance is None else _money(
        opening_balance if isinstance(opening_balance, str) else repr(opening_balance), "--opening-balance", 0,
        limit=False)
    # The file is read once, as bytes (T04 review 29).
    # Lines as the file has them (T04 review 20); the records before one the reader
    # cannot read, or before a byte that is not UTF-8, are checked first, so a problem
    # among them is the one reported (T04 review 29).
    data = Path(src).read_bytes()
    try:
        # The header is checked before any record is read (T04 review 51).
        header = read_header(data)
    except CsvRecordError as e:
        raise MonzoImportError(f"{src}: {e}") from None
    twice = sorted({h for h in header if h and header.count(h) > 1})  # T04 review 14
    if twice:
        raise MonzoImportError(f"{src}: column(s) named more than once: {', '.join(twice)}; keep one of each")
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise MonzoImportError(
            f"{src}: not a Monzo export; missing column(s): {', '.join(missing)}"
        )
    try:
        # Records are read one at a time, and reading stops at the first problem: nothing past
        # it is read (T04 review 52).
        header, records = iter_data(data)
    except CsvRecordError as e:
        raise MonzoImportError(f"{src}: {e}") from None

    has_balance = "Balance" in header

    parsed = []
    skipped = 0
    seen: set[str] = set()

    def flows_before() -> str:
        """Money in or out among the records read so far already at the limit: said with any
        later problem, so a bad row or an unreadable field never hides it (T04 reviews 23-24)."""
        # The row being read counts once its amount is known, even if a later field of it is
        # bad (T04 review 26).
        reached = flows_at_limit([a for *_, a, _b in parsed] + ([reading] if reading is not None else []))
        return (f"{' and '.join(reached)} {'total' if len(reached) > 1 else 'totals'} {AT_LIMIT} in the lines "
                "read so far: no figure could be reported to the penny; ") if reached else ""

    reading: int | None = None
    line_of: dict[str, int] = {}  # each transaction's line, for refusals after sorting

    try:
        # A record that cannot be read whole (a field too long to read, a bad byte) is still
        # read for its currency and amount if they can be (T04 reviews 36-37); past a bad byte
        # or a field too long to read, reading goes on, so a row in another currency is
        # skipped whatever it holds and the rows after it are read (T04 reviews 46-47).
        stopped = None
        any_record = False
        for i, r, problem in records:
            any_record = True
            if r is None:  # a record the reader cannot read at all: reading stops here
                stopped = problem
                break
            partial = problem is not None
            unreadable = problem
            # Its currency is needed only if the export has a Currency column; without one,
            # every row is GBP (T04 review 38). Currency is read before the amount is needed
            # (revision 29; T04 review 39).
            if partial and "Currency" in header and "Currency" not in unreadable.readable:
                raise MonzoImportError(f"{src}: {unreadable}")
            # Currency, then the amount, before anything else about the row (T04 reviews 27-28):
            # a row in another currency is skipped whatever else it holds, and a GBP amount is
            # counted toward money in or out, even one past the line limit, before any other
            # problem with its row can stop the import without saying so.
            currency = (r.get("Currency") or "GBP").strip().upper()
            if currency and currency != "GBP":
                if CURRENCY_CODE.match(currency):
                    skipped += 1  # another currency: skipped, whatever else the row holds
                    if partial and unreadable.stops:  # but reading cannot go on past it
                        stopped = unreadable
                        break
                    continue
                # Not a currency code: the row's cells are not where the header says (an unquoted
                # "1,000.00" shifts them), so it is refused, never skipped (T04 review 34).
                shifted = "; the row has more fields than the header has columns, so an amount with a comma " \
                          "in it must be quoted" if None in r else ""
                raise MonzoImportError(f"line {i}: Currency {shown(r.get('Currency'))} is not a currency code{shifted}")
            if partial and "Amount" not in unreadable.readable:
                raise MonzoImportError(f"{src}: {unreadable}")
            amount = _money(r["Amount"], "Amount", i, limit=False)
            reading = amount
            if at_limit(amount):
                raise MonzoImportError(f"line {i}: Amount {shown(r['Amount'])} is {AT_LIMIT}; no figure could be "
                                       "reported to the penny")
            if partial:
                raise MonzoImportError(f"{src}: {unreadable}")  # its amount is counted above
            # More cells than the header has columns: an unquoted thousands separator ("1,000.00")
            # splits an amount, so the row cannot be trusted. Checked after the currency, the
            # amount and its limit, as revision 29 orders them (T04 reviews 33-35).
            if None in r:
                raise MonzoImportError(f"line {i}: more fields than the header has columns; an amount with a "
                                       "comma in it must be quoted")
            tid = (r.get("Transaction ID") or "").strip()
            if not tid:
                raise MonzoImportError(f"line {i}: empty Transaction ID")
            if not SAFE_ID.match(tid):
                raise MonzoImportError(f"line {i}: unexpected characters in Transaction ID {tid!r}")
            if tid in seen:
                raise MonzoImportError(f"line {i}: duplicate Transaction ID {tid}")
            seen.add(tid)
            line_of[tid] = i
            try:
                d, written_time = _parse_when(r["Date"])
            except MonzoImportError as e:
                raise MonzoImportError(f"line {i}: {e}") from None
            # The Time column, or else the time the Date cell carries (T04 review 62).
            t = (r.get("Time") or "").strip() or written_time or "00:00:00"
            bal = _money(r["Balance"], "Balance", i) if has_balance and (r.get("Balance") or "").strip() else None
            refs = references(r.get("Notes and #tags"), r.get("Description"))
            desc = describe(r.get("Type", ""), r.get("Name", ""), amount, refs)
            # Never accept a row whose cells the statement loader could not read back: checked
            # here, as the row is read, so no later row's problem hides it (T04 reviews 50, 52).
            for name, cell in (("id", tid), ("description", desc)):
                if len(cell) > csv.field_size_limit():
                    raise MonzoImportError(f"line {i}: the {name} would be {len(cell):,} characters, longer "
                                           f"than the {csv.field_size_limit():,} a statement's CSV reader reads")
            parsed.append((d, t, tid, desc, amount, bal))
            reading = None
    except MonzoImportError as e:
        note = flows_before()
        if not note:
            raise
        raise MonzoImportError(f"{note}{e}") from None
    if stopped is not None:  # reading stopped; after the records before it are checked (T04 review 22)
        raise MonzoImportError(f"{src}: {flows_before()}{stopped}")
    if not any_record:
        raise MonzoImportError(f"{src}: no transactions")
    if not parsed:
        raise MonzoImportError(f"{src}: no GBP transactions")
    # Money in or out at the limit is said before anything else about the export (its
    # opening balance, say) can refuse it (T04 review 25).
    reached = flows_at_limit([a for *_, a, _b in parsed])
    if reached:
        raise MonzoImportError(f"{' and '.join(reached)} {'total' if len(reached) > 1 else 'totals'} "
                               f"{AT_LIMIT}: no figure could be reported to the penny")
    if opening_balance is None and not has_balance:
        raise MonzoImportError(
            "this export has no Balance column; pass --opening-balance (the balance just "
            "before the first transaction, from your Monzo statement)"
        )
    parsed.sort(key=lambda x: (x[0], x[1], x[2]))
    first = parsed[0]
    # Everything below is whole pence (T04 review 4).
    implied = opening_before(first[5], first[4]) if first[5] is not None else None
    if opening_pence is None:
        if implied is None:
            raise MonzoImportError(f"line {line_of[first[2]]}: the first transaction, {first[2]}, has no Balance; "
                                   "pass --opening-balance")
        opening_pence = implied
        source = "Monzo Balance column"
    else:
        if implied is not None and implied != opening_pence:
            raise MonzoImportError(
                f"--opening-balance {_gbp(opening_pence)} disagrees with Monzo's Balance column, which "
                f"implies {_gbp(implied)} at line {line_of[first[2]]} ({first[2]}, the first transaction); "
                "leave --opening-balance out to use Monzo's"
            )
        source = "opening balance supplied"

    # The totals and the walk are figures-layer arithmetic (R2, T04 review 13).
    reached = flows_at_limit([a for *_, a, _b in parsed])  # each named (T04 review 6)
    if reached:
        raise MonzoImportError(f"{' and '.join(reached)} {'total' if len(reached) > 1 else 'totals'} "
                               f"{AT_LIMIT}: no figure could be reported to the penny")
    out_rows = []
    bal = opening_pence
    for (d, _t, tid, desc, amount, monzo_bal), bal in zip(parsed, running_balances(opening_pence, [x[4] for x in parsed]),
                                                          strict=True):
        if at_limit(bal):
            raise MonzoImportError(f"line {line_of[tid]}: {tid}: the running balance reaches £10,000,000,000,000; "
                                   "no figure could be reported to the penny")
        if monzo_bal is not None and monzo_bal != bal:
            raise MonzoImportError(
                f"line {line_of[tid]}: {tid}: walked balance {_gbp(bal)} disagrees with Monzo's {_gbp(monzo_bal)}; "
                "the export may be missing transactions"
            )
        out_rows.append({"id": tid, "date": d.isoformat(), "description": desc,
                         "amount": _gbp(amount), "balance": _gbp(bal)})

    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    with Path(dst).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "date", "description", "amount", "balance"])
        w.writeheader()
        w.writerows(out_rows)
    return ImportResult(
        rows=len(out_rows), start=parsed[0][0], end=parsed[-1][0],
        opening_balance=pounds(opening_pence), closing_balance=pounds(bal),
        balance_source=source, skipped_non_gbp=skipped,
    )
