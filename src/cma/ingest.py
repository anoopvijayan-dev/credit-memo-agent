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
import math
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from .models import Statement, Transaction

REQUIRED = ("id", "date", "description", "amount", "balance")
MAX_REPORTED = 20


class StatementError(ValueError):
    """The statement file cannot be trusted; nothing should be computed from it."""

    def __init__(self, path: Path, problems: list[str]):
        self.problems = problems
        shown = problems[:MAX_REPORTED]
        more = f"\n  … and {len(problems) - MAX_REPORTED} more" if len(problems) > MAX_REPORTED else ""
        super().__init__(f"{path}: {len(problems)} problem(s)\n  " + "\n  ".join(shown) + more)


AMOUNT = re.compile(r"^[-+]?\d+(?:\.\d{1,2})?$")
SPLIT_COLUMNS = {"paid in", "paid out", "money in", "money out", "debit", "credit"}


def _pence(v: float) -> int:
    """Amounts have at most two decimals (checked), so whole pence compare exactly."""
    return round(v * 100)


# A statement's amounts and balances are counted to the penny, and every figure is reported
# as the float the DTO carries. Fifteen significant digits always round-trip through a
# float, so amounts are taken up to 13 digits of pounds and two of pence, and a statement
# whose money in or money out could total more is refused at import, named, rather than
# reported a penny out (R2, revision 24; T04 review 3).
MAX_STATEMENT_PENCE = 10**15


def _number(raw: str | None) -> float | None:
    """A plain signed decimal with at most two places: "-1150.00", "812.4". Thousands
    separators, currency signs, exponents and words are refused rather than guessed. The
    text is read exactly, so no penny is lost before it is counted (T04 review 3)."""
    text = (raw or "").strip()
    if not AMOUNT.match(text):
        return None
    exact = Decimal(text)
    if abs(exact) * 100 >= MAX_STATEMENT_PENCE:
        return None
    v = float(exact)
    return v if math.isfinite(v) and Decimal(repr(v)) == exact else None


def load_csv(path: Path, account_name: str | None = None, today: date | None = None) -> Statement:
    path = Path(path)
    today = today or date.today()
    if not path.is_file():
        raise StatementError(path, ["no such file"])
    try:
        with path.open(newline="", encoding="utf-8-sig") as f:  # tolerate a byte-order mark
            reader = csv.DictReader(f)
            header = [h.strip() for h in (reader.fieldnames or [])]
            reader.fieldnames = header
            rows = list(reader)
    except UnicodeDecodeError as e:
        raise StatementError(path, [
            f"not UTF-8 (byte {e.start}); re-save it as 'CSV UTF-8' (Excel) and try again"
        ]) from e
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
        if amount is None:
            problems.append(f"line {i}: amount {r['amount']!r} is not a plain number like -1150.00 "
                            "(under £10,000,000,000,000)")
        if balance is None:
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
        if None not in (amount, balance, prev_balance) and _pence(prev_balance) + _pence(amount) != _pence(balance):
            problems.append(
                f"line {line}: running balance {balance:.2f} does not follow from previous {prev_balance:.2f} "
                f"{'+' if amount >= 0 else '-'} {abs(amount):.2f}"
            )
        if balance is not None:
            prev_balance = balance

    money_in = sum(_pence(a) for _, _, _, _, a, _ in parsed if a is not None and a > 0)
    money_out = sum(-_pence(a) for _, _, _, _, a, _ in parsed if a is not None and a < 0)
    if max(money_in, money_out) >= MAX_STATEMENT_PENCE:
        problems.append("money in or money out totals £10,000,000,000,000 or more: no figure could be "
                        "reported to the penny")
    if problems:
        raise StatementError(path, problems)
    txns = [Transaction(id=tid, date=d, description=desc, amount=amount, balance=balance)
            for _, tid, d, desc, amount, balance in parsed]
    return Statement(account_name=account_name or "Applicant", transactions=txns)
