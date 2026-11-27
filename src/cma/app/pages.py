"""The case app's pages, as HTML. Every value from a case is escaped; every link and form
carries the app's token. No script and no inline style: the look is theme.css (shared with
the review workspace) and app.css. Charts are inline SVG, coloured from the stylesheet."""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from typing import Any
from urllib.parse import urlencode

from .actions import BANKS
from .cases import ATTENTION, BUSY, CHANGED, NEEDS_STATEMENTS, READY, SIGNED, STATUS_TEXT, CaseView

TITLE = "Credit memo"
FILTERS = (("", "All cases"), (NEEDS_STATEMENTS, STATUS_TEXT[NEEDS_STATEMENTS]), (READY, STATUS_TEXT[READY]),
           (CHANGED, STATUS_TEXT[CHANGED]), (SIGNED, STATUS_TEXT[SIGNED]))
STEPS = (("details", "Details", "Business and loan"), ("statements", "Statements", "Bank downloads"),
         ("review", "Review and sign", "Check every figure"), ("memo", "Signed memo", "For the credit file"))
STEP_KEYS = tuple(k for k, *_ in STEPS)

# Small line icons (24px grid), drawn with the text colour.
ICONS = {
    "cases": '<path d="M3 7h18v12H3z"/><path d="M8 7V5h8v2"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "arrow": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "upload": '<path d="M12 16V4M6 10l6-6 6 6"/><path d="M4 20h16"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "doc": '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/><path d="M10 13h6M10 17h6"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
    "alert": '<path d="M12 3l9.5 17h-19z"/><path d="M12 10v4.5M12 17.5v.5"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
}


def icon(name: str, cls: str = "") -> str:
    klass = f' class="{cls}"' if cls else ""
    return (f'<svg{klass} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>')


def _e(value: object) -> str:
    return escape("" if value is None else str(value), quote=True)


def url(token: str, path: str, **params: str) -> str:
    return f"{path}?{urlencode({**params, 'token': token})}"


def _when(stamp: str) -> str:
    """A log time (2026-11-27T12:43:37.708816Z) as a date and minute, UTC."""
    return f"{stamp[:10]} {stamp[11:16]} UTC" if len(stamp) >= 16 else stamp


def _day(stamp: str) -> str:
    return stamp[:10] if len(stamp) >= 10 else stamp


@dataclass
class Nav:
    """What the sidebar shows: the case counts by status, and the case open, if any."""
    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0
    status: str | None = None  # the list filter in force
    case: CaseView | None = None
    step: str | None = None


def _sidebar(token: str, nav: Nav) -> str:
    links = []
    for key, label in FILTERS:
        n = nav.total if not key else nav.counts.get(key, 0)
        active = " active" if nav.case is None and (nav.status or "") == key else ""
        href = url(token, "/", **({"status": key} if key else {}))
        links.append(f'<a class="side-link{active}" href="{_e(href)}"><span>{_e(label)}</span>'
                     f'<span class="count">{n}</span></a>')
    case_nav = ""
    if nav.case is not None:
        v = nav.case
        items = "".join(
            f'<a class="side-link{" active" if nav.step == key else ""}" '
            f'href="{_e(url(token, "/case", id=v.id, step=key))}"><span>{_e(label)}</span></a>'
            for key, label, _sub in STEPS)
        case_nav = (f'<nav class="side-group" aria-label="This case"><span class="side-label">This case</span>'
                    f'<span class="side-case">{_e(v.title)}</span>{items}</nav>')
    return (f'<aside class="side"><a class="brand" href="{_e(url(token, "/"))}"><span class="brand-mark">CM</span>'
            'Credit memo</a>'
            f'<a class="btn primary side-new" href="{_e(url(token, "/new"))}">{icon("plus")}New case</a>'
            f'<nav class="side-group" aria-label="Cases"><span class="side-label">Cases</span>{"".join(links)}</nav>'
            f'{case_nav}'
            f'<p class="side-foot"><span class="lock">{icon("lock")}On this computer only</span><br>'
            'Statements and memos never leave it.</p></aside>')


