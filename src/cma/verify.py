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
            if (f in m or f in new) and m.get(f) != new.get(f):
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
            if row.get(f) != new.get(f):
                out.append(Mismatch(f"{prefix}monthly {month} {f}", row.get(f), new.get(f)))
        for cell in MONTHLY_CELLS:
            n += 1
            a, b = row.get(cell) or {}, new.get(cell) or {}
            for f in sorted(set(a) | set(b)):
                if a.get(f) != b.get(f):
                    out.append(Mismatch(f"{prefix}monthly {month} {cell} {f}", a.get(f), b.get(f)))
    seen = {r.get("month") for r in recorded}
    for month in got:
        if month not in seen:
            out.append(Mismatch(f"{prefix}monthly {month}", "not recorded", "recomputed"))
            n += len(MONTHLY_CELLS)
    return n


def _buckets(money_in: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not money_in:
        return []
    return [b for b in (money_in.get("total"), *(money_in.get("buckets") or []), money_in.get("cash_within_trading"))
            if b]


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
    if recorded.get("sha256") and sha != recorded["sha256"]:
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
    position = (inputs.get("override_log") or {}).get("position")
    ruleset = inputs.get("ruleset") or record.get("ruleset") or {}
    try:
        with _ruleset(ruleset["id"]):
            figures = overrides.case_figures(folder, pairing_days=inputs.get("own_account_pairing_days"),
                                             override_position=position if isinstance(position, int) else None)
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
        checked["buckets"] += _compare(prefix, "bucket", _buckets(acct.get("money_in")),
                                       _buckets(pack._money_in(f.metrics)), BUCKET_FIELDS, out)
        checked["monthly_cells"] += _compare_months(prefix, acct.get("monthly_summary") or [],
                                                    [r.model_dump(mode="json") for r in f.monthly], out)
    recorded_accounts = {acct.get("account") for acct in record.get("accounts") or []}
    for a, f in figures.items():
        if a not in recorded_accounts and f.statement.transactions:
            out.append(Mismatch(f"account {a}", "not recorded", "recomputed"))
    return VerifyResult("pack", "full", ruleset, checked, out)


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
