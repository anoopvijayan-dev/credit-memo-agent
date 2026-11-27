"""The case app's pages, as HTML. Every value from a case is escaped; every link and form
carries the app's token. No inline script or style: the page's look is app.css."""

from __future__ import annotations

from html import escape
from urllib.parse import urlencode

from .actions import BANKS
from .cases import ATTENTION, BUSY, CHANGED, SIGNED, CaseView

TITLE = "Credit memo cases"


def _e(value: object) -> str:
    return escape("" if value is None else str(value), quote=True)


def url(token: str, path: str, **params: str) -> str:
    return f"{path}?{urlencode({**params, 'token': token})}"


def _when(stamp: str) -> str:
    """A log time (2026-11-27T12:43:37.708816Z) as a date and minute, UTC."""
    return f"{stamp[:10]} {stamp[11:16]} UTC" if len(stamp) >= 16 else stamp


def shell(token: str, title: str, body: str, *, notice: str = "", error: str = "") -> bytes:
    flash = ""
    if notice:
        flash += f'<p class="flash ok" role="status">{_e(notice)}</p>'
    if error:
        flash += f'<p class="flash error" role="alert">{_e(error)}</p>'
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<link rel="stylesheet" href="{_e(url(token, '/static/app.css'))}">
</head>
<body>
<header class="top"><a class="brand" href="{_e(url(token, '/'))}">Credit memo</a>
<span class="local">Runs on this computer only. Statements never leave it.</span></header>
<main>
{flash}
{body}
</main>
</body>
</html>
"""
    return page.encode("utf-8")


def _pill(view: CaseView) -> str:
    return f'<span class="pill {view.status}">{_e(view.status_text)}</span>'


def _form(token: str, path: str, inner: str, *, upload: bool = False, cls: str = "") -> str:
    enctype = ' enctype="multipart/form-data"' if upload else ""
    klass = f' class="{cls}"' if cls else ""
    return f'<form method="post" action="{_e(url(token, path))}"{enctype}{klass}>{inner}</form>'


# --- the list of cases ------------------------------------------------------------------------


def cases_page(token: str, views: list[CaseView], *, notice: str = "", error: str = "") -> bytes:
    new = f'<a class="button primary" href="{_e(url(token, "/new"))}">New case</a>'
    if not views:
        body = (f'<div class="head"><h1>Cases</h1>{new}</div>'
                '<section class="empty"><h2>No cases yet</h2><p>A case holds one business\'s bank statements, '
                'the figures worked out from them, your review and the signed memo. Start with '
                '<strong>New case</strong>.</p></section>')
        return shell(token, TITLE, body, notice=notice, error=error)
    rows = []
    for v in views:
        accounts = ", ".join(s["account"] for s in v.statements) or "none yet"
        demo = ' <span class="tag">demo</span>' if v.demo else ""
        rows.append(
            f'<tr><td><a href="{_e(url(token, "/case", id=v.id))}">{_e(v.title)}</a>{demo}'
            f'<div class="sub">{_e(v.business) if v.business and v.business != v.title else ""}</div></td>'
            f'<td>{_pill(v)}</td><td>{_e(accounts)}</td><td class="when">{_e(_when(v.last_change))}</td></tr>')
    body = (f'<div class="head"><h1>Cases</h1>{new}</div>'
            '<div class="table-wrap"><table class="cases"><thead><tr><th>Case</th><th>Status</th>'
            '<th>Accounts</th><th>Last change</th></tr></thead><tbody>' + "".join(rows)
            + "</tbody></table></div>")
    return shell(token, TITLE, body, notice=notice, error=error)


# --- a new case -------------------------------------------------------------------------------


def new_page(token: str, *, error: str = "", fields: dict[str, str] | None = None) -> bytes:
    f = fields or {}
    checked = " checked" if f.get("demo") == "yes" else ""
    inner = f"""