def _flash(notice: str, error: str) -> str:
    out = ""
    if notice:
        out += f'<p class="banner ok" role="status">{icon("check")}<span>{_e(notice)}</span></p>'
    if error:
        out += f'<p class="banner bad" role="alert">{icon("alert")}<span>{_e(error)}</span></p>'
    return out


def shell(token: str, title: str, body: str, *, nav: Nav | None = None, notice: str = "", error: str = "") -> bytes:
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<link rel="stylesheet" href="{_e(url(token, '/static/theme.css'))}">
<link rel="stylesheet" href="{_e(url(token, '/static/app.css'))}">
</head>
<body>
<div class="shell">
{_sidebar(token, nav or Nav())}
<main class="main">
{_flash(notice, error)}
{body}
</main>
</div>
</body>
</html>
"""
    return page.encode("utf-8")


def _chip(view: CaseView) -> str:
    return f'<span class="chip {view.status}">{_e(view.status_text)}</span>'


def _form(token: str, path: str, inner: str, *, upload: bool = False, cls: str = "") -> str:
    enctype = ' enctype="multipart/form-data"' if upload else ""
    klass = f' class="{cls}"' if cls else ""
    return f'<form method="post" action="{_e(url(token, path))}"{enctype}{klass}>{inner}</form>'


# --- step states -------------------------------------------------------------------------------


def step_states(v: CaseView) -> dict[str, str]:
    """done, next (the one to do now) or todo, for each step of the case. Details are
    optional: they are next only before any statement is in; otherwise the first step not
    done of statements, review and memo is next."""
    signed = v.status == SIGNED
    states = {
        "details": "done" if (v.business or v.profile.get("directors")) else "todo",
        "statements": "done" if v.statements else "todo",
        "review": "done" if signed else "todo",
        "memo": "done" if v.packs and signed else "todo",
    }
    if states["details"] == "todo" and not v.statements:
        states["details"] = "next"
        return states
    for key in ("statements", "review", "memo"):
        if states[key] != "done":
            states[key] = "next"
            break
    return states


def default_step(v: CaseView) -> str:
    """The step a case opens on: its memo once signed, else the step to do next."""
    if v.status == SIGNED:
        return "memo"
    states = step_states(v)
    return next((k for k in STEP_KEYS if states[k] == "next"), "memo")


def _progress(v: CaseView) -> str:
    states = step_states(v)
    dots = "".join(f'<span class="dot {states[k]}" title="{_e(label)}"></span>' for k, label, _s in STEPS)
    done = sum(1 for k in STEP_KEYS if states[k] == "done")
    return f'<span class="progress" aria-label="{done} of 4 steps done">{dots}</span>'


# --- the list of cases ------------------------------------------------------------------------


def cases_page(token: str, views: list[CaseView], nav: Nav, *, query: str = "", notice: str = "",
               error: str = "") -> bytes:
    status = nav.status or ""
    heading = dict(FILTERS).get(status, "All cases")
    search = (f'<form class="search" method="get" action="/" role="search">'
              f'<input type="hidden" name="token" value="{_e(token)}">'
              + (f'<input type="hidden" name="status" value="{_e(status)}">' if status else "")
              + f'<label class="search-box">{icon("search")}<input type="search" name="q" value="{_e(query)}" '
              'placeholder="Search by case or business" aria-label="Search cases"></label></form>')
    head = (f'<header class="page-head"><div class="titles"><span class="eyebrow">Cases</span>'
            f'<h1>{_e(heading)}</h1></div><div class="actions">{search}'
            f'<a class="btn primary" href="{_e(url(token, "/new"))}">{icon("plus")}New case</a></div></header>')
    stats = []
    for key, label, level in ((NEEDS_STATEMENTS, "Needs statements", "warn"), (READY, "Ready for review", "info"),
                              (CHANGED, "Changed since signing", "warn"), (SIGNED, "Signed", "ok")):
        n = nav.counts.get(key, 0)
        active = " active" if status == key else ""
        stats.append(f'<a class="stat {level}{active}" href="{_e(url(token, "/", status=key))}">'
                     f'<span class="stat-n">{n}</span><span class="stat-label">{_e(label)}</span></a>')
    stat_row = f'<nav class="stats" aria-label="Cases by status">{"".join(stats)}</nav>' if nav.total else ""
    if not nav.total:
        body = (head + '<section class="empty-state card">'
                f'<span class="empty-art">{icon("doc")}</span><h2>Start your first case</h2>'
                '<p>A case holds one business\'s bank statements, the figures worked out from them, your review '
                'and the signed memo. It takes about ten minutes with a year of statements.</p>'
                '<ol class="how"><li><span><strong>Details</strong> of the business and the loan</span></li>'
                '<li><span><strong>Statements</strong> as downloaded from the bank</span></li>'
                '<li><span><strong>Review</strong> every figure and the lines behind it</span></li>'
                '<li><span><strong>Sign</strong> the memo for the credit file</span></li></ol>'
                f'<a class="btn primary big" href="{_e(url(token, "/new"))}">{icon("plus")}New case</a></section>')
        return shell(token, TITLE, body, nav=nav, notice=notice, error=error)
    if not views:
        listing = ('<section class="card empty-inline"><p>No case matches. '
                   f'<a href="{_e(url(token, "/"))}">Show all cases</a></p></section>')
    else:
        rows = []
        for v in views:
            demo = ' <span class="chip neutral plain">Demo</span>' if v.demo else ""
            sub = _e(v.business) if v.business and v.business != v.title else ""
            accounts = ", ".join(s["account"] for s in v.statements) or "—"
            href = _e(url(token, "/case", id=v.id))
            rows.append(
                f'<tr><td class="case-cell"><a class="case-link" href="{href}">{_e(v.title)}</a>{demo}'
                f'<span class="sub">{sub}</span></td>'
                f'<td>{_chip(v)}</td><td>{_progress(v)}</td><td>{_e(accounts)}</td>'
                f'<td class="when">{_e(_day(v.last_change))}</td>'
                f'<td class="go"><a href="{href}" aria-label="Open {_e(v.title)}">{icon("arrow")}</a></td></tr>')
        listing = ('<section class="table-wrap cases-table"><table><thead><tr><th>Case</th><th>Status</th>'
                   '<th>Progress</th><th>Accounts</th><th>Last change</th><th><span class="sr">Open</span></th>'
                   '</tr></thead><tbody>' + "".join(rows) + "</tbody></table></section>")
    return shell(token, TITLE, head + stat_row + listing, nav=nav, notice=notice, error=error)


# --- a new case -------------------------------------------------------------------------------


def new_page(token: str, nav: Nav, *, error: str = "", fields: dict[str, str] | None = None) -> bytes:
    f = fields or {}
    checked = " checked" if f.get("demo") == "yes" else ""
    inner = f"""
