"""The workspace's firm-rule promotion (C5; R20, R21, R42).

Each override the case applies is listed with a form to promote it to a firm rule: the
counterparty pattern the underwriter types, the direction (the override's line's, unless
they choose another), the reason and their name. The form posts to /promote, which first
answers with the preview: every line of the open case the rule would match, each saying
whether the rule would set its category or what keeps it (an override, a pairing, a
protected built-in pattern, an ambiguous verdict, a profile name or an earlier firm rule).
Nothing is saved until the underwriter confirms that preview, whose form carries the number
of lines it showed and the case log position; promotion.promote refuses when either has
moved. /api/promote answers the same as data.

Every case text is escaped (R34); this module computes no money.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .. import case as caselog
from .. import promotion, store
from ..categorise import RulesetError

KEYS = frozenset({"seq", "pattern", "direction", "id", "name", "reason", "expected", "confirm", "shown", "back"})
RULE_KEYS = ("seq", "pattern", "direction", "id")


def _rule_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {k: fields.get(k) for k in RULE_KEYS}


def _shown(p: promotion.Preview) -> dict[str, Any]:
    from .summary import _line

    return {"rule": p.rule, "seq": p.seq, "count": p.count, "applies": p.applies,
            "lines": [{"account": m.account, **_line(m.line), "applies": m.applies, "why": m.why} for m in p.lines]}


def answer(folder: Path, case_id: str, fields: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Preview a promotion (no `confirm`), or save the one the preview showed (`confirm`)."""
    folder = Path(folder)
    if extra := sorted(k for k in fields if k not in KEYS):
        return 400, {"refused": f"{', '.join(extra)} is not part of a firm-rule promotion; nothing was saved."}
    confirm = fields.get("confirm") not in (None, "", False, "0")
    try:
        if not confirm:
            p = promotion.preview(folder, _rule_fields(fields))
            from .summary import log_position

            return 200, {"preview": _shown(p), "position": log_position(folder), "saved": False}
        expected = fields.get("expected")
        if isinstance(expected, str) and expected.isdigit():
            expected = int(expected)
        if not isinstance(expected, int) or isinstance(expected, bool):
            return 400, {"refused": "a promotion carries the case log position the preview was built at "
                                    "(expected); preview it again."}
        r = promotion.promote(folder, {**_rule_fields(fields), "name": fields.get("name"),
                                       "reason": fields.get("reason")},
                              shown=fields.get("shown"), expected=expected)
    except (store.CaseStoreError, RulesetError, ValueError) as e:
        return 400, {"refused": f"{str(e).rstrip('.')}."}
    except caselog.LogConflict as e:
        return 409, {"conflict": (f"The case changed since this preview was built (it was built at case log entry "
                                  f"{e.expected}, the case is now at entry {e.actual}). Nothing was saved: "
                                  "preview the rule again."), "reload": True}
    except caselog.SaveNotConfirmed as e:
        return 500, {"not_confirmed": (f"Whether this promotion is in the case log is not confirmed ({e}). Check "
                                       "the case (cma case check) before saving anything else."), "check": True}
    except caselog.SaveNotRecorded as e:
        return 503, {"not_recorded": f"The promotion was not recorded ({e}). Preview the rule again.", "reload": True}
    except caselog.CaseLocked:
        return 409, {"locked": ("The case is held: another window or command is writing to it. Nothing was "
                                "saved; try again in a moment."), "reload": True}
    except caselog.CaseLogBroken as e:
        return 409, {"refused": (f"The case log is broken at entry {e.first_bad}: {e.reason}. Nothing can be saved "
                                 "until it is resolved.")}
    except OSError as e:
        return 500, {"refused": f"A file could not be read or written ({e.strerror or type(e).__name__}); "
                                "nothing was saved."}
    return 200, {"saved": True, "rule": r.rule, "matched": r.preview.count, "applies": r.preview.applies,
                 "position": r.position, "firm_rules_sha256": r.firm_rules_sha256}


# --- HTML ----------------------------------------------------------------------------------


