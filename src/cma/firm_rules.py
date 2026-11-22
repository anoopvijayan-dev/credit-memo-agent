"""The firm's own rules: firm_rules.yaml at the case root (or where firm.yaml's paths name it).

    rules:
      - id: acme_trust                 # letters, digits, _ . - ; the line's rule is firm:<id>
        pattern: ACME TRUST            # the counterparty, matched as a listed name is
        direction: in                  # in (credits), out (debits) or any
        category: other_explained_in   # a category the direction can take (R18)
        by: Pat Underwriter            # who promoted it (R20)
        at: 2026-11-20T10:00:00Z       # when, UTC
        reason: trust distributions    # why

A pattern is a counterparty's name, not a regular expression: it matches as a name on the
maintained lists does, whole words only, whatever spaces, hyphens, dots or dashes stand
between its characters (design C1, whole words and separators). So a firm rule can carry no
exception, anchor or lookaround that hides another list's wording (R4).

A missing file at its default place means no firm rules; a file firm.yaml names itself must
exist. A malformed file (not UTF-8, not YAML, a key unknown, missing or given twice, a rule id
given twice, a value of the wrong kind, a category its direction cannot take) is refused with
FirmRulesError naming the line of the file the problem is on, so no case opens with silently
different categories. FirmRulesError is a RulesetError, so every caller that refuses a bad rule
table refuses a bad firm rules file the same way.

Firm rules are applied by the categoriser at design C1's precedence step 6: below an override,
a protected verdict, an own-account pairing, an ambiguous verdict and a case-profile name, and
above the table's unprotected rules. A line that a protected built-in pattern matches never
takes a firm rule, whatever the file says (R21, P16; categorise.classify).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .categorise import (
    _MONEY_IN_ONLY,
    _SEPARATOR_RUN,
    RESERVED_RULE_IDS,
    Rule,
    RulesetError,
    _fold,
    names_pattern,
)
from .models import CREDIT_BUCKET, Category

FILE_KEYS = ("rules",)
RULE_KEYS = ("id", "pattern", "direction", "category", "by", "at", "reason")
DIRECTIONS = ("in", "out", "any")
_ID = re.compile(r"[A-Za-z0-9_.-]+")
MAX_TEXT = 1000


class FirmRulesError(RulesetError):
    """firm_rules.yaml is malformed; no line should be categorised with it."""


@dataclass(frozen=True)
class FirmRules:
    source: str
    sha256: str | None  # of the file's bytes; None when there is no file
    rules: tuple[Rule, ...]
    records: tuple[dict[str, Any], ...]  # each rule as the file gives it, for showing and promotion


NONE = FirmRules(source="", sha256=None, rules=(), records=())


def _categories_for(direction: str) -> frozenset[Category]:
    """The categories a rule for `direction` may give: a credit's has a money-in bucket, a
    debit's is never one only money in has, and `any` matches both (overrides.valid_categories)."""
    credit = frozenset(CREDIT_BUCKET)
    debit = frozenset(c for c in Category if c not in _MONEY_IN_ONLY)
    return {"in": credit, "out": debit, "any": credit & debit}[direction]


