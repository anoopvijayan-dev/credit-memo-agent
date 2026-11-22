"""Promoting an override to a firm rule (R20, R21, R42).

The underwriter, having overridden a line, can make the change a rule for the firm: a
counterparty pattern, the direction it applies to and the override's category. Before
anything is saved the workspace shows the lines the rule would match in the open case
(preview): every line of every account whose wording the pattern matches in that direction,
and for each whether the rule would set its category or not, and why not. A line keeps what
an override, an own-account pairing, a protected built-in pattern (R21), an ambiguous verdict
(R4), a case-profile name (R5, R6) or an earlier firm rule gives it, because those rank above
a new firm rule (design C1, precedence).

Saving (promote) holds the case, builds the preview again and refuses when it no longer
counts the lines the underwriter was shown or gives another digest of them (Preview.digest), then adds the rule to the firm rules file (its
pattern, direction, category, the typed name, the UTC time and the reason) and records the
promotion in the case log (R42). The firm rules file is read as it stands by every case
categorised afterwards (firm_rules.for_case), the open case included, so the rule applies
from the next categorisation (R20). If the case log does not record the promotion, the file
is put back as it was. The firm rules file is shared by every case, so a promotion holds its
lock (firm_rules.yaml.lock beside it) from reading the file until its case log records it or
the file is put back: two cases promoting at once take turns, and neither loses the other's rule.

The case log entry names the rule by id, its direction and category, the override it came
from, the counts the preview showed, who promoted it and when, and the hash of the rules file
written. It carries neither the pattern nor the reason, which are text about the applicant's
counterparties; the rules file holds them.

Messages name lines and overrides by id and number, never by what the case holds.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import case as caselog
from . import firm, firm_rules, overrides, store
from .categorise import (
    PAIRING,
    Rule,
    _firm_rules_for,
    _fold,
    active_ruleset,
    categorise,
    names_pattern,
    pair_own_accounts,
    rule_matches,
)
from .models import Category, Transaction

EVENT = "firm_rule_promotion"
LOCK_WAIT = 30.0  # seconds a promotion waits for another case's promotion to finish

WHY = {
    "override": "an override sets this line's category",
    "pairing": "an own-account pairing settles this line",
    "protected": "a protected built-in pattern matches this line (R21)",
    "ambiguous": "this line's wording is ambiguous; only an override or a pairing settles it (R4)",
    "profile": "a name from the case profile decides this line",
    "firm": "an earlier firm rule matches this line first",
}


class PreviewChanged(store.CaseStoreError):
    """The rule no longer matches the lines the underwriter was shown; nothing was saved."""


@dataclass(frozen=True)
class Matched:
    account: str
    line: Transaction
    now: str            # the line's category as the case stands
    rule_now: str | None
    applies: bool       # whether the new rule would set its category
    why: str | None     # why not, when it would not


@dataclass(frozen=True)
class Preview:
    rule: dict[str, str]  # id, pattern, direction, category
    seq: int
    lines: list[Matched] = field(default_factory=list)
    firm_rules_sha256: str | None = None  # the firm rules file the preview read; None for none
    # Lines the rule does not match whose category the rule still changes, through the
    # case as a whole (an own-account pairing it unmakes, say): (account, line, now, then).
    changed: list[tuple[str, Transaction, str, str]] = field(default_factory=list)

    @property
    def digest(self) -> str:
        """What the underwriter was shown: the rule, the firm rules file it was read against,
        and each matched line with whether the rule sets it and why not. Saving refuses when
        the preview built again gives another digest, even with the same number of lines."""
        shown = {"rule": self.rule, "seq": self.seq, "firm_rules": self.firm_rules_sha256,
                 "lines": [[m.account, m.line.id, m.applies, m.why] for m in self.lines],
                 "changed": [[a, t.id, now, then] for a, t, now, then in self.changed]}
        return hashlib.sha256(json.dumps(shown, sort_keys=True).encode("utf-8")).hexdigest()

    @property
    def count(self) -> int:
        return len(self.lines)

    @property
    def applies(self) -> int:
        return sum(1 for m in self.lines if m.applies)


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not any(ch.isalnum() for ch in value):
        raise store.CaseStoreError(f"a firm rule needs a {what}: type one, with at least one letter or digit")
    value = value.strip()
    if len(value) > firm_rules.MAX_TEXT:
        raise store.CaseStoreError(f"the {what} is longer than {firm_rules.MAX_TEXT} characters")
    return value


def _rule_id(given: Any, pattern: str, taken: set[str]) -> str:
    """The id given, checked; or one made from the pattern, unused by the firm's rules."""
    if given not in (None, ""):
        if not isinstance(given, str) or not firm_rules._ID.fullmatch(given):
            raise store.CaseStoreError("a firm rule id may hold only letters, digits, _, . and -")
        if given in firm_rules.RESERVED_RULE_IDS:
            raise store.CaseStoreError(f"id {given} is reserved for the categoriser's own provenance")
        if given in taken:
            raise store.CaseStoreError(f"the firm already has a rule {given}; give the rule another id")
        return given
    base = re.sub(r"[^a-z0-9]+", "_", _fold(pattern).lower()).strip("_")[:40] or "rule"
    if base in firm_rules.RESERVED_RULE_IDS:
        base = f"firm_{base}"
    rid, n = base, 2
    while rid in taken:
        rid, n = f"{base}_{n}", n + 1
    return rid


