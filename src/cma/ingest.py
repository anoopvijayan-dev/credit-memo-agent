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
    pounds_text,
    text_pence,
)
from .models import Statement, Transaction

__all__ = ["MAX_STATEMENT_PENCE", "StatementError", "load_csv"]

REQUIRED = ("id", "date", "description", "amount", "balance")


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
        # Every problem found is named (revision 31, T04 review 57).
        super().__init__(f"{path}: {len(problems)} problem(s)\n  " + "\n  ".join(whole + limit + rest))


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
        # Whether reading stops at this record: an unreadable record that is not a field too
        # long to read or a bad byte, or a quoted field left open to the end of the file.
        self.stops = False


def shown(raw: str | None, limit: int = 40) -> str:
    """A cell quoted in a problem, cut to `limit` characters so a huge one is not echoed."""
    text = raw or ""
    return repr(text) if len(text) <= limit else f"{text[:limit]!r}… ({len(text):,} characters)"


def _split_record(text: str) -> list[str]:
    return _split_lines(io.StringIO(text, newline=""))[0]


def _split_record_ended(text: str) -> tuple[list[str], bool]:
    cells, ended, _ = _split_lines(io.StringIO(text, newline=""))
    return cells, ended


def _split_lines(lines) -> tuple[list[str], bool, int]:
    """The cells of the record the physical `lines` start with, by the CSV reader's own rules
    (the default dialect, not strict), with no field-size limit; whether it ended at a line
    end; and how many lines it used. `lines` is consumed only as far as the record goes, so
    measuring a record costs its own length, never the rest of the file (T04 reviews 39, 47
    and 51). Used for a record with a field too long for the reader, so the process-wide
    limit is never changed."""
    cells: list[str] = []
    cell: list[str] = []
    state = "start"
    used = 0
    for physical_line in lines:
        used += 1
        for ch in physical_line:
            if state == "quoted":
                if ch == '"':
                    state = "quote"
                else:
                    cell.append(ch)
                continue
            if ch in "\r\n" and state != "quoted":
                cells.append("".join(cell))
                return cells, True, used
            if state == "quote":  # just after a quote inside a quoted field
                if ch == '"':
                    cell.append('"')
                    state = "quoted"
                elif ch == ",":
                    cells.append("".join(cell))
                    cell, state = [], "start"
                else:
                    cell.append(ch)
                    state = "field"
            elif ch == ",":
                cells.append("".join(cell))
                cell, state = [], "start"
            elif state == "start" and ch == '"':
                state = "quoted"
            else:
                cell.append(ch)
                state = "field"
    cells.append("".join(cell))
    return cells, False, used


def _long_row(cells: list[str], header: list[str]) -> dict:
    """Cells as the reader's dict for the header, extra cells under None as the reader puts them."""
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


def _decoded(data: bytes) -> tuple[str, int | None]:
    """The file's text, a byte-order mark set aside, and the offset of its first byte that is
    not UTF-8 (counting the mark), if any. A bad byte is decoded as a marked character
    (surrogateescape), so reading goes on past it (T04 reviews 31 and 46)."""
    bom = len(codecs.BOM_UTF8) if data.startswith(codecs.BOM_UTF8) else 0
    body = data[bom:]
    try:
        return body.decode("utf-8"), None
    except UnicodeDecodeError as e:
        return body.decode("utf-8", "surrogateescape"), e.start + bom


def _head(text: str, bad: int | None) -> tuple[list[str], list[str], int]:
    """The stripped header, the file's physical lines, and how many lines the header used. A
    header cell too long for the reader, or with a bad byte, is raised, in cell order (T04
    review 45). Lines are counted as the file has them, blank lines included (T04 review 20)."""
    _check_header(text, bad)
    physical = list(io.StringIO(text, newline=""))
    reader = csv.DictReader(io.StringIO(text, newline=""), restval="")
    try:
        header = [h.strip() for h in (reader.fieldnames or [])]
    except csv.Error as e:
        raise CsvRecordError(f"line 1: not a readable CSV record ({e})") from None
    return header, physical, reader.reader.line_num