<label for="label">Name for the case</label>
<input id="label" name="label" maxlength="200" value="{_e(f.get('label'))}" placeholder="e.g. Harbourside Coffee, March 2027">
<label for="business_name">Business name</label>
<input id="business_name" name="business_name" maxlength="200" value="{_e(f.get('business_name'))}">
<label class="check"><input type="checkbox" name="demo" value="yes"{checked}>
This is a demonstration or my own statements, not a real applicant</label>
<p class="hint">A demonstration case is marked as one on every page and on the signed memo.</p>
<div class="actions"><button class="button primary" type="submit">Make the case</button>
<a class="button" href="{_e(url(token, '/'))}">Cancel</a></div>"""
    body = '<h1>New case</h1>' + _form(token, "/new", inner, cls="card form")
    return shell(token, "New case", body, error=error)


# --- one case ---------------------------------------------------------------------------------

STEPS = [("details", "Details"), ("statements", "Statements"), ("review", "Review and sign"), ("packs", "Signed memo")]


def _step_states(v: CaseView) -> dict[str, str]:
    has_details = bool(v.business or v.profile.get("directors"))
    has_statements = bool(v.statements)
    signed = v.status == SIGNED
    return {
        "details": "done" if has_details else "todo",
        "statements": "done" if has_statements else "now",
        "review": "done" if signed else ("now" if has_statements else "todo"),
        "packs": "done" if v.packs else "todo",
    }


def _steps(v: CaseView) -> str:
    states = _step_states(v)
    items = "".join(f'<li class="{states[key]}"><a href="#{key}"><span class="n">{i}</span>{_e(name)}</a></li>'
                    for i, (key, name) in enumerate(STEPS, start=1))
    return f'<ol class="steps">{items}</ol>'


def _details(token: str, v: CaseView) -> str:
    p = v.profile
    stagger = p.get("vat_stagger")
    options = "".join(f'<option value="{n}"{" selected" if stagger == (n or None) else ""}>{label}</option>'
                      for n, label in (("", "Not known"), (1, "1 (quarters end Mar, Jun, Sep, Dec)"),
                                       (2, "2 (quarters end Apr, Jul, Oct, Jan)"),
                                       (3, "3 (quarters end May, Aug, Nov, Feb)")))
    lines = lambda key: _e("\n".join(p.get(key) or []))  # noqa: E731
    inner = f"""
<div class="grid">
<div><label for="d-label">Name for the case</label>
<input id="d-label" name="label" maxlength="200" value="{_e(p.get('label'))}"></div>
<div><label for="d-business">Business name</label>
<input id="d-business" name="business_name" maxlength="200" value="{_e(p.get('business_name'))}"></div>
<div><label for="d-directors">Directors</label>
<textarea id="d-directors" name="directors" rows="3">{lines('directors')}</textarea>
<p class="hint">One a line, as they appear on statements. Money from them counts as director funding.</p></div>
<div><label for="d-shareholders">Shareholders</label>
<textarea id="d-shareholders" name="shareholders" rows="3">{lines('shareholders')}</textarea>
<p class="hint">One a line.</p></div>
<div><label for="d-own">The business's other accounts</label>
<textarea id="d-own" name="own_accounts" rows="3">{lines('own_accounts')}</textarea>
<p class="hint">Names its other accounts appear under, one a line. Moves between them are not revenue.</p></div>
<div><label for="d-stagger">VAT stagger group</label>
<select id="d-stagger" name="vat_stagger">{options}</select>
<p class="hint">From the VAT certificate. Needed to spot late or missed VAT payments.</p>
<label for="d-repay">Proposed monthly repayment (£)</label>
<input id="d-repay" name="proposed_monthly_repayment" inputmode="decimal" value="{_e(p.get('proposed_monthly_repayment'))}">
<p class="hint">For debt service cover including the new facility.</p></div>
</div>
<div class="actions"><button class="button primary" type="submit">Save details</button></div>"""
    return ('<section id="details" class="card"><h2>1. Details</h2>'
            + _form(token, "/details", f'<input type="hidden" name="id" value="{_e(v.id)}">' + inner) + "</section>")


def _statements(token: str, v: CaseView) -> str:
    if v.statements:
        rows = "".join(f'<tr><td>{_e(s["account"])}</td><td class="num">{_e(s["lines"])}</td>'
                       f'<td>{_e(s["start"])} to {_e(s["end"])}</td><td class="when">{_e(_when(s["at"]))}</td></tr>'
                       for s in v.statements)
        have = ('<div class="table-wrap"><table><thead><tr><th>Account</th><th class="num">Transactions</th>'
                f'<th>Period</th><th>Imported</th></tr></thead><tbody>{rows}</tbody></table></div>'
                '<h3>Add another statement</h3>')
    else:
        have = '<p>No statements yet. Import the business account\'s download from the bank.</p>'
    banks = "".join(f'<option value="{key}">{_e(label)}</option>' for key, label, _fmt, _needs in BANKS)
    default_account = "current" if not v.statements else ""
    inner = f"""
