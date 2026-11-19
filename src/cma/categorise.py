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

A v2 credit gets a verdict (design C1 revision 4, R4): every matching rule from both
stages is collected, and a match inside a longer match from another rule counts only as
the longer one. The bank's own returned-payment code settles the line alone. A payment
rail with cash paid-in wording is ambiguous, whatever other cue matches. Otherwise
line-type cues decide when any cue with a category matches, and the name lists decide
when none does. Rules in one money-in bucket agree; rules in two or more make the credit
ambiguous, and its candidates are every rule it matched in either stage, the rail
included. Inside the bucket decided, the table's declared category order picks the
category (cash deposit, then customer transfer, then card settlement; returned payment,
then refund), and the first deciding rule in table order carrying it is recorded. Neither
bucket nor category depends on the order of rules within a stage.

Words match whole in any script: a listed name, and a name from the case profile, matches
only where the character on each side is not a letter or a digit (CAFÉSTRIPE does not
contain STRIPE). Descriptions are compared in Unicode NFC form, so a decomposed accent
counts as part of its letter.

Precedence, highest first: a per-line override; a protected verdict (the line-type cues,
lender, director, capital-introduced, shareholder and generic loan wording); an ambiguous
verdict, which leaves the credit OTHER (unexplained) with every candidate recorded, a
matching profile name among them (D78: the wording cannot tell the payer from a reference
naming someone else); the case profile's names (directors and shareholders, own
accounts); firm rules; an unprotected verdict; the default (a debit is supplier, a credit
is OTHER, unexplained).
Debits, and every line under the frozen v1 table, keep first-match order: the protected
rules, the profile, firm rules, the rest of the table. Every line records the rule that
set its category in Transaction.rule_id: "<ruleset>:<rule id>", "<ruleset>:default",
"<ruleset>:round_trip", "<ruleset>:conflict:<id>+<id>...", "profile:director",
"profile:own_account", "firm:<rule id>" or the override's own reference.

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

import functools
import hashlib
import os
import re
import re._parser as _sre_parse  # the parser re uses; read only, to find back-references
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Literal

import yaml

from .models import CREDIT_BUCKET, Category, Statement, Transaction

Direction = Literal["in", "out", "any"]

INVOICE_REF = re.compile(r"\bINV(?:OICE)?\s*[-#:]?\s*\d+")

RULESET_ENV = "CMA_RULESET"
RULESETS = ("v1", "v2")
DEFAULT_RULESET = "v1"
DIRECTIONS = ("in", "out", "any")
TABLE_KEYS = {"id", "frozen", "rules", "category_order"}
RULE_KEYS = {"id", "direction", "category"}  # required, plus a pattern or names
# A line-type rule may have no category: a cue that only says how the money moved (the
# payment rail). It never sets a category itself.
CUE_ONLY_KEYS = RULE_KEYS - {"category"}
MATCH_KEYS = {"pattern", "names"}
OPTIONAL_KEYS = {"unless", "requires", "protected", "stage", "bank_code"}
# Stages of a table that is not frozen: explicit line-type wording first, then the name lists.
STAGES = ("line_type", "names")
ALL_RULE_KEYS = RULE_KEYS | MATCH_KEYS | OPTIONAL_KEYS
# The bank's own returned-payment codes, printed at the start of the line: the only wording
# that settles a credit's line type alone (bank_code). Kept in code, reviewed like code, so
# a table edit cannot grant the exception to payer wording.
BANK_RETURN_CODES = frozenset({r"^UNPAID DD\b"})
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
    category: Category | None  # None only for a line-type cue with no bucket (the payment rail)
    protected: bool = False  # wins over profile names and firm rules (R21)
    unless: re.Pattern[str] | None = None  # the rule does not match when this does
    requires: re.Pattern[str] | None = None  # the rule matches only when this does too
    stage: str | None = None  # "line_type" or "names" (tables that are not frozen)
    bank_code: bool = False  # the bank's own code: settles a credit's line type alone (returned_dd)
    # Each part (own pattern, names) inside a lookahead, to find its matches at every start
    # (_spans); kept apart so a part that matches first never hides a longer one.
    span_patterns: tuple[re.Pattern[str], ...] = field(default=(), compare=False, repr=False)