def _check_header(text: str, bad: int | None) -> list[str]:
    """The header's cells, refused cell by cell in order (a cell too long for the reader, or
    with a bad byte, T04 review 45), and refused if a quoted field in it is never closed: it
    would swallow the whole file, which is then no export with no transactions but a
    header that cannot be read, named on line 1 (T04 review 53)."""
    limit = csv.field_size_limit()
    cells, ended = _split_record_ended(text)
    for cell in cells:
        if len(cell) > limit:
            raise CsvRecordError(f"line 1: {TOO_LONG} (header): longer than {limit:,} characters", too_long=True)
        if _NOT_UTF8_BYTE.search(cell):
            raise not_utf8(1, bad)
    if not ended and _ends_in_quotes(text):
        raise CsvRecordError("line 1: a quoted field in the header is not closed before the end of the file")
    return cells


def read_header(data: bytes) -> list[str]:
    """Only a file's header, read from its first record alone, so an importer can check its
    columns before any record is read (T04 review 51)."""
    text, bad = _decoded(data)
    if not text or text[0] in "\r\n":
        return []
    return [c.strip() for c in _check_header(text, bad)]


def iter_data(data: bytes):
    """The header, and the file's records read one at a time, each as (line, row, problem):
    problem None for a record read whole; a CsvRecordError for one that cannot be (a field
    too long to read, a bad byte, a quoted field left open), carrying its readable cells; and,
    when reading stops at a record it cannot read at all, (line, None, problem). Nothing past
    the record a caller stops at is ever read (T04 review 52)."""
    text, bad = _decoded(data)
    header, physical, after = _head(text, bad)
    return header, _records(physical, after, header, bad)


def read_data(data: bytes) -> tuple[list[str], list[tuple[int, dict]], CsvRecordError | None]:
    """iter_data, read to the end: the records before the first one that cannot be read whole,
    and that record's CsvRecordError, carrying the records after it (`rest`) and what came
    next (`rest_err`), and so on; `rest` is None where reading stopped."""
    header, records = iter_data(data)
    top: list[tuple[int, dict]] = []
    first: CsvRecordError | None = None
    previous: CsvRecordError | None = None
    current = top
    for line, row, problem in records:
        if problem is None:
            current.append((line, row))
            continue
        if previous is None:
            first = problem
        else:
            previous.rest_err = problem
        if problem.stops or row is None:
            problem.rest = None
            break
        problem.rest = current = []
        previous = problem
    return header, top, first


def read_records(text: str) -> tuple[list[str], list[tuple[int, dict]], CsvRecordError | None]:
    """read_data on text already decoded."""
    return read_data(text.encode("utf-8", "surrogateescape"))


class _Feed:
    """The physical lines from a position on, fed to a CSV reader one at a time; the position
    is where reading has got to, and can be moved past a record read another way."""

    def __init__(self, lines: list[str], pos: int):
        self.lines, self.pos = lines, pos

    def __iter__(self):
        return self

    def __next__(self) -> str:
        if self.pos >= len(self.lines):
            raise StopIteration
        self.pos += 1
        return self.lines[self.pos - 1]