<div class="field"><label for="label">Name for the case</label>
<input id="label" name="label" maxlength="200" value="{_e(f.get('label'))}" placeholder="e.g. Harbourside Coffee, March 2027" autofocus>
<span class="hint">Your own label. You can change it later.</span></div>
<div class="field"><label for="business_name">Business name</label>
<input id="business_name" name="business_name" maxlength="200" value="{_e(f.get('business_name'))}" placeholder="As registered">
</div>
<label class="check option"><input type="checkbox" name="demo" value="yes"{checked}>
<span><strong>Demonstration or my own statements</strong><br><span class="hint">Not a real applicant. Every page and the signed memo say so.</span></span></label>
<div class="actions"><button class="btn primary big" type="submit">Make the case{icon("arrow")}</button>
<a class="btn ghost" href="{_e(url(token, '/'))}">Cancel</a></div>"""
    body = ('<header class="page-head"><div class="titles"><span class="crumbs">'
            f'<a href="{_e(url(token, "/"))}">Cases</a><span>/</span><span>New case</span></span>'
            '<h1>New case</h1><p class="subline">Start with a name. Details and statements come next.</p></div></header>'
            + _form(token, "/new", inner, cls="card narrow stack"))
    return shell(token, "New case", body, nav=nav, error=error)


# --- one case ---------------------------------------------------------------------------------


def _stepper(token: str, v: CaseView, step: str) -> str:
    states = step_states(v)
    items = []
    for i, (key, label, sub) in enumerate(STEPS, start=1):
        state = states[key]
        current = ' aria-current="step"' if key == step else ""
        mark = icon("check") if state == "done" else str(i)
        said = {"done": "Done", "next": "Next", "todo": sub}[state]
        items.append(f'<li class="{state}{" current" if key == step else ""}"><a href="'
                     f'{_e(url(token, "/case", id=v.id, step=key))}"{current}><span class="n">{mark}</span>'
                     f'<span class="t"><span class="l">{_e(label)}</span><span class="s">{_e(said)}</span></span></a></li>')
    return f'<ol class="stepper">{"".join(items)}</ol>'


def _money_compact(value: float) -> str:
    """An axis label: £38k, -£6k, £1.2m. Display only; no figure is computed from it."""
    sign = "-" if value < 0 else ""
    a = abs(value)
    if a >= 1_000_000:
        return f"{sign}£{a / 1_000_000:.1f}m"
    if a >= 1_000:
        return f"{sign}£{a / 1_000:.0f}k"
    return f"{sign}£{a:.0f}"


def monthly_chart(view: dict[str, Any]) -> str:
    """Trading receipts by month as bars, the average end-of-day balance as a line, on one
    pounds scale. Drawn from the summary's own monthly values; empty without them."""
    rows = [r for r in view.get("monthly", []) if r["trading_receipts"]["value"] is not None]
    if len(rows) < 2:
        return ""
    receipts = [float(r["trading_receipts"]["value"] or 0) for r in rows]
    balances = [r["average_balance"]["value"] for r in rows]
    values = receipts + [float(b) for b in balances if b is not None]
    top, bottom = max([0.0, *values]), min([0.0, *values])
    top = top * 1.08 or 1.0
    bottom = bottom * 1.08
    width, height, left, right, upper, lower = 720, 240, 56, 12, 14, 30
    plot_w, plot_h = width - left - right, height - upper - lower

    def y(v: float) -> float:
        return upper + (top - v) / (top - bottom) * plot_h

    step = plot_w / len(rows)
    grid = []
    for k in range(5):
        v = bottom + (top - bottom) * k / 4
        gy = y(v)
        grid.append(f'<line class="grid" x1="{left}" x2="{width - right}" y1="{gy:.1f}" y2="{gy:.1f}"/>'
                    f'<text class="axis" x="{left - 8}" y="{gy + 4:.1f}" text-anchor="end">{_e(_money_compact(v))}</text>')
    zero = f'<line class="zero" x1="{left}" x2="{width - right}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>'
    bars, labels, points = [], [], []
    for i, (r, v) in enumerate(zip(rows, receipts, strict=True)):
        x = left + i * step + step * 0.2
        top_y, base_y = y(max(v, 0)), y(0)
        bars.append(f'<rect class="bar" x="{x:.1f}" y="{top_y:.1f}" width="{step * 0.6:.1f}" '
                    f'height="{max(base_y - top_y, 0):.1f}" rx="3"><title>{_e(r["month"])}: trading receipts '
                    f'{_e(r["trading_receipts"]["display"])}</title></rect>')
        month = r["month"][5:7]
        name = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")[int(month) - 1] \
            if month.isdigit() and 1 <= int(month) <= 12 else r["month"]
        labels.append(f'<text class="axis" x="{left + i * step + step / 2:.1f}" y="{height - 10}" '
                      f'text-anchor="middle">{_e(name)}</text>')
        b = balances[i]
        if b is not None:
            points.append((left + i * step + step / 2, y(float(b)), r))
    line = ""
    if len(points) >= 2:
        path = " ".join(f"{'M' if i == 0 else 'L'}{px:.1f},{py:.1f}" for i, (px, py, _r) in enumerate(points))
        dots = "".join(f'<circle class="pt" cx="{px:.1f}" cy="{py:.1f}" r="3.5"><title>{_e(r["month"])}: average '
                       f'balance {_e(r["average_balance"]["display"])}</title></circle>' for px, py, r in points)
        line = f'<path class="line" d="{path}"/>{dots}'
    svg = (f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Trading receipts and average '
           f'balance by month">{"".join(grid)}{zero}{"".join(bars)}{line}{"".join(labels)}</svg>')
    legend = ('<span class="legend"><span class="key bar-key"></span>Trading receipts'
              '<span class="key line-key"></span>Average end-of-day balance</span>')
    return (f'<section class="card chart-card"><div class="card-head"><h2>Month by month</h2>{legend}</div>'
            f'{svg}</section>')


