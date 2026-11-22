"""Verify an evidence record (C8; R44, R45, P3, P13).

`verify` recomputes every figure, bucket and monthly cell an evidence record holds from the
inputs it recorded, with the figures layer as every other path computes them, and reports each
mismatch, or that there is none (R44):

- a `cma run` record: the statement file it names, read only when its bytes are the ones the
  record hashed, categorised with the ruleset it records and computed with its VAT stagger;
- a signed pack's record: the case's statements, profile and firm rules, each only when it is
  the one the pack recorded, and the override log up to the position the pack recorded, so an
  override made after the sign-off is not among the inputs (P13);
- a record with no schema_version (v0, the previous release's `cma run`): M1 to M16 only,
  recomputed under v1, the table the previous release categorised with; verify says that is
  all it can check.

A record made with a ruleset other than the one installed (another id, or the installed
table's bytes hashing differently) is refused before anything is computed, naming the
package version and ruleset that made it (R45). An input that is not there, or not the one
recorded, is refused naming it: the figures could not then be recomputed from the recorded
inputs. Reports name figures, buckets, months and cells by id and line ids only, never a
description."""

from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__

V0_METRICS = tuple(f"M{i}" for i in range(1, 17))
METRIC_FIELDS = ("value", "unit", "evidence", "evidence_note")
BUCKET_FIELDS = ("value", "unit", "evidence")


class VerifyRefused(ValueError):
    """The record cannot be verified: nothing was recomputed or nothing could be compared."""


@dataclass(frozen=True)
class Mismatch:
    where: str      # e.g. "account main: figure M2 value"
    recorded: Any
    recomputed: Any


@dataclass
class VerifyResult:
    kind: str                       # "run" or "pack"
    scope: str                      # "full", or "v0" for M1 to M16 only
    ruleset: dict[str, Any]
    checked: dict[str, int]         # figures, buckets and monthly cells compared
    mismatches: list[Mismatch] = field(default_factory=list)
    note: str = ""


# --- R45: the ruleset ------------------------------------------------------------------------


def _check_ruleset(record: dict[str, Any], recorded: dict[str, Any]) -> None:
    from .categorise import RULESETS, RulesetError, load_ruleset

    rid, sha = recorded.get("id"), recorded.get("sha256")
    version = record.get("package_version") or "an unrecorded package version"
    made = f"package version {version} with ruleset {rid} (sha256 {sha})"
    if rid not in RULESETS:
        raise VerifyRefused(f"this evidence record was made by {made}; the installed package "
                            f"(version {__version__}) has no ruleset {rid}. Verify it with package "
                            f"version {version}.")
    try:
        installed = load_ruleset(rid)
    except RulesetError as e:
        raise VerifyRefused(f"the installed ruleset {rid} cannot be loaded: {e}") from None
    if installed.sha256 != sha:
        raise VerifyRefused(f"this evidence record was made by {made}; the installed package (version "
                            f"{__version__}) has ruleset {rid} with sha256 {installed.sha256}. Verify it "
                            f"with package version {version}.")


@contextmanager
def _ruleset(ruleset_id: str):
    """The figures layer reads the table CMA_RULESET names; while verifying, that is the
    record's, whatever the environment says."""
    from .categorise import RULESET_ENV

    old = os.environ.get(RULESET_ENV)
    os.environ[RULESET_ENV] = ruleset_id
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(RULESET_ENV, None)
        else:
            os.environ[RULESET_ENV] = old


# --- comparing --------------------------------------------------------------------------------


