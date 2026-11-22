"""Convert a UK Open Banking (OBIE Account and Transaction API v3.1) transactions file into
the five-column statement format (R36).

The file is what GET /accounts/{AccountId}/transactions returns: one response object
({"Data": {"Transaction": [...]}, "Links": ..., "Meta": ...}), a JSON array of such
responses (one per page), or the pages written one after another. Each transaction carries
TransactionId, Status, CreditDebitIndicator, BookingDateTime, Amount {Amount, Currency},
optionally Balance {Amount, CreditDebitIndicator, Type}, TransactionInformation,
TransactionReference, MerchantDetails and the counterparty's DebtorAccount or
CreditorAccount.

So this module:
- keeps only booked transactions: a pending one is left out and counted, whatever else it
  holds; a status that is neither is refused;
- keeps only GBP: a booked transaction, or a balance, in another currency refuses the
  account, and nothing is ever converted;
- keeps the provider's TransactionId as the line id, so every figure traces back to the bank;
- keeps a transaction that a paged export repeats once, when the repeat is the same in every
  field, and refuses the file when two transactions share an id but differ;
- rebuilds the running balance from the per-line balances (the balance after each line, of a
  booked type), or, when the person supplies one, from a statement balance at the end of a
  date (--statement-balance AMOUNT --at DATE); every per-line balance the file holds is
  checked against the walked one, so a chain that breaks anywhere refuses the file;
- orders lines oldest first by booking date as the bank wrote it, then by the moment booked;
  transactions booked at the same moment are put in the one order their per-line balances
  chain in, and refused when no order does;
- builds the description from TransactionInformation, the merchant's or the counterparty's
  name and the reference, so the categoriser sees what a bank statement would show.

A refusal names the line of the file a transaction's object starts on, never a cell's value
(the importer contract, T28). The balance walk, the check that the output passes the
statement loader and the writing of the statement are the contract's (importers/contract.py).

It reads the file locally and writes a new file. Nothing is sent anywhere.
"""

from __future__ import annotations

import bisect
import json
import json.decoder
import json.scanner
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from ..metrics import AT_LIMIT, at_limit, flows_at_limit, pounds, pounds_text, text_pence
from . import contract
from .contract import ImportRefused

PLAIN_AMOUNT = re.compile(r"^\d+(?:\.\d+)?$")  # OBIE writes an unsigned decimal; the sign is its indicator
SIGNED_AMOUNT = re.compile(r"^[-+]?\d+(?:\.\d{1,2})?$")
# A provider id is kept as it is and written to a CSV text cell, so it may not start with a
# character a spreadsheet reads as a formula (R34) or with a space, nor hold a control character.
FORMULA_OR_SPACE = (*contract.FORMULA_START, " ")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MAX_ID = 210  # OBIE's Max210Text
# A per-line balance of one of these types is the booked balance after the line; an available
# or expected balance can include pending lines or an overdraft limit, so it is not read.
BOOKED_BALANCES = {"ClosingBooked", "InterimBooked", "OpeningBooked", "PreviouslyClosedBooked"}
PER_LINE = "per-line balances"
# Transactions booked at the same moment are put in order by a search for the one order their
# balances chain in; it gives up past this many steps, and the file is refused.
ORDER_STEPS = 100_000


class OpenBankingImportError(ImportRefused):
    """An Open Banking file refused; an importer's refusal under the contract (T28)."""


@dataclass
class ImportResult:
    rows: int
    start: date
    end: date
    opening_balance: float
    closing_balance: float
    balance_source: str  # "per-line balances" or "statement balance supplied at DATE"
    pending: int  # pending transactions left out
    repeated: int  # transactions a paged export repeated, kept once


class _Obj(dict):
    """A JSON object, with the offset of its opening brace and any key it gives twice."""

    __slots__ = ("at", "twice")


def _pairs(pairs):
    obj = _Obj(pairs)
    obj.twice = len(obj) != len(pairs)
    return obj


def _scanner(lines_at):
    """A JSON scanner that notes where each object starts, reads numbers exactly, and refuses
    a key given twice (which would let the second silently replace the first) and NaN or
    Infinity, which are not JSON."""
    decoder = json.JSONDecoder(object_pairs_hook=_pairs, parse_float=Decimal)

    def parse_object(s_and_end, *args):
        start = s_and_end[1] - 1
        obj, end = json.decoder.JSONObject(s_and_end, *args)
        obj.at = start
        if obj.twice:
            raise OpenBankingImportError(f"line {lines_at(start)}: a key given more than once in one object")
        return obj, end

    def parse_constant(name):
        raise ValueError(f"{name} is not JSON")

    decoder.parse_object = parse_object
    decoder.parse_constant = parse_constant
    decoder.memo = {}
    return json.scanner.py_make_scanner(decoder)