@dataclass(frozen=True)
class Ruleset:
    id: str
    sha256: str
    rules: tuple[Rule, ...]
    frozen: bool = False  # a frozen table (v1) keeps plain first-match order, to reproduce its history
    # Inside one money-in bucket, the category a credit takes when its deciding rules agree
    # on the bucket but carry different categories: the first in this order (design C1 step 6).
    category_order: tuple[Category, ...] = ()

    @property
    def protected(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.protected)

    @property
    def unprotected(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if not r.protected)


def _unicode_classes() -> tuple[str, str]:
    """Character-class bodies, from one pass over the Unicode database this Python ships:
    every combining mark (general category M: Mn, Mc, Me) as ranges, and every dash (Pd)
    and invisible format character (Cf) escaped one by one."""
    marks: list[tuple[int, int]] = []
    gaps: list[str] = []
    start = prev = None
    for cp in range(sys.maxunicode + 1):
        cat = unicodedata.category(chr(cp))
        if cat[0] == "M":
            if start is None:
                start = cp
            prev = cp
            continue
        if start is not None:
            marks.append((start, prev))
            start = None
        if cat in ("Pd", "Cf"):
            gaps.append(re.escape(chr(cp)))
    if start is not None:
        marks.append((start, prev))
    ranges = "".join(f"\\U{a:08x}" if a == b else f"\\U{a:08x}-\\U{b:08x}" for a, b in marks)
    return ranges, "".join(gaps)


_MARK_CLASS, _DASH_AND_INVISIBLE_CLASS = _unicode_classes()


# Whole-word edges in any script: the character on each side is not a letter or a digit
# (str patterns are Unicode, so [^\W_] is a letter or digit in any alphabet), nor a
# combining mark, which belongs to the letter before it.
_MARKS = _MARK_CLASS
_WORD_CHAR = rf"(?:[^\W_]|[{_MARKS}])"
WORD_START = rf"(?<![^\W_])(?<![{_MARKS}])"
WORD_END = rf"(?![^\W_])(?![{_MARKS}])"
# \b with the same notion of a word character: a letter, a digit or a combining mark.
_BOUNDARY = rf"(?:{WORD_START}(?={_WORD_CHAR})|(?<=[^\W_])(?![^\W_])(?![{_MARKS}])|(?<=[{_MARKS}])(?![^\W_])(?![{_MARKS}]))"
_PLAIN_B = re.compile(r"(?<!\\)((?:\\\\)*)\\b")


def _mark_aware(pattern: str) -> str:
    """A table pattern's word edges made mark-aware: \\b, and the letter-or-digit
    lookarounds written out, also treat a combining mark as part of the word, so a listed
    word joined to other letters by any mark is not a whole word. Frozen tables (v1) keep
    their patterns exactly, to reproduce their history (R54)."""
    pattern = pattern.replace("(?<![^\\W_])", WORD_START).replace("(?![^\\W_])", WORD_END)
    return _PLAIN_B.sub(lambda m: m.group(1) + _BOUNDARY, pattern)


# What may stand between the letters of a listed name in a description: spaces, hyphens,
# dots and slashes, or nothing (D71). Bank text spells one name many ways (SUMUP, SUM UP,
# SUM-UP; FASTER PAYMENT, FASTER-PAYMENT), and a list cannot hold every spelling.
# Every dash (Pd: hyphen, en and em dash and the rest), the minus sign, and every invisible
# format character (Cf: soft hyphen, zero-width space and joiner, byte order mark) count as
# separators too, so a typographic dash or an invisible character cannot hide a listed
# name (T03 review 9). Both are fixed sets, built once.
_SEPARATORS = "\\s./\u2212" + _DASH_AND_INVISIBLE_CLASS
_SEPARATOR_RUN = re.compile(rf"[{_SEPARATORS}]+")


def _spelling_free(name: str) -> str:
    """A listed name as a pattern that ignores spacing, hyphens, dots and slashes between
    its characters; every other character ("&", digits, letters) is matched literally."""
    compact = _SEPARATOR_RUN.sub("", _fold(name))
    return f"[{_SEPARATORS}]*".join(re.escape(c) for c in compact)