def _override(records: list[dict[str, Any]], seq: Any) -> dict[str, Any]:
    if isinstance(seq, str) and seq.isdigit():
        seq = int(seq)
    if not isinstance(seq, int) or isinstance(seq, bool):
        raise store.CaseStoreError("name the override to promote by its number")
    rec = next((r for r in records if r.get("seq") == seq), None)
    if rec is None or rec.get("kind") != "override":
        raise store.CaseStoreError(f"there is no override {seq} in this case")
    return rec


def _categorised(folder: Path, entries, rules: tuple[Rule, ...] | None = None
                 ) -> tuple[dict[str, Any], list[dict[str, Any]], dict]:
    """Every account's statement as the case stands: overrides applied and, in a case of more
    than one account, own-account pairs settled (overrides.case_figures' first steps). With
    `rules`, the statements are categorised with those firm rules in place of the file's: the
    case as it will stand once a rule is saved, worked out whole, pairing included."""
    profile = store.load_profile(folder)
    accounts = overrides._accounts(entries)
    statements = {a: overrides._load(folder, a) for a in accounts}
    records = overrides._read_records(folder, entries)
    state = overrides._decide(records, statements)
    loaded = {a: v for a, v in statements.items() if v is not None}
    for account, (statement, sha) in loaded.items():
        if rules is None:
            overrides._categorise(statement, sha, profile, state.applied.get(account, {}))
        else:
            categorise(statement, active_ruleset(), overrides=state.applied.get(account, {}),
                       profile=store.profile_names(profile), firm_rules=rules)
    if len(accounts) > 1:
        pair_own_accounts({a: v[0] for a, v in loaded.items()},
                          overridden={a: state.applied.get(a, {}).keys() for a in loaded},
                          days=firm.load().own_account_pairing_days)
    return loaded, records, profile


def _why_not(t: Transaction, rule_now: str, ruleset, desc: str, candidate: Rule) -> str:
    if rule_now.startswith("override:"):
        return WHY["override"]
    if rule_now.startswith(PAIRING + ":"):
        return WHY["pairing"]
    if not _firm_rules_for(ruleset, desc, t.amount, (candidate,)):
        return WHY["protected"]
    if ":conflict:" in rule_now:
        return WHY["ambiguous"]
    if rule_now.startswith("profile:"):
        return WHY["profile"]
    if rule_now.startswith("firm:"):
        return WHY["firm"]
    return WHY["protected"]