def _overview(summary: dict[str, Any] | None) -> str:
    """Key figures and the monthly chart, once the case has figures."""
    if summary is None:
        return ""
    from ..review import summary as review_summary

    if summary.get("state") == "refused":
        return f'<p class="banner bad">{icon("alert")}<span>No figures: {_e(summary.get("refusal"))}</span></p>'
    if summary.get("state") == "not_reconciled":
        return (f'<p class="banner bad">{icon("alert")}<span>The statement\'s figures do not add up, so none are '
                'shown. Open the review for the details.</span></p>')
    view = review_summary.headline_view(summary)
    if view is None:
        return ""
    return review_summary.headline_html(summary) + monthly_chart(view)


def _details(token: str, v: CaseView) -> str:
    p = v.profile
    stagger = p.get("vat_stagger")
    options = "".join(f'<option value="{n}"{" selected" if stagger == (n or None) else ""}>{label}</option>'
                      for n, label in (("", "Not known"), (1, "Group 1: quarters end Mar, Jun, Sep, Dec"),
                                       (2, "Group 2: quarters end Apr, Jul, Oct, Jan"),
                                       (3, "Group 3: quarters end May, Aug, Nov, Feb")))
    lines = lambda key: _e("\n".join(p.get(key) or []))  # noqa: E731
    inner = f"""
<input type="hidden" name="id" value="{_e(v.id)}">
<fieldset><legend>The business</legend><div class="form-grid">
<div class="field"><label for="d-label">Name for the case</label>
<input id="d-label" name="label" maxlength="200" value="{_e(p.get('label'))}"></div>
<div class="field"><label for="d-business">Business name</label>
<input id="d-business" name="business_name" maxlength="200" value="{_e(p.get('business_name'))}"></div>
<div class="field"><label for="d-directors">Directors</label>
<textarea id="d-directors" name="directors" rows="3" placeholder="One a line">{lines('directors')}</textarea>
<span class="hint">As they appear on statements. Money from them counts as director funding, not revenue.</span></div>
<div class="field"><label for="d-shareholders">Shareholders</label>
<textarea id="d-shareholders" name="shareholders" rows="3" placeholder="One a line">{lines('shareholders')}</textarea></div>
<div class="field wide"><label for="d-own">The business's other accounts</label>
<textarea id="d-own" name="own_accounts" rows="2" placeholder="One a line">{lines('own_accounts')}</textarea>
<span class="hint">The names its other accounts appear under. Money moved between them is not revenue.</span></div>
</div></fieldset>
<fieldset><legend>Tax and the loan</legend><div class="form-grid">
<div class="field"><label for="d-stagger">VAT stagger group</label>
<select id="d-stagger" name="vat_stagger">{options}</select>
<span class="hint">From the VAT certificate. Needed to spot late or missed VAT payments.</span></div>
<div class="field"><label for="d-repay">Proposed monthly repayment</label>
<span class="money"><span>£</span><input id="d-repay" name="proposed_monthly_repayment" inputmode="decimal" value="{_e(p.get('proposed_monthly_repayment'))}" placeholder="0.00"></span>
<span class="hint">Used for debt service cover including the new facility.</span></div>
</div></fieldset>
<div class="actions"><button class="btn primary" type="submit">Save details</button>
<a class="btn ghost" href="{_e(url(token, '/case', id=v.id, step='statements'))}">Next: statements{icon("arrow")}</a></div>"""
    return ('<section class="card"><div class="card-head"><h2>Details</h2><p>What the figures need to know about '
            'the business.</p></div>' + _form(token, "/details", inner, cls="stack") + "</section>")