def _pages(text: str, lines_at) -> list:
    """The response objects the file holds: one, an array of them, or several one after another."""
    scan = _scanner(lines_at)
    ws = json.decoder.WHITESPACE
    docs, idx = [], ws.match(text, 0).end()
    while idx < len(text):
        try:
            doc, idx = scan(text, idx)
        except ImportRefused:
            raise
        except StopIteration as e:
            raise OpenBankingImportError(f"line {lines_at(e.value)}: not JSON") from None
        except (ValueError, RecursionError) as e:  # a JSONDecodeError says where; no cell is quoted
            at = getattr(e, "pos", idx)
            raise OpenBankingImportError(f"line {lines_at(at)}: not JSON") from None
        docs.append(doc)
        idx = ws.match(text, idx).end()
    if not docs:
        raise OpenBankingImportError("line 1: not JSON (the file is empty)")
    pages = []
    for doc in docs:
        pages.extend(doc if isinstance(doc, list) else [doc])
    return pages


def _transactions(pages, lines_at) -> list:
    out = []
    for page in pages:
        data = page.get("Data") if isinstance(page, dict) else None
        txns = data.get("Transaction") if isinstance(data, dict) else None
        if not isinstance(txns, list):
            at = page.at if isinstance(page, _Obj) else 0
            raise OpenBankingImportError(f"line {lines_at(at)}: not an OBIE v3.1 transactions response "
                                         "(no Data.Transaction list)")
        for t in txns:
            if not isinstance(t, _Obj):
                raise OpenBankingImportError(f"line {lines_at(page.at)}: a transaction is not a JSON object")
            out.append(t)
    return out


def _text(t: dict, key: str, line: int, *, required: bool = True) -> str:
    v = t.get(key)
    if v is None and not required:
        return ""
    if not isinstance(v, str):
        what = "is missing" if v is None else "is not text"
        raise OpenBankingImportError(f"line {line}: {key} {what}")
    return v


def _money(obj, what: str, line: int) -> int:
    """An {Amount, Currency} object's unsigned amount in whole pence, read exactly; GBP only."""
    if not isinstance(obj, dict):
        raise OpenBankingImportError(f"line {line}: the {what} is missing or not an object")
    currency = obj.get("Currency")
    if currency != "GBP":
        tail = "; lines are never converted" if what == "amount" else ""
        raise OpenBankingImportError(f"line {line}: the {what} is not in GBP; the account is refused{tail}")
    raw = obj.get("Amount")
    text = str(raw) if isinstance(raw, (str, Decimal, int)) and not isinstance(raw, bool) else ""
    p = text_pence(text) if PLAIN_AMOUNT.match(text) else None
    if p is None:
        raise OpenBankingImportError(f"line {line}: the {what} is not a plain amount in whole pence like 12.40")
    if at_limit(p):
        raise OpenBankingImportError(f"line {line}: the {what} is {AT_LIMIT}; no figure could be reported to the penny")
    return p


def _signed(t: dict, key: str, p: int, line: int) -> int:
    indicator = t.get(key)
    if indicator == "Credit":
        return p
    if indicator == "Debit":
        return -p
    raise OpenBankingImportError(f"line {line}: {key} is neither Credit nor Debit")


def _when(raw: str, line: int, today: date) -> tuple[date, datetime]:
    """The booking date as the bank wrote it, and the moment booked (UTC; a time with no offset
    is read as UTC) to order lines within it."""
    try:
        when = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            when = datetime.combine(date.fromisoformat(raw.strip()), datetime.min.time())
        except ValueError:
            raise OpenBankingImportError(f"line {line}: BookingDateTime is not an ISO 8601 date and time") from None
    d = when.date()
    if d > today:
        raise OpenBankingImportError(f"line {line}: BookingDateTime is in the future")
    moment = when.astimezone(timezone.utc) if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return d, moment


def _description(t: dict, amount: int, line: int) -> str:
    """TransactionInformation, then the merchant's or counterparty's name and the reference,
    each added only if the text so far does not already hold it."""
    party = t.get("DebtorAccount" if amount > 0 else "CreditorAccount")
    merchant = t.get("MerchantDetails")
    parts = [t.get("TransactionInformation"),
             merchant.get("MerchantName") if isinstance(merchant, dict) else None,
             party.get("Name") if isinstance(party, dict) else None,
             t.get("TransactionReference")]
    out = ""
    for part in parts:
        if part is None:
            continue
        if not isinstance(part, str):
            raise OpenBankingImportError(f"line {line}: a description field is not text")
        part = " ".join(part.split()).upper()
        if part and part not in out:
            out = f"{out} {part}".strip()
    return out


