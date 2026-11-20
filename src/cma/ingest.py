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
        # For a field too long to read: the record's line, and its cells read with no size
        # limit, so its currency and amount are still checked first (T04 review 36).
        self.line: int | None = None
        self.row: dict | None = None
        # The record's fields that can be trusted: for a field too long to read, every other
        # field; for a bad byte, the fields without one (T04 reviews 37 and 46).
        self.readable: set[str] = set()
        # For a bad byte only: the records after this one, read on past it, and what stopped
        # reading after them, if anything (T04 review 46). None: reading stopped here.
        self.rest: list[tuple[int, dict]] | None = None
        self.rest_err: CsvRecordError | None = None


def shown(raw: str | None, limit: int = 40) -> str:
    """A cell quoted in a problem, cut to `limit` characters so a huge one is not echoed."""
    text = raw or ""
    return repr(text) if len(text) <= limit else f"{text[:limit]!r}… ({len(text):,} characters)"


def _split_record(text: str) -> list[str]:
    return _split_record_ended(text)[0]


def _split_record_ended(text: str) -> tuple[list[str], bool]:
    """The cells of the record at the start of `text`, by the CSV reader's own rules (the
    default dialect, not strict), with no field-size limit. Used only for a record with a
    field too long for the reader, so the process-wide limit is never changed (T04 review 39).
    Also says whether the record ended at a line end, or ran to the end of `text`."""
    cells: list[str] = []
    cell: list[str] = []
    state = "start"
    ended = False
    for ch in text:
        if state == "quoted":
            if ch == '"':
                state = "quote"
            else:
                cell.append(ch)
        elif state == "quote":  # just after a quote inside a quoted field
            if ch == '"':
                cell.append('"')
                state = "quoted"
            elif ch == ",":
                cells.append("".join(cell))
                cell, state = [], "start"
            elif ch in "\r\n":
                ended = True
                break
            else:
                cell.append(ch)
                state = "field"
        elif ch == ",":
            cells.append("".join(cell))
            cell, state = [], "start"
        elif ch in "\r\n":
            ended = True
            break
        elif state == "start" and ch == '"':
            state = "quoted"
        else:
            cell.append(ch)
            state = "field"
    cells.append("".join(cell))
    return cells, ended


def _read_long_record(text: str, header: list[str]) -> dict:
    """The record at the start of `text` as the reader's dict, with no field-size limit."""
    cells = _split_record(text)
    row: dict = dict(zip(header, cells + [""] * (len(header) - len(cells)), strict=False))
    if len(cells) > len(header):
        row[None] = cells[len(header):]
    return row




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


_NOT_UTF8_BYTE = re.compile("[\udc80-\udcff]")  # a byte that is not UTF-8, decoded with surrogateescape


def unreadable_cells(row: dict) -> set:
    """The columns of a record whose cells hold a byte that is not UTF-8."""
    bad = {k for k, v in row.items() if k is not None and isinstance(v, str) and _NOT_UTF8_BYTE.search(v)}
    if any(_NOT_UTF8_BYTE.search(v) for v in row.get(None) or []):
        bad.add(None)
    return bad


def not_utf8(line: int, byte: int | None = None) -> CsvRecordError:
    where = f" (byte {byte})" if byte is not None else ""
    return CsvRecordError(f"line {line}: not UTF-8{where}; re-save it as 'CSV UTF-8' (Excel) and try again")


def read_data(data: bytes) -> tuple[list[str], list[tuple[int, dict]], CsvRecordError | None]:
    """read_records on a file's bytes. A byte that is not UTF-8 is decoded as a marked
    character (surrogateescape), so reading goes on past it: the records before the first
    record holding one are returned, and the CsvRecordError names that record's line and
    the byte, carries its cells (`readable`: those without a bad byte) and every record
    after it (`rest`, `rest_err`), so a caller can skip a record its own rules exclude, a
    row in another currency, and read on (T04 review 46). The byte offset counts a
    byte-order mark (T04 review 31). A bad byte in the header is raised, after a header
    cell before it that is too long for the reader (T04 review 45)."""
    bom = len(codecs.BOM_UTF8) if data.startswith(codecs.BOM_UTF8) else 0
    body = data[bom:]
    try:
        return read_records(body.decode("utf-8"))
    except UnicodeDecodeError as e:
        bad = e.start + bom
    text = body.decode("utf-8", "surrogateescape")
    limit = csv.field_size_limit()
    for cell in _split_record_ended(text)[0]:  # the header, cell by cell in order
        if len(cell) > limit:
            raise CsvRecordError(f"line 1: {TOO_LONG} (header): longer than {limit:,} characters", too_long=True)
        if _NOT_UTF8_BYTE.search(cell):
            raise not_utf8(1, bad)
    header, rows, err = read_records(text)
    for k, (line, row) in enumerate(rows):
        bad_cells = unreadable_cells(row)
        if bad_cells:
            first = not_utf8(line, bad)
            first.line, first.row, first.readable = line, row, set(header) - bad_cells
            first.rest, first.rest_err = rows[k + 1:], err
            return header, rows[:k], first
    if err is not None and err.row is not None:
        err.readable -= unreadable_cells(err.row)  # a cell with a bad byte is never read
    return header, rows, err