def _records(physical: list[str], pos: int, header: list[str], bad: int | None):
    """The records from physical line index `pos` on, one at a time, in one pass over the
    lines: past a record with a field too long to read, reading goes on from the line after
    it, so the work is the length read however many such records there are, and nothing past
    the record a caller stops at is read (T04 reviews 47, 50, 51 and 52)."""
    feed = _Feed(physical, pos)
    first_bad = [bad]

    def begin(after: int) -> int:
        line = after + 1
        while line <= len(physical) and physical[line - 1].strip("\r\n") == "":
            line += 1
        return line

    def tail(line: int):
        return (physical[k] for k in range(line - 1, len(physical)))

    def bad_columns(cells: list[str]) -> set:
        """The columns whose cells hold a bad byte, by position, read from the record's own
        cells: blank header names that repeat collapse in a row's dict, which would hide a bad
        byte under the first of them (T04 review 55)."""
        return {header[k] if k < len(header) else None for k, c in enumerate(cells) if _NOT_UTF8_BYTE.search(c)}

    while True:
        # A missing trailing cell reads as "" rather than None, so no check meets a None (T04 review 29).
        reader = csv.DictReader(feed, fieldnames=header, restval="")
        while True:
            before = feed.pos
            try:
                row = next(reader)
            except StopIteration:
                return
            except csv.Error as e:
                line = begin(before)
                if "field larger than field limit" not in str(e):
                    err = CsvRecordError(f"line {line}: not a readable CSV record ({e})")
                    err.line, err.stops = line, True
                    yield line, None, err
                    return
                # Named as too long to read, and by its column where known; its length says
                # nothing about its value, so no monetary conclusion is drawn (revision 29; T04
                # review 40). It is never read as a value (T04 review 37); only the columns the
                # record holds are readable, never one filled in as empty (T04 review 41), and
                # never a cell with a bad byte (T04 review 48).
                limit = csv.field_size_limit()
                cells, ended, used = _split_lines(tail(line))
                long_ = [h for h, c in zip(header, cells, strict=False) if len(c) > limit]
                which = f" ({', '.join(long_)})" if long_ else ""
                message = f"line {line}: {TOO_LONG}{which}: longer than {limit:,} characters"
                open_ = not ended and _ends_in_quotes("".join(physical[line - 1:]))
                if open_:
                    # A quoted field open to the end of the file has swallowed every line after
                    # its start: reading stops here, and no rule of the caller's may skip the
                    # record and leave those lines unread (T04 review 49).
                    message += "; a quoted field in it is not closed before the end of the file"
                if bad_columns(cells):
                    # A bad byte in another of its cells is named with it, never dropped by
                    # the record being too long as well (T04 review 58).
                    message += "; " + str(not_utf8(line, first_bad[0])).removeprefix(f"line {line}: ")
                    first_bad[0] = None
                err = CsvRecordError(message, too_long=True)
                err.line, err.row = line, _long_row(cells, header)
                held = cells[:-1] if open_ else cells
                err.readable = ({h for h, c in zip(header[:len(held)], held, strict=False) if len(c) <= limit}
                                - bad_columns(cells))
                err.stops = open_
                yield line, err.row, err
                if open_:
                    return
                feed.pos = line - 1 + used  # reading goes on from the line after it (T04 review 47)
                break
            # Each record is handed out as soon as it is read: never a second record ahead
            # (revision 30, T04 review 55). The reader never reads past a record's own lines,
            # so a record that reaches the file's last line without a line end outside quotes
            # is the one whose quoted field is still open at the end of the file.
            line = begin(before)
            lines = physical[line - 1:feed.pos]
            cells, ended, _ = _split_lines(iter(lines))
            open_ = feed.pos >= len(physical) and not ended and _ends_in_quotes("".join(lines))
            bad_cells = bad_columns(cells) if _NOT_UTF8_BYTE.search("".join(lines)) else set()
            if not (bad_cells or open_):
                yield line, row, None
                continue
            if bad_cells:
                # A bad byte: the record is named, the first by the byte's offset, with its cells
                # without one, and reading goes on past it (T04 review 46).
                err = not_utf8(line, first_bad[0])
                first_bad[0] = None
                if open_:
                    err = CsvRecordError(f"{err}; a quoted field in it is not closed before the end of the file")
            else:
                # A quoted field still open at the end of the file would hold every line after
                # its start: refused, naming the line it starts on (T04 review 32).
                err = CsvRecordError(f"line {line}: a quoted field is not closed before the end of the file")
            # Readable: the record's cells without a bad byte; with a quoted field left open, only
            # those wholly before it (T04 reviews 38 and 54). An open record stops reading, so
            # no caller may skip it and read on (T04 reviews 49 and 54).
            held = header[:max(len(cells) - 1, 0)] if open_ else header
            err.line, err.row, err.stops = line, row, open_
            err.readable = set(held) - bad_cells
            yield line, row, err
            if open_:
                return


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
        # Records are read one at a time, and each is checked as it is read (T04 review 53).
        header, records = iter_data(data)
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
    problems: list[str] = []
    kinds: list[str] = []

    def add(kind: str, text: str) -> None:
        problems.append(text)
        kinds.append(kind)

    parsed: list[tuple[int, str, date | None, str, float | None, float | None]] = []
    # Each record's readable amount and balance in exact pence, those at the line limit
    # included, for the checks across records (T04 reviews 60 and 61).
    exact: dict[int, tuple[int | None, int | None]] = {}
    seen: dict[str, int] = {}
    def check(i: int, r: dict, readable: set | None = None) -> None:
        """One record's own values, checked as it is read; i is its line in the file, blank
        lines counted. `readable`: for a record that cannot be read whole, the fields that
        can be, each checked as in a whole record (T04 review 56); such a record is not
        kept as a transaction."""
        whole = readable is None
        can = (lambda f: True) if whole else (lambda f: f in readable)
        if whole and None in r:
            add("line", f"line {i}: more fields than the header has columns")
        tid = (r["id"] or "").strip() if can("id") else ""
        if can("id"):
            if not tid:
                add("line", f"line {i}: empty id")
            elif tid in seen:
                add("line", f"line {i}: duplicate id {tid} (first on line {seen[tid]})")
            else:
                seen[tid] = i
        d = None
        if can("date"):
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
        values = {}
        for field, example in (("amount", "-1150.00"), ("balance", "2304.17")):
            if not can(field):
                continue
            raw = r[field]
            values[field] = _number(raw)
            if values[field] is None and _over_limit(raw):
                add("limit", f"line {i}: {field} {shown(raw)} is {AT_LIMIT}; no figure could be reported to the penny")
            elif values[field] is None:
                add("line", f"line {i}: {field} {shown(raw)} is not a plain number like {example} "
                                "(under £10,000,000,000,000)")
        # A record that cannot be read whole takes part in the checks across records with the
        # fields it has (its date, amount and balance), and its amount counts toward money in
        # or out (T04 reviews 37 and 57); the statement is refused, so it is never kept.
        description = (r["description"] or "").strip() if can("description") else ""
        parsed.append((i, tid, d, description, values.get("amount"), values.get("balance")))
        # A value at the line limit is read, though too large to keep: it still counts toward
        # money in or out and takes part in the running balance (T04 reviews 60 and 61).
        exact[i] = tuple(
            (float_pence(values[f]) if values[f] is not None
             else text_pence((r[f] or "").strip()) if _over_limit(r[f]) else None) if f in values else None
            for f in ("amount", "balance"))

    any_record = False
    for line, row, problem in records:
        any_record = True
        if problem is None:
            check(line, row)
            continue
        # A record that cannot be read whole: reading stops at it, and the records before it
        # are already checked (T04 review 22).
        add("too_long" if problem.too_long else "line", str(problem))
        # Its own cells that can be read are checked too (T04 reviews 36-37): every one, if all
        # the columns are readable; otherwise its money cells, against the line limit, the
        # balance as well as the amount (T04 review 43), and a readable amount toward the flows.
        if row is not None and set(REQUIRED) <= problem.readable:
            check(line, row)
        elif row is not None:
            check(line, row, problem.readable)
        break
    if not any_record:
        raise StatementError(path, ["no transactions"])

    # Banks export newest first or oldest first. Decide from the dates, then insist
    # the whole file runs one way.
    # Checks that relate records to one another are made once reading has stopped (revision
    # 31): which way the file runs, decided from its dates, whether the dates keep to it, and
    # whether each balance follows from the one before.
    dates = [d for _, _, d, _, _, _ in parsed if d]
    if dates and dates[0] > dates[-1]:
        parsed.reverse()
    prev_date: date | None = None
    for line, _, d, _, _, _ in parsed:
        if d and prev_date and d < prev_date:
            add("line", f"line {line}: date {d} is out of order (after {prev_date})")
        prev_date = d or prev_date

    prev_balance: int | None = None
    for line, *_ in parsed:
        amount, balance = exact[line]
        if None not in (amount, balance, prev_balance) and not follows(prev_balance, amount, balance):
            add("line",
                f"line {line}: running balance {pounds_text(balance)} does not follow from previous "
                f"{pounds_text(prev_balance)} {'+' if amount >= 0 else '-'} {pounds_text(abs(amount))}"
            )
        if balance is not None:
            prev_balance = balance

    for name in flows_at_limit([a for a, _ in exact.values() if a is not None]):
        add("flow", f"{name} totals {AT_LIMIT}: no figure could be reported to the penny")  # each named
    if problems:
        raise StatementError(path, problems, kinds)
    txns = [Transaction(id=tid, date=d, description=desc, amount=amount, balance=balance)
            for _, tid, d, desc, amount, balance in parsed]
    return Statement(account_name=account_name or "Applicant", transactions=txns)
