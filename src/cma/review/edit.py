"""The workspace's override form and stale overrides (C5; R17, R18, R19, R52).

The underwriter changes a line's category from the workspace: the judgement queue and every
drill-down carry, for each line, a form that offers only the categories valid for the
line's direction (R18), with a reason and the name they type. Each form also carries the
case log position the page was built at, so a save made from a page that is out of date is
refused as a conflict that asks for the case to be reloaded, and records nothing.

A stale override (R19) is listed with a form to confirm it (its line still there, recorded
again against the current statement) or drop it.

Each save goes through the override log (overrides.add_override, overrides.resolve_stale),
which appends it to the case's override log and its case log while it holds the case (R42).
The reply carries the case summary recomputed through the figures layer, its figures,
indicators and policy check included (R17, R52); this module computes no money.

Two ways in, one answer:
- /api/override and /api/stale take JSON (or a form body) and answer JSON;
- /override and /stale take the page's form and, once saved, send the browser back to the
  page the form was on (303), so reloading it never saves twice; a refusal is shown as a
  page with a link to reload the case.

Messages name lines and overrides by id and number, never by what the case holds.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from .. import case as caselog
from .. import overrides, store
from ..models import Category, Transaction

OVERRIDE_KEYS = frozenset({"account", "line", "category", "reason", "name", "expected", "back"})
STALE_KEYS = frozenset({"seq", "action", "name", "expected", "back"})
BACK_PATHS = ("/", "/lines")  # the pages a form may send the browser back to


class Refused(ValueError):
    """A request the workspace refuses before anything is read from the case."""


def categories_for(t: Transaction) -> list[str]:
    """The categories a line's direction can take, in the order the form offers them (R18)."""
    valid = overrides.valid_categories(t)
    return [c.value for c in Category if c in valid]


def _position(fields: dict[str, Any]) -> int:
    value = fields.get("expected")
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise Refused("a save carries the case log position the page was built at (expected); reload the case")
    return value


def _number(fields: dict[str, Any], key: str, what: str) -> int:
    value = fields.get(key)
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise Refused(f"name the {what} by its number")
    return value


def _save_override(folder: Path, fields: dict[str, Any]) -> dict[str, Any]:
    extra = {k: fields[k] for k in fields if k not in OVERRIDE_KEYS}
    overrides.refuse_changes(extra)  # R18: a category only, never anything else about a line
    expected = _position(fields)
    for key in ("line", "category"):
        if not isinstance(fields.get(key), str) or not fields[key]:
            raise Refused(f"an override names the {key}" if key == "line" else "an override needs a category")
    account = fields.get("account") or "main"
    if not isinstance(account, str):
        raise Refused("name the account by its key")
    r = overrides.add_override(folder, fields["line"], fields["category"], reason=fields.get("reason"),
                               name=fields.get("name"), account=account, expected=expected)
    return {"recorded": r.recorded, "seq": r.seq, "account": account, "line": r.line, "old": r.old, "new": r.new}


def _resolve(folder: Path, fields: dict[str, Any]) -> dict[str, Any]:
    extra = sorted(k for k in fields if k not in STALE_KEYS)
    if extra:
        raise Refused(f"a stale override is confirmed or dropped, and nothing else; {', '.join(extra)} "
                      "is not part of it; nothing was recorded")
    expected = _position(fields)
    seq = _number(fields, "seq", "stale override")
    action = fields.get("action")
    if action not in ("confirm", "drop"):
        raise Refused("a stale override is confirmed or dropped")
    overrides.resolve_stale(folder, seq, action, name=fields.get("name"), expected=expected)
    return {"recorded": True, "seq": seq, "action": action}


