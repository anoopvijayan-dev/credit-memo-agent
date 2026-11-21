"""The workspace's case summary (C5; R3, R34, P8).

case_summary() loads the case's statements and computes every figure through the figures
layer, with the profile and the whole override log (overrides.case_figures); it does no
arithmetic on money itself. What it hands on is plain data, which /api/case sends as JSON
and render() turns into the page's HTML:

- for each account, the money-in breakdown (trading receipts M2 with the cash inside it M18,
  then M19 to M25, adding up to total money in M17), every figure, and the monthly summary,
  money to the penny as every table shows it (R2);
- or, when the money-in buckets do not reconcile with total money in, or a credit is in no
  bucket or in more than one, in any account, no figures at all, for any account: only each
  failing account's difference in pence and the lines involved (R3);
- or, when the case cannot be read (its log broken or being saved, a statement or the
  override log refused), no figures and the refusal.

Every piece of text from the case (descriptions, the label and business name, refusals that
can quote a statement) is escaped before it is part of the HTML (R34, P8); the page's markup
comes from this module alone.
"""

from __future__ import annotations

from decimal import Decimal
from html import escape
from pathlib import Path
from typing import Any

from .. import case as caselog
from .. import metrics, overrides, store
from ..categorise import RulesetError
from ..ingest import StatementError
from ..models import Metric, MonthlyCell, format_value, money_exact

BREAKDOWN = ("M2", "M19", "M20", "M21", "M22", "M23", "M24", "M25")  # the money-in buckets (R1)
TOTAL, CASH = "M17", "M18"
# The profile fields the figures read (R5, R6, R13), shown with them so the underwriter sees
# what made a credit director funding or an own-account transfer, and what M28 includes.
PROFILE_SHOWN = ("directors", "shareholders", "own_accounts", "proposed_monthly_repayment")
PROFILE_TITLES = {"directors": "Directors", "shareholders": "Shareholders", "own_accounts": "Own accounts"}
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
    failures = getattr(e, "failures", None) or [e]
    lines, accounts = [], []
    for f in failures:
        statement = getattr(f, "statement", None)
        found = statement.by_id() if statement is not None else {}
        account = getattr(f, "account", None)
        accounts.append({"account": account, "difference_pence": f.difference_pence,
                         "difference": metrics.pounds_text(f.difference_pence), "lines": len(f.lines)})
        for line_id in f.lines:
            t = found.get(line_id)
            lines.append({"account": account, "id": line_id,
                          "date": t.date.isoformat() if t else None,
                          "description": t.description if t else None,
                          "amount": money_exact(t.amount) if t else None,
                          "category": t.category.value if t and t.category else None})
    return {"difference_pence": e.difference_pence, "difference": metrics.pounds_text(e.difference_pence),
            "accounts": accounts, "lines": lines}


def refusal(e: Exception) -> dict[str, Any] | None:
    """Why a case cannot be shown, as the state and refusal (or reconciliation) the workspace
    reports, or None for a failure no refusal foresees."""
    if isinstance(e, metrics.ReconciliationError):  # R3: nothing is shown
        return {"state": "not_reconciled", "reconciliation": _not_reconciled(e)}
    if isinstance(e, caselog.CaseLocked):
        return {"state": "refused", "refusal": "Another window or command is saving this case; reload in a moment."}
    if isinstance(e, caselog.CaseLogBroken):
        return {"state": "refused", "refusal": f"The case log is broken at entry {e.first_bad}: {e.reason}. "
                                               "No figures are shown until it is resolved."}
    if isinstance(e, (store.CaseStoreError, StatementError, metrics.AmountError, RulesetError)):
        return {"state": "refused", "refusal": f"{str(e).rstrip('.')}. No figures are shown for this case."}
    if isinstance(e, OSError):
        return {"state": "refused",
                "refusal": f"A file of the case could not be read ({e.strerror or type(e).__name__})."}
    return None


def load_figures(folder: Path) -> tuple[dict[str, overrides.CaseFigures] | None, dict[str, Any] | None]:
    """Every account's figures from the figures layer, or why none can be shown (R3)."""
    try:
        return overrides.case_figures(folder), None
    except Exception as e:
        refused = refusal(e)
        if refused is None:
            raise
        return None, refused


def case_summary(folder: Path, case_id: str) -> dict[str, Any]:
    """The case as the workspace shows it, computed afresh from its files."""
    folder = Path(folder)
    out: dict[str, Any] = {"case": case_id, "label": None, "business_name": None, "profile": None,
                           "state": "shown", "accounts": [], "reconciliation": None, "refusal": None}
    try:
        profile = store.load_profile(folder)
        out["label"], out["business_name"] = profile.get("label"), profile.get("business_name")
        out["profile"] = {key: (list(profile[key]) if key in store.LIST_FIELDS else profile[key])
                          for key in PROFILE_SHOWN}
    except Exception as e:
        refused = refusal(e)
        if refused is None:
            raise
        out.update(refused)
        return out
    figures, refused = load_figures(folder)
    if refused is not None:
        out.update(refused)
        return out
    out["accounts"] = [_account(f) for f in figures.values()]
    if not out["accounts"]:
        out["state"] = "empty"
    return out


