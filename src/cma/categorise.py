"""Rule-based transaction categoriser.

Deterministic on purpose. Rules are ordered; the first match whose direction also
matches wins. Direction matters: "HOLLOWAY BRIDGE FINANCE LOAN" is a repayment when
money leaves and a drawdown when it arrives, and a drawdown is financing, not revenue
or debt service. The model never overrides a rule match.

The rule table is data, not code: src/cma/rules/<id>.yaml, validated on load like the
policy (a key repeated in a mapping is refused), with an id and the SHA-256 of the
file's bytes. CMA_RULESET chooses the table (default v1). v1 is the table as it was
when it lived here, frozen: its hash is pinned in FROZEN_SHA256 and any other bytes are
refused, so CMA_RULESET=v1 always reproduces those categories. v2 adds the maintained
lists of R4 (acquirers, gateways, marketplaces, delivery platforms, lenders and merchant
cash advance providers, own pots and savings, cash paid in, cash-withdrawal wording,
HMRC, refunds, explained non-trading money in). The matching passes below (reversals,
round trips) stay in code.

Precedence, first match wins: a per-line override; the table's protected rules (returned
DD, HMRC, lenders, director, capital, shareholder); the case profile's names (directors
and shareholders, own accounts); firm rules; the rest of the table; the default (a debit
is supplier, a credit is OTHER, unexplained). Every line records the rule that set its
category in Transaction.rule_id: "<ruleset>:<rule id>", "<ruleset>:default",
"<ruleset>:round_trip", "profile:director", "profile:own_account", "firm:<rule id>" or
the override's own reference.

Revenue is claimed narrowly. A card or gateway settlement is trading income. A bank
transfer in is only counted as a customer payment when it carries an invoice
reference (INV1234, INVOICE 1234); anything else that arrives by transfer (the owner
putting money back, a capital injection, a VAT repayment, a transfer from savings) is
OTHER and shows up in M16, non-trading money in, for a person to look at. That costs
some real revenue on statements without invoice references, but the alternative is
that money the owner moves in and out counts as trading income and lifts debt cover.

A returned direct debit ("UNPAID DD ...") is linked to the debit it reverses, so a
bounced and re-presented payment counts once, not twice. An invoice-referenced credit
from someone the applicant also pays by transfer is not revenue (round trip).
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Literal

import yaml

from .models import Category, Statement, Transaction

Direction = Literal["in", "out", "any"]

INVOICE_REF = re.compile(r"\bINV(?:OICE)?\s*[-#:]?\s*\d+")

RULESET_ENV = "CMA_RULESET"
RULESETS = ("v1", "v2")
DEFAULT_RULESET = "v1"
DIRECTIONS = ("in", "out", "any")
TABLE_KEYS = {"id", "frozen", "rules"}
RULE_KEYS = {"id", "direction", "category"}  # required, plus a pattern or names
MATCH_KEYS = {"pattern", "names"}
OPTIONAL_KEYS = {"unless", "protected"}
ALL_RULE_KEYS = RULE_KEYS | MATCH_KEYS | OPTIONAL_KEYS
# A frozen table's SHA-256, pinned here. A file that validates but differs from these
# bytes (a rule recategorised, CRLF line ends, frozen switched off) is refused, so the id
# v1 in an evidence record always means this exact table.
FROZEN_SHA256 = {"v1": "7573366854a668793ca20b3ecfaad91d406ea3018a71d524ea3324cc4ff76630"}


class RulesetError(ValueError):
    """The rule table is unknown or malformed; nothing should be categorised with it."""


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern[str]
    direction: Direction
    category: Category
    protected: bool = False  # wins over profile names and firm rules (R21)
    unless: re.Pattern[str] | None = None  # the rule does not match when this does


@dataclass(frozen=True)
class Ruleset:
    id: str
    sha256: str
    rules: tuple[Rule, ...]

    @property
    def protected(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.protected)

    @property
    def unprotected(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if not r.protected)


def names_pattern(names: Sequence[str]) -> str:
    """A pattern matching any of `names` as whole words in an upper-cased description,
    with any run of spaces between words. A name's other characters (".", "-", "&",
    digits) are matched literally."""
    alts = sorted({r"\s+".join(re.escape(w) for w in n.upper().split()) for n in names}, key=lambda a: (-len(a), a))
    return r"(?<![A-Z0-9])(?:" + "|".join(alts) + r")(?![A-Z0-9])"


def _compile_problem(pattern: str) -> str | None:
    # re.error is not the only way a pattern fails to compile: a repeat count past
    # the engine's limit raises OverflowError, deep nesting RecursionError.
    try:
        re.compile(pattern)
    except (re.error, OverflowError, RecursionError, MemoryError) as e:
        return f"{e.__class__.__name__}: {e}"
    return None


def validate_ruleset(data: object, expected_id: str, source: str = "rules") -> None:
    if not isinstance(data, dict):
        raise RulesetError(f"{source}: not a mapping")
    problems: list[str] = []
    if "id" not in data:
        problems.append("missing id")
    elif data["id"] != expected_id:
        problems.append(f"id {data['id']!r} does not match the file ({expected_id!r})")
    if not isinstance(data.get("frozen"), bool):
        problems.append("frozen must be true or false")
    for key in sorted(set(data) - TABLE_KEYS, key=repr):
        problems.append(f"unknown key {key!r}")
    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        raise RulesetError(f"{source}: " + "; ".join([*problems, "no rules"]))
    categories = {c.value for c in Category}
    seen: set[str] = set()
    for i, r in enumerate(rules, start=1):
        where = f"rule {i} ({r.get('id', '?') if isinstance(r, dict) else '?'})"
        if not isinstance(r, dict):
            problems.append(f"{where}: not a mapping")
            continue
        for key in sorted(RULE_KEYS - set(r)):
            problems.append(f"{where}: missing {key}")
        if not MATCH_KEYS & set(r):
            problems.append(f"{where}: missing pattern (or names)")
        for key in sorted(set(r) - ALL_RULE_KEYS, key=repr):
            problems.append(f"{where}: unknown key {key!r}")
        # Every field is a string. Checking the type first means a list, a mapping, a
        # number or null is reported here, not raised later as a TypeError.
        for key in sorted((RULE_KEYS | {"pattern", "unless"}) & set(r)):
            if not isinstance(r[key], str) or not r[key]:
                problems.append(f"{where}: {key} {r[key]!r} is not a non-empty string")
        if "names" in r:
            names = r["names"]
            if not isinstance(names, list) or not names or not all(isinstance(n, str) and n.strip() for n in names):
                problems.append(f"{where}: names must be a non-empty list of non-empty strings")
            elif len({" ".join(n.upper().split()) for n in names}) != len(names):
                problems.append(f"{where}: a name is listed twice")
        if "protected" in r:
            if not isinstance(r["protected"], bool):
                problems.append(f"{where}: protected must be true or false")
            elif r["protected"] and any(not isinstance(p, dict) or not p.get("protected") for p in rules[:i - 1]):
                # One ordered pass and the precedence agree only if the protected rules come first.
                problems.append(f"{where}: a protected rule must come before every rule that is not")
        if isinstance(r.get("id"), str):
            if r["id"] in seen:
                problems.append(f"{where}: duplicate id")
            seen.add(r["id"])
        if isinstance(r.get("direction"), str) and r["direction"] not in DIRECTIONS:
            problems.append(f"{where}: direction {r['direction']!r} is not one of {list(DIRECTIONS)}")
        if isinstance(r.get("category"), str) and r["category"] not in categories:
            problems.append(f"{where}: category {r['category']!r} is not a category")
        for key in ("pattern", "unless"):
            if isinstance(r.get(key), str) and r[key]:
                problem = _compile_problem(r[key])
                if problem:
                    problems.append(f"{where}: {key} does not compile ({problem})")
    if problems:
        raise RulesetError(f"{source}: " + "; ".join(problems))


def _rule_pattern(r: dict) -> re.Pattern[str]:
    parts = [p for p in (r.get("pattern"), names_pattern(r["names"]) if "names" in r else None) if p]
    return re.compile(parts[0] if len(parts) == 1 else "|".join(f"(?:{p})" for p in parts))


def ruleset_from_data(data: object, expected_id: str, source: str, sha256: str) -> Ruleset:
    validate_ruleset(data, expected_id, source)
    assert isinstance(data, dict)
    return Ruleset(
        id=data["id"],
        sha256=sha256,
        rules=tuple(
            Rule(r["id"], _rule_pattern(r), r["direction"], Category(r["category"]),
                 protected=r.get("protected", False),
                 unless=re.compile(r["unless"]) if "unless" in r else None)
            for r in data["rules"]
        ),
    )


class _UniqueKeyLoader(yaml.SafeLoader):
    """safe_load keeps the last of two equal keys, so a second `category:` inside a rule
    would silently change it with nothing left for the validator to see. Refuse instead."""

    def construct_mapping(self, node, deep=False):
        seen: set = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
            except TypeError:  # an unhashable key; the base class reports it
                continue
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    f"found duplicate key {key!r}", key_node.start_mark)
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _table_bytes(ruleset_id: str) -> bytes:
    return resources.files("cma.rules").joinpath(f"{ruleset_id}.yaml").read_bytes()


@lru_cache
def load_ruleset(ruleset_id: str) -> Ruleset:
    """A packaged table by id. Only ids in RULESETS load, so an id never names a path."""
    if ruleset_id not in RULESETS:
        raise RulesetError(f"unknown ruleset {ruleset_id!r}; known: {', '.join(RULESETS)}")
    source = f"cma/rules/{ruleset_id}.yaml"
    try:
        raw = _table_bytes(ruleset_id)
    except (OSError, ImportError) as e:  # ImportError: the cma.rules package itself is gone
        raise RulesetError(f"{source}: cannot be read from the installed package ({e.__class__.__name__})") from e
    try:
        data = yaml.load(raw.decode("utf-8"), Loader=_UniqueKeyLoader)
    except UnicodeDecodeError as e:
        raise RulesetError(f"{source}: not UTF-8 text") from e
    except yaml.YAMLError as e:
        problem = getattr(e, "problem", None) or e.__class__.__name__
        raise RulesetError(f"{source}: not valid YAML ({problem})") from e
    except RecursionError as e:
        raise RulesetError(f"{source}: not valid YAML (nested too deeply)") from e
    except (ValueError, TypeError, OverflowError) as e:  # a scalar the loader cannot build, e.g. 2026-02-30
        raise RulesetError(f"{source}: not valid YAML ({e.__class__.__name__} building a value)") from e
    # The hash is of the file's bytes as installed, so it matches sha256sum on that file.
    digest = hashlib.sha256(raw).hexdigest()
    ruleset = ruleset_from_data(data, ruleset_id, source, digest)
    pinned = FROZEN_SHA256.get(ruleset_id)
    if pinned is not None and digest != pinned:
        raise RulesetError(f"{source}: frozen table {ruleset_id} has been changed "
                           f"(sha256 {digest}, pinned {pinned}); reinstall the package")
    if data["frozen"] and pinned is None:
        raise RulesetError(f"{source}: marked frozen but no hash is pinned for {ruleset_id}")
    return ruleset


def active_ruleset() -> Ruleset:
    """The table CMA_RULESET names, or the default when it is unset."""
    chosen = os.environ.get(RULESET_ENV) or DEFAULT_RULESET
    try:
        return load_ruleset(chosen)
    except RulesetError as e:
        raise RulesetError(f"{RULESET_ENV}={chosen!r}: {e}") from e


def _direction_ok(direction: Direction, amount: float) -> bool:
    if direction == "any":
        return True
    return amount > 0 if direction == "in" else amount < 0


def rule_matches(rule: Rule, description: str, amount: float) -> bool:
    """`description` upper-cased."""
    return (_direction_ok(rule.direction, amount) and rule.pattern.search(description) is not None
            and (rule.unless is None or rule.unless.search(description) is None))


@dataclass(frozen=True)
class LineOverride:
    """A person's category for one line, with the reference recorded as its rule id."""

    category: Category
    ref: str


