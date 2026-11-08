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
  Monzo's own Balance column when the export has one and checks it agrees;
- builds a bank-style description from Type, direction and Name (for example
  "FPS CREDIT ACME LTD", "DD COUNCIL TAX", "CARD TESCO"), so the categoriser sees
  the payment rail the way it would on a bank statement.

It reads the file locally and writes a new file. Nothing is sent anywhere.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

REQUIRED = ("Transaction ID", "Date", "Type", "Name", "Amount")


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


def _parse_date(raw: str) -> date:
    raw = raw.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError as e:
        raise MonzoImportError(f"unrecognised date {raw!r}") from e


def _money(raw: str | None, field: str, line: int) -> float:
    try:
        v = float((raw or "").replace(",", "").strip())
    except ValueError as e:
        raise MonzoImportError(f"line {line}: {field} {raw!r} is not a number") from e
    if not math.isfinite(v):
        raise MonzoImportError(f"line {line}: {field} {raw!r} is not finite")
    return v


def describe(kind: str, name: str, amount: float, extra: str = "") -> str:
    """A bank-statement style description from Monzo's Type, Name and direction."""
    k = kind.strip().lower()
    who = " ".join(f"{name} {extra}".split()).upper() or "UNKNOWN"
    incoming = amount > 0
    if "faster payment" in k or "monzo-to-monzo" in k or k == "p2p_payment":
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


def convert(src: Path, dst: Path, opening_balance: float | None = None) -> ImportResult:
    with Path(src).open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = [h.strip() for h in (reader.fieldnames or [])]
        reader.fieldnames = header
        missing = [c for c in REQUIRED if c not in header]
        if missing:
            raise MonzoImportError(
                f"{src}: not a Monzo export; missing column(s): {', '.join(missing)}"
            )
        raw_rows = list(reader)
    if not raw_rows:
        raise MonzoImportError(f"{src}: no transactions")

    has_balance = "Balance" in header
    if opening_balance is None and not has_balance:
        raise MonzoImportError(
            "this export has no Balance column; pass --opening-balance (the balance just "
            "before the first transaction, from your Monzo statement)"
        )

    parsed = []
    skipped = 0
    seen: set[str] = set()
    for i, r in enumerate(raw_rows, start=2):
        tid = (r.get("Transaction ID") or "").strip()
        if not tid:
            raise MonzoImportError(f"line {i}: empty Transaction ID")
        if tid in seen:
            raise MonzoImportError(f"line {i}: duplicate Transaction ID {tid}")
        seen.add(tid)
        currency = (r.get("Currency") or "GBP").strip().upper()
        if currency and currency != "GBP":
            skipped += 1
            continue
        d = _parse_date(r["Date"])
        t = (r.get("Time") or "00:00:00").strip()
        amount = _money(r["Amount"], "Amount", i)
        bal = _money(r["Balance"], "Balance", i) if has_balance and (r.get("Balance") or "").strip() else None
        desc = describe(r.get("Type", ""), r.get("Name", ""), amount)
        parsed.append((d, t, tid, desc, amount, bal))

    parsed.sort(key=lambda x: (x[0], x[1], x[2]))
    if opening_balance is None:
        first = parsed[0]
        if first[5] is None:
            raise MonzoImportError("first row has no Balance; pass --opening-balance")
        opening_balance = round(first[5] - first[4], 2)
        source = "Monzo Balance column"
    else:
        source = "opening balance supplied"

    out_rows = []
    bal = opening_balance
    for n, (d, _t, tid, desc, amount, monzo_bal) in enumerate(parsed, start=1):
        bal = round(bal + amount, 2)
        if monzo_bal is not None and source == "Monzo Balance column" and abs(monzo_bal - bal) > 0.01:
            raise MonzoImportError(
                f"{tid}: walked balance {bal:.2f} disagrees with Monzo's {monzo_bal:.2f}; "
                "the export may be missing transactions"
            )
        out_rows.append({"id": f"M{n:05d}", "date": d.isoformat(), "description": desc,
                         "amount": f"{amount:.2f}", "balance": f"{bal:.2f}"})

    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    with Path(dst).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "date", "description", "amount", "balance"])
        w.writeheader()
        w.writerows(out_rows)
    return ImportResult(
        rows=len(out_rows), start=parsed[0][0], end=parsed[-1][0],
        opening_balance=opening_balance, closing_balance=bal,
        balance_source=source, skipped_non_gbp=skipped,
    )
