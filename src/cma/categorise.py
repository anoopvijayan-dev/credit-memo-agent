"""Rule-based transaction categoriser.

Deterministic on purpose. Rules are ordered; the first match whose direction also
matches wins. Direction matters: "HOLLOWAY BRIDGE FINANCE LOAN" is a repayment when
money leaves and a drawdown when it arrives, and a drawdown is financing, not revenue
or debt service. The model never overrides a rule match.

The rule table is data, not code: src/cma/rules/<id>.yaml, validated on load like the
policy (a key repeated in a mapping is refused), with an id and the SHA-256 of the
file's bytes. CMA_RULESET chooses the table (default v1). v1 is the table as it was
when it lived here, frozen, so CMA_RULESET=v1 always reproduces those categories. The
matching passes below (reversals, round trips) stay in code.

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
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Literal

import yaml

from .models import Category, Statement, Transaction

Direction = Literal["in", "out", "any"]

INVOICE_REF = re.compile(r"\bINV(?:OICE)?\s*[-#:]?\s*\d+")

RULESET_ENV = "CMA_RULESET"
RULESETS = ("v1",)
DEFAULT_RULESET = "v1"
DIRECTIONS = ("in", "out", "any")
TABLE_KEYS = {"id", "frozen", "rules"}
RULE_KEYS = {"id", "pattern", "direction", "category"}


class RulesetError(ValueError):
    """The rule table is unknown or malformed; nothing should be categorised with it."""


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern[str]
    direction: Direction
    category: Category


@dataclass(frozen=True)
class Ruleset:
    id: str
    sha256: str
    rules: tuple[Rule, ...]


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
        for key in sorted(set(r) - RULE_KEYS, key=repr):
            problems.append(f"{where}: unknown key {key!r}")
        # Every field is a string. Checking the type first means a list, a mapping, a
        # number or null is reported here, not raised later as a TypeError.
        for key in sorted(RULE_KEYS & set(r)):
            if not isinstance(r[key], str) or not r[key]:
                problems.append(f"{where}: {key} {r[key]!r} is not a non-empty string")
        if isinstance(r.get("id"), str):
            if r["id"] in seen:
                problems.append(f"{where}: duplicate id")
            seen.add(r["id"])
        if isinstance(r.get("direction"), str) and r["direction"] not in DIRECTIONS:
            problems.append(f"{where}: direction {r['direction']!r} is not one of {list(DIRECTIONS)}")
        if isinstance(r.get("category"), str) and r["category"] not in categories:
            problems.append(f"{where}: category {r['category']!r} is not a category")
        if isinstance(r.get("pattern"), str) and r["pattern"]:
            try:
                re.compile(r["pattern"])
            except re.error as e:
                problems.append(f"{where}: pattern does not compile ({e})")
    if problems:
        raise RulesetError(f"{source}: " + "; ".join(problems))


def ruleset_from_data(data: object, expected_id: str, source: str, sha256: str) -> Ruleset:
    validate_ruleset(data, expected_id, source)
    assert isinstance(data, dict)
    return Ruleset(
        id=data["id"],
        sha256=sha256,
        rules=tuple(
            Rule(r["id"], re.compile(r["pattern"]), r["direction"], Category(r["category"]))
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
    except OSError as e:
        raise RulesetError(f"{source}: cannot be read from the installed package ({e.__class__.__name__})") from e
    try:
        data = yaml.load(raw.decode("utf-8"), Loader=_UniqueKeyLoader)
    except UnicodeDecodeError as e:
        raise RulesetError(f"{source}: not UTF-8 text") from e
    except yaml.YAMLError as e:
        problem = getattr(e, "problem", None) or e.__class__.__name__
        raise RulesetError(f"{source}: not valid YAML ({problem})") from e
    # The hash is of the file's bytes as installed, so it matches sha256sum on that file.
    return ruleset_from_data(data, ruleset_id, source, hashlib.sha256(raw).hexdigest())


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


def categorise_one(t: Transaction, ruleset: Ruleset | None = None) -> Category:
    desc = t.description.upper()
    for rule in (ruleset or active_ruleset()).rules:
        if rule.pattern.search(desc) and _direction_ok(rule.direction, t.amount):
            return rule.category
    if t.amount < 0:
        return Category.SUPPLIER
    return Category.OTHER


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


def unmark_round_trips(statement: Statement) -> Statement:
    """A "customer" the applicant also sends transfers to is not counted as revenue.

    Money sent out by transfer and received back with an invoice reference is the
    cheapest way to manufacture revenue. Such credits become OTHER (and so show in M16).
    This also catches a genuine customer who is paid a refund by transfer: the cost is
    understated revenue, which a person can add back; the alternative is gameable.
    """
    paid_out = {counterparty(t.description) for t in statement.transactions
                if t.amount < 0 and OUTGOING_TRANSFER.search(t.description.upper())}
    for t in statement.transactions:
        if t.category == Category.REVENUE_TRANSFER and counterparty(t.description) in paid_out:
            t.category = Category.OTHER
    return statement


def categorise(statement: Statement, ruleset: Ruleset | None = None) -> Statement:
    ruleset = ruleset or active_ruleset()
    for t in statement.transactions:
        t.category = categorise_one(t, ruleset)
        t.reverses = None
    return link_reversals(unmark_round_trips(statement))
