"""The workspace's drill-down (C5; R16, P2, R34).

When the underwriter selects a figure, a money-in bucket (a figure too: M2 and M19 to M25)
or a monthly cell, the workspace lists exactly the lines behind it, the figure's or cell's
own evidence and no other, in the order the figures layer cites them. Each line comes with
its date, description, amount, balance, category and the rule or override that set the
category. Beside the lines it shows, by the figure's kind (P2):

- a sum figure: the total of the lines' signed amounts, which is the figure;
- an outflow figure: that total, saying it is the figure negated;
- a derived figure (an average, share, ratio, count or lowest balance, or net operating
  cash flow): its inputs and its method, and for net operating cash flow (M9) the lines it
  counts only in part.

Every total and input is the figures layer's (metrics.lines_total, metrics.derived_inputs,
metrics.cell_inputs); this module computes no money (R2). Its HTML escapes every piece of
text from the case (R34).
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .. import metrics
from ..models import MonthlyCell, format_value, money_exact
from . import summary
from .edit import categories_for, override_form

CELL_TITLES = dict(summary.CELLS)
CELL_METHODS = {
    "money_in": "Every credit dated in the month.",
    "money_out": "Every debit dated in the month, reported as a positive amount.",
    "trading_receipts": "The month's trading receipts (M2's lines dated in the month), cash paid in included.",
    "average_balance": ("The close of every day of the month in the period, carried over days with no lines, "
                        "averaged over those days."),
    "lowest_balance": "The lowest close of any day of the month in the period, carried over days with no lines.",
    "days_overdrawn": "Days of the month in the period that closed overdrawn, carried over days with no lines.",
}
NEGATED = "the figure negated: this figure reports money paid out as a positive amount"


class DrillError(ValueError):
    """A request that names no figure or cell of the case. Its message names only what was asked."""


def _line(t, overridden: dict[str, str], note: set[str]) -> dict[str, Any]:
    return {"id": t.id, "date": t.date.isoformat(), "description": t.description,
            "amount": t.amount, "amount_display": money_exact(t.amount),
            "balance": t.balance, "balance_display": money_exact(t.balance),
            "category": t.category.value if t.category else None, "rule": t.rule_id,
            "overridden": t.id in overridden, "counted_in_part": t.id in note,
            "categories": categories_for(t)}


def _total(statement, ids: list[str], kind: str) -> dict[str, Any] | None:
    if kind == "derived":
        return None
    p = metrics.lines_total(statement, ids)
    return {"pence": p, "display": metrics.money_text(p),
            "is": "the figure" if kind == "sum" else NEGATED}


def lines_behind(folder: Path, case_id: str, *, account: str | None = None, figure: str | None = None,
                 month: str | None = None, cell: str | None = None) -> dict[str, Any]:
    """The lines behind one figure (`figure`) or one monthly cell (`month` and `cell`) of an
    account, with their total, or the inputs and method, by the figure's kind (R16)."""
    folder = Path(folder)
    if (figure is not None and (month is not None or cell is not None)) or (
            figure is None and (month is None or cell is None)):
        raise DrillError("name a figure, or a month and a cell")
    out: dict[str, Any] = {"case": case_id, "state": "shown", "refusal": None, "reconciliation": None,
                           "position": summary.log_position(folder)}  # read first, as the summary's (R17)
    figures, refused = summary.load_figures(folder)
    if refused is not None:
        out.update(refused)
        return out
    if account is None:
        if len(figures) != 1:
            raise DrillError("name the account")
        account = next(iter(figures))
    f = figures.get(account)
    if f is None:
        raise DrillError("no such account in this case")
    statement, by_line = f.statement, f.statement.by_id()
    out["account"] = account
    if figure is not None:
        m = next((x for x in f.metrics if x.id == figure), None)
        if m is None:
            raise DrillError("no such figure")
        kind = metrics.figure_kind(m.id)
        note = set(m.evidence_note or [])
        out.update(target={"figure": m.id}, id=m.id, name=m.name, unit=m.unit, value=m.value,
                   display=m.display_exact(), kind=kind, method=m.method, overridden=m.id in f.marked,
                   lines=[_line(by_line[i], f.overridden, note) for i in m.evidence],
                   total=_total(statement, m.evidence, kind), counted_in_part=list(m.evidence_note or []),
                   inputs=None, closes=None)
        if kind == "derived":
            out.update(metrics.derived_inputs(statement, f.metrics, m.id, f.proposed_repayment, f.vat_stagger))
        return out
    row = next((r for r in f.monthly if r.month == month), None)
    if row is None or cell not in CELL_TITLES:
        raise DrillError("no such monthly cell")
    c: MonthlyCell = getattr(row, cell)
    out.update(target={"month": row.month, "cell": cell}, id=f"{row.month} {cell}",
               name=f"{CELL_TITLES[cell]}, {row.month}" + (" (part month)" if row.part_month else ""),
               unit=c.unit, value=c.value, display=_cell_display(c), kind=c.kind, method=CELL_METHODS[cell],
               note=c.note, overridden=(row.month, cell) in f.marked_cells,
               lines=[_line(by_line[i], f.overridden, set()) for i in c.evidence],
               total=_total(statement, c.evidence, c.kind), counted_in_part=[], inputs=None, closes=None)
    if c.kind == "derived":
        out.update(metrics.cell_inputs(statement, row))
    return out


