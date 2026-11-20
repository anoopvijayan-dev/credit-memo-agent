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

import codecs
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

    def __init__(self, path: Path, problems: list[str], kinds: list[str] | None = None):
        # Each problem's kind is recorded where it is found, never read back from its text,
        # which can quote a cell (T04 review 22): "flow" (money in or out at the limit),
        # "file" (the file as a whole), "limit" (a line's amount or balance at the limit),
        # "too_long" (a field too long to read) or "line". Problems with the whole
        # statement, then lines at the limit or too long to read, are always said in full;
        # other line problems fill the rest of the space (R2, T04 review 13).
        kinds = kinds or ["line" if p.startswith("line ") else "file" for p in problems]
        order = {"flow": 0, "file": 0, "limit": 1, "too_long": 1, "line": 2}
        pairs = sorted(zip(problems, kinds, strict=True), key=lambda pk: order[pk[1]])
        self.problems = [p for p, _ in pairs]
        self.kinds = [k for _, k in pairs]
        whole = [p for p, k in pairs if order[k] == 0]
        limit = [p for p, k in pairs if order[k] == 1]
        rest = [p for p, k in pairs if order[k] == 2]
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


# How a field the CSV reader cannot read is reported: whether it is an amount is not known.
TOO_LONG = "a field is too long to read"


class CsvRecordError(ValueError):
    """The CSV reader could not read a record; the message names its line in the file."""

    def __init__(self, message: str, too_long: bool = False):
        super().__init__(message)
        self.too_long = too_long


NOT_UTF8 = "not UTF-8 (byte {}); re-save it as 'CSV UTF-8' (Excel) and try again"


def _ends_in_quotes(text: str) -> bool:
    """Whether a record's text ends inside a quoted field, by the rules the CSV reader reads
    it with (the default dialect, not strict): a quote opens a field only at its start, a
    doubled quote inside one is a quote, and text after a closing quote joins the field
    (T04 review 32)."""
    state = "start"
    for ch in text:
        if state == "quoted":
            state = "quote" if ch == '"' else "quoted"
        elif state == "quote":  # just after a quote inside a quoted field
            state = "quoted" if ch == '"' else "start" if ch in ",\r\n" else "field"
        elif ch in ",\r\n":
            state = "start"
        elif state == "start" and ch == '"':
            state = "quoted"
        else:
            state = "field"
    return state == "quoted"


def read_data(data: bytes) -> tuple[list[str], list[tuple[int, dict]], CsvRecordError | None]:
    """read_records on a file's bytes. If a byte is not UTF-8, every record before the one it
    falls in is still returned and checked, and the CsvRecordError names that record's first
    line, whatever the file's line endings (CR, LF or CRLF) and even when the record runs
    over several lines (T04 reviews 29-30). A bad byte in the header line is raised."""
    # A byte-order mark is set aside first, so the bad byte's offset and the slice before it
    # are both taken in the same bytes (T04 review 31); the offset reported counts the mark.
    bom = len(codecs.BOM_UTF8) if data.startswith(codecs.BOM_UTF8) else 0
    body = data[bom:]
    try:
        return read_records(body.decode("utf-8"))
    except UnicodeDecodeError as e:
        bad = e.start
    text = body[:bad].decode("utf-8")
    bad += bom
    physical = list(io.StringIO(text, newline=""))
    if not physical or not physical[0].endswith(("\n", "\r")):
        raise CsvRecordError("line 1: " + NOT_UTF8.format(bad))
    header, rows, err = read_records(text, cut=True)
    if err is not None:
        return header, rows, err  # an earlier record the reader stopped on comes first
    at_line_end = text.endswith(("\n", "\r"))
    line = len(physical) + 1  # the bad byte starts a new record
    if rows:
        start = rows[-1][0]
        # Is the last record closed, or still open in a quoted field at the bad byte? Read as
        # the reader reads it, so a record it accepted is never dropped (T04 review 32).
        if _ends_in_quotes("".join(physical[start - 1:])) or not at_line_end:
            rows.pop()  # the record the bad byte falls in
            line = start
    return header, rows, CsvRecordError(f"line {line}: " + NOT_UTF8.format(bad))


