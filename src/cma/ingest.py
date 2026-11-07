"""Load a statement CSV into the Statement model, refusing bad input.

Expected columns: id, date (ISO), description, amount (signed), balance.
Credits are positive, debits negative. CAMT/MT940 adapters belong in a later milestone.

A credit memo built on a corrupt statement is worse than no memo, so this module
checks the file before anything is computed and raises StatementError listing every
problem it found (up to a limit), with the CSV line number of each.
"""

from __future__ import annotations

import csv
import math
from datetime import date
from pathlib import Path

from .models import Statement, Transaction

REQUIRED = ("id", "date", "description", "amount", "balance")
BALANCE_TOLERANCE = 0.01
MAX_REPORTED = 20


class StatementError(ValueError):
    """The statement file cannot be trusted; nothing should be computed from it."""

    def __init__(self, path: Path, problems: list[str]):
        self.problems = problems
        shown = problems[:MAX_REPORTED]
        more = f"\n  … and {len(problems) - MAX_REPORTED} more" if len(problems) > MAX_REPORTED else ""
        super().__init__(f"{path}: {len(problems)} problem(s)\n  " + "\n  ".join(shown) + more)


def _number(raw: str | None) -> float | None:
    try:
        v = float((raw or "").strip())
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def load_csv(path: Path, account_name: str | None = None) -> Statement:
    path = Path(path)
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in REQUIRED if c not in header]
        if missing:
            raise StatementError(path, [f"missing column(s): {', '.join(missing)} (found: {', '.join(header) or 'none'})"])
        rows = list(reader)
    if not rows:
        raise StatementError(path, ["no transactions"])

    problems: list[str] = []
    txns: list[Transaction] = []
    seen: dict[str, int] = {}
    prev_balance: float | None = None
    for i, r in enumerate(rows, start=2):  # line 1 is the header
        tid = (r["id"] or "").strip()
        if not tid:
            problems.append(f"line {i}: empty id")
        elif tid in seen:
            problems.append(f"line {i}: duplicate id {tid} (first on line {seen[tid]})")
        else:
            seen[tid] = i
        try:
            d = date.fromisoformat((r["date"] or "").strip())
        except ValueError:
            problems.append(f"line {i}: date {r['date']!r} is not YYYY-MM-DD")
            d = None
        amount, balance = _number(r["amount"]), _number(r["balance"])
        if amount is None:
            problems.append(f"line {i}: amount {r['amount']!r} is not a finite number")
        if balance is None:
            problems.append(f"line {i}: balance {r['balance']!r} is not a finite number")
        if None not in (amount, balance, prev_balance) and abs(prev_balance + amount - balance) > BALANCE_TOLERANCE:
            problems.append(
                f"line {i}: running balance {balance:.2f} does not follow from previous {prev_balance:.2f} "
                f"{'+' if amount >= 0 else '-'} {abs(amount):.2f}"
            )
        if balance is not None:
            prev_balance = balance
        if tid and d and amount is not None and balance is not None:
            txns.append(Transaction(id=tid, date=d, description=(r["description"] or "").strip(),
                                    amount=amount, balance=balance))

    if problems:
        raise StatementError(path, problems)
    txns.sort(key=lambda t: (t.date, t.id))
    return Statement(account_name=account_name or "Applicant", transactions=txns)
