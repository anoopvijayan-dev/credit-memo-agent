"""The workspace's case summary (C5; R3, R34, P8).

case_summary() loads the case's statements and computes every figure through the figures
layer, with the profile and the whole override log (overrides.case_figures); it does no
arithmetic on money itself. What it hands on is plain data, which /api/case sends as JSON
and render() turns into the page's HTML:

- for each account, the money-in breakdown (trading receipts M2 with the cash inside it M18,
  then M19 to M25, adding up to total money in M17), every figure, and the monthly summary,
  money to the penny as every table shows it (R2);
- for a case of more than one account, the same across all its accounts ("All accounts",
  overrides.combined_figures): line ids prefixed with the account key, flow figures across
  every account, and balance figures on the combined end-of-day balance over the days every
  account covers, n/a with the reason when there are none (R41);
- the judgement queue, listed first: every line a person has to decide, across the case's
  accounts, largest amount first (R15), each with why it is listed, the indicators that
  flagged it and, for an ambiguous credit, its candidates and the bucket each would give;
- for each account, the indicators panel: each indicator with its lines and the thresholds it
  fired at, a threshold still at its shipped value labelled illustrative (R14). Indicators
  are raised by the figures layer (indicators.py) from the firm's thresholds file, the case
  root's indicators.yaml or the path firm.yaml gives, when there is one, and the packaged,
  illustrative file otherwise;
- while show_policy_verdict is on in firm.yaml (the default), for each account the policy
  check: the overall pass, refer or fail outcome and each rule's result from the packaged
  policy, by policy.evaluate on the account's current figures, with the illustrative label in
  the same panel (R25). With it off, no outcome and no rule result, in the HTML or the data
  (R56). The recommendation is a field left blank for the underwriter either way (R24);
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
from .. import firm, metrics, overrides, policy, store
from .. import indicators as ind
from ..categorise import RulesetError
from ..ingest import StatementError
from ..models import (
    Category,
    Indicator,
    Metric,
    MonthlyCell,
    QueueEntry,
    Transaction,
    format_value,
    money_exact,
)

BREAKDOWN = ("M2", "M19", "M20", "M21", "M22", "M23", "M24", "M25")  # the money-in buckets (R1)
TOTAL, CASH = "M17", "M18"
# The profile fields the figures read (R5, R6, R13), shown with them so the underwriter sees
# what made a credit director funding or an own-account transfer, and what M28 includes.
PROFILE_SHOWN = ("directors", "shareholders", "own_accounts", "proposed_monthly_repayment")
PROFILE_TITLES = {"directors": "Directors", "shareholders": "Shareholders", "own_accounts": "Own accounts"}
CELLS = (("money_in", "Money in"), ("money_out", "Money out"), ("trading_receipts", "Trading receipts"),
         ("average_balance", "Average end-of-day balance"), ("lowest_balance", "Lowest end-of-day balance"),
         ("days_overdrawn", "Days overdrawn"))
INDICATOR_TITLES = {
    "large_credit": "Single credit large against typical trading receipts",
    "single_cash_deposit": "Cash deposit at or above the single-deposit limit",
    "cash_heavy_month": "Month with cash deposits above a share of its trading receipts",
    "cash_round_trip": "Cash paid in soon after a similar cash withdrawal",
    "gambling": "Payment to a gambling operator",
    "returned_dd": "Returned direct debit",
    "overdrawn_run": "Run of consecutive overdrawn days",
    "irregular_acquirer": "Irregular credit from a named acquirer",
}
REASON_TITLES = {"unexplained": "unexplained", "ambiguous": "ambiguous",
                 "possible_own_account_transfer": "possible own-account transfer", "indicator": "flagged"}
UNIT_SUFFIX = {"pct": "%", "times": " times", "days": " days", "count": ""}
COMBINED_BASIS = ("Across every account of the case: each line's id is its account key, a colon and its own id. "
                  "Money in and out and the figures built on them count every account's lines (own-account "
                  "transfers stay out of trading receipts); the balance figures read the combined end-of-day "
                  "balance of the accounts over the days every account covers.")
ILLUSTRATIVE = ("Thresholds marked illustrative are still at the values shipped with this tool; they are "
                "not your firm's lending policy.")


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


def _line(t: Transaction) -> dict[str, Any]:
    return {"id": t.id, "date": t.date.isoformat(), "description": t.description, "amount": money_exact(t.amount),
            "category": t.category.value if t.category else None, "rule": t.rule_id}


def _threshold_shown(value: str, unit: str) -> str:
    """A threshold as the thresholds file gives it, money to the penny (R2)."""
    if unit == "GBP":  # value is pounds_text: exact pounds and pence, formatted, never through a float
        return f"£{Decimal(value):,.2f}"
    return f"{value}{UNIT_SUFFIX.get(unit, '')}"


def _indicator(i: Indicator, lines: dict[str, Transaction]) -> dict[str, Any]:
    return {"id": i.id, "kind": i.kind, "title": INDICATOR_TITLES.get(i.kind, i.kind), "reason": i.reason,
            "illustrative": i.illustrative,
            "thresholds": [{"key": th.key, "value": th.value, "unit": th.unit,
                            "shown": _threshold_shown(th.value, th.unit), "illustrative": th.illustrative}
                           for th in i.thresholds],
            "lines": [_line(lines[x]) for x in i.evidence]}


def _queue_entry(account: str, e: QueueEntry, lines: dict[str, Transaction],
                 kinds: dict[str, str]) -> dict[str, Any]:
    from .edit import categories_for

    t = lines[e.line_id]
    return {"account": account, "line": {**_line(t), "categories": categories_for(t)}, "reasons": list(e.reasons),
            "indicators": [{"id": i, "kind": kinds[i]} for i in e.indicators],
            "candidates": [{"rule_id": c.rule_id, "category": c.category.value if c.category else None,
                            "bucket": c.bucket.value if c.bucket else None} for c in e.candidates],
            "pairs_with": list(e.pairs_with)}


def case_thresholds(config: firm.FirmConfig | None = None) -> ind.Thresholds:
    """The firm's thresholds file when the case root has one (firm.yaml can name another),
    or the packaged, illustrative one (R14). A malformed file is refused naming its line, and
    a file firm.yaml names that is not there is refused naming it, as `cma run --thresholds`
    refuses it: no case opens with flags the firm did not choose."""
    config = config or firm.load()
    path = config.thresholds_path
    if config.names_path("thresholds") or path.exists():
        return ind.load_thresholds(path)
    return ind.shipped()


def _policy_check(f: overrides.CaseFigures, rules: dict | None) -> dict[str, Any] | None:
    """The packaged policy's outcome on the account's current figures (R25), or None when the
    firm hides it (R56). policy.evaluate compares; nothing here does."""
    if rules is None:
        return None
    result = policy.evaluate(f.metrics, rules)
    return {"policy_name": result.policy_name, "policy_sha256": result.policy_sha256,
            "overall": result.overall.value,
            "rules": [{"rule_id": r.rule_id, "description": r.description, "metric_id": r.metric_id,
                       "outcome": r.outcome.value, "detail": r.detail} for r in result.rules]}


def _account(f: overrides.CaseFigures, raised: list[Indicator], rules: dict | None = None) -> dict[str, Any]:
    by_id = {m.id: m for m in f.metrics}
    txns = f.statement.transactions
    lines = {t.id: t for t in txns}
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
        "indicators": [_indicator(i, lines) for i in raised],
        **({"policy_check": _policy_check(f, rules)} if rules is not None else {}),
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
    if isinstance(e, (store.CaseStoreError, StatementError, metrics.AmountError, RulesetError,
                      ind.ThresholdsError, firm.FirmConfigError, policy.PolicyError)):
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


def log_position(folder: Path) -> int | None:
    """How many entries the case log holds, or None when it cannot be read whole now; a page
    with no position offers no form."""
    try:
        return len(caselog.for_case(folder).entries())
    except (caselog.CaseLogError, OSError):
        return None


def stale_list(folder: Path, figures: dict[str, overrides.CaseFigures] | None) -> list[dict[str, Any]]:
    """The stale overrides (R19), each saying whether it can be confirmed: its line is still in
    the case and its category still valid for the line's direction."""
    try:
        stale = overrides.stale_overrides(folder)
    except Exception:  # the override log could not be read: the figures' refusal says why
        return []
    out = []
    for s in stale:
        t = figures[s.account].statement.by_id().get(s.line) if figures and s.account in figures else None
        ok = s.why == "statement changed" and (t is None or Category(s.new) in overrides.valid_categories(t))
        out.append({"seq": s.seq, "account": s.account, "line": s.line, "new": s.new, "why": s.why,
                    "can_confirm": ok})
    return out


