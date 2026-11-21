"""The preview (C6; R22, R24, R25, R26, R27, R28, R48, R56, P14, P20).

`cma case preview` builds what a sign-off will be bound to, before anything is written:

- the memo-ready sections, in the order of the memo field map (memo_fields.yaml at the case
  root, or the path firm.yaml names, or the packaged map of the reply's 13 fields). Each
  field's sentence template is filled in from the figures layer's figures, each figure as
  the memo quotes it, to the whole pound (R2), followed by its id ("£55,000 [M2]"). A field
  with no figure mapped to it is left empty and marked for the underwriter (R27), and the
  recommendation and the other judgement fields always are (R24);
- while show_policy_verdict is on (the default), a policy-check section after the memo
  fields, which no field maps to: the illustrative label as its first line, then for each
  account the overall outcome and each rule, the figure tagged [Mn] and the threshold tagged
  [Rn] after a limit word (R25). With it off, no outcome and no rule result (R56);
- on a demo case, the demo stamp, saying the case demonstrates the mechanism and is not an
  applicant file (R48);
- the grounding guard's verdict over that text, each account's sentences against its own
  figures and policy result (R28, P14);
- the snapshot hash, over every input R22 names: each account's statement hash, the ruleset,
  the firm rules file, the profile, the override log position, the thresholds, the memo field
  map, the packaged policy's hash, the show_policy_verdict setting and the package version.

Nothing here does arithmetic on money: figures come from overrides.case_figures and the
outcome from policy.evaluate. The preview writes no file; it appends one `preview` entry to
the case log, holding the snapshot and the hashes it is made of (R42), while it holds the
case, so every input it reads is the case as one writer saw it.
"""

from __future__ import annotations

import hashlib
import json
import re
import string
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from . import firm, guard, overrides, policy, store
from .categorise import active_ruleset
from .models import GuardResult, Metric, PolicyResult, RuleResult, format_value

DEFAULT_FIELD_MAP_NAME = "memo_fields.yaml"
DEFAULT_FIELD_MAP_SOURCE = f"cma/memo_fields/{DEFAULT_FIELD_MAP_NAME}"
EVENT = "preview"
FIGURE_IDS = frozenset(f"M{i}" for i in range(1, 29))
PERIOD_PLACEHOLDERS = frozenset({"period_start", "period_end"})
FIELD_ID = re.compile(r"[a-z][a-z_]{0,63}")
FIELD_KEYS = frozenset({"id", "title", "figures", "template"})
MAP_KEYS = frozenset({"name", "fields"})
# Judgement fields: the underwriter's, never a figure's (R24).
JUDGEMENT_FIELDS = frozenset({"recommendation"})

FOR_UNDERWRITER = "For the underwriter: no figure is mapped to this field; it is left empty."
DEMO_STAMP = ("Demonstration case: this demonstrates the mechanism and is not an applicant file. "
              "It holds synthetic data or the owner's own statements.")
POLICY_TITLE = "Policy check (illustrative)"
OPS = {">=": "at least", "<=": "at most", ">": "more than", "<": "less than", "==": "exactly the limit of"}


class FieldMapError(ValueError):
    """The memo field map is malformed; no preview is built from it."""


class PreviewError(ValueError):
    """The case cannot be previewed (no statement yet); nothing was recorded."""


# --- the memo field map -----------------------------------------------------------------


@dataclass(frozen=True)
class MemoField:
    id: str
    title: str
    figures: tuple[str, ...] = ()
    template: str | None = None

    @property
    def for_underwriter(self) -> bool:
        return not self.figures


@dataclass(frozen=True)
class FieldMap:
    name: str
    source: str
    sha256: str
    fields: tuple[MemoField, ...]


def default_field_map_text() -> str:
    return resources.files("cma.memo_fields").joinpath(DEFAULT_FIELD_MAP_NAME).read_text(encoding="utf-8")


def _placeholders(template: str, where: str) -> list[str]:
    names = []
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as e:
        raise FieldMapError(f"{where}: the template's braces do not pair up ({e})") from None
    for _literal, name, spec, conversion in parsed:
        if name is None:
            continue
        if spec or conversion:
            raise FieldMapError(f"{where}: placeholder {{{name}}} takes no format; write it as {{{name}}}")
        names.append(name)
    return names