def _build(folder: Path, entries, fields: dict[str, Any], existing: firm_rules.FirmRules) -> Preview:
    loaded, records, _profile = _categorised(folder, entries)
    rec = _override(records, fields.get("seq"))
    pattern = _text(fields.get("pattern"), "counterparty pattern")
    if not firm_rules._SEPARATOR_RUN.sub("", _fold(pattern)):
        raise store.CaseStoreError("a firm rule's pattern needs at least one letter or digit")
    account = rec["account"]
    line = next((t for t in loaded[account][0].transactions if t.id == rec["line"]), None) \
        if account in loaded else None
    direction = fields.get("direction") or None
    if direction is None:
        if line is None:
            raise store.CaseStoreError(f"override {rec['seq']}'s line is not in the case; say the rule's "
                                       "direction (in, out or any)")
        direction = "in" if line.amount > 0 else "out"
    if direction not in firm_rules.DIRECTIONS:
        raise store.CaseStoreError(f"direction must be one of {', '.join(firm_rules.DIRECTIONS)}")
    category = Category(rec["new"])
    if category not in firm_rules._categories_for(direction):
        raise store.CaseStoreError(f"override {rec['seq']}'s category {category.value} is not one a rule for "
                                   f"direction {direction} can give")
    rid = _rule_id(fields.get("id"), pattern, {r.id for r in existing.rules})
    candidate = Rule(rid, re.compile(names_pattern([pattern])), direction, category)
    ruleset = active_ruleset()
    with_rule = (*existing.rules, candidate)
    # The case as it will stand with the rule saved, worked out whole: a line the rule takes
    # can unmake an own-account pairing, so whether the rule sets a line, and what else moves,
    # is read from the whole case, not from each line alone (verify-268).
    after = {(a, t.id): t for a, (st, _sha) in _categorised(folder, entries, with_rule)[0].items()
             for t in st.transactions}
    matched: list[Matched] = []
    changed: list[tuple[str, Transaction, str, str]] = []
    for acct, (statement, _sha) in loaded.items():
        for t in statement.transactions:
            now = t.category.value if t.category else ""
            then_t = after.get((acct, t.id))
            then = then_t.category.value if then_t is not None and then_t.category else ""
            desc = t.description.upper() if ruleset.frozen else _fold(t.description)
            if not rule_matches(candidate, desc, t.amount):
                if then != now:
                    changed.append((acct, t, now, then))
                continue
            rule_now = t.rule_id or ""
            applies = then_t is not None and then_t.rule_id == f"firm:{rid}"
            why = None if applies else _why_not(t, rule_now, ruleset, desc, candidate)
            matched.append(Matched(acct, t, now, rule_now or None, applies, why))
    return Preview({"id": rid, "pattern": pattern, "direction": direction, "category": category.value},
                   rec["seq"], matched, existing.sha256, changed)


def _firm_rules_now() -> tuple[Path, bytes | None, firm_rules.FirmRules]:
    config = firm.load()
    path = config.firm_rules_path
    existing = firm_rules.read(path, required=config.names_path("firm_rules"))
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        data = None
    return path, data, existing


def preview(folder: Path, fields: dict[str, Any]) -> Preview:
    """The lines a rule promoted from override `fields['seq']` with `fields['pattern']` would
    match in the open case, and whether it would set each one's category. Nothing is written."""
    folder = Path(folder)
    _, _, existing = _firm_rules_now()
    return _build(folder, caselog.for_case(folder).entries(), fields, existing)


@dataclass(frozen=True)
class Promoted:
    rule: dict[str, str]
    preview: Preview
    position: int
    firm_rules_sha256: str


