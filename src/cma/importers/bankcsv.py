"""Convert a bank's CSV export into the five-column statement format (R38), through a named
bank profile or a custom column mapping, with the same checks as the five-column loader.

A bank's own CSV download rarely is the five-column statement: it can split money into a
paid-in and a paid-out column, write dates as 03/03/2026 or 3 Mar 2026, run newest first, and
name its columns its own way. A mapping says where each of the statement's columns is:

    columns:
      date: Transaction Date                     # required
      description: [Transaction Type, Details]   # required; one column or a list, joined
      amount: Value                              # a signed column; or
      paid_in: Credit Amount                     # two unsigned columns,
      paid_out: Debit Amount                     # one filled per row
      balance: Balance                           # optional: the bank's balance after each row
      id: Reference                              # optional: the bank's own id for each row
    date_format: "%d/%m/%Y"                      # or a list tried in turn
    order: auto                                  # oldest_first, newest_first or auto

The named profiles are packaged (src/cma/bank_profiles/profiles.yaml) and take the same keys;
a firm passes its own mapping file for any other export.

So this module:
- reads each amount exactly, in whole pence, never through a binary float: a plain amount,
  with a £ sign and thousands separators allowed where they are grouped as written in the UK
  (1,234.56); a paid-in or paid-out cell is unsigned, and a paid-out amount is written into
  the statement as money out;
- reads each date with the mapping's formats, month names in English whatever the machine's
  locale;
- turns a newest-first file oldest first: from its dates, or, where every row has one date,
  from the bank's balances; a declared order the dates contradict is refused;
- keeps the bank's id where the mapping names its column, and otherwise gives each line the
  id L and the line of the file its row starts on, so every figure traces back to the file;
- walks the balance from the bank's own balance column, or from an opening balance the person
  supplies, checking every walked balance against the bank's (the importer contract);
- writes the statement only once the statement loader accepts it, so the five-column checks
  (ISO dates none in the future, dates running one way, plain amounts, a balance that follows
  line to line, and the £10,000,000,000,000 limit, R2) all apply.

A refusal names the line of the file a row starts on and what is wrong, never a cell's value
(the importer contract, T28; R34). It reads the file locally and writes a new file. Nothing is
sent anywhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from importlib import resources
from itertools import pairwise
from pathlib import Path
from typing import Any

import yaml

from ..firm import _RepeatedKey, _UniqueKeyLoader
from ..ingest import CsvRecordError, iter_data, read_header
from ..metrics import AT_LIMIT, at_limit, flows_at_limit, follows, pounds, text_pence
from . import contract
from .contract import ImportRefused
from .openbanking import CONTROL, FORMULA_OR_SPACE, MAX_ID

ROLES = ("date", "description", "amount", "paid_in", "paid_out", "balance", "id")
ORDERS = ("oldest_first", "newest_first", "auto")
BALANCE_COLUMN = "the bank's balance column"

# £1,234.56: an optional £, digits grouped in threes by commas (or not at all), and at most
# two decimal places. A signed amount may also start with - or +.
_DIGITS = r"£?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
UNSIGNED = re.compile(rf"^{_DIGITS}$")
SIGNED = re.compile(rf"^[-+]?{_DIGITS}$")

_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
           "october", "november", "december"]
_MONTH_NUMBER = {**{m: i for i, m in enumerate(_MONTHS, 1)}, **{m[:3]: i for i, m in enumerate(_MONTHS, 1)},
                 "sept": 9}
_MONTH_NAME = re.compile(r"(?<![A-Za-z])(" + "|".join(sorted(_MONTH_NUMBER, key=len, reverse=True))
                         + r")(?![A-Za-z])", re.IGNORECASE)


class BankCsvImportError(ImportRefused):
    """A bank CSV refused; an importer's refusal under the contract (T28)."""


@dataclass
class ImportResult:
    rows: int
    start: date
    end: date
    opening_balance: float
    closing_balance: float
    balance_source: str  # "opening balance supplied" or "the bank's balance column"
    order: str  # "oldest first" or "newest first", as the file ran


# --- mappings ---------------------------------------------------------------------------------