@dataclass(frozen=True)
class ProfileNames:
    """Names from the case profile: directors and shareholders, and the applicant's other
    accounts. Matched as whole words in the upper-cased description."""

    directors: tuple[str, ...] = ()
    own_accounts: tuple[str, ...] = ()


def _names_in(names: Sequence[str], description: str) -> bool:
    names = [n for n in names if n.strip()]
    return bool(names) and re.search(names_pattern(names), description) is not None


def classify(
    t: Transaction,
    ruleset: Ruleset | None = None,
    *,
    override: LineOverride | None = None,
    profile: ProfileNames | None = None,
    firm_rules: Sequence[Rule] = (),
) -> tuple[Category, str]:
    """The category of one line and the id of the rule that set it, in precedence order."""
    ruleset = ruleset or active_ruleset()
    if override is not None:
        return override.category, override.ref
    desc = t.description.upper()
    for rule in ruleset.protected:
        if rule_matches(rule, desc, t.amount):
            return rule.category, f"{ruleset.id}:{rule.id}"
    if profile is not None:
        if t.amount > 0 and _names_in(profile.directors, desc):
            return Category.DIRECTOR_FUNDING, "profile:director"
        if t.amount != 0 and _names_in(profile.own_accounts, desc):
            return Category.OWN_ACCOUNT_TRANSFER, "profile:own_account"
    for rule in firm_rules:
        if rule_matches(rule, desc, t.amount):
            return rule.category, f"firm:{rule.id}"
    for rule in ruleset.unprotected:
        if rule_matches(rule, desc, t.amount):
            return rule.category, f"{ruleset.id}:{rule.id}"
    return (Category.SUPPLIER if t.amount < 0 else Category.OTHER), f"{ruleset.id}:default"