def _e(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def _hidden(name: str, value: Any) -> str:
    return f'<input type="hidden" name="{_e(name)}" value="{_e(value)}">'


def _action(token: str) -> str:
    return _e("/promote?" + urlencode({"token": token}))


def promotable_html(promotable: list[dict[str, Any]], token: str | None, position: int | None) -> str:
    """The overrides the case applies, each with a form to promote it to a firm rule (R20)."""
    if not promotable or not token or position is None:
        return ""
    rows = []
    for o in promotable:
        options = "".join(f'<option value="{d}"{" selected" if d == o["direction"] else ""}>{d}</option>'
                          for d in ("in", "out", "any"))
        form = (f'<form class="promote" method="post" action="{_action(token)}">' + _hidden("seq", o["seq"])
                + '<input name="pattern" required maxlength="1000" placeholder="Counterparty, as the bank prints it" '
                  'aria-label="Counterparty pattern"> '
                + f'<select name="direction" aria-label="Direction">{options}</select> '
                + '<button type="submit">Preview firm rule</button></form>')
        rows.append(f'<tr><td>{_e(o["seq"])}</td><td>{_e(o["account"])}</td><td>{_e(o["line"])}</td>'
                    f'<td>{_e(o["category"])}</td><td>{form}</td></tr>')
    return ('<section id="promote-overrides"><h2>Make an override a firm rule</h2>'
            '<p>A firm rule gives a counterparty\'s lines a category in every case categorised afterwards. '
            'You see the lines it would match in this case before anything is saved.</p>'
            '<table class="promotable"><thead><tr><th>Override</th><th>Account</th><th>Line</th>'
            f'<th>Category</th><th></th></tr></thead><tbody>{"".join(rows)}</tbody></table></section>')


def _reload(token: str) -> str:
    return f'<p><a href="{_e("/?" + urlencode({"token": token}))}">Back to the case</a></p>'


def html(status: int, reply: dict[str, Any], token: str, fields: dict[str, Any]) -> str:
    """The page's main element for a promotion's preview, its saving, or why either was refused."""
    if status != 200:
        text = next((reply[k] for k in ("conflict", "locked", "refused", "not_recorded", "not_confirmed")
                     if k in reply), "The rule could not be saved.")
        return (f'<section class="refusal" id="promote-refused"><h2>No firm rule saved</h2><p>{_e(text)}</p>'
                + _reload(token) + "</section>")
    if reply.get("saved"):
        r = reply["rule"]
        return ('<section id="promoted"><h2>Firm rule saved</h2>'
                f'<p>Rule <code>{_e(r["id"])}</code>: {_e(r["direction"])} lines naming {_e(r["pattern"])} are '
                f'{_e(r["category"])}. It matched {_e(reply["matched"])} lines in this case and sets the category '
                f'of {_e(reply["applies"])}; every case categorised from now on applies it. The promotion is case '
                f'log entry {_e(reply["position"])}.</p>' + _reload(token) + "</section>")
    p = reply["preview"]
    r = p["rule"]
    rows = "".join(
        f'<tr><td>{_e(x["account"])}</td><td>{_e(x["id"])}</td><td>{_e(x["date"])}</td><td>{_e(x["description"])}</td>'
        f'<td class="money">{_e(x["amount"])}</td><td>{_e(x["category"])}</td>'
        f'<td>{"yes" if x["applies"] else _e("no: " + (x["why"] or ""))}</td></tr>' for x in p["lines"])
    form = ""
    if reply.get("position") is not None:
        form = (f'<form class="promote-confirm" method="post" action="{_action(token)}">'
                + _hidden("seq", p["seq"]) + _hidden("pattern", r["pattern"]) + _hidden("direction", r["direction"])
                + _hidden("id", r["id"]) + _hidden("shown", p["count"]) + _hidden("expected", reply["position"])
                + _hidden("confirm", "1")
                + '<input name="reason" required maxlength="1000" placeholder="Reason" aria-label="Reason"> '
                '<input name="name" required maxlength="1000" placeholder="Your name" aria-label="Your name"> '
                '<button type="submit">Save firm rule</button></form>')
    return ('<section id="promote-preview"><h2>Preview of the firm rule</h2>'
            f'<p>Rule <code>{_e(r["id"])}</code>: {_e(r["direction"])} lines naming {_e(r["pattern"])} become '
            f'{_e(r["category"])}. In this case it matches {_e(p["count"])} lines and would set the category of '
            f'{_e(p["applies"])}.</p>'
            '<table class="lines"><thead><tr><th>Account</th><th>Line</th><th>Date</th><th>Description</th>'
            '<th>Amount</th><th>Category now</th><th>Rule sets it</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>{form}{_reload(token)}</section>')