def _id(t: dict, line: int) -> str:
    tid = t.get("TransactionId")
    if tid is None:
        raise OpenBankingImportError(f"line {line}: a booked transaction has no TransactionId; its id must be kept")
    if not isinstance(tid, str) or not tid:
        raise OpenBankingImportError(f"line {line}: TransactionId is empty or not text")
    if tid.startswith(FORMULA_OR_SPACE) or tid != tid.strip() or CONTROL.search(tid) or len(tid) > MAX_ID:
        raise OpenBankingImportError(f"line {line}: TransactionId starts with a character a spreadsheet reads as "
                                     f"a formula (= + - @), or with a space, holds a control character or is "
                                     f"longer than {MAX_ID} characters")
    return tid


def _balance(t: dict, line: int) -> int | None:
    bal = t.get("Balance")
    if bal is None:
        return None
    if not isinstance(bal, dict):
        raise OpenBankingImportError(f"line {line}: the balance is not an object")
    if bal.get("Type", "InterimBooked") not in BOOKED_BALANCES:
        return None  # an available or expected balance is not the booked balance after the line
    return _signed(bal, "CreditDebitIndicator", _money(bal.get("Amount"), "balance", line), line)


def _order(group: list[tuple], before: int | None) -> list[tuple] | None:
    """Lines booked at the same moment, in the one order their per-line balances chain in from
    `before` (the walked balance before them, None if not yet known); None if no order does."""
    if len(group) == 1 or all(x.balance is None for _, x in group):
        return group
    steps = 0

    def search(rest, bal, done):
        nonlocal steps
        if not rest:
            return done
        for k, (key, x) in enumerate(rest):
            steps += 1
            if steps > ORDER_STEPS:
                return None
            after = None if bal is None else bal + x.amount
            if x.balance is not None and after is not None and x.balance != after:
                continue
            nxt = x.balance if x.balance is not None else after
            found = search(rest[:k] + rest[k + 1:], nxt, [*done, (key, x)])
            if found is not None:
                return found
        return None

    return search(group, before, [])


def _first_break(group: list[tuple], before: int | None) -> contract.Line:
    """The first line, in the order the file dates them, whose per-line balance does not follow."""
    bal = before
    for _, x in group:
        after = None if bal is None else bal + x.amount
        if x.balance is not None and after is not None and x.balance != after:
            return x
        bal = x.balance if x.balance is not None else after
    return group[0][1]


def _statement_balance(raw: str | None, at_raw: str | None) -> tuple[int, date] | None:
    if raw is None and at_raw is None:
        return None
    if raw is None:
        raise OpenBankingImportError("--at needs --statement-balance: the balance at the end of that date")
    if at_raw is None:
        raise OpenBankingImportError("--statement-balance needs --at: the date whose closing balance it is")
    text = raw.strip()
    p = text_pence(text) if SIGNED_AMOUNT.match(text) else None
    if p is None:
        raise OpenBankingImportError("--statement-balance is not a plain amount like -12.40")
    if at_limit(p):
        raise OpenBankingImportError(f"--statement-balance is {AT_LIMIT}; no figure could be reported to the penny")
    try:
        at = date.fromisoformat(at_raw.strip())
    except ValueError:
        raise OpenBankingImportError("--at is not a date like 2026-04-30") from None
    return p, at


