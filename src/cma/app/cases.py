"""What the case app shows of each case: read from its profile and its case log, never
computed from the statements, so the list of cases opens at once however many there are."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import case as caselog
from .. import firm, report, store

# The steps of a case, in order, as the app names them.
NEEDS_STATEMENTS = "needs_statements"
READY = "ready"
SIGNED = "signed"
CHANGED = "changed"
ATTENTION = "attention"
BUSY = "busy"

STATUS_TEXT = {
    NEEDS_STATEMENTS: "Needs statements",
    READY: "Ready for review",
    SIGNED: "Signed",
    CHANGED: "Changed since signing",
    ATTENTION: "Needs attention",
    BUSY: "Busy",
}

# Log events that change what a signed pack would say.
_CHANGES = frozenset({"import", "override", "override_confirmed", "override_dropped", "profile_changed"})


@dataclass
class CaseView:
    id: str
    folder: Path
    label: str = ""
    business: str = ""
    demo: bool = False
    profile: dict[str, Any] = field(default_factory=dict)
    status: str = NEEDS_STATEMENTS
    problem: str = ""
    created: str = ""
    last_change: str = ""
    statements: list[dict[str, Any]] = field(default_factory=list)  # the latest import of each account
    packs: list[dict[str, Any]] = field(default_factory=list)  # newest first
    overrides: int = 0

    @property
    def title(self) -> str:
        return self.label or self.business or f"Case {self.id}"

    @property
    def status_text(self) -> str:
        return STATUS_TEXT[self.status]


def root() -> Path:
    return firm.case_root()


def case_ids() -> list[str]:
    return report.case_ids(root())


def case_exists(case_id: str) -> bool:
    try:
        return (store.case_folder(case_id) / store.PROFILE).is_file()
    except (store.CaseStoreError, OSError):
        return False


def load(case_id: str) -> CaseView:
    """One case as the app shows it. A case whose log cannot be read is shown as needing
    attention, with the reason, rather than left out of the list."""
    folder = store.case_folder(case_id)
    view = CaseView(id=case_id, folder=folder)
    try:
        view.profile = store.load_profile(folder)
    except (store.CaseStoreError, OSError) as e:
        view.status, view.problem = ATTENTION, f"Its details could not be read ({e})."
        return view
    view.label = view.profile.get("label") or ""
    view.business = view.profile.get("business_name") or ""
    view.demo = bool(view.profile.get("demo"))
    try:
        entries = caselog.for_case(folder).entries()
    except caselog.CaseLogBroken as e:
        view.status = ATTENTION
        view.problem = f"Its record of changes is broken at entry {e.first_bad}: {e.reason}."
        return view
    except caselog.CaseLocked:
        view.status, view.problem = BUSY, "Another window is saving to this case. Reload in a moment."
        return view
    except OSError as e:
        view.status, view.problem = ATTENTION, f"Its record of changes could not be read ({e.strerror or e})."
        return view
    statements: dict[str, dict[str, Any]] = {}
    last_changed = signed_at = ""
    for entry in entries:
        if entry.seq == 1:
            view.created = entry.at
        view.last_change = entry.at
        if entry.event == "import":
            d = entry.data
            statements[d.get("account", "")] = {"account": d.get("account", ""), "lines": d.get("lines"),
                                                "start": d.get("start"), "end": d.get("end"),
                                                "format": d.get("format", ""), "at": entry.at}
        if entry.event == "override":
            view.overrides += 1
        if entry.event in _CHANGES:
            last_changed = entry.at
        if entry.event == "signed_off":
            d = entry.data
            signed_at = entry.at
            view.packs.append({"snapshot": d.get("snapshot", ""), "name": d.get("name", ""), "at": entry.at,
                               "files": d.get("files", [])})
    view.statements = [statements[k] for k in sorted(statements)]
    view.packs.reverse()
    if not view.statements:
        view.status = NEEDS_STATEMENTS
    elif not signed_at:
        view.status = READY
    else:
        view.status = SIGNED if last_changed <= signed_at else CHANGED
    return view


def load_all() -> list[CaseView]:
    """Every case, the most recently changed first."""
    views = [load(case_id) for case_id in case_ids()]
    return sorted(views, key=lambda v: v.last_change, reverse=True)


def pack_folder(view: CaseView, snapshot: str) -> Path | None:
    """The folder of one of the case's signed packs, or None if the case has no such pack."""
    if not any(p["snapshot"] == snapshot for p in view.packs):
        return None
    folder = view.folder / "packs" / snapshot
    return folder if folder.is_dir() else None