def _column(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BankCsvImportError(f"mapping: columns.{key} must be a column name")
    return value.strip()


def _date_format_ok(fmt: str) -> bool:
    """A format names a day, a month and a year, and reads back the date it writes."""
    if not ("%d" in fmt and any(m in fmt for m in ("%m", "%b", "%B")) and any(y in fmt for y in ("%Y", "%y"))):
        return False
    try:
        probe = date(2026, 11, 23)
        return datetime.strptime(probe.strftime(fmt), fmt).date() == probe
    except ValueError:
        return False


def check_mapping(raw: Any) -> dict[str, Any]:
    """The mapping, checked and normalised; refused naming the key that is wrong. A mapping is
    the firm's own configuration, not the applicant's data, so its keys may be named."""
    if not isinstance(raw, dict):
        raise BankCsvImportError("mapping: not a mapping of columns, date_format and order")
    for key in raw:
        if key not in ("columns", "date_format", "order"):
            raise BankCsvImportError(f"mapping: unknown key {key}; a mapping has columns, date_format and order")
    cols = raw.get("columns")
    if not isinstance(cols, dict):
        raise BankCsvImportError("mapping: columns is required, a mapping of the statement's columns to the file's")
    for key in cols:
        if key not in ROLES:
            raise BankCsvImportError(f"mapping: unknown key columns.{key}; the columns are {', '.join(ROLES)}")
    for key in ("date", "description"):
        if key not in cols:
            raise BankCsvImportError(f"mapping: columns.{key} is required")
    out: dict[str, Any] = {}
    for key, value in cols.items():
        if key == "description":
            names = value if isinstance(value, list) else [value]
            if not names:
                raise BankCsvImportError("mapping: columns.description must name at least one column")
            out[key] = [_column(v, key) for v in names]
        else:
            out[key] = _column(value, key)
    split = "paid_in" in out or "paid_out" in out
    if ("amount" in out) == split or (split and not ("paid_in" in out and "paid_out" in out)):
        raise BankCsvImportError("mapping: columns names either amount or paid_in and paid_out, not both "
                                 "and not one of the two alone")
    used: dict[str, str] = {}
    for key, value in out.items():
        for name in value if isinstance(value, list) else [value]:
            if (name in used and used[name] != key) or (key == "description" and value.count(name) > 1):
                raise BankCsvImportError(f"mapping: uses column {name} for more than one of the statement's columns")
            used[name] = key
    fmts = raw.get("date_format")
    if fmts is None:
        raise BankCsvImportError("mapping: date_format is required, such as \"%d/%m/%Y\"")
    fmts = fmts if isinstance(fmts, list) else [fmts]
    if not fmts or not all(isinstance(f, str) and _date_format_ok(f) for f in fmts):
        bad = next((f for f in fmts if not (isinstance(f, str) and _date_format_ok(f))), None)
        raise BankCsvImportError(f"mapping: date_format {bad!r} must name a day, a month and a year (such as "
                                 "%d/%m/%Y) and read back the date it writes")
    order = raw.get("order", "auto")
    if order not in ORDERS:
        raise BankCsvImportError("mapping: order is oldest_first, newest_first or auto")
    return {"columns": out, "date_format": list(fmts), "order": order}


def _load(text: str) -> Any:
    """safe_load with a key given twice refused (firm.yaml's rule): YAML would otherwise keep
    the last value, so `paid_in: Out` then `paid_in: In` would silently swap money in and out."""
    return yaml.load(text, Loader=_UniqueKeyLoader)  # a SafeLoader subclass


def _profiles() -> dict[str, Any]:
    return _load(resources.files("cma.bank_profiles").joinpath("profiles.yaml").read_text("utf-8"))


def profile_names() -> list[str]:
    """The packaged bank profiles, by name."""
    return sorted(_profiles())


def profile(name: str) -> dict[str, Any]:
    """A packaged bank profile's mapping, checked."""
    table = _profiles()
    if name not in table:
        raise BankCsvImportError(f"unknown bank profile; use one of {', '.join(sorted(table))}, or "
                                 "--format bank-csv with --mapping FILE")
    return check_mapping(table[name])


def parse_mapping(data: bytes, where: str = "the mapping file") -> dict[str, Any]:
    try:
        raw = _load(data.decode("utf-8-sig"))
    except _RepeatedKey as e:
        raise BankCsvImportError(f"{where}: key {e.key} is given more than once; keep one") from None
    except (UnicodeDecodeError, yaml.YAMLError):
        raise BankCsvImportError(f"{where}: not readable YAML") from None
    if not isinstance(raw, dict):
        raise BankCsvImportError(f"{where}: not a mapping of columns, date_format and order")
    return check_mapping(raw)


def load_mapping(path: Path) -> dict[str, Any]:
    """A custom mapping from a YAML file, checked."""
    path = Path(path)
    return parse_mapping(path.read_bytes(), path.name)


# --- cells ------------------------------------------------------------------------------------


def _pence(raw: str | None, column: str, line: int, *, signed: bool) -> int | None:
    """The cell's amount in whole pence, read exactly; None for an empty cell."""
    text = (raw or "").strip()
    if not text:
        return None
    if not (SIGNED if signed else UNSIGNED).match(text):
        kind = "a plain amount like -1,234.56" if signed else "an unsigned amount like 1,234.56"
        raise BankCsvImportError(f"line {line}: {column} is not {kind}")
    p = text_pence(text.replace("£", "").replace(",", ""))  # exact, in the figures layer (R2)
    if p is None:
        raise BankCsvImportError(f"line {line}: {column} is not whole pence")
    if at_limit(p):
        raise BankCsvImportError(f"line {line}: {column} is {AT_LIMIT}; no figure could be reported to the penny")
    return p


def _money_out(raw: str, column: str, line: int) -> int:
    """A paid-out cell as money out: its text written with a minus sign, read exactly."""
    text = raw.strip().replace("£", "").replace(",", "")
    p = text_pence(f"-{text}")
    if p is None:  # cannot happen for a cell _pence read; kept as a refusal, never a crash
        raise BankCsvImportError(f"line {line}: {column} is not whole pence")
    return p


def _date(raw: str | None, column: str, formats: list[str], line: int) -> date:
    text = " ".join((raw or "").split())
    for fmt in formats:
        cell, f = text, fmt
        if "%b" in fmt or "%B" in fmt:  # English month names, whatever the locale
            cell = _MONTH_NAME.sub(lambda m: f"{_MONTH_NUMBER[m.group(1).lower()]:02d}", text)
            f = fmt.replace("%B", "%m").replace("%b", "%m")
        try:
            return datetime.strptime(cell, f).date()
        except ValueError:
            continue
    raise BankCsvImportError(f"line {line}: {column} is not a date in the form {' or '.join(formats)}")


def _id(raw: str | None, column: str, line: int) -> str:
    tid = (raw or "").strip()
    if not tid:
        raise BankCsvImportError(f"line {line}: {column}, the row's id, is empty")
    if tid.startswith(FORMULA_OR_SPACE) or CONTROL.search(tid) or len(tid) > MAX_ID:
        raise BankCsvImportError(f"line {line}: {column}, the row's id, starts with a character a spreadsheet "
                                 f"reads as a formula (= + - @), holds a control character or is longer than "
                                 f"{MAX_ID} characters")
    return tid


# --- order ------------------------------------------------------------------------------------


def _chain_holds(lines: list[contract.Line]) -> bool:
    return all(a.balance is not None and b.balance is not None and follows(a.balance, b.amount, b.balance)
               for a, b in pairwise(lines))


def _newest_first(lines: list[contract.Line], declared: str) -> bool:
    """Whether the file runs newest first, from its dates, or, where its first and last rows
    share a date, from the bank's balances; a declared order the dates contradict is refused."""
    first, last = lines[0].date, lines[-1].date
    by_dates = None if first == last else first > last
    if declared != "auto":
        newest = declared == "newest_first"
        if by_dates is not None and by_dates != newest:
            runs = "newest first" if by_dates else "oldest first"
            raise BankCsvImportError(f"the mapping says {declared}, but its dates run {runs} (line "
                                     f"{lines[0].line} to line {lines[-1].line}); set order to auto or fix it")
        return newest
    if by_dates is not None:
        return by_dates
    return not _chain_holds(lines) and _chain_holds(lines[::-1])


# --- convert ----------------------------------------------------------------------------------


def convert(src: Path, dst: Path, *, mapping: dict[str, Any], opening_balance: str | None = None) -> ImportResult:
    """`mapping` is a checked mapping (profile() or load_mapping()); `opening_balance` is the text
    the person gave, read exactly, for an export with no balance column."""
    mapping = check_mapping(mapping)  # idempotent: a checked mapping passes unchanged
    cols = mapping["columns"]
    formats = mapping["date_format"]
    supplied = None
    if opening_balance is not None:
        text = opening_balance.strip()
        if not SIGNED.match(text) or text_pence(text.replace("£", "").replace(",", "")) is None:
            raise BankCsvImportError("--opening-balance is not a plain amount like -1,234.56")
        supplied = text_pence(text.replace("£", "").replace(",", ""))  # not a line: not held to the line limit

    data = Path(src).read_bytes()  # read once
    try:
        header = read_header(data)
    except CsvRecordError as e:
        raise BankCsvImportError(f"{src}: {e}") from None
    twice = sorted({h for h in header if h and header.count(h) > 1})
    if twice:
        raise BankCsvImportError(f"{src}: column(s) named more than once: {', '.join(twice)}; keep one of each")
    wanted = [c for role in ROLES if role in cols for c in (cols[role] if role == "description" else [cols[role]])]
    missing = [c for c in wanted if c not in header]
    if missing:
        raise BankCsvImportError(f"{src}: the file does not match the mapping; missing column(s): {', '.join(missing)}")
    try:
        header, records = iter_data(data)
    except CsvRecordError as e:
        raise BankCsvImportError(f"{src}: {e}") from None

    lines: list[contract.Line] = []
    seen: dict[str, int] = {}
    for i, r, problem in records:
        if problem is not None:  # a field too long to read, a bad byte, a quoted field left open
            raise BankCsvImportError(f"{src}: {problem}")
        if None in r:
            raise BankCsvImportError(f"line {i}: more fields than the header has columns; an amount with a "
                                     "comma in it must be quoted")
        if "amount" in cols:
            amount = _pence(r[cols["amount"]], cols["amount"], i, signed=True)
            if amount is None:
                raise BankCsvImportError(f"line {i}: {cols['amount']} is empty")
        else:
            paid_in = _pence(r[cols["paid_in"]], cols["paid_in"], i, signed=False)
            paid_out = _pence(r[cols["paid_out"]], cols["paid_out"], i, signed=False)
            if paid_in is None and paid_out is None:
                raise BankCsvImportError(f"line {i}: neither the paid-in nor the paid-out column holds an amount")
            if paid_in and paid_out:
                raise BankCsvImportError(f"line {i}: both the paid-in and the paid-out column hold an amount; a "
                                         "row is money in or money out")
            amount = _money_out(r[cols["paid_out"]], cols["paid_out"], i) if paid_out else (paid_in or 0)
        d = _date(r[cols["date"]], cols["date"], formats, i)
        bal = _pence(r[cols["balance"]], cols["balance"], i, signed=True) if "balance" in cols else None
        desc = " ".join(" ".join((r[c] or "") for c in cols["description"]).split()).upper()
        if not desc:
            raise BankCsvImportError(f"line {i}: the description is empty")
        tid = _id(r[cols["id"]], cols["id"], i) if "id" in cols else f"L{i}"
        if tid in seen:
            raise BankCsvImportError(f"line {i}: the id repeats line {seen[tid]}'s; each row's id must be its own")
        seen[tid] = i
        lines.append(contract.Line(line=i, id=tid, date=d, description=desc, amount=amount, balance=bal))
    if not lines:
        raise BankCsvImportError(f"{src}: no transactions")
    reached = flows_at_limit([x.amount for x in lines])
    if reached:
        raise BankCsvImportError(f"{' and '.join(reached)} {'total' if len(reached) > 1 else 'totals'} "
                                 f"{AT_LIMIT}: no figure could be reported to the penny")

    newest = _newest_first(lines, mapping["order"])
    if newest:
        lines.reverse()
    if supplied is None and "balance" not in cols:
        raise BankCsvImportError("this export has no balance column; pass --opening-balance (the balance just "
                                 "before the first transaction, from the bank's statement)")
    # The opening balance, the walk and the writing are the importer contract's (T28); the
    # statement loader's checks apply to what is written, by the source file's lines.
    opening, source = contract.opening_balance(lines, supplied, column=BALANCE_COLUMN, source=BALANCE_COLUMN)
    balances = contract.walk(lines, opening, column=BALANCE_COLUMN)
    contract.write_statement(dst, lines, balances, label=src)
    return ImportResult(rows=len(lines), start=lines[0].date, end=lines[-1].date,
                        opening_balance=pounds(opening), closing_balance=pounds(balances[-1]),
                        balance_source=source, order="newest first" if newest else "oldest first")