def names_pattern(names: Sequence[str]) -> str:
    """A pattern matching any of `names` as whole words in an upper-cased description,
    whatever spaces, hyphens, dots or slashes stand between the name's characters
    (_spelling_free). Whole means no letter, digit or combining mark of any script on
    either side (WORD_START, WORD_END)."""
    alts = sorted({_spelling_free(n) for n in names if _SEPARATOR_RUN.sub("", _fold(n))},
                  key=lambda a: (-len(a), a))
    if not alts:  # nothing but separators: a name that names nothing matches nothing
        return "(?!)"
    return WORD_START + "(?:" + "|".join(alts) + ")" + WORD_END


def _fold(text: str) -> str:
    """The form every description and listed name is matched in: NFC, upper case."""
    return unicodedata.normalize("NFC", text).upper()


def _has_group_reference(pattern: str) -> bool:
    """The pattern refers back to a group (\\1, (?P=name)) or tests one ((?(1)...))."""
    def walk(sub: _sre_parse.SubPattern) -> bool:
        for op, av in sub:
            if str(op).startswith("GROUPREF"):
                return True
            stack = [av]
            while stack:
                item = stack.pop()
                if isinstance(item, _sre_parse.SubPattern):
                    if walk(item):
                        return True
                elif isinstance(item, (list, tuple)):
                    stack.extend(item)
        return False

    return walk(_sre_parse.parse(pattern))


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
        cue_only = r.get("stage") == "line_type" and data.get("frozen") is False
        for key in sorted((CUE_ONLY_KEYS if cue_only else RULE_KEYS) - set(r)):
            problems.append(f"{where}: missing {key}")
        if not MATCH_KEYS & set(r):
            problems.append(f"{where}: missing pattern (or names)")
        for key in sorted(set(r) - ALL_RULE_KEYS, key=repr):
            problems.append(f"{where}: unknown key {key!r}")
        # Every field is a string. Checking the type first means a list, a mapping, a
        # number or null is reported here, not raised later as a TypeError.
        for key in sorted((RULE_KEYS | {"pattern", "unless", "requires"}) & set(r)):
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
        if "bank_code" in r:
            # The one exception to the ambiguity rule is the bank's own returned-payment
            # code at the start of the line: allowed only there, never gained by anchoring.
            if not isinstance(r["bank_code"], bool):
                problems.append(f"{where}: bank_code must be true or false")
            elif r["bank_code"] and not (r.get("stage") == "line_type" and r.get("category") == Category.RETURNED_DD.value
                                         and r.get("pattern") in BANK_RETURN_CODES and "names" not in r):
                problems.append(f"{where}: bank_code is only for the bank's own returned-payment code "
                                f"({', '.join(sorted(BANK_RETURN_CODES))}), alone")
        if data.get("frozen") is False:
            for key in ("pattern", "unless", "requires"):
                if isinstance(r.get(key), str) and r[key] and not _compile_problem(r[key]) and _has_group_reference(r[key]):
                    # Matches are found at every position by wrapping the pattern in a group,
                    # which renumbers groups: a back-reference would silently change meaning.
                    problems.append(f"{where}: {key} uses a back-reference or a conditional group, "
                                    "which a v2 table does not allow")
        if "stage" in r and r["stage"] not in STAGES:
            problems.append(f"{where}: stage {r['stage']!r} is not one of {list(STAGES)}")
        if data.get("frozen") is False:
            # Line-type wording beats every name list, the case profile and firm rules
            # included, so it is protected and comes before any name list.
            if "stage" not in r:
                problems.append(f"{where}: missing stage (line_type or names)")
            elif r["stage"] == "line_type":
                if not r.get("protected"):
                    problems.append(f"{where}: a line_type rule must be protected")
                if any(isinstance(p, dict) and p.get("stage") == "names" for p in rules[:i - 1]):
                    problems.append(f"{where}: a line_type rule must come before every names rule")
        if isinstance(r.get("id"), str):
            if r["id"] in seen:
                problems.append(f"{where}: duplicate id")
            seen.add(r["id"])
        if isinstance(r.get("direction"), str) and r["direction"] not in DIRECTIONS:
            problems.append(f"{where}: direction {r['direction']!r} is not one of {list(DIRECTIONS)}")
        if isinstance(r.get("category"), str) and r["category"] not in categories:
            problems.append(f"{where}: category {r['category']!r} is not a category")
        for key in ("pattern", "unless", "requires"):
            if isinstance(r.get(key), str) and r[key]:
                problem = _compile_problem(r[key])
                if problem:
                    problems.append(f"{where}: {key} does not compile ({problem})")
        # A pattern and names that each compile can still fail joined (a global flag such
        # as (?i) is allowed only at the start), so the joined text is checked too.
        if (isinstance(r.get("pattern"), str) and r["pattern"] and isinstance(r.get("names"), list) and r["names"]
                and all(isinstance(n, str) and n.strip() for n in r["names"]) and not _compile_problem(r["pattern"])):
            problem = _compile_problem(_rule_pattern_text(r))
            if problem:
                problems.append(f"{where}: pattern and names do not compile together ({problem})")
    problems += _category_order_problems(data, rules)
    if problems:
        raise RulesetError(f"{source}: " + "; ".join(problems))