def _statements(token: str, v: CaseView) -> str:
    have = ""
    if v.statements:
        rows = "".join(f'<tr><td><strong>{_e(s["account"])}</strong></td><td class="num">{_e(s["lines"])}</td>'
                       f'<td>{_e(s["start"])} to {_e(s["end"])}</td><td class="when">{_e(_when(s["at"]))}</td></tr>'
                       for s in v.statements)
        have = ('<section class="card"><div class="card-head"><h2>Imported</h2>'
                f'<p>{len(v.statements)} account{"s" if len(v.statements) != 1 else ""}</p></div>'
                '<div class="table-wrap"><table><thead><tr><th>Account</th><th class="num">Transactions</th>'
                f'<th>Period</th><th>Imported</th></tr></thead><tbody>{rows}</tbody></table></div></section>')
    banks = "".join(
        f'<label class="bank"><input type="radio" name="bank" value="{key}" required>'
        f'<span class="bank-name">{_e(label.split(" (")[0])}</span>'
        f'<span class="bank-kind">{_e(label.split(" (")[1].rstrip(")") if " (" in label else "")}</span></label>'
        for key, label, _fmt, _needs in BANKS)
    default_account = "current" if not v.statements else ""
    title = "Add another statement" if v.statements else "Import a statement"
    inner = f"""
<input type="hidden" name="id" value="{_e(v.id)}">
<label class="drop" for="s-file">{icon("upload")}<span><strong>Choose the statement file</strong><br>
<span class="hint">The CSV or JSON exactly as downloaded from the bank. Do not edit it.</span></span>
<input id="s-file" type="file" name="file" required accept=".csv,.json,.txt"></label>
<fieldset><legend>Which bank or kind of file?</legend><div class="banks">{banks}</div></fieldset>
<div class="form-grid">
<div class="field"><label for="s-account">Account name</label>
<input id="s-account" name="account" required maxlength="32" value="{default_account}" placeholder="current">
<span class="hint">Short, such as current, savings or reserve. The same name again replaces that account.</span></div>
<div class="field"><label for="s-opening">Opening balance</label>
<span class="money"><span>£</span><input id="s-opening" name="opening_balance" inputmode="decimal" placeholder="e.g. 3,800.50"></span>
<span class="hint"><strong>HSBC and Monzo:</strong> the balance just before the oldest transaction in the file
(the bottom line of an HSBC download), from the bank statement. Minus sign if overdrawn. Other banks: leave blank.</span></div>
</div>
<details class="more"><summary>Open Banking or TrueLayer file without balances</summary>
<div class="form-grid"><div class="field"><label for="s-sb">Balance from the bank statement</label>
<span class="money"><span>£</span><input id="s-sb" name="statement_balance" inputmode="decimal"></span></div>
<div class="field"><label for="s-at">At the end of</label><input id="s-at" name="at" type="date"></div></div>
</details>
<div class="actions"><button class="btn primary" type="submit">{icon("upload")}Import statement</button>"""
    if v.statements:
        inner += (f'<a class="btn ghost" href="{_e(url(token, "/case", id=v.id, step="review"))}">'
                  f'Next: review{icon("arrow")}</a>')
    inner += "</div>"
    return have + (f'<section class="card"><div class="card-head"><h2>{title}</h2><p>Nothing is uploaded '
                   'anywhere: the file is read on this computer.</p></div>'
                   + _form(token, "/import", inner, upload=True, cls="stack") + "</section>")


