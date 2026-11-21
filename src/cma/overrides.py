"""The override log (R17, R18, R19): a person's category for one line, and what it does to
the figures.

    cases/<id>/overrides.jsonl    append-only, one JSON object per line, 0600

An override changes one line's category and nothing else (R18): its record names the
account, the line, the category the line had, the category it is given, the reason, the
name the underwriter typed, the operating-system user, the UTC time and the hash of the
statement file the line was read from. The category must be one the line's direction can
take: a credit's has a money-in bucket, and a debit's is never one only money in can have.

The override log is the case log's: each record is appended while this process holds the
case (R42), and the case log entry that records it carries the record's hash. Reading the
log checks every record against the entry that recorded it, so a record changed by hand
is refused, naming it, and a record no entry recorded (a crash between the two writes, or
a refused append) is a leftover that is ignored and dropped by the next append.

A stale override (R19) is one whose line is not in the case's statement any more, or that
was recorded against a statement whose hash no longer matches. It is not applied, and it
is listed as stale until the underwriter confirms it (a line still there, its category
still valid for its direction, recorded now against the current statement) or drops it,
each of which is a record of its own. For each line the latest override decides; an
earlier one is superseded.

Figures are recomputed, every time, from the statements and the whole override log
(case_figures): nothing is carried from one computation to the next, so applying overrides
one at a time gives what a fresh computation with all of them gives (P3), and a line's id,
date, amount, balance and description are never touched (P4). A figure or monthly cell is
marked when it rests on an overridden line: an overridden line is among its lines, or its
value or lines differ from what the statements give without overrides.

Messages name records by number and lines by id, never by their content.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import case as caselog
from . import store
from .categorise import _MONEY_IN_ONLY, LineOverride, active_ruleset, categorise
from .metrics import ReconciliationError
from .models import CREDIT_BUCKET, Category, Metric, MonthRow, Statement, Transaction

OVERRIDES = "overrides.jsonl"
EVENTS = {"override": "override", "confirm": "override_confirmed", "drop": "override_dropped"}
OVERRIDE_FIELDS = ("seq", "kind", "account", "line", "old", "new", "reason", "name", "os_user", "at",
                   "statement_sha256")
RESOLVE_FIELDS = ("seq", "kind", "of", "name", "os_user", "at", "statement_sha256")
CELLS = ("money_in", "money_out", "trading_receipts", "average_balance", "lowest_balance", "days_overdrawn")
LINE_FIELDS = ("amount", "date", "balance", "description", "id")  # what an override may never change (R18)
MAX_TEXT = 1000


def ref(seq: int) -> str:
    """The rule id an overridden line carries: the override's own reference."""
    return f"override:{seq}"