def _category_order_problems(data: dict, rules: list) -> list[str]:
    """A table that is not frozen declares the category order inside every money-in bucket
    its credit rules reach with more than one category, so rule order never picks one."""
    order = data.get("category_order")
    frozen = data.get("frozen")
    if frozen is True:
        return ["a frozen table has no category_order"] if "category_order" in data else []
    if frozen is not False:
        return []
    if order is None:
        order = []
    if not isinstance(order, list) or not all(isinstance(c, str) for c in order):
        return ["category_order must be a list of categories"]
    categories = {c.value for c in Category}
    problems = [f"category_order: {c!r} is not a category" for c in order if c not in categories]
    if len(set(order)) != len(order):
        problems.append("category_order: a category is listed twice")
    credit_cats = {r["category"] for r in rules if isinstance(r, dict) and r.get("direction") in ("in", "any")
                   and isinstance(r.get("category"), str) and r["category"] in categories}
    by_bucket: dict[object, set[str]] = {}
    for c in credit_cats:
        by_bucket.setdefault(CREDIT_BUCKET.get(Category(c), c), set()).add(c)
    for bucket, cats in sorted(by_bucket.items(), key=lambda kv: str(kv[0])):
        missing = sorted(cats - set(order)) if len(cats) > 1 else []
        if missing:
            name = getattr(bucket, "value", bucket)
            problems.append(f"category_order: bucket {name} has categories {sorted(cats)} but does not order "
                            f"{missing}")
    return problems


def _rule_parts(r: dict, frozen: bool = True) -> list[str]:
    """A rule's own pattern (made mark-aware for a v2 table) and its names, each a pattern."""
    own = r.get("pattern")
    if own and not frozen:
        own = _mark_aware(own)
    return [p for p in (own, names_pattern(r["names"]) if "names" in r else None) if p]


def _rule_pattern_text(r: dict, frozen: bool = True) -> str:
    parts = _rule_parts(r, frozen)
    return parts[0] if len(parts) == 1 else "|".join(f"(?:{p})" for p in parts)


def _rule_pattern(r: dict, frozen: bool) -> re.Pattern[str]:
    return re.compile(_rule_pattern_text(r, frozen))


def _side_pattern(text: str, frozen: bool) -> re.Pattern[str]:
    return re.compile(text if frozen else _mark_aware(text))


def ruleset_from_data(data: object, expected_id: str, source: str, sha256: str) -> Ruleset:
    validate_ruleset(data, expected_id, source)
    assert isinstance(data, dict)
    frozen = bool(data.get("frozen", False))
    rules = []
    for r in data["rules"]:
        # Every pattern a line will meet is compiled here, at load: the rule's own (made
        # mark-aware for a v2 table), its exceptions, and the form that finds its matches
        # at every position (_spans). A pattern that fails any of them refuses the table
        # by rule id, rather than failing on the first line it meets.
        try:
            rule = Rule(r["id"], _rule_pattern(r, frozen), r["direction"],
                        Category(r["category"]) if "category" in r else None,
                        protected=r.get("protected", False), stage=r.get("stage"),
                        bank_code=r.get("bank_code", False),
                        span_patterns=tuple(re.compile(f"(?=({part}))") for part in _rule_parts(r, frozen)),
                        unless=_side_pattern(r["unless"], frozen) if "unless" in r else None,
                        requires=_side_pattern(r["requires"], frozen) if "requires" in r else None)
        except (re.error, OverflowError, RecursionError) as e:
            raise RulesetError(f"{source}: rule {r['id']}: its pattern cannot be used: {e}") from e
        rules.append(rule)
    return Ruleset(
        id=data["id"],
        sha256=sha256,
        frozen=frozen,
        category_order=tuple(Category(c) for c in data.get("category_order") or ()),
        rules=tuple(rules),
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
            and (rule.unless is None or rule.unless.search(description) is None)
            and (rule.requires is None or rule.requires.search(description) is not None))


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
    """`description` folded (_fold)."""
    # A profile value of only spaces, dashes, dots or invisible characters names no one
    # (T03 review 9): it must not settle an ambiguous credit by matching everywhere.
    names = [n for n in names if _SEPARATOR_RUN.sub("", _fold(n))]
    return bool(names) and re.search(names_pattern(names), description) is not None