def _cell_display(c: MonthlyCell) -> str:
    if c.value is None:
        return "n/a"
    return money_exact(c.value) if c.unit == "GBP" else format_value(c.value, c.unit)


# --- HTML ----------------------------------------------------------------------------------


def _e(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def link(token: str, account: str, **target: str) -> str:
    """The workspace URL of a figure's or cell's lines, already escaped for an attribute."""
    return _e("/lines?" + urlencode({"token": token, "account": account, **target}))


def render(d: dict[str, Any], token: str) -> str:
    """The drill-down as HTML for the page's main element; every case text escaped (R34)."""
    back = f'<p><a href="{_e("/?" + urlencode({"token": token}))}">Back to the case</a></p>'
    if d["state"] != "shown":
        return back + summary.render({"business_name": None, "label": None, "profile": None, **d,
                                      "accounts": []})
    head = (f'<section id="drilldown"><h2>{_e(d["id"])}: {_e(d["name"])}</h2>'
            f'<p class="figure-value">Account {_e(d["account"])}: <strong>{_e(d["display"])}</strong>'
            + (' <span class="overridden">rests on an override</span>' if d["overridden"] else "")
            + f'</p><p class="method">{_e(d["method"])}</p>')
    if d.get("note"):
        head += f'<p class="note">{_e(d["note"])}</p>'
    t = d["total"]
    if t is not None:
        head += (f'<p class="lines-total">Total of the lines\' signed amounts: <strong>{_e(t["display"])}</strong>, '
                 f'{_e(t["is"])}.</p>')
    if d["inputs"]:
        rows = "".join(f'<tr><td>{_e(i["label"])}' + (f' ({_e(i["figure"])})' if i.get("figure") else "")
                       + f'</td><td class="num">{_e(i["display"])}</td></tr>' for i in d["inputs"])
        head += f'<h3>Inputs</h3><table class="inputs"><tbody>{rows}</tbody></table>'
    if d["counted_in_part"]:
        head += ('<p class="in-part">Counted only in part (money sent back by someone the business paid by '
                 f'transfer, offsetting no more than was sent): {_e(", ".join(d["counted_in_part"]))}</p>')
    if d["closes"] is not None:
        rows = "".join(f'<tr><td>{_e(c["date"])}</td><td class="num">{_e(c["display"])}</td>'
                       f'<td>{_e(c["line"])}</td></tr>' for c in d["closes"])
        head += ('<h3>End-of-day closes read</h3><table class="closes"><thead><tr><th>Day</th>'
                 f'<th class="num">Close</th><th>Set by line</th></tr></thead><tbody>{rows}</tbody></table>')
    position = d.get("position")
    here = "/lines?" + urlencode({"account": d["account"], **d["target"]})  # where a form comes back to

    def form(x):
        return f'<td>{override_form(token, d["account"], x, position, here)}</td>' if position is not None else ""

    rows = "".join(
        f'<tr><td>{_e(x["id"])}</td><td>{_e(x["date"])}</td><td>{_e(x["description"])}</td>'
        f'<td class="num">{_e(x["amount_display"])}</td><td class="num">{_e(x["balance_display"])}</td>'
        f'<td>{_e(x["category"])}</td><td>{_e(x["rule"])}'
        + (' <span class="overridden">override</span>' if x["overridden"] else "")
        + (' <span class="in-part">counted in part</span>' if x["counted_in_part"] else "")
        + f'</td>{form(x)}</tr>' for x in d["lines"])
    change = "<th>Change category</th>" if position is not None else ""
    table = ('<h3>Lines</h3><table class="lines"><thead><tr><th>Line</th><th>Date</th><th>Description</th>'
             '<th class="num">Amount</th><th class="num">Balance</th><th>Category</th><th>Set by</th>'
             f'{change}</tr></thead><tbody>{rows}</tbody></table></section>')
    return back + head + table
