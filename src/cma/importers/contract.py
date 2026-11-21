"""The importer contract (C4; design, Interfaces and contracts).

An importer reads one source file and an optional opening balance, and writes the
five-column statement for one account (id, date, description, amount, balance). Every
importer goes through this module, so that:

- one step walks and verifies the running balance: `opening_balance` settles the balance
  before the first line, from a supplied one or from the provider's own balance column, and
  `walk` walks it forward, checking every walked balance against the provider's and against
  the £10,000,000,000,000 limit (R2), in whole pence, with the figures layer's arithmetic;
- the output passes ingest.load_csv: `write_statement` loads the bytes it would write with
  the statement loader and writes nothing the loader refuses;
- refusals name lines, never a cell's value: every refusal here, and every refusal an
  importer raises (ImportRefused), names the line of the source file its record starts on and
  what is wrong, and quotes nothing the file holds (R34; as the MCP server does).

`cma import monzo` and `cma case import` catch ImportRefused, the refusal of any importer.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .. import ingest
from ..metrics import AT_LIMIT, at_limit, opening_before, pounds_text, running_balances
from ..models import Statement

COLUMNS = ("id", "date", "description", "amount", "balance")
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


class ImportRefused(ValueError):
    """An importer's refusal: it names lines of the source file, never a cell's value."""


@dataclass(frozen=True)
class Line:
    """One line of the statement an importer builds, in whole pence. `line` is the line of the
    source file its record starts on; `balance` is the provider's own balance after it, when
    the source gives one."""

    line: int
    id: str
    date: date
    description: str
    amount: int
    balance: int | None = None


def neutralise_formula(text: str) -> str:
    """A text cell a spreadsheet would read as a formula gets a leading apostrophe (R34)."""
    return "'" + text if text.startswith(FORMULA_START) else text


def opening_balance(lines: Sequence[Line], supplied: int | None, *, column: str,
                    option: str = "--opening-balance", source: str | None = None) -> tuple[int, str]:
    """The balance before the first line, in pence, and where it came from: `supplied` when
    given, checked against the one the provider's balance column (`column`, as a refusal
    names it) implies; else that implied one, from `source` (`column` if not given). `lines`
    are in the order they are walked."""
    first = lines[0]
    implied = opening_before(first.balance, first.amount) if first.balance is not None else None
    if supplied is None:
        if implied is None:
            raise ImportRefused(f"line {first.line}: the first transaction has no balance; pass {option}")
        return implied, source or column
    if implied is not None and implied != supplied:
        # The implied balance is the tool's own arithmetic, which the person needs to mend the
        # option they gave; no cell is quoted.
        raise ImportRefused(f"{option} {pounds_text(supplied)} disagrees with {column}, which implies "
                            f"{pounds_text(implied)} at line {first.line}, the first transaction; leave "
                            f"{option} out to use it")
    return supplied, "opening balance supplied"


def walk(lines: Sequence[Line], opening: int, *, column: str) -> list[int]:
    """The running balance after each line, walked from `opening` (R2): each held below the
    limit, and each the provider's own balance where the source gives one."""
    balances = running_balances(opening, [x.amount for x in lines])
    for x, bal in zip(lines, balances, strict=True):
        if at_limit(bal):
            raise ImportRefused(f"line {x.line}: the running balance reaches £10,000,000,000,000; "
                                "no figure could be reported to the penny")
        if x.balance is not None and x.balance != bal:
            raise ImportRefused(f"line {x.line}: the balance walked from the opening balance disagrees with "
                                f"{column}; the export may be missing transactions")
    return balances


def statement_bytes(lines: Sequence[Line], balances: Sequence[int]) -> bytes:
    """The five-column statement, as the csv module writes it."""
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=list(COLUMNS))
    w.writeheader()
    for x, bal in zip(lines, balances, strict=True):
        w.writerow({"id": x.id, "date": x.date.isoformat(), "description": neutralise_formula(x.description),
                    "amount": pounds_text(x.amount), "balance": pounds_text(bal)})
    return buf.getvalue().encode("utf-8")


def checked(data: bytes, label: str | Path, *, account_name: str | None = None,
            source_lines: Sequence[int] | None = None) -> Statement:
    """The statement these bytes hold, as ingest.load_csv reads it; refused, by line and never
    by a cell's value, if the loader refuses it. `source_lines`, when given, are the source
    file's lines of the statement's rows, so a refusal names the line the person can find."""

    def line(n: int) -> int:
        k = n - 2  # the statement's line 2 is its first row
        return source_lines[k] if source_lines is not None and 0 <= k < len(source_lines) else n

    try:
        return ingest.load_csv(Path(label), account_name=account_name, data=data)
    except ingest.StatementError as e:
        parts = ingest.refusal_parts(e, line)
        raise ImportRefused(f"{label}: {len(e.problems)} problem(s): {'; '.join(parts)}") from None


def write_statement(dst: Path, lines: Sequence[Line], balances: Sequence[int], *,
                    label: str | Path) -> Statement:
    """Write the statement to `dst` once the statement loader accepts it; nothing otherwise."""
    data = statement_bytes(lines, balances)
    stmt = checked(data, label, source_lines=[x.line for x in lines])
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("wb") as f:  # into the file as it is, so a private one keeps its mode
        f.write(data)
    return stmt


__all__ = ["AT_LIMIT", "ImportRefused", "Line", "checked", "neutralise_formula", "opening_balance",
           "statement_bytes", "walk", "write_statement"]
