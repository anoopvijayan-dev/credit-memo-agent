"""The workspace's comparison entry (C5; R51, R2, R34).

The underwriter enters their own figure for a memo field (one of the figures the memo field
map maps to it) or for a month (one cell of the monthly summary). The workspace shows:

- the tool's figure beside theirs, and the difference, theirs less the tool's, which the
  figures layer computes (metrics.comparison): in whole pence for money (R2), exactly for any
  other unit, and n/a when the tool's figure is n/a;
- the lines in that period whose bucket a person might read differently, largest first:
  cash deposits, unexplained lines, possible own-account transfers and overridden lines,
  each with why it is listed (indicators.comparison_lines). A memo field's period is the
  statement's; a month's is the month's days in the period.

Nothing is saved: a comparison writes nothing to the case or its log. The data path is
POST /api/compare and the page path POST /compare, as the workspace's other forms are;
this module computes no money, and its HTML escapes every piece of text from the case (R34).
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .. import firm, metrics, overrides, pack
from .. import indicators as ind
from ..models import money_exact
from . import summary
from .drilldown import CELL_TITLES, account_title

REASON_TITLES = {"cash_deposit": "cash deposit", "unexplained": "unexplained",
                 "possible_own_account_transfer": "possible own-account transfer", "overridden": "overridden"}
KEYS = ("account", "field", "figure", "month", "cell", "value", "target")


class CompareError(ValueError):
    """A comparison that names no memo field or month of the case, or a figure that cannot be
    compared. Its message names only what was asked."""


def _text(fields: dict, key: str) -> str | None:
    v = fields.get(key)
    if v is None or v == "":
        return None
    if isinstance(v, bool) or not isinstance(v, (str, int)):
        raise CompareError(f"give {key} as text")
    return str(v)


def _field_target(fm: pack.FieldMap, field: str, figure: str | None) -> str:
    f = next((x for x in fm.fields if x.id == field), None)
    if f is None:
        raise CompareError("no such memo field")
    if not f.figures:
        raise CompareError("no figure is mapped to this memo field")
    if figure is None:
        if len(f.figures) > 1:
            raise CompareError("name the figure of this memo field")
        return f.figures[0]
    if figure not in f.figures:
        raise CompareError("that figure is not mapped to this memo field")
    return figure


def _pair_ids(f: overrides.CaseFigures, account: str) -> list[list[str]]:
    """The possible own-account transfers (R9) as this account's lines read them; the case's
    combined figures name every account's lines by their case ids."""
    if account == overrides.COMBINED:
        return [[metrics.case_line_id(a, line) for a, line in g] for g in f.possible_pairs]
    return [[line for a, line in g if a == account] for g in f.possible_pairs]


def _line(t, reasons: list[str]) -> dict[str, Any]:
    return {"id": t.id, "date": t.date.isoformat(), "description": t.description,
            "amount": t.amount, "amount_display": money_exact(t.amount),
            "category": t.category.value if t.category else None, "rule": t.rule_id,
            "reasons": reasons}


