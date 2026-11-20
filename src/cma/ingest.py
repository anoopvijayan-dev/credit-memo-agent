"""Load a statement CSV into the Statement model, refusing bad input.

Expected columns: id, date (ISO), description, amount (signed), balance.
Credits are positive, debits negative. CAMT/MT940 adapters belong in a later milestone.

A credit memo built on a corrupt statement is worse than no memo, so this module
checks the file before anything is computed and raises StatementError listing every
problem it found (up to a limit), with the CSV line number of each: ids, ISO dates
(none in the future), plain signed amounts, dates running one way (a newest-first
file is reversed), and a running balance that follows line to line. Transactions keep
file order, because the balance chain is defined by it.
"""

from __future__ import annotations

import csv
import io
import math
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from .metrics import (
    AT_LIMIT,
    MAX_STATEMENT_PENCE,
    at_limit,
    float_pence,
    flows_at_limit,
    follows,
    text_pence,
)
from .models import Statement, Transaction

__all__ = ["MAX_STATEMENT_PENCE", "StatementError", "load_csv"]

REQUIRED = ("id", "date", "description", "amount", "balance")
MAX_REPORTED = 20


class StatementError(ValueError):
    """The statement file cannot be trusted; nothing should be computed from it."""

    def __init__(self, path: Path, problems: list[str]):
        # Problems with the whole statement, then every line at the limit, are always said in
        # full; other line problems fill the rest of the space (R2, T04 review 13).
        whole = [p for p in problems if not p.startswith("line ")]
        limit = [p for p in problems if p.startswith("line ") and AT_LIMIT in p]
        rest = [p for p in problems if p.startswith("line ") and AT_LIMIT not in p]
        self.problems = whole + limit + rest
        shown = whole + limit + rest[:max(0, MAX_REPORTED - len(whole) - len(limit))]
        more = f"\n  … and {len(problems) - len(shown)} more" if len(problems) > len(shown) else ""
        super().__init__(f"{path}: {len(problems)} problem(s)\n  " + "\n  ".join(shown) + more)


AMOUNT = re.compile(r"^[-+]?\d+(?:\.\d{1,2})?$")
SPLIT_COLUMNS = {"paid in", "paid out", "money in", "money out", "debit", "credit"}


# A statement's amounts and balances are counted to the penny, and every figure is reported
# as the float the DTO carries. Fifteen significant digits always round-trip through a
# float, so amounts are taken up to 13 digits of pounds and two of pence, and a statement
# whose money in or money out could total more is refused at import, named, rather than
# reported a penny out (R2, revision 24; T04 review 3). The limit and all the arithmetic on
# money live in the figures layer, metrics (T04 review 13).


def _over_limit(raw: str | None) -> bool:
    text = (raw or "").strip()
    p = text_pence(text) if AMOUNT.match(text) else None
    return p is not None and at_limit(p)


def _number(raw: str | None) -> float | None:
    """A plain signed decimal with at most two places: "-1150.00", "812.4". Thousands
    separators, currency signs, exponents and words are refused rather than guessed. The
    text is read exactly, so no penny is lost before it is counted (T04 review 3)."""
    text = (raw or "").strip()
    if not AMOUNT.match(text):
        return None
    p = text_pence(text)
    if p is None or at_limit(p):
        return None
    v = float(Decimal(text))
    return v if math.isfinite(v) and float_pence(v) == p else None