def categorise_one(t: Transaction, ruleset: Ruleset | None = None) -> Category:
    return classify(t, ruleset)[0]


RAIL_PREFIX = re.compile(
    r"^(?:UNPAID DD|FPS CREDIT|FPS DEBIT|BACS CREDIT|BACS DEBIT|DD REFUND|CARD REFUND|FPS|BACS|TFR|BGC|DD|SO|CARD)\s+"
)
REFERENCE_TOKEN = re.compile(rf"{INVOICE_REF.pattern}|\b\w*\d\w*\b")


def counterparty(description: str) -> str:
    """Who is on the other side of a line: the description without the payment rail,
    a trailing " DD", invoice numbers or other references with digits in them. One
    normaliser for reversal matching, round-trip detection and concentration (M15)."""
    d = RAIL_PREFIX.sub("", description.upper())
    d = re.sub(r"\s+DD$", "", d)
    d = REFERENCE_TOKEN.sub(" ", d)
    return " ".join(d.split()) or description.upper().strip()


def link_reversals(statement: Statement) -> Statement:
    """Point each returned DD credit at the most recent unreversed debit it undoes.

    Same amount, same payee, on or before the reversal date. A returned DD with no
    amount (some banks print a zero-value notice) or no matching debit stays unlinked:
    it still counts in M7 but changes no sum.
    """
    taken: set[str] = set()
    txns = statement.transactions
    for i, r in enumerate(txns):
        if r.category != Category.RETURNED_DD or r.amount <= 0:
            continue
        payee = counterparty(r.description)
        for o in reversed(txns[:i]):
            if (
                o.id not in taken
                and o.amount < 0
                and o.category not in (Category.RETURNED_DD, None)
                and abs(o.amount + r.amount) < 0.005
                and counterparty(o.description) == payee
            ):
                r.reverses = o.id
                taken.add(o.id)
                break
    return statement


