"""The workspace's case summary (C5; R3, R34, P8).

case_summary() loads the case's statements and computes every figure through the figures
layer, with the profile and the whole override log (overrides.case_figures); it does no
arithmetic on money itself. What it hands on is plain data, which /api/case sends as JSON
and render() turns into the page's HTML:

- for each account, the money-in breakdown (trading receipts M2 with the cash inside it M18,
  then M19 to M25, adding up to total money in M17), every figure, and the monthly summary,
  money to the penny as every table shows it (R2);
- or, when the money-in buckets do not reconcile with total money in, or a credit is in no
  bucket or in more than one, no figures at all, for any account: only the difference in
  pence and the lines involved (R3);
- or, when the case cannot be read (its log broken or being saved, a statement or the
  override log refused), no figures and the refusal.

Every piece of text from the case (descriptions, the label and business name, refusals that
can quote a statement) is escaped before it is part of the HTML (R34, P8); the page's markup
comes from this module alone.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from .. import case as caselog
from .. import metrics, overrides, store
from ..ingest import StatementError
from ..models import Metric, MonthlyCell, format_value, money_exact

BREAKDOWN = ("M2", "M19", "M20", "M21", "M22", "M23", "M24", "M25")  # the money-in buckets (R1)
TOTAL, CASH = "M17", "M18"
CELLS = (("money_in", "Money in"), ("money_out", "Money out"), ("trading_receipts", "Trading receipts"),
         ("average_balance", "Average end-of-day balance"), ("lowest_balance", "Lowest end-of-day balance"),
         ("days_overdrawn", "Days overdrawn"))


def _figure(m: Metric, marked: set[str]) -> dict[str, Any]:
    return {"id": m.id, "name": m.name, "value": m.value, "unit": m.unit, "display": m.display_exact(),
            "method": m.method, "lines": len(m.evidence), "overridden": m.id in marked}


def _cell(c: MonthlyCell, overridden: bool) -> dict[str, Any]:
    if c.value is None:
        shown = "n/a"
    elif c.unit == "GBP":
        shown = money_exact(c.value)
    else:
        shown = format_value(c.value, c.unit)
    return {"value": c.value, "unit": c.unit, "kind": c.kind, "display": shown, "note": c.note,
            "lines": len(c.evidence), "overridden": overridden}


def _account(f: overrides.CaseFigures) -> dict[str, Any]:
    by_id = {m.id: m for m in f.metrics}
    txns = f.statement.transactions
    return {
        "account": f.account,
        "statement_sha256": f.statement_sha256,
        "period": {"start": f.statement.start.isoformat(), "end": f.statement.end.isoformat()} if txns else None,
        "line_count": len(txns),
        "money_in": {"total": _figure(by_id[TOTAL], f.marked),
                     "buckets": [_figure(by_id[i], f.marked) for i in BREAKDOWN],
                     "cash_within_trading": _figure(by_id[CASH], f.marked)},
        "figures": [_figure(m, f.marked) for m in f.metrics],
        "monthly": [{"month": r.month, "part_month": r.part_month, "start": r.start.isoformat(),
                     "end": r.end.isoformat(),
                     **{key: _cell(getattr(r, key), (r.month, key) in f.marked_cells) for key, _ in CELLS}}
                    for r in f.monthly],
    }


def _not_reconciled(e: metrics.ReconciliationError) -> dict[str, Any]:
    statement = getattr(e, "statement", None)
    found = statement.by_id() if statement is not None else {}
    lines = []
    for line_id in e.lines:
        t = found.get(line_id)
        lines.append({"account": getattr(e, "account", None), "id": line_id,
                      "date": t.date.isoformat() if t else None,
                      "description": t.description if t else None,
                      "amount": money_exact(t.amount) if t else None,
                      "category": t.category.value if t and t.category else None})
    return {"difference_pence": e.difference_pence, "difference": metrics.pounds_text(e.difference_pence),
            "lines": lines}


def case_summary(folder: Path, case_id: str) -> dict[str, Any]:
    """The case as the workspace shows it, computed afresh from its files."""
    folder = Path(folder)
    out: dict[str, Any] = {"case": case_id, "label": None, "business_name": None, "state": "shown",
                           "accounts": [], "reconciliation": None, "refusal": None}
    try:
        profile = store.load_profile(folder)
        out["label"], out["business_name"] = profile.get("label"), profile.get("business_name")
        figures = overrides.case_figures(folder)
    except metrics.ReconciliationError as e:  # R3: nothing is shown
        out.update(state="not_reconciled", reconciliation=_not_reconciled(e))
        return out
    except caselog.CaseLocked:
        out.update(state="refused", refusal="Another window or command is saving this case; reload in a moment.")
        return out
    except caselog.CaseLogBroken as e:
        out.update(state="refused", refusal=f"The case log is broken at entry {e.first_bad}: {e.reason}. "
                                            "No figures are shown until it is resolved.")
        return out
    except (store.CaseStoreError, StatementError, metrics.AmountError) as e:
        out.update(state="refused", refusal=f"{str(e).rstrip('.')}. No figures are shown for this case.")
        return out
    except OSError as e:
        out.update(state="refused", refusal=f"A file of the case could not be read ({e.strerror or type(e).__name__}).")
        return out
    out["accounts"] = [_account(f) for f in figures.values()]
    if not out["accounts"]:
        out["state"] = "empty"
    return out


# --- HTML ----------------------------------------------------------------------------------


def _e(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def _figure_row(f: dict[str, Any], cls: str = "") -> str:
    mark = ' <span class="overridden">rests on an override</span>' if f["overridden"] else ""
    attr = f' class="{cls}"' if cls else ""
    return (f'<tr{attr}><td>{_e(f["id"])}</td><td>{_e(f["name"])}{mark}</td>'
            f'<td class="num">{_e(f["display"])}</td><td class="num">{_e(f["lines"])}</td></tr>')


def _account_html(a: dict[str, Any]) -> str:
    period = f'{_e(a["period"]["start"])} to {_e(a["period"]["end"])}, ' if a["period"] else ""
    head = '<thead><tr><th>Id</th><th>Figure</th><th class="num">Value</th><th class="num">Lines</th></tr></thead>'
    mi = a["money_in"]
    rows = []
    for b in mi["buckets"]:
        rows.append(_figure_row(b))
        if b["id"] == "M2":
            rows.append(_figure_row(mi["cash_within_trading"], "within"))
    breakdown = (f'<table class="breakdown">{head}<tbody>{"".join(rows)}</tbody>'
                 f'<tfoot>{_figure_row(mi["total"], "total")}</tfoot></table>')
    figures = ('<table class="figures"><thead><tr><th>Id</th><th>Figure</th><th class="num">Value</th>'
               '<th>Method</th><th class="num">Lines</th></tr></thead><tbody>'
               + "".join(f'<tr><td>{_e(f["id"])}</td><td>{_e(f["name"])}'
                         + (' <span class="overridden">rests on an override</span>' if f["overridden"] else "")
                         + f'</td><td class="num">{_e(f["display"])}</td><td>{_e(f["method"])}</td>'
                         f'<td class="num">{_e(f["lines"])}</td></tr>' for f in a["figures"])
               + "</tbody></table>")
    month_rows = []
    for r in a["monthly"]:
        cells = []
        for key, _ in CELLS:
            c = r[key]
            mark = ' <span class="overridden">*</span>' if c["overridden"] else ""
            cells.append(f'<td class="num">{_e(c["display"])}{mark}</td>')
        part = ' <span class="part">part month</span>' if r["part_month"] else ""
        month_rows.append(f'<tr><td>{_e(r["month"])}{part}</td>{"".join(cells)}</tr>')
    monthly = ('<table class="monthly"><thead><tr><th>Month</th>'
               + "".join(f'<th class="num">{_e(title)}</th>' for _, title in CELLS)
               + f'</tr></thead><tbody>{"".join(month_rows)}</tbody></table>')
    return (f'<section class="account"><h2>Account {_e(a["account"])}</h2>'
            f'<p class="period">{period}{_e(a["line_count"])} lines</p>'
            f'<h3>Money in</h3>{breakdown}<h3>Figures</h3>{figures}<h3>Monthly summary</h3>{monthly}</section>')


def _pounds(text: str) -> str:
    return f"-£{text[1:]}" if text.startswith("-") else f"£{text}"


def _reconciliation_html(r: dict[str, Any]) -> str:
    rows = "".join(
        f'<tr><td>{_e(x["account"])}</td><td>{_e(x["id"])}</td><td>{_e(x["date"])}</td>'
        f'<td>{_e(x["description"])}</td><td class="num">{_e(x["amount"])}</td><td>{_e(x["category"])}</td></tr>'
        for x in r["lines"])
    return ('<section class="refusal" id="not-reconciled"><h2>No figures are shown for this case</h2>'
            f'<p>The money-in buckets do not reconcile with total money in: they differ by '
            f'{_e(r["difference_pence"])} pence ({_e(_pounds(r["difference"]))}), and {len(r["lines"])} line(s) are in '
            'no bucket, in more than one, or counted as trading receipts without being money in.</p>'
            '<table class="lines"><thead><tr><th>Account</th><th>Line</th><th>Date</th><th>Description</th>'
            f'<th class="num">Amount</th><th>Category</th></tr></thead><tbody>{rows}</tbody></table></section>')


def render(summary: dict[str, Any]) -> str:
    """The case summary as HTML for the page's main element; every case text escaped."""
    names = [x for x in (summary["business_name"], summary["label"]) if x]
    head = (f'<section id="summary-head"><p class="case-name">{_e(" — ".join(names))}</p></section>'
            if names else "")
    state = summary["state"]
    if state == "not_reconciled":
        return head + _reconciliation_html(summary["reconciliation"])
    if state == "refused":
        return (head + '<section class="refusal" id="refused"><h2>No figures are shown for this case</h2>'
                f'<p>{_e(summary["refusal"])}</p></section>')
    if state == "empty":
        return head + '<section id="empty"><p>No statement has been imported into this case yet.</p></section>'
    return head + "".join(_account_html(a) for a in summary["accounts"])