def parse_field_map(text: str, source: str) -> FieldMap:
    """Validate a memo field map, refusing it naming the first problem."""
    try:
        raw = yaml.load(text, Loader=firm._UniqueKeyLoader)
    except firm._RepeatedKey as e:
        raise FieldMapError(f"{source}: the key {e.key!r} is given more than once; keep one") from None
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        at = f" (line {mark.line + 1})" if mark is not None else ""
        raise FieldMapError(f"{source}: not valid YAML{at}") from None
    if not isinstance(raw, dict):
        raise FieldMapError(f"{source}: not a mapping with a fields list")
    if unknown := sorted(set(map(str, raw)) - MAP_KEYS):
        raise FieldMapError(f"{source}: unknown key {unknown[0]!r}; use name and fields")
    name = raw.get("name", "memo_fields")
    if not isinstance(name, str) or not name.strip():
        raise FieldMapError(f"{source}: name must be text")
    items = raw.get("fields")
    if not isinstance(items, list) or not items:
        raise FieldMapError(f"{source}: no fields")
    fields: list[MemoField] = []
    seen: set[str] = set()
    for n, item in enumerate(items, start=1):
        where = f"{source}: field {n}"
        if not isinstance(item, dict):
            raise FieldMapError(f"{where}: not a mapping")
        if unknown := sorted(set(map(str, item)) - FIELD_KEYS):
            raise FieldMapError(f"{where}: unknown key {unknown[0]!r}; use id, title, figures and template")
        fid = item.get("id")
        if not isinstance(fid, str) or not FIELD_ID.fullmatch(fid):
            raise FieldMapError(f"{where}: id must be lower-case letters and _")
        where = f"{source}: field {fid}"
        if fid in seen:
            raise FieldMapError(f"{where}: given more than once")
        seen.add(fid)
        title = item.get("title")
        if not isinstance(title, str) or not title.strip() or "\n" in title:
            raise FieldMapError(f"{where}: title must be one line of text")
        figures = item.get("figures", [])
        if figures is None:
            figures = []
        if not isinstance(figures, list) or not all(isinstance(f, str) for f in figures):
            raise FieldMapError(f"{where}: figures must be a list of figure ids")
        if bad := [f for f in figures if f not in FIGURE_IDS]:
            raise FieldMapError(f"{where}: {bad[0]!r} is not a figure id (M1 to M28)")
        if len(set(figures)) != len(figures):
            raise FieldMapError(f"{where}: a figure is listed more than once")
        template = item.get("template")
        if figures and fid in JUDGEMENT_FIELDS:
            raise FieldMapError(f"{where}: a judgement field maps no figure; it is the underwriter's (R24)")
        if not figures:
            if template is not None:
                raise FieldMapError(f"{where}: a template needs the figures it shows listed")
            fields.append(MemoField(fid, title.strip()))
            continue
        if not isinstance(template, str) or not template.strip():
            raise FieldMapError(f"{where}: the figures it shows need a template")
        used = _placeholders(template, where)
        if unknown := [u for u in used if u not in figures and u not in PERIOD_PLACEHOLDERS]:
            raise FieldMapError(f"{where}: placeholder {{{unknown[0]}}} is not one of its figures, "
                                "{period_start} or {period_end}")
        if unused := [f for f in figures if f not in used]:
            raise FieldMapError(f"{where}: {unused[0]} is listed but the template does not show it")
        fields.append(MemoField(fid, title.strip(), tuple(figures), " ".join(template.split())))
    return FieldMap(name.strip(), source, hashlib.sha256(text.encode("utf-8")).hexdigest(), tuple(fields))