def read_records(text: str, cut: bool = False) -> tuple[list[str], list[tuple[int, dict]], CsvRecordError | None]:
    """The stripped header and each record with its line in the file. Lines are counted as
    the file has them, blank lines included, so every problem names the line a person sees
    (T04 review 20). The CSV reader stops on a field longer than it reads, or a malformed
    record: the records before it are returned with the CsvRecordError naming its line, so
    the caller still checks them (T04 review 22); a header it cannot read is raised."""
    # The physical lines as the CSV reader sees them, to find where each record starts: a
    # record is named by its first line, never by a continuation of a multi-line field
    # (T04 review 21). Blank lines between records are skipped by the reader and counted here.
    physical = list(io.StringIO(text, newline=""))
    # A missing trailing cell reads as "" rather than None, so no check meets a None (T04 review 29).
    reader = csv.DictReader(io.StringIO(text, newline=""), restval="")
    rows: list[tuple[int, dict]] = []

    def start(after: int) -> int:
        line = after + 1
        while line <= len(physical) and physical[line - 1].strip("\r\n") == "":
            line += 1
        return line

    header: list[str] | None = None
    try:
        header = [h.strip() for h in (reader.fieldnames or [])]
        reader.fieldnames = header
        while True:
            before = reader.reader.line_num  # DictReader.line_num lags on an error
            try:
                row = next(reader)
            except StopIteration:
                break
            rows.append((start(before), row))
    except csv.Error as e:
        line = start(before) if "before" in locals() else 1
        if "field larger than field limit" in str(e):
            err = CsvRecordError(f"line {line}: {TOO_LONG} (longer than {csv.field_size_limit():,} characters); "
                                 "an amount or balance that long would be past the £10,000,000,000,000 limit",
                                 too_long=True)
        else:
            err = CsvRecordError(f"line {line}: not a readable CSV record ({e})")
        if header is None:
            raise err from None
        return header, rows, err
    # A quoted field still open at the end of the file would hold every line after its
    # start, other transactions included: refused, naming the line it starts on (T04 review 32).
    # (`cut`: the text stops at a bad byte, and read_data names the record it falls in.)
    if not cut and rows and _ends_in_quotes("".join(physical[rows[-1][0] - 1:])):
        start, _ = rows.pop()
        return header, rows, CsvRecordError(f"line {start}: a quoted field is not closed before the end of the file")
    return header, rows, None


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
    try:
        header, rows, unreadable = read_data(data)
    except CsvRecordError as e:
        raise StatementError(path, [str(e)], ["too_long" if e.too_long else "file"]) from None
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
    if not rows and unreadable is None:
        raise StatementError(path, ["no transactions"])

    problems: list[str] = []
    kinds: list[str] = []

    def add(kind: str, text: str) -> None:
        problems.append(text)
        kinds.append(kind)

    if unreadable is not None:  # the records before it are still checked (T04 review 22)
        add("too_long" if unreadable.too_long else "line", str(unreadable))
    parsed: list[tuple[int, str, date | None, str, float | None, float | None]] = []
    seen: dict[str, int] = {}
    for i, r in rows:  # i: the record's line in the file, blank lines counted
        if None in r:
            add("line", f"line {i}: more fields than the header has columns")
        tid = (r["id"] or "").strip()
        if not tid:
            add("line", f"line {i}: empty id")
        elif tid in seen:
            add("line", f"line {i}: duplicate id {tid} (first on line {seen[tid]})")
        else:
            seen[tid] = i
        raw_date = (r["date"] or "").strip()
        try:
            d = date.fromisoformat(raw_date)
            if len(raw_date) != 10:
                raise ValueError
        except ValueError:
            add("line", f"line {i}: date {r['date']!r} is not YYYY-MM-DD")
            d = None
        if d and d > today:
            add("line", f"line {i}: date {d} is in the future")
        amount, balance = _number(r["amount"]), _number(r["balance"])
        for field, value, raw in (("amount", amount, r["amount"]), ("balance", balance, r["balance"])):
            if value is None and _over_limit(raw):
                add("limit", f"line {i}: {field} {raw!r} is {AT_LIMIT}; no figure could be reported to the penny")
        if amount is None and not _over_limit(r["amount"]):
            add("line", f"line {i}: amount {r['amount']!r} is not a plain number like -1150.00 "
                            "(under £10,000,000,000,000)")
        if balance is None and not _over_limit(r["balance"]):
            add("line", f"line {i}: balance {r['balance']!r} is not a plain number like 2304.17 "
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
            add("line", f"line {line}: date {d} is out of order (after {prev_date})")
        prev_date = d or prev_date

    prev_balance: float | None = None
    for line, _, _, _, amount, balance in parsed:
        if None not in (amount, balance, prev_balance) and not follows(
                float_pence(prev_balance), float_pence(amount), float_pence(balance)):
            add("line",
                f"line {line}: running balance {balance:.2f} does not follow from previous {prev_balance:.2f} "
                f"{'+' if amount >= 0 else '-'} {abs(amount):.2f}"
            )
        if balance is not None:
            prev_balance = balance

    for name in flows_at_limit([float_pence(a) for _, _, _, _, a, _ in parsed if a is not None]):
        add("flow", f"{name} totals {AT_LIMIT}: no figure could be reported to the penny")  # each named
    if problems:
        raise StatementError(path, problems, kinds)
    txns = [Transaction(id=tid, date=d, description=desc, amount=amount, balance=balance)
            for _, tid, d, desc, amount, balance in parsed]
    return Statement(account_name=account_name or "Applicant", transactions=txns)