def valid_categories(t: Transaction) -> frozenset[Category]:
    """The categories a line's direction can take (R18): a credit's has a money-in bucket;
    a debit's is never one only money in has. The categoriser's sign rule: a credit is a
    positive amount."""
    if t.amount > 0:
        return frozenset(CREDIT_BUCKET)
    return frozenset(c for c in Category if c not in _MONEY_IN_ONLY)


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not any(ch.isalnum() for ch in value):
        raise store.CaseStoreError(f"an override needs a {what}: type one, with at least one letter or digit")
    value = value.strip()
    if len(value) > MAX_TEXT:
        raise store.CaseStoreError(f"the {what} is longer than {MAX_TEXT} characters")
    return value


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _line_bytes(rec: dict[str, Any]) -> bytes:
    return (json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- reading the log ---------------------------------------------------------------------


@dataclass(frozen=True)
class Override:
    seq: int
    account: str
    line: str
    old: str
    new: str
    reason: str
    name: str
    os_user: str
    at: str
    statement_sha256: str


@dataclass(frozen=True)
class Stale:
    seq: int
    account: str
    line: str
    new: str
    why: str  # "line not in the case" or "statement changed"


def _recorded_hashes(entries) -> dict[int, str]:
    """seq -> record hash, for every record the case log recorded, in order."""
    out: dict[int, str] = {}
    for e in entries:
        if e.event in EVENTS.values() and isinstance(e.data.get("seq"), int):
            out[e.data["seq"]] = e.data.get("record_sha256")
    return out


def _read_records(folder: Path, entries) -> list[dict[str, Any]]:
    """The override log as the case log recorded it: each record checked against the hash its
    entry carries. A record no entry recorded is a leftover, left out; a recorded record that
    is missing or changed is refused, naming it."""
    recorded = _recorded_hashes(entries)
    path = folder / OVERRIDES
    raw = path.read_bytes() if path.is_file() else b""
    by_seq: dict[int, tuple[dict[str, Any], bytes]] = {}
    for line in raw.splitlines(keepends=True):
        try:
            rec = json.loads(line.decode("utf-8"))
            seq = rec["seq"] if isinstance(rec, dict) else None
        except (UnicodeDecodeError, ValueError, KeyError):
            seq = None
        if not isinstance(seq, int) or seq not in recorded:
            continue  # not one the case log recorded: a leftover
        if seq in by_seq:
            raise store.CaseStoreError(f"{OVERRIDES} holds override record {seq} twice; it was changed by hand")
        by_seq[seq] = (rec, line)
    records = []
    for seq, want in recorded.items():
        got = by_seq.get(seq)
        if got is None:
            raise store.CaseStoreError(f"{OVERRIDES} is missing override record {seq}, which the case log "
                                       "recorded; it was changed by hand")
        if _sha(got[1]) != want:
            raise store.CaseStoreError(f"{OVERRIDES}: override record {seq} is not the one the case log "
                                       "recorded; it was changed by hand")
        records.append(got[0])
    return records


# --- the case's statements ---------------------------------------------------------------


def _accounts(entries) -> list[str]:
    return sorted({e.data["account"] for e in entries if e.event == "import" and "account" in e.data})


def _load(folder: Path, account: str) -> tuple[Statement, str] | None:
    from .ingest import load_csv

    path = folder / store.STATEMENTS / f"{account}.csv"
    if not path.is_file():
        return None
    data = path.read_bytes()
    return load_csv(path, account_name=account, data=data), _sha(data)


@dataclass
class _State:
    """What the whole override log decides for the case's current statements."""

    applied: dict[str, dict[str, LineOverride]] = field(default_factory=dict)  # account -> line -> override
    stale: list[Stale] = field(default_factory=list)
    overrides: dict[int, Override] = field(default_factory=dict)
    resolved: set[int] = field(default_factory=set)  # stale ones confirmed or dropped
    dropped: set[int] = field(default_factory=set)


def _decide(records: list[dict[str, Any]], statements: dict[str, tuple[Statement, str] | None]) -> _State:
    state = _State()
    basis: dict[int, str] = {}  # seq -> the statement hash the override stands on
    latest: dict[tuple[str, str], int] = {}
    for rec in records:
        if rec["kind"] == "override":
            o = Override(**{k: rec[k] for k in OVERRIDE_FIELDS if k != "kind"})
            state.overrides[o.seq] = o
            basis[o.seq] = o.statement_sha256
            latest[(o.account, o.line)] = o.seq
        elif rec["kind"] == "confirm":
            basis[rec["of"]] = rec["statement_sha256"]
        elif rec["kind"] == "drop":
            state.dropped.add(rec["of"])
    for (account, line), seq in sorted(latest.items(), key=lambda kv: kv[1]):
        if seq in state.dropped:
            continue
        o = state.overrides[seq]
        loaded = statements.get(account)
        t = next((t for t in loaded[0].transactions if t.id == line), None) if loaded else None
        if t is None:
            state.stale.append(Stale(seq, account, line, o.new, "line not in the case"))
        elif basis[seq] != loaded[1]:
            state.stale.append(Stale(seq, account, line, o.new, "statement changed"))
        else:
            state.applied.setdefault(account, {})[line] = LineOverride(Category(o.new), ref(seq))
    return state


# --- figures -----------------------------------------------------------------------------


@dataclass
class CaseFigures:
    """One account's figures from its statement and the whole override log (R17)."""

    account: str
    statement_sha256: str
    statement: Statement            # categorised, overrides applied
    metrics: list[Metric]
    monthly: list[MonthRow]
    overridden: dict[str, str]      # line id -> the override reference it carries
    stale: list[Stale]
    marked: set[str]                # ids of figures that rest on an overridden line
    marked_cells: set[tuple[str, str]]  # (YYYY-MM, cell) of monthly cells that do


class CaseNotReconciled(ReconciliationError):
    """One or more accounts of a case do not reconcile (R3). Every failing account is
    computed and reported, each in failures with its account and categorised statement; the
    difference is the case's total and lines every account's, in account order. account and
    statement are the first failing account's, as for a case of one account."""

    def __init__(self, failures: list[ReconciliationError]):
        self.failures = failures
        ValueError.__init__(self, " ".join(f"Account {f.account}: {f}" for f in failures))
        self.difference_pence = sum(f.difference_pence for f in failures)
        self.lines = [line for f in failures for line in f.lines]
        self.account, self.statement = failures[0].account, failures[0].statement


def _figures(statement: Statement, profile: dict[str, Any], applied: dict[str, LineOverride]):
    from . import metrics

    categorise(statement, active_ruleset(), overrides=applied, profile=store.profile_names(profile))
    return (metrics.compute(statement, profile["vat_stagger"], profile["proposed_monthly_repayment"]),
            metrics.monthly_summary(statement))


def _rests_on(a, b, lines: set[str]) -> bool:
    return bool(lines & set(a.evidence)) or a.value != b.value or a.evidence != b.evidence


def case_figures(folder: Path) -> dict[str, CaseFigures]:
    """Every account's figures, recomputed from its statement, the profile and the whole
    override log; stale overrides listed and not applied (R17, R19)."""
    folder = Path(folder)
    entries = caselog.for_case(folder).entries()
    profile = store.load_profile(folder)
    accounts = _accounts(entries)
    statements = {a: _load(folder, a) for a in accounts}
    state = _decide(_read_records(folder, entries), statements)
    out: dict[str, CaseFigures] = {}
    failed: list[ReconciliationError] = []
    for account in accounts:
        loaded = statements[account]
        if loaded is None:
            continue
        statement, sha = loaded
        applied = state.applied.get(account, {})
        bare = statement.model_copy(deep=True)
        try:
            metrics, monthly = _figures(statement, profile, applied)
        except ReconciliationError as e:  # R3: every failing account is named, with its lines
            e.account, e.statement = account, statement
            failed.append(e)
            continue
        lines = set(applied)
        marked: set[str] = set()
        cells: set[tuple[str, str]] = set()
        if applied:
            try:
                base_metrics, base_monthly = _figures(bare, profile, {})
            except ReconciliationError as e:
                e.account, e.statement = account, bare
                failed.append(e)
                continue
            marked = {m.id for m, b in zip(metrics, base_metrics, strict=True) if _rests_on(m, b, lines)}
            base_rows = {r.month: r for r in base_monthly}
            for row in monthly:
                base = base_rows.get(row.month)
                for cell in CELLS:
                    if base is None or _rests_on(getattr(row, cell), getattr(base, cell), lines):
                        cells.add((row.month, cell))
        out[account] = CaseFigures(account, sha, statement, metrics, monthly,
                                   {k: v.ref for k, v in applied.items()},
                                   [s for s in state.stale if s.account == account], marked, cells)
    if failed:  # R3: no figures for any account of the case
        raise CaseNotReconciled(failed)
    return out


def stale_overrides(folder: Path) -> list[Stale]:
    """Every stale override of the case, accounts with no statement included (R19)."""
    folder = Path(folder)
    entries = caselog.for_case(folder).entries()
    accounts = _accounts(entries)
    records = _read_records(folder, entries)
    return _decide(records, {a: _load(folder, a) for a in accounts}).stale


def listing(folder: Path) -> list[tuple[Override, str]]:
    """Every override with what it is now: applied, stale (and why), superseded or dropped."""
    folder = Path(folder)
    entries = caselog.for_case(folder).entries()
    accounts = _accounts(entries)
    state = _decide(_read_records(folder, entries), {a: _load(folder, a) for a in accounts})
    stale = {s.seq: s for s in state.stale}
    applied = {v.ref for lines in state.applied.values() for v in lines.values()}
    out = []
    for seq, o in sorted(state.overrides.items()):
        if ref(seq) in applied:
            status = "applied"
        elif seq in stale:
            status = f"stale: {stale[seq].why}"
        elif seq in state.dropped:
            status = "dropped"
        else:
            status = "superseded"
        out.append((o, status))
    return out


# --- writing -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Added:
    recorded: bool
    seq: int | None
    line: str
    old: str
    new: str


def _append(folder: Path, save, records: list[dict[str, Any]], rec: dict[str, Any]) -> None:
    """Rewrite the override log as the recorded records plus `rec` (so a leftover goes), and
    have the case log record it with the record's hash. Put back as it was if not recorded."""
    path = folder / OVERRIDES
    save.undo.append(store._restorer(path))
    line = _line_bytes(rec)
    store._write_private(path, b"".join(_line_bytes(r) for r in records) + line)
    save.data = {**{k: rec[k] for k in rec if k != "reason"}, "record_sha256": _sha(line)}


def _check_position(head, expected: int | None) -> None:
    if expected is not None and head != expected:
        raise caselog.LogConflict(expected, head)


def add_override(folder: Path, line: str, category: str, *, reason: str, name: str,
                 account: str = "main", expected: int | None = None, **changes: Any) -> Added:
    """Give one line a category, with a reason and the typed name (R17). Anything but a
    category is refused (R18): `changes` exists only so that a request to change a line's
    amount, date, balance or description is refused, naming it, instead of ignored. The
    same category the line has now changes nothing and records nothing. `expected`, when
    given, is the case log position the caller last saw; a stale one is a LogConflict."""
    folder = Path(folder)
    if changes:
        which = ", ".join(sorted(changes))
        raise store.CaseStoreError(f"an override changes a line's category only, never its {which}; "
                                   "nothing was recorded")
    if not isinstance(line, str) or not line:
        raise store.CaseStoreError("an override names a line by its id")
    try:
        new = Category(category)
    except ValueError:
        raise store.CaseStoreError(f"{category!r} is not a category; use one of "
                                   f"{', '.join(c.value for c in Category)}") from None
    reason = _text(reason, "reason")
    name = _text(name, "name")
    with store._recorded(folder, EVENTS["override"]) as save:
        _check_position(len(save.entries), expected)
        accounts = _accounts(save.entries)
        statements = {a: _load(folder, a) for a in accounts}
        records = _read_records(folder, save.entries)
        loaded = statements.get(account)
        if loaded is None:
            raise store.CaseStoreError(f"no account {account} in this case")
        state = _decide(records, statements)
        profile = store.load_profile(folder)
        _figures(loaded[0], profile, state.applied.get(account, {}))
        t = next((t for t in loaded[0].transactions if t.id == line), None)
        if t is None:
            raise store.CaseStoreError(f"no line {line} in account {account}")
        if new not in valid_categories(t):
            direction = "credit" if t.amount > 0 else "debit"
            raise store.CaseStoreError(f"{new.value} is not valid for a {direction}; line {line} is a {direction}")
        old = t.category.value
        if new.value == old:
            return Added(False, None, line, old, old)
        seq = len(records) + 1
        rec = {"seq": seq, "kind": "override", "account": account, "line": line, "old": old, "new": new.value,
               "reason": reason, "name": name, "os_user": store._os_user(), "at": _now(),
               "statement_sha256": loaded[1]}
        _append(folder, save, records, rec)
    return Added(True, seq, line, old, new.value)


def resolve_stale(folder: Path, seq: int, action: str, *, name: str, expected: int | None = None) -> None:
    """Confirm or drop a stale override (R19). Confirming records it against the current
    statement, and needs its line still there with its category still valid for the line's
    direction; an override whose line is gone can only be dropped."""
    folder = Path(folder)
    if action not in ("confirm", "drop"):
        raise store.CaseStoreError("a stale override is confirmed or dropped")
    name = _text(name, "name")
    with store._recorded(folder, EVENTS[action]) as save:
        _check_position(len(save.entries), expected)
        accounts = _accounts(save.entries)
        statements = {a: _load(folder, a) for a in accounts}
        records = _read_records(folder, save.entries)
        stale = {s.seq: s for s in _decide(records, statements).stale}
        if seq not in stale:
            raise store.CaseStoreError(f"override {seq} is not stale; only a stale override is confirmed or dropped")
        s = stale[seq]
        loaded = statements.get(s.account)
        sha = loaded[1] if loaded else ""
        if action == "confirm":
            if s.why == "line not in the case":
                raise store.CaseStoreError(f"override {seq}'s line {s.line} is not in the case; it can only be "
                                           "dropped")
            t = next(t for t in loaded[0].transactions if t.id == s.line)
            if Category(s.new) not in valid_categories(t):
                raise store.CaseStoreError(f"override {seq}'s category is not valid for line {s.line}'s direction "
                                           "now; it can only be dropped")
        rec = {"seq": len(records) + 1, "kind": action, "of": seq, "name": name, "os_user": store._os_user(),
               "at": _now(), "statement_sha256": sha}
        _append(folder, save, records, rec)

