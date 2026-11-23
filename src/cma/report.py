"""`cma report` (R50, C11): a local summary of every case under the case root.

For each case: how many lines the judgement queue lists, by why they are listed; how many
lines each kind of indicator flagged; the overrides the case applies, counted by old and new
category; the unexplained share of money in (M26, across every account for a case of more
than one); whether every figure was traced (each figure and monthly cell has the lines behind
it, is n/a with its reason, or is nothing, zero with no line to cite); whether its figures
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


def _nothing_to_cite(item: dict[str, Any]) -> bool:
    return item["value"] is None or item["value"] == 0


def _untraced(summary: dict[str, Any]) -> list[str]:
    """Each figure or monthly cell with a value but no line behind it, as account:id or
    account:month:cell. A figure that cannot be computed says why in its method (n/a)."""
    out = []
    accounts = list(summary["accounts"]) + ([summary["combined"]] if summary.get("combined") else [])
    for a in accounts:
        name = a["account"]
        for f in a["figures"]:
            no_lines = f["lines"] == 0 and not _nothing_to_cite(f)
            if no_lines or (f["value"] is None and not f["method"]):  # n/a says why in its method
                out.append(f"{name}:{f['id']}")
        for row in a["monthly"]:
            for key, cell in row.items():
                if isinstance(cell, dict) and "lines" in cell and cell["lines"] == 0 and not _nothing_to_cite(cell):
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
    try:
        out["overrides"], out["stale_overrides"] = _overrides(folder)
        entries = caselog.for_case(folder).entries()
        out["signed_off"] = any(e.event == pack.SIGN_EVENT for e in entries)
    except Exception:  # the log or the override log could not be read: the state says the case is refused
        out["signed_off"] = None
        if out["state"] != "not_reconciled":
            out["state"], out["traced"], out["reconciled"] = "refused", None, None
    return out


def case_ids(root: Path | None = None) -> list[str]:
    root = Path(root) if root is not None else firm.case_root()
    cases = root / store.CASES
    if not cases.is_dir():
        return []
    return sorted(p.name for p in cases.iterdir()
                  if store.CASE_ID.fullmatch(p.name) and (p / store.PROFILE).is_file())


def report(root: Path | None = None) -> list[dict[str, Any]]:
    """Every case under the case root, in case id order."""
    rows = []
    for case_id in case_ids(root):
        rows.append(case_report(store.case_folder(case_id, root), case_id))
    return rows


def failing(row: dict[str, Any]) -> bool:
    """A case whose figures are not all traced, or not known to reconcile."""
    return row["traced"] is not True or row["reconciled"] is not True