def answer(folder: Path, case_id: str, kind: str, fields: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Save one override ('override') or resolve one stale override ('stale'), and the HTTP
    status and reply: on success the saved change, the case log position now and the case
    summary recomputed (R17, R52); otherwise why nothing was recorded."""
    from . import summary

    folder = Path(folder)
    try:
        done = _save_override(folder, fields) if kind == "override" else _resolve(folder, fields)
    except (Refused, store.CaseStoreError) as e:
        return 400, {"refused": f"{str(e).rstrip('.')}."}
    except caselog.LogConflict as e:
        return 409, {"conflict": (f"The case changed since this page was loaded (the page was built at case log "
                                  f"entry {e.expected}, the case is now at entry {e.actual}). Nothing was "
                                  "recorded: reload the case and save again."),
                     "reload": True, "expected": e.expected, "position": e.actual}
    except caselog.SaveNotConfirmed as e:
        return 500, {"not_confirmed": (f"Whether this save is in the case log is not confirmed ({e}). Check the "
                                       "case (cma case check) before saving anything else."), "check": True}
    except caselog.SaveNotRecorded as e:
        return 503, {"not_recorded": f"The save was not recorded ({e}). Reload the case and save again.",
                     "reload": True}
    except caselog.CaseLocked:
        return 409, {"locked": ("The case is held: another window or command is writing to it. Nothing was "
                                "recorded; try again in a moment."), "reload": True}
    except caselog.CaseLogBroken as e:
        return 409, {"refused": (f"The case log is broken at entry {e.first_bad}: {e.reason}. Nothing can be saved "
                                 "until it is resolved.")}
    except OSError as e:
        return 500, {"refused": f"A file of the case could not be read ({e.strerror or type(e).__name__}); "
                                "nothing was recorded."}
    try:
        shown = summary.case_summary(folder, case_id)
    except Exception:  # the save is recorded; only the recomputed view failed
        shown = None
    done["position"] = shown["position"] if shown else None
    done["case"] = shown
    return 200, done


def back_url(back: Any, token: str) -> str:
    """Where a form sends the browser once it has saved: the workspace page it came from,
    with the token, or the case page. Nothing outside the workspace is ever named."""
    target = "/"
    query: list[tuple[str, str]] = []
    if isinstance(back, str) and back.startswith("/") and not back.startswith("//"):
        parts = urlsplit(back)
        if parts.path in BACK_PATHS and not parts.netloc and not parts.scheme:
            target = parts.path
            query = [(k, v) for k, v in parse_qsl(parts.query) if k in ("account", "figure", "month", "cell")]
    return target + "?" + urlencode([("token", token), *query])


# --- HTML ----------------------------------------------------------------------------------


def _e(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def _hidden(name: str, value: Any) -> str:
    return f'<input type="hidden" name="{_e(name)}" value="{_e(value)}">'


def override_form(token: str, account: str, line: dict[str, Any], position: int, back: str) -> str:
    """One line's form: only the categories valid for its direction, the line's own selected (R18)."""
    options = "".join(f'<option value="{_e(c)}"{" selected" if c == line.get("category") else ""}>{_e(c)}</option>'
                      for c in line["categories"])
    return (f'<form class="override" method="post" action="{_e("/override?" + urlencode({"token": token}))}">'
            + _hidden("account", account) + _hidden("line", line["id"]) + _hidden("expected", position)
            + _hidden("back", back)
            + f'<select name="category" required aria-label="Category for line {_e(line["id"])}">{options}</select> '
            '<input name="reason" required maxlength="1000" placeholder="Reason" aria-label="Reason"> '
            '<input name="name" required maxlength="1000" placeholder="Your name" aria-label="Your name"> '
            '<button type="submit">Change category</button></form>')


def stale_html(stale: list[dict[str, Any]], token: str | None, position: int | None) -> str:
    """The stale overrides (R19), each with a form to confirm or drop it."""
    if not stale:
        return ""
    action = _e("/stale?" + urlencode({"token": token})) if token else ""
    rows = []
    for s in stale:
        forms = ""
        if token and position is not None:
            for verb, label in (("confirm", "Confirm"), ("drop", "Drop")):
                if verb == "confirm" and not s["can_confirm"]:
                    continue
                forms += (f'<form class="stale" method="post" action="{action}">' + _hidden("seq", s["seq"])
                          + _hidden("action", verb) + _hidden("expected", position) + _hidden("back", "/")
                          + '<input name="name" required maxlength="1000" placeholder="Your name" '
                            'aria-label="Your name"> '
                          + f'<button type="submit">{label}</button></form>')
        rows.append(f'<tr><td>{_e(s["seq"])}</td><td>{_e(s["account"])}</td><td>{_e(s["line"])}</td>'
                    f'<td>{_e(s["new"])}</td><td>{_e(s["why"])}</td><td>{forms}</td></tr>')
    return ('<section id="stale-overrides" class="refusal"><h2>Stale overrides</h2>'
            '<p>These overrides are not applied: their line is not in the case any more, or their statement has '
            'changed. Confirm one to apply it to the current statement, or drop it.</p>'
            '<table class="stale"><thead><tr><th>Override</th><th>Account</th><th>Line</th><th>Category</th>'
            f'<th>Why stale</th><th></th></tr></thead><tbody>{"".join(rows)}</tbody></table></section>')


def outcome_html(status: int, reply: dict[str, Any], token: str) -> str:
    """A refused form save, as the page's main element, with a link to reload the case."""
    text = next((reply[k] for k in ("conflict", "locked", "refused", "not_recorded", "not_confirmed") if k in reply),
                "The change could not be saved.")
    reload = _e("/?" + urlencode({"token": token}))
    return (f'<section class="refusal" id="save-refused"><h2>Not saved</h2><p>{_e(text)}</p>'
            f'<p><a href="{reload}">Reload the case</a></p></section>')