def _review(token: str, v: CaseView, summary: dict[str, Any] | None) -> str:
    if not v.statements:
        return ('<section class="card tint"><h2>Review and sign</h2><p class="muted">Import a statement first; the '
                f'review needs figures.</p><div class="actions"><a class="btn" href="'
                f'{_e(url(token, "/case", id=v.id, step="statements"))}">Go to statements</a></div></section>')
    changed = ""
    if v.status == CHANGED:
        changed = (f'<p class="banner warn">{icon("alert")}<span>The case changed after it was last signed. Review '
                   'it and sign again for a memo that matches.</span></p>')
    queue = len((summary or {}).get("queue") or [])
    stale = len((summary or {}).get("stale") or [])
    todo = []
    if stale:
        todo.append(f'<li class="todo-item warn"><strong>{stale}</strong> correction{"s" if stale != 1 else ""} to '
                    'confirm or drop, because the statement changed after they were made</li>')
    todo.append(f'<li class="todo-item"><strong>{queue}</strong> line{"s" if queue != 1 else ""} to decide: flagged, '
                'unexplained or matched by more than one rule</li>' if queue else
                '<li class="todo-item ok">No lines waiting for a decision</li>')
    inner = (f'<input type="hidden" name="id" value="{_e(v.id)}">'
             f'<button class="btn primary big" type="submit">Open the review{icon("arrow")}</button>')
    policy = ""
    if summary and summary.get("state") == "shown":
        from ..review import summary as review_summary

        policy = ('<section class="card policy-card">' + review_summary._policy_checks_html(summary)
                  + "</section>")
    return (f'{changed}<section class="card review-cta"><div class="card-head"><h2>Review and sign</h2>'
            '<p>Signing confirms the figures were checked. It does not approve the loan.</p></div>'
            f'<ul class="todo">{"".join(todo)}</ul>'
            '<ol class="how"><li>Check each figure against the lines behind it</li>'
            '<li>Put any line in the wrong category right, with a reason</li>'
            '<li>Build the memo and sign off the figures</li></ol>'
            + _form(token, "/review", inner) + f"</section>{policy}")