@functools.lru_cache(maxsize=None)
def _at_every_position(pattern: re.Pattern[str]) -> re.Pattern[str]:
    # The pattern inside a lookahead matches at every start position, so a longer listed
    # phrase that starts inside another match of the same rule is still found; plain
    # finditer resumes after each match and would skip it.
    return re.compile(f"(?=({pattern.pattern}))", pattern.flags)


def _spans(rule: Rule, description: str) -> list[tuple[int, int]]:
    """Every match of the rule, at every start, the longest at each start across its parts:
    one alternation returns its first part that matches, which may be the shorter."""
    longest: dict[int, int] = {}
    for part in rule.span_patterns or (_at_every_position(rule.pattern),):
        for m in part.finditer(description):
            start, end = m.span(1)
            if end > start and end > longest.get(start, -1):
                longest[start] = end
    return sorted(longest.items())


def credit_matches(ruleset: Ruleset, description: str, amount: float) -> list[Rule]:
    """Every rule of `ruleset` that matches a line, in table order, less those whose every
    match lies inside a longer match of another rule (STRIPE inside STRIPE CAPITAL, REFUND
    inside HMRC REFUND, LOAN inside DIRECTOR LOAN): those count only as the longer one."""
    desc = _fold(description)
    matched = [(r, _spans(r, desc)) for r in ruleset.rules if rule_matches(r, desc, amount)]

    def inside_longer(rule: Rule, spans: list[tuple[int, int]]) -> bool:
        others = [o for other, ss in matched if other is not rule for o in ss]
        return bool(spans) and all(any(a <= s and e <= b and b - a > e - s for a, b in others) for s, e in spans)

    return [r for r, spans in matched if not inside_longer(r, spans)]


@dataclass(frozen=True)
class Verdict:
    """What the table says about a credit: the rule that settles it, or, when the wording
    is ambiguous, every candidate rule it matched in either stage, in table order."""

    rules: tuple[Rule, ...]
    ambiguous: bool = False

    @property
    def rule(self) -> Rule:
        return self.rules[0]


def _bucket(rule: Rule) -> object:
    return CREDIT_BUCKET.get(rule.category, rule.category) if rule.category is not None else None


def _decide(ruleset: Ruleset, deciding: Sequence[Rule], candidates: Sequence[Rule]) -> Verdict:
    """Design C1 steps 4 to 7 over the deciding stage's rules (each with a category)."""
    if len({_bucket(r) for r in deciding}) > 1:
        return Verdict(tuple(candidates), ambiguous=True)
    order = ruleset.category_order
    position = {r.id: i for i, r in enumerate(ruleset.rules)}

    def rank(c: Category) -> tuple[int, int]:
        # Every category a bucket can hold twice is in the declared order (the validator
        # insists); the table position is only a deterministic fallback for a hand-built table.
        first = min(position.get(r.id, 0) for r in deciding if r.category is c)
        return (order.index(c) if c in order else len(order), first)

    category = min({r.category for r in deciding}, key=rank)  # type: ignore[arg-type]
    chosen = [r for r in deciding if r.category is category]
    return Verdict((min(chosen, key=lambda r: position.get(r.id, 0)),))