def load_field_map(config: firm.FirmConfig | None = None) -> FieldMap:
    """The firm's field map when the case root has one (firm.yaml can name another), or the
    packaged one. A file firm.yaml names that is not there is refused, never replaced by the
    packaged map: the firm chose that file."""
    config = config or firm.load()
    path = config.memo_fields_path
    if config.names_path("memo_fields") or path.exists():
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise FieldMapError(f"{path}: no such file") from None
        except (OSError, UnicodeDecodeError) as e:
            raise FieldMapError(f"{path}: cannot be read ({e.__class__.__name__})") from None
        return parse_field_map(text, str(path))
    return parse_field_map(default_field_map_text(), DEFAULT_FIELD_MAP_SOURCE)


# --- section text -------------------------------------------------------------------------


def quote(m: Metric) -> str:
    """A figure as the memo quotes it, to the whole pound (R2), followed by its id."""
    return f"{m.display()} [{m.id}]"


def field_sentence(f: MemoField, metrics: list[Metric], start: str, end: str) -> str:
    by_id = {m.id: m for m in metrics}
    values = {fid: quote(by_id[fid]) for fid in f.figures}
    return f.template.format(**values, period_start=start, period_end=end)


def _limit(value: float | None, unit: str, rule_id: str) -> str:
    return f"{format_value(value, unit)} [{rule_id}]"


def rule_sentence(r: RuleResult, by_id: dict[str, Metric]) -> str:
    """One rule's line in the policy-check section: its outcome, the figure tagged [Mn] and
    each limit tagged [Rn] after a limit word, so the guard can tell a limit from a figure."""
    m = by_id[r.metric_id]
    head = f"- {r.rule_id} ({m.id} {m.name}): {r.outcome.value}."
    if m.value is None:
        return (f"{head} {m.name} cannot be computed: n/a [{m.id}]; the policy says "
                f"{r.outcome.value} when the figure is missing.")
    req = f"the rule requires {OPS.get(r.op, r.op)} {_limit(r.threshold, r.unit, r.rule_id)}"
    if r.refer_below is not None:
        req += f", referred below {_limit(r.refer_below, r.unit, r.rule_id)}"
    if r.refer_above is not None:
        req += f", referred above {_limit(r.refer_above, r.unit, r.rule_id)}"
    return f"{head} {m.name} is {quote(m)}; {req}."


@dataclass
class AccountPreview:
    account: str
    statement_sha256: str
    start: str
    end: str
    metrics: list[Metric]
    policy: PolicyResult | None  # None while the policy check is hidden (R56)
    sentences: dict[str, str]  # field id -> its sentence, for the fields figures map to
    policy_lines: list[str]
    guard: GuardResult | None = None


@dataclass
class Preview:
    case: str
    demo: bool
    field_map: FieldMap
    accounts: list[AccountPreview]
    policy_shown: bool
    inputs: dict[str, Any]
    snapshot: str
    text: str = ""
    position: int | None = None  # the case log position of the preview's entry
    fields: list[dict[str, Any]] = field(default_factory=list)

    @property
    def grounded(self) -> bool:
        return all(_grounded(a.guard) for a in self.accounts)

    def findings(self) -> list[tuple[str, str, str]]:
        """(account, the number as written or the citation, why) for every guard finding."""
        out = []
        for a in self.accounts:
            g = a.guard
            if g is None:
                continue
            out += [(a.account, f.number_text, f.reason) for f in g.unmatched]
            out += [(a.account, mid, "quoted without its id") for mid in g.missing_citations]
        return out


def _grounded(g: GuardResult | None) -> bool:
    return g is not None and not g.unmatched and not g.missing_citations


def _text(p: Preview, only: str | None = None) -> str:
    """The pack's memo-ready text: every account's sentences, or `only` that account's (what
    the guard reads against that account's figures)."""
    accounts = [a for a in p.accounts if only is None or a.account == only]
    several = len(p.accounts) > 1
    out = ["# Credit memo sections", ""]
    if p.demo:
        out += [f"> {DEMO_STAMP}", ""]
    for f in p.field_map.fields:
        out += [f"## {f.title}", ""]
        if f.for_underwriter:
            out += [f"_{FOR_UNDERWRITER}_", ""]
            continue
        for a in accounts:
            prefix = f"Account {a.account}: " if several else ""
            out += [prefix + a.sentences[f.id], ""]
    if p.policy_shown:
        out += [f"## {POLICY_TITLE}", "", policy.ILLUSTRATIVE_LABEL, ""]
        for a in accounts:
            if several:
                out += [f"### Account {a.account}", ""]
            out += [*a.policy_lines, ""]
    return "\n".join(out).rstrip() + "\n"