def case_summary(folder: Path, case_id: str) -> dict[str, Any]:
    """The case as the workspace shows it, computed afresh from its files."""
    folder = Path(folder)
    out: dict[str, Any] = {"case": case_id, "label": None, "business_name": None, "profile": None,
                           "state": "shown", "accounts": [], "queue": [], "thresholds": None,
                           "reconciliation": None, "refusal": None, "position": None, "stale": [],
                           "policy_check": {"shown": False}, "recommendation": None,  # left blank (R24)
                           "demo": False, "combined": None}
    # The case log position the page is built at, read before anything else, so a save made
    # from this page is refused as a conflict once anything was recorded after it (R17).
    out["position"] = log_position(folder)
    try:
        profile = store.load_profile(folder)
        out["label"], out["business_name"] = profile.get("label"), profile.get("business_name")
        out["demo"] = bool(profile.get("demo"))  # R48: the banner says what the case is
        out["profile"] = {key: (list(profile[key]) if key in store.LIST_FIELDS else profile[key])
                          for key in PROFILE_SHOWN}
    except Exception as e:
        refused = refusal(e)
        if refused is None:
            raise
        out.update(refused)
        return out
    figures, refused = load_figures(folder)
    out["stale"] = stale_list(folder, figures)
    if refused is not None:
        out.update(refused)
        return out
    try:  # R14: the indicators and the queue, raised by the figures layer; R25: the policy check
        config = firm.load()
        thresholds = case_thresholds(config)
        raised = {a: ind.raise_indicators(f.statement, thresholds) for a, f in figures.items()}
        rules = policy.load_policy() if config.show_policy_verdict else None  # the packaged policy
        accounts = [_account(f, raised[a], rules) for a, f in figures.items()]
    except Exception as e:
        refused = refusal(e)
        if refused is None:
            raise
        out.update(refused)
        return out
    out["thresholds"] = {"source": thresholds.source, "sha256": thresholds.sha256,
                         "shipped": thresholds.sha256 == ind.shipped().sha256}
    out["accounts"] = accounts
    try:  # R41: a case of more than one account, across all its accounts
        both = overrides.combined_figures(figures)
    except Exception as e:
        refused = refusal(e)
        if refused is None:
            raise
        out.update(refused)
        return out
    out["combined"] = None if both is None else {**_account(both, []), "basis": COMBINED_BASIS}
    if rules is not None and accounts:
        out["policy_check"] = {"shown": True, "label": policy.ILLUSTRATIVE_LABEL}
    queues = [(a, f.statement, ind.judgement_queue(f.statement, raised[a])) for a, f in figures.items()]
    lines = {a: {t.id: t for t in f.statement.transactions} for a, f in figures.items()}
    kinds = {a: {i.id: i.kind for i in raised[a]} for a in figures}
    out["queue"] = [_queue_entry(a, e, lines[a], kinds[a]) for a, e in ind.case_judgement_queue(queues)]
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