def load_csv(path: Path, account_name: str | None = None, today: date | None = None,
             data: bytes | None = None) -> Statement:
    """`data`, when given, is the file's bytes as the caller read them: the statement is
    parsed from exactly the bytes the caller hashes, never from a second read of a file that
    may have been replaced in between (R1, R2, P2; T04 review 17)."""
    path = Path(path)
    today = today or date.today()
    if data is None:
        if not path.is_file():
            raise StatementError(path, ["no such file"])
        data = path.read_bytes()
    rows: list[dict] = []
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))  # tolerate a byte-order mark
        header = [h.strip() for h in (reader.fieldnames or [])]
        reader.fieldnames = header
        for row in reader:
            rows.append(row)
    except csv.Error as e:
        # The CSV reader stops on a field longer than it reads, or a malformed record, before
        # any check: refused here, naming the line, never reported as another failure. A field
        # that long that is an amount or balance is past the limit (R2, T04 review 19).
        line = len(rows) + 2 if "header" in locals() else 1
        if "field larger than field limit" in str(e):
            raise StatementError(path, [
                f"line {line}: a field is longer than {csv.field_size_limit():,} characters and is not read; "
                f"an amount or balance that long is {AT_LIMIT}"]) from None
        raise StatementError(path, [f"line {line}: not a readable CSV record ({e})"]) from None
    except UnicodeDecodeError as e:
        raise StatementError(path, [
            f"not UTF-8 (byte {e.start}); re-save it as 'CSV UTF-8' (Excel) and try again"
        ]) from e
    # A column named twice would let the second silently replace the first's value on every
    # line, before any check (R2, T04 review 14): refused, naming the column.
    twice = sorted({h for h in header if h and header.count(h) > 1})
    if twice:
        raise StatementError(path, [f"column(s) named more than once: {', '.join(twice)}; keep one of each"])
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        hint = ""
        if SPLIT_COLUMNS & {h.lower() for h in header}:
            hint = " This looks like a paid-in/paid-out export; convert it to one signed amount column first."
        if {"Transaction ID", "Money Out"} <= set(header):
            hint = " This looks like a Monzo export; run `cma import monzo` on it first."
        raise StatementError(path, [
            f"missing column(s): {', '.join(missing)} (found: {', '.join(header) or 'none'}).{hint}"
        ])
    if not rows:
        raise StatementError(path, ["no transactions"])

    problems: list[str] = []
    parsed: list[tuple[int, str, date | None, str, float | None, float | None]] = []
    seen: dict[str, int] = {}
    for i, r in enumerate(rows, start=2):  # line 1 is the header
        if None in r:
            problems.append(f"line {i}: more fields than the header has columns")
        tid = (r["id"] or "").strip()
        if not tid:
            problems.append(f"line {i}: empty id")
        elif tid in seen:
            problems.append(f"line {i}: duplicate id {tid} (first on line {seen[tid]})")
        else:
            seen[tid] = i
        raw_date = (r["date"] or "").strip()
        try:
            d = date.fromisoformat(raw_date)
            if len(raw_date) != 10:
                raise ValueError
        except ValueError:
            problems.append(f"line {i}: date {r['date']!r} is not YYYY-MM-DD")
            d = None
        if d and d > today:
            problems.append(f"line {i}: date {d} is in the future")
        amount, balance = _number(r["amount"]), _number(r["balance"])
        for field, value, raw in (("amount", amount, r["amount"]), ("balance", balance, r["balance"])):
            if value is None and _over_limit(raw):
                problems.append(f"line {i}: {field} {raw!r} is {AT_LIMIT}; no figure could be reported to the penny")
        if amount is None and not _over_limit(r["amount"]):
            problems.append(f"line {i}: amount {r['amount']!r} is not a plain number like -1150.00 "
                            "(under £10,000,000,000,000)")
        if balance is None and not _over_limit(r["balance"]):
            problems.append(f"line {i}: balance {r['balance']!r} is not a plain number like 2304.17 "
                            "(under £10,000,000,000,000)")
        parsed.append((i, tid, d, (r["description"] or "").strip(), amount, balance))

    # Banks export newest first or oldest first. Decide from the dates, then insist
    # the whole file runs one way.
    dates = [d for _, _, d, _, _, _ in parsed if d]
    if dates and dates[0] > dates[-1]:
        parsed.reverse()
    prev_date: date | None = None
    for line, _, d, _, _, _ in parsed:
        if d and prev_date and d < prev_date:
            problems.append(f"line {line}: date {d} is out of order (after {prev_date})")
        prev_date = d or prev_date

    prev_balance: float | None = None
    for line, _, _, _, amount, balance in parsed:
        if None not in (amount, balance, prev_balance) and not follows(
                float_pence(prev_balance), float_pence(amount), float_pence(balance)):
            problems.append(
                f"line {line}: running balance {balance:.2f} does not follow from previous {prev_balance:.2f} "
                f"{'+' if amount >= 0 else '-'} {abs(amount):.2f}"
            )
        if balance is not None:
            prev_balance = balance

    for name in flows_at_limit([float_pence(a) for _, _, _, _, a, _ in parsed if a is not None]):
        problems.append(f"{name} totals {AT_LIMIT}: no figure could be reported to the penny")  # each named
    if problems:
        raise StatementError(path, problems)
    txns = [Transaction(id=tid, date=d, description=desc, amount=amount, balance=balance)
            for _, tid, d, desc, amount, balance in parsed]
    return Statement(account_name=account_name or "Applicant", transactions=txns)
