"""`cma report` (R50, C11): a local summary of every case under the case root.

For each case: how many lines the judgement queue lists, by why they are listed; how many
lines each kind of indicator flagged; the overrides the case applies, counted by old and new
category; the unexplained share of money in (M26, across every account for a case of more
than one); whether every figure was traced (each figure and monthly cell has the lines behind
it that its rule cites at that value, or is n/a with its reason); whether its figures
reconcile (R3); and whether it was signed off.

Everything comes from the workspace's case summary (review.summary.case_summary), so the
report counts what the underwriter sees, and does no arithmetic on money itself. It carries
the case's random id, counts, category and indicator names, flags and a share; never an
applicant's name, the case's label or business name, a description, an amount, an
override's reason or typed name, a refusal's own words (which can quote a statement), or any
time from the case log, so no duration can be read from it (D32).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from . import case as caselog
from . import firm, overrides, pack, store

SHARE = "M26"  # the unexplained share of money in


# Which figures cite lines at which values (P2, R10, R11, R12). A sum or outflow figure, or a
# count of lines, is zero exactly when it has no line to cite, so only a non-zero one must
# cite lines. These derived figures read lines whenever they have a value, zero included:
# months covered (the first and last line), the trend (the quarters' revenue lines), the
# lowest balance (the line that set it), debt service cover with and without the proposed
# facility, the top customer share and the two shares of money in (their denominators'
# credits), and the average end-of-day balance (the line that set each close). Its value
# None, each is n/a with its reason. Late or missed VAT payments (M13) counts quarters with
# no payment, an absence no line records: it cites the VAT payments it checked, which a
# missed quarter can leave none of, so only a count of nought (every quarter paid) must cite.
# The proposed cover (M28) is not among them: its numerator is net operating cash flow and its
# denominator the debt service plus the proposed repayment, so an account with no trading and
# no debt (a reserve account) has a cover of 0.00x resting on no line, and only a non-zero
# cover must cite lines (T40 pass: a generated two-account case flagged its reserve account).
CITES_WHENEVER_VALUED = frozenset({"M1", "M4", "M5", "M10", "M15", "M16", "M26", "M27"})
ABSENCE_COUNTS = frozenset({"M13"})


def _figure_untraced(f: dict[str, Any]) -> bool:
    if f["value"] is None:
        return not f["method"]  # n/a says why in its method
    if f["lines"]:
        return False
    if f["id"] in ABSENCE_COUNTS:
        return f["value"] == 0
    return f["id"] in CITES_WHENEVER_VALUED or f["value"] != 0


def _cell_untraced(cell: dict[str, Any]) -> bool:
    """A monthly cell: n/a with its note; a sum or outflow cell cites lines when non-zero; a
    balance or overdrawn-day cell (derived) cites the line that set each close whenever it has
    a value, a count of nought days included (R11, P2)."""
    if cell["value"] is None:
        return not cell.get("note")
    if cell["lines"]:
        return False
    return cell.get("kind") == "derived" or cell["value"] != 0


def _untraced(summary: dict[str, Any]) -> list[str]:
    """Each figure or monthly cell with no line behind it that its rule cites, or n/a without
    its reason, as account:id or account:month:cell."""
    out = []
    accounts = list(summary["accounts"]) + ([summary["combined"]] if summary.get("combined") else [])
    for a in accounts:
        name = a["account"]
        out += [f"{name}:{f['id']}" for f in a["figures"] if _figure_untraced(f)]
        for row in a["monthly"]:
            for key, cell in row.items():
                if isinstance(cell, dict) and "lines" in cell and _cell_untraced(cell):
                    out.append(f"{name}:{row['month']}:{key}")
    return out


def _share(summary: dict[str, Any]) -> str | None:
    source = summary.get("combined") or (summary["accounts"][0] if summary["accounts"] else None)
    if source is None:
        return None
    return next((f["display"] for f in source["figures"] if f["id"] == SHARE), None)


def _overrides(folder: Path) -> tuple[list[dict[str, Any]], int]:
    """The applied overrides counted by (old, new) category, and how many are stale."""
    counts: Counter[tuple[str, str]] = Counter()
    stale = 0
    for o, status in overrides.listing(folder):
        if status == "applied":
            counts[(o.old, o.new)] += 1
        elif status.startswith("stale"):
            stale += 1
    return [{"old": old, "new": new, "count": n} for (old, new), n in sorted(counts.items())], stale


def case_report(folder: Path, case_id: str) -> dict[str, Any]:
    from .review.summary import case_summary

    out: dict[str, Any] = {"case": case_id, "state": None, "accounts": 0, "queue_lines": 0,
                           "queue_by_reason": {}, "indicator_lines": {}, "overrides": [],
                           "stale_overrides": 0, "unexplained_share": None, "traced": False,
                           "untraced": [], "reconciled": False, "signed_off": False}
    try:
        if not (folder / store.PROFILE).is_file():  # purged, or its case.yaml lost, since it was listed
            raise store.CaseStoreError("no such case")
        s = case_summary(folder, case_id)
    except Exception:  # a failure no refusal foresees: this case is refused, the others still reported
        out.update(state="refused", traced=None, reconciled=None, signed_off=None)
        return out
    out["state"] = s["state"]
    if s["state"] in ("shown", "empty"):
        out["accounts"] = len(s["accounts"])
        out["queue_lines"] = len(s["queue"])
        out["queue_by_reason"] = dict(sorted(Counter(r for e in s["queue"] for r in e["reasons"]).items()))
        flagged: dict[str, set[tuple[str, str]]] = {}
        for a in s["accounts"]:
            for i in a["indicators"]:
                flagged.setdefault(i["kind"], set()).update((a["account"], line["id"]) for line in i["lines"])
        out["indicator_lines"] = {k: len(v) for k, v in sorted(flagged.items())}
        out["unexplained_share"] = _share(s)
        out["untraced"] = _untraced(s)
        out["traced"] = not out["untraced"]
        out["reconciled"] = True
    elif s["state"] == "not_reconciled":
        out["reconciled"] = False
    else:  # refused: the case could not be read; nothing is known about its figures
        out["reconciled"] = None
        out["traced"] = None
    # The override log and the case log are read apart, so one that cannot be read leaves the
    # other's answer, and the figures' answer, as they are: not known, never a guess.
    try:
        out["overrides"], out["stale_overrides"] = _overrides(folder)
    except Exception:
        out["overrides"], out["stale_overrides"] = None, None
    try:
        out["signed_off"] = any(e.event == pack.SIGN_EVENT for e in caselog.for_case(folder).entries())
    except Exception:
        out["signed_off"] = None
    return out


def _root(root: Path | None) -> Path:
    """The case root, checked against git as every case command checks it (R31), and only
    read: the report makes nothing and changes no file's mode."""
    root = Path(root).resolve() if root is not None else firm.case_root()
    store.check_not_in_git(root)
    return root


def case_ids(root: Path | None = None) -> list[str]:
    cases = _root(root) / store.CASES
    try:
        if not cases.is_dir():
            return []
        return sorted(p.name for p in cases.iterdir()
                      if store.CASE_ID.fullmatch(p.name) and (p / store.PROFILE).is_file())
    except OSError as e:
        raise store.CaseStoreError(f"the case root's cases folder could not be read ({e.strerror})") from None


def report(root: Path | None = None) -> list[dict[str, Any]]:
    """Every case under the case root, in case id order. One case that cannot be read is
    reported as refused; the others are still reported."""
    base = _root(root)
    return [case_report(base / store.CASES / case_id, case_id) for case_id in case_ids(base)]


def failing(row: dict[str, Any]) -> bool:
    """A case whose figures are not all traced, or not known to reconcile."""
    return row["traced"] is not True or row["reconciled"] is not True