def _appended(before: bytes | None, existing: firm_rules.FirmRules, record: dict[str, Any], path: Path) -> str:
    """The firm rules file with `record` added. The new rule is appended to the file's own text,
    so the firm's comments and layout stay, when the result reads back as the file's rules and
    then the new one; otherwise (a file in another layout) the file is written afresh."""
    full = yaml.safe_dump({"rules": [dict(r) for r in existing.records] + [record]}, sort_keys=False,
                          allow_unicode=True, width=1000)
    want = firm_rules.parse(full, str(path)).records
    if before is None or not existing.records:
        return full
    try:
        text = before.decode("utf-8")
    except UnicodeDecodeError:
        return full
    item = yaml.safe_dump([record], sort_keys=False, allow_unicode=True, width=1000)
    for indent in ("", "  ", "    "):
        block = "".join(indent + ln if ln.strip() else ln for ln in item.splitlines(keepends=True))
        candidate = text.rstrip("\n") + "\n" + block
        try:
            if firm_rules.parse(candidate, str(path)).records == want:
                return candidate
        except (firm_rules.FirmRulesError, yaml.YAMLError):
            continue
    return full


@contextmanager
def _firm_rules_held(path: Path):
    """Hold the firm rules file's lock (FILE.lock beside it) alone. The file is the firm's, shared
    by every case, while a promotion holds only its own case; so two cases' promotions take
    turns here, from reading the file to the case log recording the promotion or its undo
    putting the file back, and neither writes over the other's rule (verify-268)."""
    if not path.parent.is_dir():
        raise store.CaseStoreError(f"the folder of the firm rules file {path.name} does not exist")
    lock = path.with_name(path.name + ".lock")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), store.FILE_MODE)
    try:
        deadline = time.monotonic() + LOCK_WAIT
        while not caselog._try_exclusive(fd):
            if time.monotonic() > deadline:
                raise store.CaseStoreError("another case is saving a firm rule; try again in a moment; "
                                           "nothing was saved")
            time.sleep(0.05)
        try:
            yield
        finally:
            with suppress(OSError):
                caselog._unlock(fd)
    finally:
        os.close(fd)


def promote(folder: Path, fields: dict[str, Any], *, shown: Any, expected: int | None = None,
            seen: str | None = None) -> Promoted:
    """Save the rule the preview showed (R20): `shown` is the number of lines the preview the
    underwriter saw counted, and `seen` its digest; a preview that now counts another number,
    or gives another digest (other lines, another outcome for one, another firm rules file), is
    refused and nothing is saved. `expected` is the case log position the page was built at."""
    folder = Path(folder)
    if isinstance(shown, str) and shown.isdigit():
        shown = int(shown)
    if not isinstance(shown, int) or isinstance(shown, bool) or shown < 0:
        raise store.CaseStoreError("a promotion carries the number of lines its preview showed (shown); "
                                   "preview the rule first")
    name = _text(fields.get("name"), "name")
    reason = _text(fields.get("reason"), "reason")
    with _firm_rules_held(firm.load().firm_rules_path), store._recorded(folder, EVENT) as save:
        overrides._check_position(len(save.entries), expected)
        path, before, existing = _firm_rules_now()
        p = _build(folder, save.entries, fields, existing)
        if p.count != shown:
            raise PreviewChanged(f"the preview showed {shown} matching lines and the rule now matches {p.count}; "
                                 "preview it again; nothing was saved")
        if seen is not None and seen != p.digest:
            raise PreviewChanged("the lines the rule matches, or what it would set, changed since the preview; "
                                 "preview it again; nothing was saved")
        at = overrides._now()
        record = {**p.rule, "by": name, "at": at, "reason": reason}
        text = _appended(before, existing, record, path)
        firm_rules.parse(text, str(path))  # the file as it will be read: never written otherwise
        now = path.read_bytes() if path.is_file() else None
        if now != before:
            raise store.CaseStoreError("the firm rules file changed while the rule was being saved; preview it "
                                       "again; nothing was saved")
        data = text.encode("utf-8")
        save.undo.append(store._restorer(path))
        store._write_private(path, data)
        sha = hashlib.sha256(data).hexdigest()
        save.data = {"rule": p.rule["id"], "direction": p.rule["direction"], "category": p.rule["category"],
                     "from_override": p.seq, "matched": p.count, "applies": p.applies, "name": name,
                     "os_user": store._os_user(), "at": at, "firm_rules_sha256": sha}
        position = len(save.entries) + 1
    return Promoted(p.rule, p, position, sha)