def _same(a: Any, b: Any) -> bool:
    """Equal as JSON values, so a boolean is never a number: in Python True == 1 and
    False == 0.0, which would let a figure tampered to true or a flag given as 0 pass."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, dict) or isinstance(b, dict):
        return (isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys()
                and all(_same(a[k], b[k]) for k in a))
    if isinstance(a, list) or isinstance(b, list):
        return (isinstance(a, list) and isinstance(b, list) and len(a) == len(b)
                and all(_same(x, y) for x, y in zip(a, b, strict=True)))
    return a == b


def _compare(prefix: str, kind: str, recorded: list[dict[str, Any]], recomputed: list[dict[str, Any]],
             fields: tuple[str, ...], out: list[Mismatch], only: tuple[str, ...] | None = None) -> int:
    """Compare figures (or buckets) by id; return how many recorded ones were compared."""
    got = {m["id"]: m for m in recomputed}
    n = 0
    for m in recorded:
        mid = m.get("id") if isinstance(m, dict) else None
        if only is not None and mid not in only:
            continue
        n += 1
        new = got.get(mid)
        if new is None:
            out.append(Mismatch(f"{prefix}{kind} {mid}", "recorded", "not recomputed"))
            continue
        for f in fields:
            if (f in m or f in new) and not _same(m.get(f), new.get(f)):
                out.append(Mismatch(f"{prefix}{kind} {mid} {f}", m.get(f), new.get(f)))
    seen = {m.get("id") for m in recorded if isinstance(m, dict)}
    for mid in got:
        if mid not in seen and (only is None or mid in only):
            n += 1
            out.append(Mismatch(f"{prefix}{kind} {mid}", "not recorded", "recomputed"))
    return n


def _compare_months(prefix: str, recorded: list[dict[str, Any]], recomputed: list[dict[str, Any]],
                    out: list[Mismatch]) -> int:
    from .pack import MONTHLY_CELLS

    got = {r["month"]: r for r in recomputed}
    n = 0
    for row in recorded:
        month = row.get("month")
        new = got.get(month)
        if new is None:
            out.append(Mismatch(f"{prefix}monthly {month}", "recorded", "not recomputed"))
            n += len(MONTHLY_CELLS)
            continue
        for f in ("part_month", "start", "end"):
            if not _same(row.get(f), new.get(f)):
                out.append(Mismatch(f"{prefix}monthly {month} {f}", row.get(f), new.get(f)))
        for cell in MONTHLY_CELLS:
            n += 1
            a, b = row.get(cell) or {}, new.get(cell) or {}
            for f in sorted(set(a) | set(b)):
                if not _same(a.get(f), b.get(f)):
                    out.append(Mismatch(f"{prefix}monthly {month} {cell} {f}", a.get(f), b.get(f)))
    seen = {r.get("month") for r in recorded}
    for month in got:
        if month not in seen:
            out.append(Mismatch(f"{prefix}monthly {month}", "not recorded", "recomputed"))
            n += len(MONTHLY_CELLS)
    return n


def _compare_money_in(prefix: str, recorded: dict[str, Any] | None, recomputed: dict[str, Any],
                      out: list[Mismatch]) -> int:
    """Compare total money in (M17) and the cash within trading receipts (M18) each in its own
    place, and the buckets by id; return how many were compared. A figure moved from its place
    into the buckets, or the other way, is a mismatch, never flattened away."""
    recorded = recorded or {}
    n = 0
    for slot in ("total", "cash_within_trading"):
        a, b = recorded.get(slot), recomputed.get(slot)
        if a is None and b is None:
            continue
        n += 1
        if a is None or b is None:
            out.append(Mismatch(f"{prefix}bucket {slot}", "not recorded" if a is None else "recorded",
                                "recomputed" if b is not None else "not recomputed"))
            continue
        if not _same(a.get("id"), b.get("id")):
            out.append(Mismatch(f"{prefix}bucket {slot} id", a.get("id"), b.get("id")))
            continue
        for f in BUCKET_FIELDS:
            if (f in a or f in b) and not _same(a.get(f), b.get(f)):
                out.append(Mismatch(f"{prefix}bucket {a['id']} {f}", a.get(f), b.get(f)))
    return n + _compare(prefix, "bucket", recorded.get("buckets") or [], recomputed.get("buckets") or [],
                        BUCKET_FIELDS, out)


def _dumped(metrics) -> list[dict[str, Any]]:
    return [m.model_dump(mode="json") for m in metrics]


# --- a `cma run` record -----------------------------------------------------------------------


def _statement_path(recorded: dict[str, Any], base: Path | None, statement: Path | None) -> Path:
    if statement is not None:
        return Path(statement)
    raw = recorded.get("path")
    if not isinstance(raw, str) or not raw:
        raise VerifyRefused("the record names no statement file")
    path = Path(raw)
    if path.is_absolute() or path.is_file() or base is None:
        return path
    beside = Path(base) / path
    return beside if beside.is_file() else path


def _verify_run(record: dict[str, Any], v0: bool, base: Path | None, statement: Path | None) -> VerifyResult:
    from . import metrics as met
    from .categorise import categorise, load_ruleset
    from .ingest import StatementError, load_csv
    from .metrics import AmountError, ReconciliationError

    recorded = record.get("statement")
    if not isinstance(recorded, dict):
        raise VerifyRefused("the record holds no statement")
    path = _statement_path(recorded, base, statement)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise VerifyRefused(f"the statement file {path} cannot be read ({e.strerror or e.__class__.__name__})") from None
    sha = hashlib.sha256(data).hexdigest()
    if not isinstance(recorded.get("sha256"), str) or not recorded["sha256"]:
        raise VerifyRefused("the record names no statement sha256, so the statement it was made from "
                            "cannot be known")
    if sha != recorded["sha256"]:
        raise VerifyRefused(f"the statement file {path} is not the one the record was made from "
                            f"(sha256 {sha}, recorded {recorded['sha256']})")
    ruleset = {"id": "v1", "sha256": load_ruleset("v1").sha256} if v0 else record.get("ruleset") or {}
    try:
        s = categorise(load_csv(path, data=data), load_ruleset(ruleset["id"]))
        computed = met.compute(s, vat_stagger=record.get("vat_stagger"))
        monthly = [r.model_dump(mode="json") for r in met.monthly_summary(s)] if not v0 else []
    except (StatementError, AmountError, ReconciliationError) as e:
        raise VerifyRefused(f"the figures cannot be recomputed from {path}: {e}") from None
    out: list[Mismatch] = []
    if v0:
        n = _compare("", "figure", record.get("metrics") or [], _dumped(computed), ("value", "evidence"), out,
                     only=V0_METRICS)
        return VerifyResult("run", "v0", ruleset, {"figures": n, "buckets": 0, "monthly_cells": 0}, out,
                            "a v0 record (no schema_version): only M1 to M16 were recomputed and compared, "
                            "under v1; that is all it can check")
    n = _compare("", "figure", record.get("metrics") or [], _dumped(computed), METRIC_FIELDS, out)
    cells = _compare_months("", record.get("monthly_summary") or [], monthly, out)
    # A run's buckets are its figures M17 to M25 (and M18), compared above.
    ids = {"M2", "M17", "M18", *(fid for fid, _, _ in met.BUCKET_METRICS.values())}
    buckets = sum(1 for m in record.get("metrics") or [] if m.get("id") in ids)
    return VerifyResult("run", "full", ruleset, {"figures": n, "buckets": buckets, "monthly_cells": cells}, out)


# --- a signed pack's record -------------------------------------------------------------------


def _verify_pack(record: dict[str, Any], folder: Path | None) -> VerifyResult:
    from . import firm, overrides, pack, store
    from .metrics import AmountError, ReconciliationError

    case_id = record.get("case")
    if folder is None:
        try:
            folder = store.case_folder(case_id)
        except store.CaseStoreError as e:
            raise VerifyRefused(f"the record's case cannot be found: {e}; give it with --case") from None
    folder = Path(folder)
    if folder.name != case_id:
        raise VerifyRefused(f"the record was made for case {case_id}, not for case {folder.name}")
    inputs = record.get("inputs")
    if not isinstance(inputs, dict):
        raise VerifyRefused("the record holds no recorded inputs")
    # The inputs the figures rest on must be the recorded ones (R44); the override log is read
    # up to the recorded position below.
    changed = []
    for account, sha in sorted((inputs.get("statements") or {}).items()):
        if pack._file_sha(folder / store.STATEMENTS / f"{account}.csv") != sha:
            changed.append(f"the statement for account {account}")
    config = firm.load()
    if pack._file_sha(config.firm_rules_path) != inputs.get("firm_rules_sha256"):
        changed.append("the firm rules file")
    try:
        if store.profile_sha256(store.load_profile(folder)) != inputs.get("profile_sha256"):
            changed.append("the case profile")
    except store.CaseStoreError as e:
        raise VerifyRefused(f"the case profile cannot be read: {e}") from None
    if changed:
        raise VerifyRefused("the figures cannot be recomputed from the recorded inputs, which are no longer "
                            "in the case: " + "; ".join(changed) + " changed since the pack was signed")
    position = inputs["override_log"]["position"]
    ruleset = inputs.get("ruleset") or record.get("ruleset") or {}
    try:
        with _ruleset(ruleset["id"]):
            # Only the accounts the pack recorded: one imported since is no recorded input (P13).
            figures = overrides.case_figures(folder, pairing_days=inputs["own_account_pairing_days"],
                                             override_position=position, accounts=inputs["statements"])
    except store.CaseStoreError as e:
        raise VerifyRefused(f"the case's override log cannot be read as recorded: {e}") from None
    except (AmountError, ReconciliationError) as e:
        raise VerifyRefused(f"the figures cannot be recomputed from the case: {e}") from None
    out: list[Mismatch] = []
    checked = {"figures": 0, "buckets": 0, "monthly_cells": 0}
    for acct in record.get("accounts") or []:
        a = acct.get("account")
        prefix = f"account {a}: "
        f = figures.get(a)
        if f is None or not f.statement.transactions:
            out.append(Mismatch(f"account {a}", "recorded", "not recomputed"))
            continue
        metrics = _dumped(f.metrics)
        checked["figures"] += _compare(prefix, "figure", acct.get("metrics") or [], metrics, METRIC_FIELDS, out)
        checked["buckets"] += _compare_money_in(prefix, acct.get("money_in"), pack._money_in(f.metrics), out)
        checked["monthly_cells"] += _compare_months(prefix, acct.get("monthly_summary") or [],
                                                    [r.model_dump(mode="json") for r in f.monthly], out)
    recorded_accounts = {acct.get("account") for acct in record.get("accounts") or []}
    for a, f in figures.items():
        if a not in recorded_accounts and f.statement.transactions:
            out.append(Mismatch(f"account {a}", "not recorded", "recomputed"))
    return VerifyResult("pack", "full", ruleset, checked, out)


# --- the record's shape ----------------------------------------------------------------------------


def _list_of_objects(value: Any, what: str, key: str | None = None, *, required: bool = False) -> None:
    """A list of objects, each with a text `key` given once across the list."""
    if value is None and not required:
        return
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise VerifyRefused(f"the record's {what} is not a list of objects")
    if key is not None:
        if not all(isinstance(v.get(key), str) for v in value):
            raise VerifyRefused(f"an entry of the record's {what} has no {key}")
        keys = [v[key] for v in value]
        if len(set(keys)) != len(keys):
            raise VerifyRefused(f"the record's {what} gives a {key} more than once")


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _months(rows: Any, what: str) -> None:
    from .pack import MONTHLY_CELLS

    _list_of_objects(rows, what, "month")
    for row in rows or []:
        if "part_month" in row and not isinstance(row["part_month"], bool):
            raise VerifyRefused(f"the record's {what} {row['month']} part_month is not true or false")
        for f in ("start", "end"):
            if row.get(f) is not None and not isinstance(row[f], str):
                raise VerifyRefused(f"the record's {what} {row['month']} {f} is not a date")
        for cell in MONTHLY_CELLS:
            if row.get(cell) is not None and not isinstance(row[cell], dict):
                raise VerifyRefused(f"the record's {what} {row['month']} {cell} is not an object")


def _figure(value: Any, what: str) -> None:
    if value is not None and not (isinstance(value, dict) and isinstance(value.get("id"), str)):
        raise VerifyRefused(f"the record's {what} is not a figure with a text id")


def _check_inputs(inputs: dict[str, Any]) -> None:
    """A pack's recorded inputs: the statements by account, the override log's position and
    the pairing window, each of the kind the pack writes, so none is read as something else
    (a position of true read as 1, or of -1 as all but the last record)."""
    statements = inputs.get("statements")
    if not isinstance(statements, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                   for k, v in statements.items()):
        raise VerifyRefused("the record's inputs.statements is not an object of statement hashes by account")
    log = inputs.get("override_log")
    if not isinstance(log, dict) or not _count(log.get("position")):
        raise VerifyRefused("the record's inputs.override_log has no position that is a whole number of "
                            "records")
    if not _count(inputs.get("own_account_pairing_days")):
        raise VerifyRefused("the record's inputs.own_account_pairing_days is not a whole number of days")


def _check_shape(record: dict[str, Any]) -> None:
    """Refuse, naming the field, a record whose fields are not of the kinds an evidence record
    writes, so a hand-edited or truncated record is refused (exit 2), never reported as a
    mismatch or raised as an error."""
    _list_of_objects(record.get("metrics"), "metrics", "id")
    _months(record.get("monthly_summary"), "monthly summary")
    vat = record.get("vat_stagger")
    if vat is not None and (isinstance(vat, bool) or vat not in (1, 2, 3)):
        raise VerifyRefused("the record's vat_stagger is not 1, 2 or 3")
    if "statement" in record and not isinstance(record["statement"], dict):
        raise VerifyRefused("the record's statement is not an object")
    if "accounts" in record:
        _list_of_objects(record["accounts"], "accounts", "account", required=True)
        for acct in record["accounts"]:
            a = acct["account"]
            _list_of_objects(acct.get("metrics"), f"account {a} metrics", "id")
            _months(acct.get("monthly_summary"), f"account {a} monthly summary")
            money_in = acct.get("money_in")
            if money_in is not None and not isinstance(money_in, dict):
                raise VerifyRefused(f"the record's account {a} money_in is not an object")
            if isinstance(money_in, dict):
                _list_of_objects(money_in.get("buckets"), f"account {a} buckets", "id")
                for slot in ("total", "cash_within_trading"):
                    _figure(money_in.get(slot), f"account {a} money_in {slot}")
    for name in ("ruleset", "inputs"):
        if name in record and not isinstance(record[name], dict):
            raise VerifyRefused(f"the record's {name} is not an object")
    if "accounts" in record and "case" in record and isinstance(record.get("inputs"), dict):
        _check_inputs(record["inputs"])
    for ruleset in (record.get("ruleset"), (record.get("inputs") or {}).get("ruleset")):
        if ruleset is not None and (not isinstance(ruleset, dict)
                                    or not isinstance(ruleset.get("id"), (str, type(None)))):
            raise VerifyRefused("the record's ruleset is not an object with a text id")
    if not isinstance(record.get("statement", {}).get("sha256", ""), (str, type(None))):
        raise VerifyRefused("the record's statement sha256 is not text")


# --- the entry point -----------------------------------------------------------------------------


def verify(record: dict[str, Any], *, case_folder: Path | None = None, base: Path | None = None,
           statement: Path | None = None) -> VerifyResult:
    """Recompute `record`'s figures, buckets and monthly cells from its recorded inputs and
    report every mismatch (R44). `case_folder`: a signed pack's case, found by the record's case
    id when not given. `base`: the folder a `cma run` record's relative statement path is also
    looked for in (the record's own folder). `statement`: the statement file, in place of the
    path the record names; it is still checked against the recorded hash."""
    if not isinstance(record, dict):
        raise VerifyRefused("an evidence record is a JSON object")
    _check_shape(record)
    v0 = "schema_version" not in record
    is_pack = isinstance(record.get("accounts"), list) and "case" in record
    if v0:
        if is_pack:
            raise VerifyRefused("a pack's evidence record always carries a schema_version")
        # v0: the previous release categorised with the table now packaged as v1 (R54).
        return _verify_run(record, True, base, statement)
    recorded = (record.get("inputs") or {}).get("ruleset") if is_pack else record.get("ruleset")
    if not isinstance(recorded, dict):
        raise VerifyRefused("the record names no ruleset")
    _check_ruleset(record, recorded)  # R45: before anything is computed
    if is_pack:
        return _verify_pack(record, case_folder)
    with _ruleset(recorded["id"]):
        return _verify_run(record, False, base, statement)