def compare(folder: Path, case_id: str, *, account: str | None = None, field: str | None = None,
            figure: str | None = None, month: str | None = None, cell: str | None = None,
            value: str | None = None) -> dict[str, Any]:
    """The underwriter's figure for a memo field (`field`, and `figure` when the field maps
    more than one) or a month (`month` and `cell`), against the tool's, with the lines of that
    period a person might read differently (R51)."""
    folder = Path(folder)
    if (field is None) == (month is None) or (month is not None and (cell is None or figure is not None)) \
            or (field is not None and cell is not None):
        raise CompareError("name a memo field, or a month and a cell")
    if value is None:
        raise CompareError("enter your figure")
    out: dict[str, Any] = {"case": case_id, "state": "shown", "refusal": None, "reconciliation": None}
    figures, refused = summary.load_figures(folder)
    if refused is not None:
        out.update(refused)
        return out
    if account is None:
        if len(figures) != 1:
            raise CompareError("name the account")
        account = next(iter(figures))
    first = next(iter(figures.values()), None)
    f = overrides.combined_figures(figures) if account == overrides.COMBINED else figures.get(account)
    if f is None:
        raise CompareError("no such account in this case")
    if account == overrides.COMBINED and first is not None:
        f.possible_pairs = first.possible_pairs  # the same list on every account of the case (R9)
    statement = f.statement
    if not statement.transactions:
        raise CompareError("this account has no lines")
    if field is not None:
        try:
            fm = pack.load_field_map(firm.load())
        except (pack.FieldMapError, firm.FirmConfigError) as e:
            out.update(state="refused", refusal=f"{str(e).rstrip('.')}. No comparison is shown.")
            return out
        figure = _field_target(fm, field, figure)
        m = next((x for x in f.metrics if x.id == figure), None)
        if m is None:
            raise CompareError("no such figure")
        title = next(x.title for x in fm.fields if x.id == field)
        target = {"field": field, "figure": m.id}
        name, unit, ours, note = f"{title}: {m.id} {m.name}", m.unit, m.value, (m.method if m.value is None else None)
        period = (statement.start, statement.end)
    else:
        row = next((r for r in f.monthly if r.month == month), None)
        if row is None or cell not in CELL_TITLES:
            raise CompareError("no such monthly cell")
        c = getattr(row, cell)
        target = {"month": row.month, "cell": cell}
        name = f"{CELL_TITLES[cell]}, {row.month}" + (" (part month)" if row.part_month else "")
        unit, ours, note = c.unit, c.value, c.note
        period = (row.start, row.end)
    try:
        result = metrics.comparison(ours, unit, value)
    except metrics.ComparisonError as e:
        raise CompareError(str(e).rstrip(".")) from None
    listed = ind.comparison_lines(statement, period[0], period[1], f.overridden, _pair_ids(f, account))
    out.update(account=account, target=target, name=name, unit=unit, note=note,
               period={"start": period[0].isoformat(), "end": period[1].isoformat()}, **result,
               lines=[_line(t, reasons) for t, reasons in listed])
    return out


def _fields_of(fields: dict) -> dict[str, str | None]:
    args = {k: _text(fields, k) for k in KEYS}
    target = args.pop("target")
    if target is not None:  # the page's one select: "field:<id>:<figure>" or "month:<YYYY-MM>:<cell>"
        kind, _, rest = target.partition(":")
        first, _, second = rest.partition(":")
        if kind == "field" and first and second and args["field"] is None and args["month"] is None:
            args["field"], args["figure"] = first, second
        elif kind == "month" and first and second and args["field"] is None and args["month"] is None:
            args["month"], args["cell"] = first, second
        else:
            raise CompareError("name a memo field, or a month and a cell")
    return args


def answer(folder: Path, case_id: str, fields: dict) -> tuple[int, dict[str, Any]]:
    """One comparison from the workspace: 200 with the comparison (or the refusal the case's
    figures give), or 400 naming only what was asked. Nothing is written."""
    unknown = sorted(k for k in fields if k not in KEYS and k != "back")
    if unknown:
        return 400, {"error": "Give only the account, the memo field or month and cell, and your figure."}
    try:
        return 200, compare(folder, case_id, **_fields_of(fields))
    except CompareError as e:
        return 400, {"error": f"{str(e)[:1].upper()}{str(e)[1:]}."}


# --- HTML ----------------------------------------------------------------------------------