def credit_verdict(ruleset: Ruleset, description: str, amount: float) -> Verdict | None:
    """R4's verdict on a credit under a table that is not frozen (design C1, revision 4),
    or None when no rule with a category decides it."""
    rules = credit_matches(ruleset, description, amount)  # step 1, both stages, in table order
    cues = [r for r in rules if r.stage == "line_type"]
    bank = [r for r in cues if r.bank_code]
    if bank:  # step 2: the bank's own code at the start of the line (UNPAID DD), not payer wording
        return Verdict((bank[0],))
    rails = [r for r in cues if r.category is None]
    names = [r for r in rules if r.stage == "names" and r.category is not None]
    if rails and any(r.category is Category.CASH_DEPOSIT for r in names):
        return Verdict(tuple(rules), ambiguous=True)  # step 3: electronic money is not cash
    bucketed = [r for r in cues if r.category is not None]
    if bucketed:  # step 4: the cues decide
        return _decide(ruleset, bucketed, rules)
    return _decide(ruleset, names, rules) if names else None  # step 5: the names decide


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
    # The frozen table matches the description exactly as it always did (R54).
    desc = t.description.upper() if ruleset.frozen else _fold(t.description)
    if t.amount > 0 and not ruleset.frozen:
        return _classify_credit(t, desc, ruleset, profile, firm_rules)
    for rule in ruleset.protected:
        if rule.category is not None and rule_matches(rule, desc, t.amount):
            return rule.category, f"{ruleset.id}:{rule.id}"
    found = _profile(t, desc, profile)
    if found:
        return found
    for rule in firm_rules:
        if rule_matches(rule, desc, t.amount):
            return rule.category, f"firm:{rule.id}"
    for rule in ruleset.unprotected:
        if rule.category is not None and rule_matches(rule, desc, t.amount):
            return rule.category, f"{ruleset.id}:{rule.id}"
    return (Category.SUPPLIER if t.amount < 0 else Category.OTHER), f"{ruleset.id}:default"


def _profile_matches(t: Transaction, desc: str, profile: ProfileNames | None) -> list[tuple[Category, str]]:
    """Every kind of case-profile name the line's wording matches, director first."""
    found: list[tuple[Category, str]] = []
    if profile is not None:
        if t.amount > 0 and _names_in(profile.directors, desc):
            found.append((Category.DIRECTOR_FUNDING, "profile:director"))
        if t.amount != 0 and _names_in(profile.own_accounts, desc):
            found.append((Category.OWN_ACCOUNT_TRANSFER, "profile:own_account"))
    return found


def _profile(t: Transaction, desc: str, profile: ProfileNames | None) -> tuple[Category, str] | None:
    found = _profile_matches(t, desc, profile)
    return found[0] if found else None


def _classify_credit(t: Transaction, desc: str, ruleset: Ruleset, profile: ProfileNames | None,
                     firm_rules: Sequence[Rule]) -> tuple[Category, str]:
    """Design C1's precedence for a credit, after the override: (2) a protected verdict,
    (3) an ambiguous verdict, (4) a profile name, (5) firm rules, (6) an unprotected
    verdict, (7) the default. Only an override (or an own-account pairing, applied over
    the whole statement) settles an ambiguous credit (D78): a profile name the wording
    matches is one more candidate, because the description cannot tell the payer from a
    payment reference that names someone else. A credit naming both a director and an own
    account is ambiguous too (D86)."""
    verdict = credit_verdict(ruleset, desc, t.amount)
    if verdict is not None and not verdict.ambiguous and verdict.rule.protected:
        return verdict.rule.category, f"{ruleset.id}:{verdict.rule.id}"
    kinds = _profile_matches(t, desc, profile)
    # Ambiguous when the table finds the wording ambiguous (R4), or when two kinds of
    # profile name claim the credit, a director (R5) and an own account (R6) (D86). Every
    # profile name the wording matches is then a candidate, never the settlement (D78).
    if (verdict is not None and verdict.ambiguous) or len(kinds) > 1:
        # The candidates term (R4): every rule matched in either stage, then each kind of
        # profile name. An ambiguous verdict already carries them; a credit made ambiguous
        # only by two profile names takes them from the table's matches (D90).
        rules = verdict.rules if verdict is not None and verdict.ambiguous else credit_matches(ruleset, desc, t.amount)
        candidates = [r.id for r in rules] + [ref for _, ref in kinds]
        return Category.OTHER, f"{ruleset.id}:conflict:" + "+".join(candidates)
    if kinds:
        return kinds[0]
    for rule in firm_rules:
        if rule_matches(rule, desc, t.amount):
            return rule.category, f"firm:{rule.id}"
    if verdict is not None:
        return verdict.rule.category, f"{ruleset.id}:{verdict.rule.id}"
    return Category.OTHER, f"{ruleset.id}:default"


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