def parse(text: str, source: str) -> FirmRules:
    """Validate a firm rules file's text, refusing it naming the line of the first problem."""
    def refuse(line: int, problem: str):
        raise FirmRulesError(f"{source}: line {line}: {problem}")

    def line(node) -> int:
        return node.start_mark.line + 1

    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.MarkedYAMLError as e:
        mark = e.problem_mark or e.context_mark
        refuse(min(mark.line + 1, max(1, len(text.splitlines()))) if mark else 1,
               f"not valid YAML ({e.problem or e.__class__.__name__})")
    except yaml.YAMLError as e:
        refuse(1, f"not valid YAML ({e.__class__.__name__})")
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if root is None:
        return FirmRules(source=source, sha256=sha, rules=(), records=())
    if not isinstance(root, yaml.MappingNode):
        refuse(line(root), "the file must be a mapping with one key, rules")
    top = _mapping(root, FILE_KEYS, "key", refuse, line)
    if "rules" not in top:
        refuse(line(root), "missing rules")
    rules_node = top["rules"][1]
    if not isinstance(rules_node, yaml.SequenceNode):
        refuse(line(rules_node), "rules must be a list of rules")
    built: list[Rule] = []
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in rules_node.value:
        if not isinstance(node, yaml.MappingNode):
            refuse(line(node), "each rule must be a mapping of " + ", ".join(RULE_KEYS))
        given = _mapping(node, RULE_KEYS, "key of a rule", refuse, line)
        for key in RULE_KEYS:
            if key not in given:
                refuse(line(node), f"a rule is missing {key}")
        values: dict[str, Any] = {}
        for key in RULE_KEYS:
            _, v = given[key]
            if key == "at" and isinstance(v, yaml.ScalarNode) and v.tag.endswith(":timestamp"):
                stamp = yaml.safe_load(yaml.serialize(v))  # an unquoted YAML timestamp
                values[key] = stamp.isoformat() if isinstance(stamp, datetime) else str(stamp)
                continue
            if not isinstance(v, yaml.ScalarNode) or not v.tag.endswith(":str"):
                refuse(line(v), f"{key} must be text")
            text_value = v.value.strip()
            if not any(ch.isalnum() for ch in text_value):
                refuse(line(v), f"{key} must have at least one letter or digit")
            if len(text_value) > MAX_TEXT:
                refuse(line(v), f"{key} is longer than {MAX_TEXT} characters")
            values[key] = text_value
        rid = values["id"]
        if not _ID.fullmatch(rid):
            refuse(line(given["id"][1]), "id may hold only letters, digits, _, . and -")
        if rid in RESERVED_RULE_IDS:
            refuse(line(given["id"][1]), f"id {rid} is reserved for the categoriser's own provenance")
        if rid in seen:
            refuse(line(given["id"][1]), f"rule {rid} is given more than once")
        seen.add(rid)
        if not _SEPARATOR_RUN.sub("", _fold(values["pattern"])):
            refuse(line(given["pattern"][1]), "pattern must have at least one letter or digit")
        direction = values["direction"]
        if direction not in DIRECTIONS:
            refuse(line(given["direction"][1]), f"direction must be one of {', '.join(DIRECTIONS)}")
        try:
            category = Category(values["category"])
        except ValueError:
            refuse(line(given["category"][1]), f"unknown category {values['category']!r}")
        if category not in _categories_for(direction):
            side = "for money in" if category in _MONEY_IN_ONLY else "for money out"
            refuse(line(given["category"][1]),
                   f"category {category.value} is {side}, so a rule for direction {direction} cannot give it")
        built.append(Rule(rid, re.compile(names_pattern([values["pattern"]])), direction, category))
        records.append(values)
    return FirmRules(source=source, sha256=sha, rules=tuple(built), records=tuple(records))


def _mapping(node, allowed: tuple[str, ...], what: str, refuse, line) -> dict[str, tuple]:
    out: dict[str, tuple] = {}
    for k, v in node.value:
        if not isinstance(k, yaml.ScalarNode):
            refuse(line(k), f"a {what} must be a name")
        if k.value in out:
            refuse(line(k), f"{k.value} is given more than once")
        if k.value not in allowed:
            refuse(line(k), f"unknown {what} {k.value!r}; expected one of {', '.join(allowed)}")
        out[k.value] = (k, v)
    return out


_MEMO: dict[tuple[str, str], FirmRules] = {}


def read(path: Path, *, required: bool) -> FirmRules:
    """The rules of the file at `path`, validated; no rules when it is absent and not required."""
    source = str(path)
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError as e:
        if not required:
            return NONE
        raise FirmRulesError(f"{source}: no such file (firm.yaml names it)") from e
    except OSError as e:
        raise FirmRulesError(f"{source}: cannot be read ({e.__class__.__name__})") from e
    key = (source, hashlib.sha256(data).hexdigest())
    got = _MEMO.get(key)
    if got is None:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise FirmRulesError(f"{source}: line 1: not UTF-8") from e
        got = parse(text, source)
        if len(_MEMO) > 16:
            _MEMO.clear()
        _MEMO[key] = got
    return got


def for_case(config=None) -> FirmRules:
    """The firm rules every case is categorised with now: the file firm.yaml names, or
    firm_rules.yaml at the case root, read as it stands (R20: applied to every case
    categorised afterwards)."""
    from . import firm

    config = config or firm.load()
    return read(config.firm_rules_path, required=config.names_path("firm_rules"))
