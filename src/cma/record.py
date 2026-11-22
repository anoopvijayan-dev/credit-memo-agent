"""The pack's record.html (C6; R2, R16, R25, R26, R34, R48, R56, P8, P20).

One self-contained HTML file, written once at sign-off beside memo.md, evidence.json and
monthly.csv, from the signed preview. It works offline: its style is inline, it holds no
script, and it refers to nothing outside itself (no URL, no image, no stylesheet, no font),
and its content security policy lets it load nothing. It holds:

- on a demo case, the demo stamp first (R48);
- the sign-off: the typed name, the operating-system user, the UTC time and the snapshot,
  in the words of the sign-off, never as approving the loan (R22, R24);
- the memo-ready sections as memo.md holds them;
- for each account, the money-in breakdown, every figure, the monthly summary and the
  indicators, money to the penny as every table shows it (R2). Every figure, bucket and
  monthly cell links to a section of the record that lists exactly the lines behind it, with
  their total, or the inputs and method, by the figure's kind, as the workspace's drill-down
  shows them (R16, R26);
- while show_policy_verdict is on, for each account the policy check, with the illustrative
  label in the same section as the outcome (R25, P20); with it off, no outcome and no rule
  result (R56). The recommendation is a field left blank for the underwriter (R24).

It renders what the figures layer and policy.evaluate computed for the preview; it computes
no money. Every piece of text from the case is escaped (R34, P8).
"""

from __future__ import annotations

from html import escape
from typing import Any

from . import indicators as ind
from .review import drilldown, summary

CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; form-action 'none'; base-uri 'none'"
STYLE = """
:root { color-scheme: light dark; }
body { margin: 0 auto; max-width: 72rem; padding: 1rem; font-family: system-ui, sans-serif; line-height: 1.4; }
h1 { font-size: 1.4rem; margin: 0 0 0.25rem; }
h2 { font-size: 1.15rem; margin: 1.5rem 0 0.25rem; }
h3 { font-size: 1rem; margin: 1rem 0 0.25rem; }
table { border-collapse: collapse; width: 100%; margin: 0 0 0.75rem; font-size: 0.9rem; }
th, td { border-bottom: 1px solid #8884; padding: 0.2rem 0.5rem; text-align: left; vertical-align: top; }
.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tr.within td:nth-child(2) { padding-left: 1.5rem; font-style: italic; }
tr.total td { font-weight: 600; border-top: 2px solid #8888; }
.overridden, .part, .illustrative, .firm, .note, .none { font-size: 0.8rem; opacity: 0.8; }
.policy-check, .lines-behind { border: 1px solid #8888; padding: 0.5rem 1rem; margin: 0 0 0.75rem; }
.illustrative-label, .demo-banner { font-weight: 600; }
.demo-banner { border: 2px solid #c80; padding: 0.25rem 1rem; }
.field { min-height: 3rem; border: 1px dashed #8888; }
pre.memo { white-space: pre-wrap; }
"""


def _e(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def anchor(account: str, **target: str) -> str:
    """The id of the record's section listing the lines behind a figure or a monthly cell."""
    if "figure" in target:
        return f"lines-{account}-{target['figure']}"
    return f"lines-{account}-{target['month']}-{target['cell']}"


def _href(account: str, **target: str) -> str:
    return _e("#" + anchor(account, **target))


def _policy_check(a) -> dict[str, Any]:
    """The signed preview's policy result, in the shape the workspace's panel shows it."""
    r = a.policy
    return {"policy_name": r.policy_name, "policy_sha256": r.policy_sha256, "overall": r.overall.value,
            "rules": [{"rule_id": x.rule_id, "description": x.description, "metric_id": x.metric_id,
                       "outcome": x.outcome.value, "detail": x.detail} for x in r.rules]}


def _details(a) -> list[str]:
    """A section for every figure and every monthly cell of the account: exactly its lines."""
    f = a.figures
    targets = [{"figure": m.id} for m in a.metrics]
    targets += [{"month": r.month, "cell": key} for r in f.monthly for key, _ in summary.CELLS]
    back = f'<p class="back"><a href="#account-{_e(a.account)}">Back to account {_e(a.account)}</a></p>'
    out = []
    for t in targets:
        d = {"account": a.account, **drilldown.detail(f, **t)}
        out.append(drilldown.detail_html(d, section_id=anchor(a.account, **t), back=back))
    return out


def _signoff_html(signoff: dict[str, Any]) -> str:
    return ('<section id="sign-off"><h2>Sign-off</h2>'
            f'<p>Figures signed off by <strong>{_e(signoff["name"])}</strong> (operating-system user '
            f'{_e(signoff["os_user"])}) at {_e(signoff["at"])} (UTC), bound to snapshot '
            f'<code>{_e(signoff["snapshot"])}</code>.</p>'
            f'<p class="wording">{_e(signoff["wording"])}</p></section>')


def render(p, signoff: dict[str, Any]) -> str:
    """The record of a signed preview `p` (pack.Preview) and its sign-off, as one HTML file."""
    thresholds = (None if p.thresholds is None else
                  {"source": p.thresholds.source,
                   "shipped": p.thresholds.sha256 == ind.shipped().sha256})
    contents = "".join(f'<li><a href="#account-{_e(a.account)}">Account {_e(a.account)}</a></li>' for a in p.accounts)
    body = []
    body.append(summary.demo_banner(p.demo))  # the workspace's banner, the pack's stamp (R48)
    body.append(f'<header><h1>Statement review record</h1><p>Case {_e(p.case)}, snapshot '
                f'<code>{_e(p.snapshot)}</code>.</p></header>')
    body.append(_signoff_html(signoff))
    body.append(f'<nav id="contents"><ul><li><a href="#memo">Memo-ready sections</a></li>{contents}'
                + ('<li><a href="#policy-checks">Policy check</a></li>' if p.policy_shown else "")
                + '<li><a href="#recommendation">Recommendation</a></li></ul></nav>')
    body.append(f'<section id="memo"><h2>Memo-ready sections</h2><pre class="memo">{_e(p.text)}</pre></section>')
    details = []
    for a in p.accounts:
        shown = summary._account(a.figures, a.indicators, None)
        html = summary._account_html(shown, thresholds=thresholds, href=_href)
        body.append(html.replace('<section class="account">',
                                 f'<section class="account" id="account-{_e(a.account)}">', 1))
        details += _details(a)
    if p.policy_shown:
        body.append('<section id="policy-checks"><h2>Policy check</h2>'
                    + "".join(summary._policy_html({"account": a.account, "policy_check": _policy_check(a)})
                              for a in p.accounts if a.policy is not None) + "</section>")
    body.append(summary.RECOMMENDATION_HTML)
    body.append('<section id="lines"><h2>The lines behind each figure and cell</h2>' + "".join(details)
                + "</section>")
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            f'<meta http-equiv="Content-Security-Policy" content="{_e(CSP)}">\n'
            '<meta name="referrer" content="no-referrer">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>Statement review record: case {_e(p.case)}</title>\n<style>{STYLE}</style>\n</head>\n'
            f'<body>\n{"".join(body)}\n</body>\n</html>\n')
