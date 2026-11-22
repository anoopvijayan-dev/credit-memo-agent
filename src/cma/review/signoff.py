"""The workspace's preview and sign-off (C5, C6; R22, R23, R24, R26, R28, R43).

From the case page the underwriter builds a preview (POST /preview, or /api/preview for
data): the pack's memo-ready text, the grounding guard's verdict and the snapshot hash, and
one preview entry in the case log, nothing else (pack.build_preview). The preview page then
offers one form, worded as signing off the figures, never as approving the loan (R24): a
typed name and the preview's snapshot. POST /sign (or /api/sign) signs off that snapshot
through pack.sign_off, which refuses, writing nothing, when the case changed since the
preview (naming what), when the case log is broken (naming the first bad entry), when the
figures do not reconcile or the text fails the guard, and when the snapshot's pack is
already written.

The preview's text is the case's own figures and labels; it is shown escaped, as text
(R34). Messages name entries by number and inputs by what they are, never by their content.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .. import case as caselog
from .. import pack, store
from ..metrics import ReconciliationError

PREVIEW_KEYS = frozenset({"back"})
SIGN_KEYS = frozenset({"snapshot", "name", "back"})


def _refusal(e: Exception) -> tuple[int, dict[str, Any]] | None:
    if isinstance(e, ReconciliationError):
        return 409, {"refused": f"{str(e).rstrip('.')}. The figures do not reconcile (R3); nothing was "
                                "signed off or written."}
    if isinstance(e, caselog.CaseLogBroken):
        return 409, {"refused": (f"The case log is broken at entry {e.first_bad}: {e.reason}. Nothing can be "
                                 "signed off until it is resolved."), "first_bad": e.first_bad}
    if isinstance(e, caselog.SaveNotConfirmed):
        return 500, {"not_confirmed": (f"Whether this is in the case log is not confirmed ({e}). Check the case "
                                       "(cma case check) before anything else.")}
    if isinstance(e, caselog.SaveNotRecorded):
        return 503, {"not_recorded": f"Nothing was recorded ({e}). Reload the case and try again."}
    if isinstance(e, caselog.CaseLocked):
        return 409, {"locked": ("The case is held: another window or command is writing to it. Nothing was "
                                "recorded; try again in a moment.")}
    if isinstance(e, caselog.LogConflict):
        return 409, {"refused": "The case changed while this was being recorded; reload the case and try again."}
    if isinstance(e, (pack.SignOffRefused, pack.FieldMapError, pack.PreviewError, store.CaseStoreError)):
        reply: dict[str, Any] = {"refused": f"{str(e).rstrip('.')}."}
        if isinstance(e, pack.SignOffRefused) and e.changed:
            reply["changed"] = e.changed
        return (409 if isinstance(e, pack.SignOffRefused) else 400), reply
    if isinstance(e, OSError):
        return 500, {"refused": f"A file of the case could not be read or written ({e.strerror or type(e).__name__}); "
                                "nothing was signed off."}
    return None


def answer(folder: Path, case_id: str, kind: str, fields: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Build a preview ('preview') or sign one off ('sign'), and the HTTP status and reply."""
    folder = Path(folder)
    allowed = PREVIEW_KEYS if kind == "preview" else SIGN_KEYS
    if extra := sorted(k for k in fields if k not in allowed):
        return 400, {"refused": f"{', '.join(extra)} is not part of a {kind}; nothing was recorded."}
    try:
        if kind == "preview":
            p = pack.build_preview(folder, case_id)
            return 200, {"snapshot": p.snapshot, "grounded": p.grounded, "text": p.text, "position": p.position,
                         "policy_check": "shown" if p.policy_shown else "hidden",
                         "findings": [{"account": a, "number": n, "why": w} for a, n, w in p.findings()],
                         "wording": pack.SIGN_WORDING}
        s = pack.sign_off(folder, case_id, fields.get("snapshot"), fields.get("name"))
    except Exception as e:
        refused = _refusal(e)
        if refused is None:
            raise
        return refused
    return 200, {"signed": True, "snapshot": s.snapshot, "name": s.name, "os_user": s.os_user, "at": s.at,
                 "pack": f"{pack.PACKS}/{s.snapshot}", "files": s.files, "position": s.position}


# --- HTML ----------------------------------------------------------------------------------


def _e(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def preview_form(token: str) -> str:
    """The case page's button to build a preview of the pack."""
    action = _e("/preview?" + urlencode({"token": token}))
    return ('<section id="preview-pack"><h2>Preview and sign off</h2>'
            '<p>Build the pack\'s memo sections from the figures as they are now. Nothing is written but a '
            'preview entry in the case log.</p>'
            f'<form class="preview" method="post" action="{action}"><input type="hidden" name="back" value="/">'
            '<button type="submit">Preview the pack</button></form></section>')


def _reload(token: str) -> str:
    return f'<p><a href="{_e("/?" + urlencode({"token": token}))}">Back to the case</a></p>'


def html(kind: str, status: int, reply: dict[str, Any], token: str) -> str:
    """The page's main element for a preview, a sign-off, or why either was refused."""
    if status != 200:
        text = next((reply[k] for k in ("refused", "locked", "not_recorded", "not_confirmed") if k in reply),
                    "This could not be done.")
        changed = "".join(f"<li>{_e(c)}</li>" for c in reply.get("changed") or [])
        title = "Not signed off" if kind == "sign" else "No preview"
        return (f'<section class="refusal" id="{kind}-refused"><h2>{title}</h2><p>{_e(text)}</p>'
                + (f'<p>What changed:</p><ul class="changed">{changed}</ul>' if changed else "")
                + _reload(token) + "</section>")
    if kind == "sign":
        files = ", ".join(_e(f) for f in reply["files"])
        return ('<section id="signed-off"><h2>Figures signed off</h2>'
                f'<p>Signed off by {_e(reply["name"])} (operating-system user {_e(reply["os_user"])}) at '
                f'{_e(reply["at"])}, bound to snapshot <code>{_e(reply["snapshot"])}</code>.</p>'
                f'<p>The pack was written once to <code>{_e(reply["pack"])}</code>: {files}. It is never '
                'overwritten. The recommendation and every judgement field are left blank for you.</p>'
                + _reload(token) + "</section>")
    findings = "".join(f'<li>account {_e(f["account"])}: {_e(f["number"] or "-")}: {_e(f["why"])}</li>'
                       for f in reply["findings"])
    if reply["grounded"]:
        verdict = "<p>Grounding guard: every number is a figure or a policy threshold, with its id.</p>"
        action = _e("/sign?" + urlencode({"token": token}))
        form = (f'<form class="sign" method="post" action="{action}">'
                f'<input type="hidden" name="snapshot" value="{_e(reply["snapshot"])}">'
                '<input type="hidden" name="back" value="/">'
                f'<p class="wording">{_e(reply["wording"])}</p>'
                '<input name="name" required maxlength="200" placeholder="Your name" aria-label="Your name"> '
                '<button type="submit">Sign off these figures</button></form>')
    else:
        verdict = (f'<p class="refusal">The text fails the grounding guard; no pack can be signed off from this '
                   f'preview (R28):</p><ul>{findings}</ul>')
        form = ""
    return ('<section id="pack-preview"><h2>Preview of the pack</h2>'
            f'<p>Snapshot <code>{_e(reply["snapshot"])}</code>, recorded as case log entry {_e(reply["position"])}.</p>'
            f'<pre class="memo">{_e(reply["text"])}</pre>{verdict}{form}{_reload(token)}</section>')