OUTGOING_TRANSFER = re.compile(r"^(?:FPS DEBIT|BACS DEBIT|TFR)\b")


def unmark_round_trips(statement: Statement, ruleset_id: str | None = None) -> Statement:
    """A "customer" the applicant also sends transfers to is not counted as revenue.

    Money sent out by transfer and received back with an invoice reference is the
    cheapest way to manufacture revenue. Such credits become OTHER (and so show in M16).
    This also catches a genuine customer who is paid a refund by transfer: the cost is
    understated revenue, which a person can add back; the alternative is gameable.

    Only a category the built-in table set is undone: a person's override, a profile name
    or a firm rule ranks above the table and is left alone.
    """
    paid_out = {counterparty(t.description) for t in statement.transactions
                if t.amount < 0 and OUTGOING_TRANSFER.search(t.description.upper())}
    for t in statement.transactions:
        set_by_table = ruleset_id is None or (t.rule_id or "").startswith(f"{ruleset_id}:")
        if t.category == Category.REVENUE_TRANSFER and counterparty(t.description) in paid_out and set_by_table:
            t.category = Category.OTHER
            if ruleset_id is not None:
                t.rule_id = f"{ruleset_id}:round_trip"
    return statement


def categorise(
    statement: Statement,
    ruleset: Ruleset | None = None,
    *,
    overrides: Mapping[str, LineOverride] | None = None,
    profile: ProfileNames | None = None,
    firm_rules: Sequence[Rule] = (),
) -> Statement:
    ruleset = ruleset or active_ruleset()
    overrides = overrides or {}
    for t in statement.transactions:
        t.category, t.rule_id = classify(t, ruleset, override=overrides.get(t.id), profile=profile,
                                         firm_rules=firm_rules)
        t.reverses = None
    return link_reversals(unmark_round_trips(statement, ruleset.id))