def read_records(text: str) -> tuple[list[str], list[tuple[int, dict]], CsvRecordError | None]:
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
            # Named as too long to read, and by its column where known; its length says nothing
            # about its value, so no monetary conclusion is drawn (revision 29; T04 review 40).
            limit = csv.field_size_limit()
            text_of_record = "".join(physical[line - 1:])
            cells, ended = _split_record_ended(text_of_record) if header is not None else ([], True)
            row = _read_long_record(text_of_record, header) if header is not None else None
            long_ = [h for h, c in zip(header or [], cells, strict=False) if len(c) > limit]
            which = f" ({', '.join(long_)})" if long_ else ""
            err = CsvRecordError(f"line {line}: {TOO_LONG}{which}: longer than {limit:,} characters", too_long=True)
            if header is not None:
                # The field too long to read is never read as a value (T04 review 37). Only the
                # columns the record holds are readable, never one filled in as empty (T04
                # review 41); a byte that is not UTF-8 no longer cuts the text (T04 review 46).
                present = len(cells)
                err.line, err.row = line, row
                err.readable = {h for h, c in zip(header[:max(present, 0)], cells, strict=False) if len(c) <= limit}
        else:
            err = CsvRecordError(f"line {line}: not a readable CSV record ({e})")
        if header is None:
            raise err from None
        return header, rows, err
    # A quoted field still open at the end of the file would hold every line after its
    # start, other transactions included: refused, naming the line it starts on (T04 review 32).
    if rows and _ends_in_quotes("".join(physical[rows[-1][0] - 1:])):
        start, row = rows.pop()
        err = CsvRecordError(f"line {start}: a quoted field is not closed before the end of the file")
        # The fields wholly before the open one can still be read (T04 review 38).
        cells = next(csv.reader(io.StringIO("".join(physical[start - 1:]), newline="")), [])
        err.line, err.row, err.readable = start, row, set(header[:max(len(cells) - 1, 0)])
        return header, rows, err
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

    extra_amounts: list[int] = []  # a readable amount in a record that cannot be read whole
    if unreadable is not None:  # the records before it are still checked (T04 review 22)
        add("too_long" if unreadable.too_long else "line", str(unreadable))
        # Its own cells that can be read are checked too (T04 reviews 36-37): every one, if all
        # the columns are readable; otherwise its amount, toward money in or out.
        if unreadable.row is not None and set(REQUIRED) <= unreadable.readable:
            rows = [*rows, (unreadable.line, unreadable.row)]
        elif unreadable.row is not None:
            # Each readable money cell is still checked against the line limit, the balance as
            # well as the amount (T04 review 43); a readable amount counts toward the flows.
            for field in ("amount", "balance"):
                raw = unreadable.row.get(field)
                if field not in unreadable.readable:
                    continue
                if _over_limit(raw):
                    add("limit", f"line {unreadable.line}: {field} {shown(raw)} is {AT_LIMIT}; no figure could "
                        "be reported to the penny")
                elif field == "amount" and (value := _number(raw)) is not None:
                    extra_amounts.append(float_pence(value))
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
                add("limit", f"line {i}: {field} {shown(raw)} is {AT_LIMIT}; no figure could be reported to the penny")
        if amount is None and not _over_limit(r["amount"]):
            add("line", f"line {i}: amount {shown(r['amount'])} is not a plain number like -1150.00 "
                            "(under £10,000,000,000,000)")
        if balance is None and not _over_limit(r["balance"]):
            add("line", f"line {i}: balance {shown(r['balance'])} is not a plain number like 2304.17 "
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

    for name in flows_at_limit([float_pence(a) for _, _, _, _, a, _ in parsed if a is not None] + extra_amounts):
        add("flow", f"{name} totals {AT_LIMIT}: no figure could be reported to the penny")  # each named
    if problems:
        raise StatementError(path, problems, kinds)
    txns = [Transaction(id=tid, date=d, description=desc, amount=amount, balance=balance)
            for _, tid, d, desc, amount, balance in parsed]
    return Statement(account_name=account_name or "Applicant", transactions=txns)