def convert(src: Path, dst: Path, *, statement_balance: str | None = None, at: str | None = None,
            today: date | None = None) -> ImportResult:
    """`statement_balance` is the balance at the end of the date `at`, as the person gave it
    (read exactly); both or neither."""
    today = today or date.today()
    supplied = _statement_balance(statement_balance, at)
    data = Path(src).read_bytes()  # read once
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        line = data.count(b"\n", 0, e.start) + 1
        raise OpenBankingImportError(f"line {line}: a byte that is not UTF-8") from None
    breaks = [i for i, c in enumerate(text) if c == "\n"]

    def lines_at(offset: int) -> int:
        return bisect.bisect_left(breaks, offset) + 1

    txns = _transactions(_pages(text, lines_at), lines_at)
    if not txns:
        raise OpenBankingImportError(f"{src}: no transactions")

    booked: list[tuple] = []  # ((date, moment, file position), Line)
    first: dict[str, tuple[int, dict]] = {}  # each kept id's line and object
    pending = repeated = 0
    pending_ids: set[str] = set()  # a pending transaction a page repeats is counted once
    account = None
    for pos, t in enumerate(txns):
        line = lines_at(t.at)
        status = t.get("Status")
        if status == "Pending":
            tid = t.get("TransactionId")
            if not (isinstance(tid, str) and tid in pending_ids):
                pending += 1  # left out, whatever else it holds
            if isinstance(tid, str):
                pending_ids.add(tid)
            continue
        if status != "Booked":
            raise OpenBankingImportError(f"line {line}: Status is neither Booked nor Pending")
        acc = t.get("AccountId")
        if acc is not None:
            if account is None:
                account = (acc, line)
            elif acc != account[0]:
                raise OpenBankingImportError(f"line {line}: another account's transaction (the first is on line "
                                             f"{account[1]}); import one account per file")
        tid = _id(t, line)
        if tid in first:
            if first[tid][1] == t:
                repeated += 1  # a page repeated it: kept once
                continue
            raise OpenBankingImportError(f"line {line}: transaction id repeated from line {first[tid][0]} with "
                                         "different content")
        first[tid] = (line, t)
        amount = _signed(t, "CreditDebitIndicator", _money(t.get("Amount"), "amount", line), line)
        d, moment = _when(_text(t, "BookingDateTime", line), line, today)
        x = contract.Line(line=line, id=tid, date=d, description=_description(t, amount, line), amount=amount,
                          balance=_balance(t, line))
        booked.append(((d, moment, pos), x))
    if not booked:
        raise OpenBankingImportError(f"{src}: no booked transactions ({pending} pending left out)")
    reached = flows_at_limit([x.amount for _, x in booked])
    if reached:
        raise OpenBankingImportError(f"{' and '.join(reached)} {'total' if len(reached) > 1 else 'totals'} "
                                     f"{AT_LIMIT}: no figure could be reported to the penny")

    # Oldest first. A file written newest first keeps its lines booked at one moment in the
    # reverse of the order it wrote them, before their balances order them.
    newest_first = (booked[0][0][:2] > booked[-1][0][:2])
    booked.sort(key=lambda b: (b[0][0], b[0][1], -b[0][2] if newest_first else b[0][2]))

    # Lines booked at one moment, in the order their per-line balances chain in. The chain is
    # the per-line balances' own, so the order is found before any opening balance is known.
    lines: list[contract.Line] = []
    walked: int | None = None  # the balance after the lines ordered so far, once a per-line one is seen
    i = 0
    while i < len(booked):
        j = i
        while j < len(booked) and booked[j][0][:2] == booked[i][0][:2]:
            j += 1
        group = _order(booked[i:j], walked)
        if group is None:
            raise OpenBankingImportError(f"line {_first_break(booked[i:j], walked).line}: no order of the "
                                         "transactions booked at the same time keeps the balance chain; the "
                                         "export may be missing transactions")
        for _, x in group:
            walked = x.balance if walked is None else walked + x.amount
            lines.append(x)
        i = j

    # The opening balance: from the statement balance supplied, else the first per-line balance;
    # with both, they must agree.
    k = next((k for k, x in enumerate(lines) if x.balance is not None), None)
    implied = None if k is None else lines[k].balance - sum(x.amount for x in lines[:k + 1])
    if supplied is not None:
        bal_at, at_date = supplied
        start, end = lines[0].date, lines[-1].date
        if not start - timedelta(days=1) <= at_date <= end:
            raise OpenBankingImportError(f"--at {at_date} is outside the file's period, {start - timedelta(days=1)} "
                                         f"(the day before its first transaction) to {end}")
        before_at = sum(x.amount for x in lines if x.date <= at_date)
        opening = bal_at - before_at
        source = f"statement balance supplied at {at_date}"
        if implied is not None and implied != opening:
            raise OpenBankingImportError(f"--statement-balance {pounds_text(bal_at)} at {at_date} disagrees with "
                                         f"the per-line balances, which give {pounds_text(implied + before_at)} "
                                         f"then (line {lines[k].line}); leave it out to use them")
    elif implied is not None:
        opening, source = implied, PER_LINE
    else:
        raise OpenBankingImportError(f"{src}: no per-line balances; pass --statement-balance AMOUNT --at DATE "
                                     "(a balance from the bank's statement, at the end of a date)")
    try:
        balances = contract.walk(lines, opening, column="the per-line balance")
        contract.write_statement(dst, lines, balances, label=src)
    except ImportRefused as e:  # said as this importer's refusal
        raise OpenBankingImportError(str(e)) from None
    return ImportResult(rows=len(lines), start=lines[0].date, end=lines[-1].date,
                        opening_balance=pounds(opening), closing_balance=pounds(balances[-1]),
                        balance_source=source, pending=pending, repeated=repeated)