def _e(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def _back(token: str) -> str:
    return f'<p><a href="{_e("/?" + urlencode({"token": token}))}">Back to the case</a></p>'


def form_html(s: dict[str, Any], token: str) -> str:
    """The case page's comparison entry: pick a memo field's figure or a month's cell, enter
    your own figure (R51)."""
    if s.get("state") != "shown" or not s.get("accounts"):
        return ""
    try:
        fm = pack.load_field_map(firm.load())
        field_opts = [(f"field:{f.id}:{fig}", f"{f.title}: {fig}") for f in fm.fields for fig in f.figures]
    except Exception:  # the field map's refusal is shown by the preview; months can still be compared
        field_opts = []
    accounts = ([overrides.COMBINED] if s.get("combined") else []) + [a["account"] for a in s["accounts"]]
    monthly = (s["combined"] if s.get("combined") else s["accounts"][0])["monthly"]
    month_opts = [(f"month:{r['month']}:{key}", f"{r['month']}: {title}") for r in monthly
                  for key, title in CELL_TITLES.items()]
    options = "".join(f'<option value="{_e(v)}">{_e(label)}</option>' for v, label in field_opts + month_opts)
    pick = ""
    if len(accounts) > 1:
        pick = ('<select name="account" aria-label="Account">'
                + "".join(f'<option value="{_e(a)}">{_e(account_title(a))}</option>' for a in accounts)
                + "</select> ")
    action = _e("/compare?" + urlencode({"token": token}))
    return ('<section id="compare"><h2>Compare with your own figure</h2>'
            '<p>Enter your figure for a memo field or a month. The tool shows the difference and the lines of '
            'that period whose bucket you might read differently. Nothing is saved.</p>'
            f'<form class="compare" method="post" action="{action}">{pick}'
            f'<select name="target" required aria-label="Memo field or month">{options}</select> '
            '<input name="value" required maxlength="40" placeholder="Your figure" aria-label="Your figure"> '
            '<button type="submit">Compare</button></form></section>')


def html(status: int, reply: dict[str, Any], token: str) -> str:
    """The page's main element for a comparison, or why none is shown; every case text escaped (R34)."""
    if status != 200:
        return (f'<section class="refusal" id="compare-refused"><h2>No comparison</h2>'
                f'<p>{_e(reply.get("error", "This could not be compared."))}</p>{_back(token)}</section>')
    if reply["state"] != "shown":
        return (f'<section class="refusal" id="compare-refused"><h2>No comparison</h2>'
                f'<p>{_e(reply.get("refusal") or "The case figures do not reconcile, so none is shown.")}</p>'
                f'{_back(token)}</section>')
    note = f'<p class="note">{_e(reply["note"])}</p>' if reply.get("note") else ""
    rows = "".join(
        f'<tr><td>{_e(x["id"])}</td><td>{_e(x["date"])}</td><td>{_e(x["description"])}</td>'
        f'<td class="num">{_e(x["amount_display"])}</td><td>{_e(x["category"])}</td>'
        f'<td>{_e(", ".join(REASON_TITLES.get(r, r) for r in x["reasons"]))}</td></tr>' for x in reply["lines"])
    lines = ('<p>No cash deposit, unexplained line, possible own-account transfer or overridden line in this '
             'period.</p>' if not reply["lines"] else
             '<table class="lines"><thead><tr><th>Line</th><th>Date</th><th>Description</th>'
             '<th class="num">Amount</th><th>Category</th><th>Why listed</th></tr></thead>'
             f'<tbody>{rows}</tbody></table>')
    return (f'<section id="comparison"><h2>{_e(reply["name"])}</h2>'
            f'<p>{_e(account_title(reply["account"]))}, {_e(reply["period"]["start"])} to '
            f'{_e(reply["period"]["end"])}.</p>'
            '<table class="comparison"><tbody>'
            f'<tr><th>Your figure</th><td class="num">{_e(reply["given_display"])}</td></tr>'
            f'<tr><th>The tool\'s figure</th><td class="num">{_e(reply["ours_display"])}</td></tr>'
            f'<tr><th>Difference (yours less the tool\'s)</th><td class="num">{_e(reply["difference_display"])}'
            '</td></tr></tbody></table>' + note
            + '<h3>Lines a person might read differently, largest first</h3>' + lines + _back(token) + '</section>')