def _account_html(a: dict[str, Any], token: str | None = None,
                  thresholds: dict[str, Any] | None = None, href=None) -> str:
    """One account's money in, figures, monthly summary and indicators. Each figure, bucket and
    cell links to its lines: through the workspace with the page's token, or through `href`
    (account, **target) -> an escaped link, as the pack's record links within itself (R26)."""
    acct = a["account"]
    if href is None:
        def href(account, **target):
            return _href(token, account, **target)
    period = f'{_e(a["period"]["start"])} to {_e(a["period"]["end"])}, ' if a["period"] else ""
    head = '<thead><tr><th>Id</th><th>Figure</th><th class="num">Value</th><th class="num">Lines</th></tr></thead>'
    mi = a["money_in"]
    rows = []
    for b in mi["buckets"]:
        rows.append(_figure_row(b, href=href(acct, figure=b["id"])))
        if b["id"] == "M2":
            cash = mi["cash_within_trading"]
            rows.append(_figure_row(cash, "within", href(acct, figure=cash["id"])))
    total = mi["total"]
    breakdown = (f'<table class="breakdown">{head}<tbody>{"".join(rows)}</tbody>'
                 f'<tfoot>{_figure_row(total, "total", href(acct, figure=total["id"]))}</tfoot></table>')
    figures = ('<table class="figures"><thead><tr><th>Id</th><th>Figure</th><th class="num">Value</th>'
               '<th>Method</th><th class="num">Lines</th></tr></thead><tbody>'
               + "".join(f'<tr><td>{_linked(f["id"], href(acct, figure=f["id"]))}</td><td>{_e(f["name"])}'
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
            shown = _linked(c["display"], href(acct, month=r["month"], cell=key))
            cells.append(f'<td class="num">{shown}{why}{mark}</td>')
        part = ' <span class="part">part month</span>' if r["part_month"] else ""
        month_rows.append(f'<tr><td>{_e(r["month"])}{part}</td>{"".join(cells)}</tr>')
    monthly = ('<table class="monthly"><thead><tr><th>Month</th>'
               + "".join(f'<th class="num">{_e(title)}</th>' for _, title in CELLS)
               + f'</tr></thead><tbody>{"".join(month_rows)}</tbody></table>')
    if a.get("basis"):  # the case's figures across all its accounts (R41)
        return (f'<section class="account combined" id="combined"><h2>All accounts</h2>'
                f'<p class="period">{period}{_e(a["line_count"])} lines</p><p class="note">{_e(a["basis"])}</p>'
                f'<h3>Money in</h3>{breakdown}<h3>Figures</h3>{figures}<h3>Monthly summary</h3>{monthly}</section>')
    return (f'<section class="account"><h2>Account {_e(a["account"])}</h2>'
            f'<p class="period">{period}{_e(a["line_count"])} lines</p>'
            f'<h3>Money in</h3>{breakdown}<h3>Figures</h3>{figures}<h3>Monthly summary</h3>{monthly}'
            f'{_indicators_html(a["indicators"], acct, thresholds)}</section>')


def _line_cells(x: dict[str, Any]) -> str:
    return (f'<td>{_e(x["id"])}</td><td>{_e(x["date"])}</td><td>{_e(x["description"])}</td>'
            f'<td class="num">{_e(x["amount"])}</td><td>{_e(x["category"])}</td>')


LINE_HEAD = ('<th>Line</th><th>Date</th><th>Description</th><th class="num">Amount</th><th>Category</th>')


def _threshold_html(th: dict[str, Any]) -> str:
    mark = (' <span class="illustrative">illustrative: the shipped value</span>' if th["illustrative"]
            else ' <span class="firm">firm threshold</span>')
    return f'<span class="threshold">{_e(th["key"])} {_e(th["shown"])}{mark}</span>'


def _indicators_html(indicators: list[dict[str, Any]], account: str = "",
                     thresholds: dict[str, Any] | None = None) -> str:
    """Each indicator with the thresholds it fired at and the lines behind it (R14), and the
    thresholds file they were raised from, so a firm can see whose file decided the flags."""
    source = (f'<p class="thresholds-source">Thresholds from {_e(thresholds["source"])}'
              f'{" (shipped with this tool)" if thresholds["shipped"] else ""}.</p>' if thresholds else "")
    note = f'<p class="note">{_e(ILLUSTRATIVE)}</p>{source}'
    if not indicators:
        return f'<section class="indicators"><h3>Indicators</h3><p>No indicator fired.</p>{note}</section>'
    parts = []
    for i in indicators:
        ths = "; ".join(_threshold_html(th) for th in i["thresholds"])
        rows = "".join(f"<tr>{_line_cells(x)}</tr>" for x in i["lines"])
        parts.append(f'<section class="indicator" id="indicator-{_e(account)}-{_e(i["id"])}"><p><strong>{_e(i["id"])} '
                     f'{_e(i["title"])}</strong>: {_e(i["reason"])}</p><p>Threshold that fired: {ths}</p>'
                     f'<table class="lines"><thead><tr>{LINE_HEAD}</tr></thead><tbody>{rows}</tbody></table>'
                     '</section>')
    return f'<section class="indicators"><h3>Indicators</h3>{note}{"".join(parts)}</section>'


OUTCOME_CLASS = {"pass": "pass", "refer": "refer", "fail": "fail"}


def _policy_html(a: dict[str, Any]) -> str:
    """One account's policy check: the illustrative label first, inside the same panel as the
    overall outcome and each rule's result (R25, P20), so copying the panel copies the label."""
    pc = a["policy_check"]
    rows = "".join(
        f'<tr><td>{_e(r["rule_id"])}</td><td>{_e(r["description"])}</td><td>{_e(r["metric_id"])}</td>'
        f'<td class="rule-outcome {OUTCOME_CLASS.get(r["outcome"], "")}">{_e(r["outcome"])}</td>'
        f'<td>{_e(r["detail"])}</td></tr>' for r in pc["rules"])
    return (f'<section class="policy-check" data-account="{_e(a["account"])}">'
            f'<h3>Policy check (illustrative): account {_e(a["account"])}</h3>'
            f'<p class="illustrative-label">{_e(policy.ILLUSTRATIVE_LABEL)}</p>'
            f'<p class="outcome {OUTCOME_CLASS.get(pc["overall"], "")}">Overall outcome: {_e(pc["overall"].upper())}</p>'
            f'<p class="policy-source">From {_e(pc["policy_name"])}, the policy shipped with this tool, '
            'on the figures as they now stand.</p>'
            '<table class="rules"><thead><tr><th>Rule</th><th>Description</th><th>Figure</th><th>Outcome</th>'
            f'<th>Detail</th></tr></thead><tbody>{rows}</tbody></table></section>')


def _policy_checks_html(summary: dict[str, Any]) -> str:
    if not summary.get("policy_check", {}).get("shown"):
        return ('<section id="policy-checks"><h2>Policy check</h2><p class="note">Not shown: the firm '
                'configuration has show_policy_verdict off.</p></section>')
    return ('<section id="policy-checks"><h2>Policy check</h2>'
            + "".join(_policy_html(a) for a in summary["accounts"] if a.get("policy_check")) + "</section>")


RECOMMENDATION_HTML = ('<section id="recommendation"><h2>Recommendation</h2>'
                       '<p class="for-underwriter">For the underwriter: the tool leaves this field blank, '
                       'whatever the policy check shows.</p>'
                       '<p class="field" data-field="recommendation"></p></section>')


def _candidate_html(c: dict[str, Any]) -> str:
    bucket = _e(c["bucket"]) if c["bucket"] else '<span class="none">no bucket</span>'
    return f'{_e(c["rule_id"])} ({bucket})'


def _queue_html(queue: list[dict[str, Any]], token: str | None = None, position: int | None = None) -> str:
    """The judgement queue, listed first, largest amount first (R15); given the page's token
    and case log position, each line carries the override form (R17)."""
    from .edit import override_form

    forms = token is not None and position is not None
    if not queue:
        return ('<section id="judgement-queue"><h2>Judgement queue</h2>'
                '<p>No line needs a decision.</p></section>')
    rows = []
    for e in queue:
        why = ", ".join(_e(REASON_TITLES.get(r, r)) for r in e["reasons"])
        flags = ", ".join(f'{_e(i["id"])} {_e(INDICATOR_TITLES.get(i["kind"], i["kind"]))}' for i in e["indicators"])
        cands = "; ".join(_candidate_html(c) for c in e["candidates"])
        pairs = ", ".join(_e(p) for p in e["pairs_with"])
        form = override_form(token, e["account"], e["line"], position, "/") if forms else ""
        rows.append(f'<tr><td>{_e(e["account"])}</td>{_line_cells(e["line"])}<td>{why}</td><td>{flags}</td>'
                    f'<td>{cands}{(" pairs with " + pairs) if pairs else ""}</td><td>{form}</td></tr>')
    return ('<section id="judgement-queue"><h2>Judgement queue</h2>'
            '<p>The lines a person has to decide, largest amount first.</p>'
            f'<table class="queue"><thead><tr><th>Account</th>{LINE_HEAD}<th>Why listed</th><th>Indicators</th>'
            f'<th>Candidates</th><th>Change category</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
            '</section>')


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


def demo_banner(demo: bool) -> str:
    """The banner a demo case shows on every page (R48), saying it demonstrates the mechanism
    and is not an applicant file; the same words as the pack's stamp."""
    from ..pack import DEMO_STAMP

    return f'<section id="demo-banner" class="demo-banner"><p>{_e(DEMO_STAMP)}</p></section>' if demo else ""


def is_demo(folder: Path) -> bool:
    """Whether the case's profile marks it as a demo (R48); a profile that cannot be read shows
    no banner, and the page says why it shows no figures."""
    try:
        return bool(store.load_profile(folder).get("demo"))
    except Exception:
        return False


def render(summary: dict[str, Any], token: str | None = None) -> str:
    """The case summary as HTML for the page's main element; every case text escaped. Given
    the page's token, each figure, bucket and monthly cell links to the lines behind it (R16)."""
    names = [x for x in (summary["business_name"], summary["label"]) if x]
    head = (f'<section id="summary-head"><p class="case-name">{_e(" — ".join(names))}</p></section>'
            if names else "")
    from .edit import stale_html

    head += stale_html(summary.get("stale") or [], token, summary.get("position"))  # R19
    if summary["state"] == "shown":
        head += _queue_html(summary.get("queue") or [], token, summary.get("position"))  # listed first (R15)
    head += _profile_html(summary.get("profile"))
    state = summary["state"]
    if state == "not_reconciled":
        return head + _reconciliation_html(summary["reconciliation"])
    if state == "refused":
        return (head + '<section class="refusal" id="refused"><h2>No figures are shown for this case</h2>'
                f'<p>{_e(summary["refusal"])}</p></section>')
    if state == "empty":
        return head + '<section id="empty"><p>No statement has been imported into this case yet.</p></section>'
    from .signoff import preview_form

    combined = _account_html(summary["combined"], token) if summary.get("combined") else ""  # R41
    return (head + combined + "".join(_account_html(a, token, summary.get("thresholds")) for a in summary["accounts"])
            + _policy_checks_html(summary) + RECOMMENDATION_HTML + (preview_form(token) if token else ""))
