"""Convert a TrueLayer Data API transactions export into the five-column statement format
(R37), with the same checks as the OBIE importer (R36).

The file is what GET /data/v1/accounts/{account_id}/transactions returns: one response object
({"results": [...], "status": "Succeeded"}), a JSON array of such responses (one per page or
date range), or the responses written one after another. Each result carries transaction_id,
timestamp, description, amount (signed: negative for money out), currency, transaction_type
(CREDIT or DEBIT), and optionally running_balance {amount, currency}, merchant_name,
meta.counter_party_preferred_name, transaction_category and transaction_classification.

So this module, as openbanking.py does for an OBIE file:
- keeps only booked transactions: the transactions endpoint returns settled ones; a result
  that says it is pending ("status": "pending", as a file that also holds the pending
  endpoint's results can mark them) is left out and counted, and any other status is refused;
- keeps only GBP: a transaction, or a running balance, in another currency refuses the
  account, and nothing is ever converted;
- keeps the provider's transaction_id as the line id;
- keeps a transaction that a paged export repeats once, when the repeat is the same in every
  field, and refuses the file when two transactions share an id but differ;
- rebuilds the running balance from the running balances, or from a statement balance the
  person supplies (--statement-balance AMOUNT --at DATE), and refuses the file if the balance
  chain breaks anywhere; lines booked at one moment are ordered as openbanking.finish orders
  them, which is where those checks live, shared by both importers;
- builds the description from description, merchant_name and the counterparty's name.

TrueLayer's own categories (transaction_category, transaction_classification) are kept only
as untrusted hints (R37, R34): returned in ImportResult.hints by line id, written by the case
store beside the statement, and read onto Transaction.provider_hint. No figure, rule or
indicator reads them, so they never change a category or a figure, and they never reach a
model.

A refusal names the line of the file a transaction's object starts on, never a cell's value
(the importer contract, T28). It reads the file locally and writes a new file. Nothing is sent
anywhere.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from ..metrics import AT_LIMIT, at_limit, text_pence
from . import contract, openbanking
from .contract import ImportRefused
from .openbanking import CONTROL, FORMULA_OR_SPACE, MAX_ID, SIGNED_AMOUNT, _Obj

BOOKED = {"settled", "booked"}
PENDING = {"pending"}
MAX_HINT = 200  # a provider category longer than this is cut; it is a hint, never data


class TrueLayerImportError(ImportRefused):
    """A TrueLayer file refused; an importer's refusal under the contract (T28)."""


@dataclass
class ImportResult(openbanking.ImportResult):
    hints: dict[str, str] = field(default_factory=dict)  # line id -> the provider's category, untrusted


def _results(pages, lines_at) -> list:
    out = []
    for page in pages:
        results = page.get("results") if isinstance(page, dict) else None
        if not isinstance(results, list):
            at = page.at if isinstance(page, _Obj) else 0
            raise TrueLayerImportError(f"line {lines_at(at)}: not a TrueLayer Data API transactions response "
                                       "(no results list)")
        for t in results:
            if not isinstance(t, _Obj):
                raise TrueLayerImportError(f"line {lines_at(page.at)}: a transaction is not a JSON object")
            out.append(t)
    return out


def _pence(raw, what: str, line: int) -> int:
    """A signed amount in whole pence, read exactly from the JSON number (or text) as written."""
    text = str(raw) if isinstance(raw, (str, Decimal, int)) and not isinstance(raw, bool) else ""
    p = text_pence(text) if SIGNED_AMOUNT.match(text.strip()) else None
    if p is None:
        raise TrueLayerImportError(f"line {line}: the {what} is missing or not a plain amount in whole pence "
                                   "like -12.40")
    if at_limit(p):
        raise TrueLayerImportError(f"line {line}: the {what} is {AT_LIMIT}; no figure could be reported to the penny")
    return p


def _amount(t: dict, line: int) -> int:
    if t.get("currency") != "GBP":
        raise TrueLayerImportError(f"line {line}: the amount is not in GBP; the account is refused; "
                                   "lines are never converted")
    p = _pence(t.get("amount"), "amount", line)
    kind = t.get("transaction_type")
    if kind not in ("CREDIT", "DEBIT"):
        raise TrueLayerImportError(f"line {line}: transaction_type is neither CREDIT nor DEBIT")
    if (kind == "CREDIT" and p < 0) or (kind == "DEBIT" and p > 0):
        raise TrueLayerImportError(f"line {line}: the amount's sign disagrees with transaction_type")
    return p


def _balance(t: dict, line: int) -> int | None:
    bal = t.get("running_balance")
    if bal is None:
        return None
    if not isinstance(bal, dict):
        raise TrueLayerImportError(f"line {line}: running_balance is not an object")
    if bal.get("currency") != "GBP":
        raise TrueLayerImportError(f"line {line}: the balance is not in GBP; the account is refused")
    return _pence(bal.get("amount"), "balance", line)