<input type="hidden" name="id" value="{_e(v.id)}">
<div class="grid">
<div><label for="s-file">Statement file</label>
<input id="s-file" type="file" name="file" required accept=".csv,.json,.txt">
<p class="hint">The file as downloaded from the bank. Do not edit it.</p></div>
<div><label for="s-bank">Bank or kind of file</label>
<select id="s-bank" name="bank" required><option value="">Choose…</option>{banks}</select></div>
<div><label for="s-account">Account name</label>
<input id="s-account" name="account" required maxlength="32" value="{default_account}" placeholder="current">
<p class="hint">A short name such as current, savings or reserve. Importing again under the same name replaces it.</p></div>
<div><label for="s-opening">Opening balance (£)</label>
<input id="s-opening" name="opening_balance" inputmode="decimal" placeholder="e.g. 3,800.50">
<p class="hint">HSBC and Monzo files: the balance just before the oldest transaction in the file
(the bottom line of an HSBC download), from the bank statement. Put a minus sign if overdrawn.
Leave blank for other banks.</p></div>
</div>
<details class="more"><summary>Open Banking or TrueLayer file without balances</summary>
<div class="grid"><div><label for="s-sb">Balance from the bank statement (£)</label>
<input id="s-sb" name="statement_balance" inputmode="decimal"></div>
<div><label for="s-at">at the end of (date)</label><input id="s-at" name="at" type="date"></div></div>
</details>
<div class="actions"><button class="button primary" type="submit">Import statement</button></div>"""
    return (f'<section id="statements" class="card"><h2>2. Statements</h2>{have}'
            + _form(token, "/import", inner, upload=True) + "</section>")


def _review(token: str, v: CaseView) -> str:
    if not v.statements:
        return ('<section id="review" class="card muted"><h2>3. Review and sign</h2>'
                '<p>Import a statement first.</p></section>')
    changed = ""
    if v.status == CHANGED:
        changed = ('<p class="flash warn">The case changed after it was last signed. Review it and sign '
                   'again for a memo that matches.</p>')
    inner = (f'<input type="hidden" name="id" value="{_e(v.id)}">'
             '<button class="button primary" type="submit">Open the review</button>')
    return (f'<section id="review" class="card"><h2>3. Review and sign</h2>{changed}'
            '<p>The review shows every figure, the lines behind it and the policy check. Correct any line '
            'in the wrong category there (with a reason), then build the memo and sign off the figures. '
            'Signing confirms the figures were checked; it does not approve the loan.</p>'
            + _form(token, "/review", inner) + "</section>")


def _packs(token: str, v: CaseView) -> str:
    if not v.packs:
        return ('<section id="packs" class="card muted"><h2>4. Signed memo</h2>'
                '<p>Nothing signed yet.</p></section>')
    rows = "".join(
        f'<li><a href="{_e(url(token, "/pack", id=v.id, snapshot=p["snapshot"]))}">Memo signed by '
        f'{_e(p["name"])}</a> <span class="when">{_e(_when(p["at"]))}</span></li>' for p in v.packs)
    return f'<section id="packs" class="card"><h2>4. Signed memo</h2><ul class="packs">{rows}</ul></section>'


def case_page(token: str, v: CaseView, *, notice: str = "", error: str = "") -> bytes:
    demo = ('<p class="flash warn">Demonstration case: every page and the signed memo say it is not an '
            'applicant file.</p>' if v.demo else "")
    if v.status in (ATTENTION, BUSY):
        body = (f'<p class="crumb"><a href="{_e(url(token, "/"))}">All cases</a></p>'
                f'<div class="head"><h1>{_e(v.title)}</h1>{_pill(v)}</div><p class="flash error">{_e(v.problem)}</p>')
        return shell(token, v.title, body, notice=notice, error=error)
    body = (f'<p class="crumb"><a href="{_e(url(token, "/"))}">All cases</a></p>'
            f'<div class="head"><h1>{_e(v.title)}</h1>{_pill(v)}</div>{demo}{_steps(v)}'
            + _details(token, v) + _statements(token, v) + _review(token, v) + _packs(token, v)
            + f'<p class="foot">Case {_e(v.id)}</p>')
    return shell(token, v.title, body, notice=notice, error=error)


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



def pack_page(token: str, v: CaseView, pack: dict, memo: str, verified: tuple[bool, str], folder: str) -> bytes:
    ok, said = verified
    badge = (f'<p class="flash ok"><strong>Verified.</strong> {_e(said)}</p>' if ok
             else f'<p class="flash error"><strong>Not verified.</strong> {_e(said)}</p>')
    record = url(token, "/pack/record", id=v.id, snapshot=pack["snapshot"])
    body = (f'<p class="crumb"><a href="{_e(url(token, "/"))}">All cases</a> / '
            f'<a href="{_e(url(token, "/case", id=v.id))}">{_e(v.title)}</a></p>'
            f'<div class="head"><h1>Signed memo</h1></div>'
            f'<p>Signed off by <strong>{_e(pack["name"])}</strong> at {_e(_when(pack["at"]))}.</p>{badge}'
            f'<p><a class="button primary" href="{_e(record)}" target="_blank" rel="noopener">Open the full record</a> '
            '<span class="hint">Every figure with the lines behind it, for the credit file.</span></p>'
            f'<section class="card"><h2>Memo</h2>{memo_html(memo)}</section>'
            f'<p class="hint">The signed files are on this computer in {_e(folder)}</p>')
    return shell(token, "Signed memo", body)


def message_page(token: str, title: str, text: str) -> bytes:
    body = f'<h1>{_e(title)}</h1><p>{_e(text)}</p><p><a href="{_e(url(token, "/"))}">All cases</a></p>'
    return shell(token, title, body)