def _fields(p: Preview) -> list[dict[str, Any]]:
    return [{"id": f.id, "title": f.title, "figures": list(f.figures), "for_underwriter": f.for_underwriter,
             "text": {a.account: a.sentences[f.id] for a in p.accounts} if f.figures else {}}
            for f in p.field_map.fields]


# --- the snapshot -----------------------------------------------------------------------


def _file_sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def snapshot_hash(inputs: dict[str, Any]) -> str:
    blob = json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(blob).hexdigest()


def snapshot_inputs(folder: Path, entries, figures: dict[str, overrides.CaseFigures], profile: dict[str, Any],
                    config: firm.FirmConfig, field_map: FieldMap, thresholds_sha256: str,
                    policy_sha256: str) -> dict[str, Any]:
    """Every input R22 names, as hashes and counts only: never a name or a description."""
    from . import __version__

    ruleset = active_ruleset()
    records = overrides._read_records(folder, entries)
    return {
        "statements": {a: f.statement_sha256 for a, f in figures.items()},
        "ruleset": {"id": ruleset.id, "sha256": ruleset.sha256},
        "firm_rules_sha256": _file_sha(config.firm_rules_path),
        "profile_sha256": store.profile_sha256(profile),
        "override_log": {"position": len(records), "sha256": _file_sha(Path(folder) / overrides.OVERRIDES)},
        "thresholds_sha256": thresholds_sha256,
        "memo_field_map_sha256": field_map.sha256,
        "policy_sha256": policy_sha256,
        "show_policy_verdict": bool(config.show_policy_verdict),
        "package_version": __version__,
    }


# --- the preview --------------------------------------------------------------------------


def _build(folder: Path, case_id: str, entries) -> Preview:
    from .review.summary import case_thresholds

    config = firm.load()
    field_map = load_field_map(config)
    profile = store.load_profile(folder)
    every = overrides.case_figures(folder)  # R3: refused when the buckets do not reconcile
    figures = {a: f for a, f in every.items() if f.statement.transactions}
    if not figures:
        raise PreviewError("no statement has been imported into this case yet; nothing to preview")
    thresholds = case_thresholds(config)
    rules = policy.load_policy()  # the packaged policy: the workspace and the pack evaluate only it
    shown = bool(config.show_policy_verdict)
    accounts = []
    for a, f in figures.items():
        start, end = f.statement.start.isoformat(), f.statement.end.isoformat()
        result = policy.evaluate(f.metrics, rules) if shown else None
        by_id = {m.id: m for m in f.metrics}
        accounts.append(AccountPreview(
            a, f.statement_sha256, start, end, f.metrics, result,
            {mf.id: field_sentence(mf, f.metrics, start, end) for mf in field_map.fields if mf.figures},
            [rule_sentence(r, by_id) for r in result.rules] if result else []))
        if result:
            accounts[-1].policy_lines.insert(0, f"Overall outcome: {result.overall.value}.")
    inputs = snapshot_inputs(folder, entries, every, profile, config, field_map, thresholds.sha256,
                             rules["_sha256"])
    p = Preview(case_id, bool(profile.get("demo")), field_map, accounts, shown, inputs, snapshot_hash(inputs))
    for a in accounts:  # R28, P14: each account's text against its own figures and policy result
        a.guard = guard.check(_text(p, a.account), a.metrics, a.policy)
    p.text = _text(p)
    p.fields = _fields(p)
    return p


def build_preview(folder: Path, case_id: str) -> Preview:
    """Build the case's preview and record it in the case log; write nothing else."""
    folder = Path(folder)
    with store._recorded(folder, EVENT) as save:
        p = _build(folder, case_id, save.entries)
        p.position = len(save.entries) + 1
        save.data = {"snapshot": p.snapshot, "inputs": p.inputs, "grounded": p.grounded,
                     "policy_check": "shown" if p.policy_shown else "hidden"}
    return p