def _memo(token: str, v: CaseView) -> str:
    if not v.packs:
        return ('<section class="card tint"><h2>Signed memo</h2><p class="muted">Nothing signed yet. The memo appears '
                f'here once you sign it in the review.</p><div class="actions"><a class="btn" href="'
                f'{_e(url(token, "/case", id=v.id, step="review"))}">Go to review</a></div></section>')
    cards = []
    for i, p in enumerate(v.packs):
        latest = '<span class="chip ok">Latest</span>' if i == 0 and v.status == SIGNED else ""
        older = '<span class="chip neutral plain">Earlier</span>' if i > 0 else ""
        cards.append(f'<a class="memo-card" href="{_e(url(token, "/pack", id=v.id, snapshot=p["snapshot"]))}">'
                     f'<span class="memo-icon">{icon("doc")}</span><span class="memo-text"><strong>Memo signed by '
                     f'{_e(p["name"])}</strong><span class="muted">{_e(_when(p["at"]))}</span></span>'
                     f'{latest}{older}<span class="go">{icon("arrow")}</span></a>')
    return (f'<section class="card"><div class="card-head"><h2>Signed memo</h2><p>Each sign-off, newest first.</p>'
            f'</div><div class="memo-list">{"".join(cards)}</div></section>')


def case_page(token: str, v: CaseView, nav: Nav, step: str, summary: dict[str, Any] | None, *,
              notice: str = "", error: str = "") -> bytes:
    demo = ' <span class="chip neutral plain">Demo</span>' if v.demo else ""
    crumbs = f'<span class="crumbs"><a href="{_e(url(token, "/"))}">Cases</a><span>/</span><span>{_e(v.title)}</span></span>'
    if v.status in (ATTENTION, BUSY):
        body = (f'<header class="page-head"><div class="titles">{crumbs}<h1>{_e(v.title)}</h1>'
                f'<p class="subline">{_chip(v)}</p></div></header>'
                f'<p class="banner bad">{icon("alert")}<span>{_e(v.problem)}</span></p>')
        return shell(token, v.title, body, nav=nav, notice=notice, error=error)
    sub = [p for p in (_e(v.business) if v.business and v.business != v.title else "",
                       f'{len(v.statements)} account{"s" if len(v.statements) != 1 else ""}' if v.statements else "",
                       f"Opened {_e(_day(v.created))}" if v.created else "") if p]
    head = (f'<header class="page-head"><div class="titles">{crumbs}<h1>{_e(v.title)}</h1>'
            f'<p class="subline">{_chip(v)}{demo}{"".join(f"<span>{s}</span>" for s in sub)}</p></div></header>')
    banner = (f'<p class="banner warn">{icon("info")}<span>Demonstration case: every page and the signed memo say '
              'it is not an applicant file.</span></p>' if v.demo else "")
    content = {"details": lambda: _details(token, v), "statements": lambda: _statements(token, v),
               "review": lambda: _review(token, v, summary), "memo": lambda: _memo(token, v)}[step]()
    body = (head + banner + _overview(summary) + _stepper(token, v, step) + content
            + f'<p class="foot muted">Case {_e(v.id)}</p>')
    return shell(token, v.title, body, nav=nav, notice=notice, error=error)


