"""Load a statement CSV into the Statement model.

Expected columns: id, date (ISO), description, amount (signed), balance.
Credits are positive, debits negative. That is the only format for now; CAMT/MT940
adapters belong in a later milestone.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from .models import Statement, Transaction


def load_csv(path: Path, account_name: str | None = None) -> Statement:
    with Path(path).open(newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"{path} has no transactions")
    txns = [
        Transaction(
            id=r["id"],
            date=date.fromisoformat(r["date"]),
            description=r["description"].strip(),
            amount=float(r["amount"]),
            balance=float(r["balance"]),
        )
        for r in rows
    ]
    txns.sort(key=lambda t: (t.date, t.id))
    return Statement(account_name=account_name or Path(path).stem, transactions=txns)