def _id(t: dict, line: int) -> str:
    tid = t.get("transaction_id")
    if tid is None:
        raise TrueLayerImportError(f"line {line}: a booked transaction has no transaction_id; its id must be kept")
    if not isinstance(tid, str) or not tid:
        raise TrueLayerImportError(f"line {line}: transaction_id is empty or not text")
    if tid.startswith(FORMULA_OR_SPACE) or tid != tid.strip() or CONTROL.search(tid) or len(tid) > MAX_ID:
        raise TrueLayerImportError(f"line {line}: transaction_id starts with a character a spreadsheet reads as "
                                   f"a formula (= + - @), or with a space, holds a control character or is "
                                   f"longer than {MAX_ID} characters")
    return tid


def _description(t: dict, line: int) -> str:
    """description, then the merchant's and the counterparty's name, each added only if the
    text so far does not already hold it."""
    meta = t.get("meta")
    parts = [t.get("description"), t.get("merchant_name"),
             meta.get("counter_party_preferred_name") if isinstance(meta, dict) else None]
    out = ""
    for part in parts:
        if part is None:
            continue
        if not isinstance(part, str):
            raise TrueLayerImportError(f"line {line}: a description field is not text")
        part = " ".join(part.split()).upper()
        if part and part not in out:
            out = f"{out} {part}".strip()
    return out


def _hint(t: dict) -> str | None:
    """The provider's category and classification as one line of text, or None. Untrusted: it
    is kept as a hint and never refuses the file, whatever it holds."""
    parts = []
    cat = t.get("transaction_category")
    if isinstance(cat, str):
        parts.append(cat)
    cls = t.get("transaction_classification")
    if isinstance(cls, list):
        named = [c for c in cls if isinstance(c, str)]
        if named:
            parts.append(" / ".join(named))
    text = " ".join(CONTROL.sub(" ", ": ".join(p for p in parts if p)).split())[:MAX_HINT]
    return text or None


def convert(src: Path, dst: Path, *, statement_balance: str | None = None, at: str | None = None,
            today: date | None = None) -> ImportResult:
    """`statement_balance` is the balance at the end of the date `at`, as the person gave it
    (read exactly); both or neither."""
    today = today or date.today()
    try:
        supplied = openbanking._statement_balance(statement_balance, at)
    except ImportRefused as e:
        raise TrueLayerImportError(str(e)) from None
    data = Path(src).read_bytes()  # read once
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        line = data.count(b"\n", 0, e.start) + 1
        raise TrueLayerImportError(f"line {line}: a byte that is not UTF-8") from None
    breaks = [i for i, c in enumerate(text) if c == "\n"]

    def lines_at(offset: int) -> int:
        return bisect.bisect_left(breaks, offset) + 1

    try:
        pages = openbanking._pages(text, lines_at)
    except ImportRefused as e:
        raise TrueLayerImportError(str(e)) from None
    txns = _results(pages, lines_at)
    if not txns:
        raise TrueLayerImportError(f"{src}: no transactions")

    booked: list[tuple] = []
    first: dict[str, tuple[int, dict]] = {}
    hints: dict[str, str] = {}
    pending = repeated = 0
    pending_ids: set[str] = set()
    for pos, t in enumerate(txns):
        line = lines_at(t.at)
        status = t.get("status", "settled")
        status = status.lower() if isinstance(status, str) else status
        if status in PENDING:
            tid = t.get("transaction_id")
            if not (isinstance(tid, str) and tid in pending_ids):
                pending += 1  # left out, whatever else it holds
            if isinstance(tid, str):
                pending_ids.add(tid)
            continue
        if status not in BOOKED:
            raise TrueLayerImportError(f"line {line}: status is neither settled nor pending")
        tid = _id(t, line)
        if tid in first:
            if first[tid][1] == t:
                repeated += 1  # a page repeated it: kept once
                continue
            raise TrueLayerImportError(f"line {line}: transaction id repeated from line {first[tid][0]} with "
                                       "different content")
        first[tid] = (line, t)
        amount = _amount(t, line)
        raw_when = t.get("timestamp")
        if not isinstance(raw_when, str):
            raise TrueLayerImportError(f"line {line}: timestamp is {'missing' if raw_when is None else 'not text'}")
        try:
            d, moment = openbanking._when(raw_when, line, today)
        except ImportRefused as e:
            raise TrueLayerImportError(str(e).replace("BookingDateTime", "timestamp")) from None
        x = contract.Line(line=line, id=tid, date=d, description=_description(t, line), amount=amount,
                          balance=_balance(t, line))
        booked.append(((d, moment, pos), x))
        hint = _hint(t)
        if hint is not None:
            hints[tid] = hint
    pending -= len(pending_ids & first.keys())
    try:
        r = openbanking.finish(booked, src, dst, supplied, pending=pending, repeated=repeated)
    except ImportRefused as e:  # said as this importer's refusal
        raise TrueLayerImportError(str(e).replace("per-line balance", "running balance")) from None
    r.balance_source = r.balance_source.replace("per-line balances", "running balances")
    return ImportResult(**vars(r), hints=hints)