# --- a signed pack ----------------------------------------------------------------------------


def memo_html(text: str) -> str:
    """The pack's memo.md as a page: headings, quoted notes, lists and paragraphs. Only the
    few forms pack.py writes are read; everything else is shown as plain text, escaped."""
    out: list[str] = []
    para: list[str] = []
    items: list[str] = []

    def flush() -> None:
        if para:
            out.append(f"<p>{_e(' '.join(para))}</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{_e(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
        elif stripped.startswith("## "):
            flush()
            out.append(f"<h3>{_e(stripped[3:])}</h3>")
        elif stripped.startswith("# "):
            flush()  # the memo's own title; the page has one
        elif stripped.startswith("> "):
            flush()
            out.append(f'<p class="memo-note">{_e(stripped[2:])}</p>')
        elif stripped.startswith("- "):
            if para:
                flush()
            items.append(stripped[2:])
        elif stripped.startswith("_") and stripped.endswith("_") and len(stripped) > 2:
            flush()
            out.append(f'<p class="memo-empty">{_e(stripped[1:-1])}</p>')
        else:
            if items:
                flush()
            para.append(stripped)
    flush()
    return '<div class="memo">' + "".join(out) + "</div>"


def pack_page(token: str, v: CaseView, nav: Nav, pack: dict, memo: str, verified: tuple[bool, str],
              folder: str) -> bytes:
    ok, said = verified
    badge = (f'<p class="banner ok">{icon("shield")}<span><strong>Verified.</strong> {_e(said)}</span></p>' if ok
             else f'<p class="banner bad">{icon("alert")}<span><strong>Not verified.</strong> {_e(said)}</span></p>')
    record = url(token, "/pack/record", id=v.id, snapshot=pack["snapshot"])
    body = (f'<header class="page-head"><div class="titles"><span class="crumbs"><a href="{_e(url(token, "/"))}">Cases'
            f'</a><span>/</span><a href="{_e(url(token, "/case", id=v.id, step="memo"))}">{_e(v.title)}</a>'
            '<span>/</span><span>Signed memo</span></span><h1>Signed memo</h1>'
            f'<p class="subline"><span>Signed off by <strong>{_e(pack["name"])}</strong></span>'
            f'<span>{_e(_when(pack["at"]))}</span></p></div>'
            f'<div class="actions"><a class="btn primary" href="{_e(record)}" target="_blank" rel="noopener">'
            f'{icon("doc")}Open the full record</a></div></header>{badge}'
            f'<article class="card paper">{memo_html(memo)}</article>'
            f'<p class="foot muted">The signed files (memo, full record, evidence and monthly table) are on this '
            f'computer in {_e(folder)}</p>')
    return shell(token, "Signed memo", body, nav=nav)


def message_page(token: str, title: str, text: str, nav: Nav | None = None) -> bytes:
    body = (f'<section class="card narrow"><h1>{_e(title)}</h1><p>{_e(text)}</p>'
            f'<div class="actions"><a class="btn" href="{_e(url(token, "/"))}">All cases</a></div></section>')
    return shell(token, title, body, nav=nav)