# --- HTML ----------------------------------------------------------------------------------


def _e(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def _linked(text: str, href: str | None) -> str:
    """Escaped text, as a link to its lines when the page has a token to link with (R16)."""
    return f'<a href="{href}">{_e(text)}</a>' if href else _e(text)


def _href(token: str | None, account: str, **target: str) -> str | None:
    if token is None:
        return None
    from .drilldown import link

    return link(token, account, **target)


def _figure_row(f: dict[str, Any], cls: str = "", href: str | None = None) -> str:
    mark = ' <span class="overridden">rests on an override</span>' if f["overridden"] else ""
    attr = f' class="{cls}"' if cls else ""
    return (f'<tr{attr}><td>{_linked(f["id"], href)}</td><td>{_e(f["name"])}{mark}</td>'
            f'<td class="num">{_e(f["display"])}</td><td class="num">{_e(f["lines"])}</td></tr>')


def _account_html(a: dict[str, Any], token: str | None = None) -> str:
    acct = a["account"]
    period = f'{_e(a["period"]["start"])} to {_e(a["period"]["end"])}, ' if a["period"] else ""
    head = '<thead><tr><th>Id</th><th>Figure</th><th class="num">Value</th><th class="num">Lines</th></tr></thead>'
    mi = a["money_in"]
    rows = []
    for b in mi["buckets"]:
        rows.append(_figure_row(b, href=_href(token, acct, figure=b["id"])))
        if b["id"] == "M2":
            cash = mi["cash_within_trading"]
            rows.append(_figure_row(cash, "within", _href(token, acct, figure=cash["id"])))
    total = mi["total"]
    breakdown = (f'<table class="breakdown">{head}<tbody>{"".join(rows)}</tbody>'
                 f'<tfoot>{_figure_row(total, "total", _href(token, acct, figure=total["id"]))}</tfoot></table>')
    figures = ('<table class="figures"><thead><tr><th>Id</th><th>Figure</th><th class="num">Value</th>'
               '<th>Method</th><th class="num">Lines</th></tr></thead><tbody>'
               + "".join(f'<tr><td>{_linked(f["id"], _href(token, acct, figure=f["id"]))}</td><td>{_e(f["name"])}'
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
            why = f' <span class="note">{_e(c["note"])}</span>' if c["value"] is None and c["note"] else ""
            shown = _linked(c["display"], _href(token, acct, month=r["month"], cell=key))
            cells.append(f'<td class="num">{shown}{why}{mark}</td>')
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
    per = "".join(
        f'<p class="account-difference">Account {_e(a["account"])}: the money-in buckets differ from total money in by '
        f'{_e(a["difference_pence"])} pence ({_e(_pounds(a["difference"]))}), and {_e(a["lines"])} line(s) are '
        'in no bucket, in more than one, or counted as trading receipts without being money in.</p>'
        for a in r["accounts"])
    return ('<section class="refusal" id="not-reconciled"><h2>No figures are shown for this case</h2>'
            f'<p>The money-in buckets do not reconcile with total money in in {len(r["accounts"])} account(s).</p>'
            f'{per}'
            '<table class="lines"><thead><tr><th>Account</th><th>Line</th><th>Date</th><th>Description</th>'
            f'<th class="num">Amount</th><th>Category</th></tr></thead><tbody>{rows}</tbody></table></section>')


def _profile_html(p: dict[str, Any] | None) -> str:
    """The profile fields the figures read: each list of names (R5, R6), and the proposed
    monthly repayment that M28 includes (R13)."""
    if p is None:
        return ""
    rows = []
    for key, title in PROFILE_TITLES.items():
        names = p[key]
        shown = ", ".join(_e(n) for n in names) if names else '<span class="none">none named</span>'
        rows.append(f"<tr><th>{_e(title)}</th><td>{shown}</td></tr>")
    repayment = p["proposed_monthly_repayment"]
    shown = (_e(f"£{Decimal(repayment):,.2f}") if repayment is not None  # as typed, to the penny
             else '<span class="none">No proposed monthly repayment given: M28 is n/a</span>')
    rows.append(f"<tr><th>Proposed monthly repayment</th><td>{shown}</td></tr>")
    return f'<section id="profile"><h2>Case profile</h2><table class="profile"><tbody>{"".join(rows)}</tbody></table></section>'


def render(summary: dict[str, Any], token: str | None = None) -> str:
    """The case summary as HTML for the page's main element; every case text escaped. Given
    the page's token, each figure, bucket and monthly cell links to the lines behind it (R16)."""
    names = [x for x in (summary["business_name"], summary["label"]) if x]
    head = (f'<section id="summary-head"><p class="case-name">{_e(" — ".join(names))}</p></section>'
            if names else "")
    head += _profile_html(summary.get("profile"))
    state = summary["state"]
    if state == "not_reconciled":
        return head + _reconciliation_html(summary["reconciliation"])
    if state == "refused":
        return (head + '<section class="refusal" id="refused"><h2>No figures are shown for this case</h2>'
                f'<p>{_e(summary["refusal"])}</p></section>')
    if state == "empty":
        return head + '<section id="empty"><p>No statement has been imported into this case yet.</p></section>'
    return head + "".join(_account_html(a, token) for a in summary["accounts"])
