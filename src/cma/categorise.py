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

import bisect
import functools
import hashlib
import os
import re
import re._compiler as _sre_compile  # to test what one parsed element of a pattern matches
import re._parser as _sre_parse  # the parser re uses; read only, to find back-references
import sys
import unicodedata
from collections.abc import Callable, Collection, Mapping, Sequence
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
_RULE_ID = re.compile(r"[A-Za-z0-9_.\-]+")
# The provenance ids the categoriser sets itself: "<table>:default", "<table>:round_trip"
# and "<table>:conflict:..."; a rule with one of these ids could not be told from them.
RESERVED_RULE_IDS = frozenset({"default", "round_trip", "conflict"})
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
    # For each part: its source when an alternative or a lazy repeat could end a match
    # early, so _spans looks for the longest end (D102); None when the first match is it.
    span_longest: tuple[str | None, ...] = field(default=(), compare=False, repr=False)
    # Per part, its end-forced forms and widest match, built with the rule at load and kept
    # with it, so categorising never compiles (T03 reviews 35 to 37).
    span_ends: tuple[tuple[re.Pattern[str], re.Pattern[str], int | None] | None, ...] = field(
        default=(), compare=False, repr=False)


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


def _has_lookaround(pattern: str) -> bool:
    """The pattern tests wording it does not match ((?=...), (?!...), (?<=...), (?<!...)):
    an exception written into the pattern itself."""
    def walk(sub: _sre_parse.SubPattern) -> bool:
        for op, av in sub:
            if str(op) in ("ASSERT", "ASSERT_NOT"):
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


_ANCHORS = {"AT_BEGINNING", "AT_END", "AT_BEGINNING_STRING", "AT_END_STRING"}


def _has_anchor(pattern: str) -> bool:
    """The pattern tests where the line starts or ends (^, $, \\A, \\Z)."""
    def walk(sub: _sre_parse.SubPattern) -> bool:
        for op, av in sub:
            if str(op) == "AT" and str(av) in _ANCHORS:
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


def _atoms(sub: _sre_parse.SubPattern):
    """The single-character elements a parsed pattern is made of, through groups,
    alternatives and repeats."""
    for op, av in sub:
        name = str(op)
        if name in ("LITERAL", "NOT_LITERAL", "IN", "ANY", "CATEGORY"):
            yield op, av
        elif name == "SUBPATTERN":
            yield from _atoms(av[3])
        elif name == "BRANCH":
            for branch in av[1]:
                yield from _atoms(branch)
        elif name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
            yield from _atoms(av[2])
        elif name == "ATOMIC_GROUP":
            yield from _atoms(av)


def _no_backtrack(pattern: str) -> bool:
    """An atomic group or a possessive repeat: forcing a later end cannot reopen it, so the
    longest match at a start could not be found (T03 review 16)."""
    def walk(sub: _sre_parse.SubPattern) -> bool:
        for op, av in sub:
            if str(op) in ("ATOMIC_GROUP", "POSSESSIVE_REPEAT"):
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


def _repeat_crosses_words(pattern: str) -> bool:
    """A repeat that can take in both a space and a word character (.*, [A-Z ]+, (?:\\d+\\s)+):
    with the longest match counting (D102), it would swallow every other list's wording
    after its start (D110). A repeat of spaces, digits or letters alone is fine."""
    parsed = _sre_parse.parse(pattern)

    def matches(atom, char: str) -> bool:
        return _sre_compile.compile(_sre_parse.SubPattern(parsed.state, [atom])).fullmatch(char) is not None

    def walk(sub: _sre_parse.SubPattern) -> bool:
        for op, av in sub:
            if str(op) in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT") and av[1] > 1:
                atoms = list(_atoms(av[2]))
                if (any(matches(a, " ") or matches(a, "\t") for a in atoms)
                        and any(matches(a, c) for a in atoms for c in "Aa1-.\u00e9")):
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

    return walk(parsed)


def _bank_code_shape(r: dict) -> bool:
    """The bank's returned-payment code as its own rule: the pinned text alone, as a
    returned-payment rule, with no names or exceptions. Only this keeps its anchor, whatever
    other checks a table skips (T03 review 30)."""
    return (_is_bank_code_text(r.get("pattern")) and r.get("category") == Category.RETURNED_DD.value
            and r.get("direction") in ("in", "any")  # it settles credits; a debit-only copy is not it
            and "names" not in r and "unless" not in r and "requires" not in r)


def _is_bank_code_text(value: object) -> bool:
    # A malformed value (a list, a mapping) is reported by the rule's own checks, never
    # raised as a TypeError from this membership test (T03 review 25).
    return isinstance(value, str) and value in BANK_RETURN_CODES


def _compile_problem(pattern: str) -> str | None:
    # re.error is not the only way a pattern fails to compile: a repeat count past
    # the engine's limit raises OverflowError, deep nesting RecursionError.
    try:
        re.compile(pattern)
    except (re.error, OverflowError, RecursionError, MemoryError) as e:
        return f"{e.__class__.__name__}: {e}"
    return None


def validate_ruleset(data: object, expected_id: str, source: str = "rules", sha256: str | None = None) -> None:
    if not isinstance(data, dict):
        raise RulesetError(f"{source}: not a mapping")
    # Every check a v2 table gets applies to every table but the pinned frozen ones (v1):
    # a table cannot skip them by calling itself frozen, and a refusal names the rule
    # (T03 review 24).
    strict = not (isinstance(data, dict) and data.get("frozen") is True and expected_id in FROZEN_SHA256)
    # The exceptions R4 forbids are checked on every table but the pinned one as its bytes
    # were verified (`sha256`), so a changed v1 names them too (T03 review 27).
    # Only a digest that matches the pin counts: no digest given is no verification (review 39).
    verified = not strict and sha256 is not None and FROZEN_SHA256.get(expected_id) == sha256
    problems: list[str] = []
    if "id" not in data:
        problems.append("missing id")
    elif data["id"] != expected_id:
        problems.append(f"id {data['id']!r} does not match the file ({expected_id!r})")
    if not isinstance(data.get("frozen"), bool):
        problems.append("frozen must be true or false")
    for key in sorted(set(data) - TABLE_KEYS, key=repr):
        problems.append(f"unknown key {key!r}")
    for key, value in data.items():  # before any early refusal, so a repeated key is named
        for bad in _unbuilt_in(value) if key != "rules" or isinstance(value, _Unbuilt) else ():
            problems.append(f"{key} holds a {bad!r}")
    rules = data.get("rules")
    if isinstance(rules, _Repeated):
        # Each list given is still checked rule by rule (review 39), and a value that is not a
        # list of rules, or is empty, is named too (review 40); the repeat is named above.
        for k, v in enumerate(rules.values, start=1):
            if not isinstance(v, list):
                problems.append(f"rules value {k} of {len(rules.values)} is not a list of rules")
            elif not v:
                problems.append(f"rules value {k} of {len(rules.values)} is empty")
        rules = [r for v in rules.values if isinstance(v, list) for r in v]
    if not isinstance(rules, list) or not rules:
        raise RulesetError(f"{source}: " + "; ".join([*problems, "no rules"]))
    categories = {c.value for c in Category}
    seen: set[str] = set()
    for i, rule_given in enumerate(rules, start=1):
        r = rule_given
        where = f"rule {i} ({r.get('id', '?') if isinstance(r, dict) and not isinstance(r.get('id'), _Unbuilt) else '?'})"
        if isinstance(r, dict):
            for key, value in r.items():
                for bad in _unbuilt_in(value):
                    # Never loaded as text, and named by rule and field (T03 review 31).
                    problems.append(f"{where}: {key} holds a {bad!r}")
        if not isinstance(r, dict):
            problems.append(f"{where}: not a mapping")
            continue
        # Every field is checked in each value it was given (review 40): the rule as given,
        # then once per value of each repeated field.
        for vi, r in enumerate(_variants(rule_given)):
            cue_only = r.get("stage") == "line_type" and strict  # every table but the pinned v1 (review 35)
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
                                             and _is_bank_code_text(r.get("pattern")) and "names" not in r
                                             and "unless" not in r and "requires" not in r
                                             and r.get("direction") in ("in", "any")):
                    problems.append(f"{where}: bank_code is only for the bank's own returned-payment code "
                                    f"({', '.join(sorted(BANK_RETURN_CODES))}), alone, with no unless or requires, "
                                    "for credits (direction in or any)")
            # A field given twice is checked in each of its values, so a repeated pattern never
            # hides a forbidden exception (T03 review 36).
            for rv in (_variants(r) if not verified else ()):
                for key in ("unless", "requires"):
                    if key in rv:
                        # An exception could switch a rule off where another list's wording sits
                        # (cash wording beside a rail, say), deciding a collision before the
                        # ambiguity check sees it. R4 lets an exception exclude only wording on no
                        # list, which a pattern cannot be shown to keep to, so a v2 table has none:
                        # list a longer phrase, or let the line be ambiguous (D52, T03 review 20).
                        problems.append(f"{where}: {key} is not allowed in a v2 table: an exception could "
                                        "hide another list's wording from the ambiguity check (R4); list a "
                                        "longer phrase instead")
                if (isinstance(rv.get("pattern"), str) and rv["pattern"] and not _compile_problem(rv["pattern"])
                        and _has_lookaround(rv["pattern"])):
                    # A lookahead or lookbehind is an exception inside the pattern: (?!.*FPS)
                    # would switch the rule off beside another list's wording, as an unless
                    # would (T03 review 21).
                    problems.append(f"{where}: pattern uses a lookahead or lookbehind, which a v2 table "
                                    "does not allow: an exception could hide another list's wording (R4)")
                if (isinstance(rv.get("pattern"), str) and rv["pattern"] and not _compile_problem(rv["pattern"])
                        and not _bank_code_shape(rv) and _has_anchor(rv["pattern"])):
                    # An anchor tests the whole line beside a match: CASH DEPOSIT$ misses
                    # FPS CASH DEPOSIT STRIPE, so it hides wording as an exception would. Only
                    # the bank's own code keeps its ^ (D137).
                    problems.append(f"{where}: pattern uses an anchor (^, $, \\A or \\Z), which a v2 table "
                                    "allows only on the bank's returned-payment code")
            if strict:
                if isinstance(r.get("id"), str) and not _RULE_ID.fullmatch(r["id"]):
                    # Candidates are recorded joined by + after a colon: an id holding either
                    # would make two different candidate sets read the same (T03 review 17).
                    problems.append(f"{where}: id may use only letters, digits, _, . and -")
                if isinstance(r.get("id"), str) and r["id"] in RESERVED_RULE_IDS:
                    # These name the categoriser's own decisions in a line's provenance.
                    problems.append(f"{where}: id {r['id']!r} is reserved for the categoriser's own "
                                    "provenance")
                if _is_bank_code_text(r.get("pattern")) and r.get("bank_code") is not True:
                    problems.append(f"{where}: the bank's own returned-payment code must carry bank_code: true")
                for key in ("pattern", "unless", "requires"):
                    if isinstance(r.get(key), str) and r[key] and not _compile_problem(r[key]) and _has_group_reference(r[key]):
                        # Matches are found at every position by wrapping the pattern in a group,
                        # which renumbers groups: a back-reference would silently change meaning.
                        problems.append(f"{where}: {key} uses a back-reference or a conditional group, "
                                        "which a v2 table does not allow")
                if (isinstance(r.get("pattern"), str) and r["pattern"] and not _compile_problem(r["pattern"])
                        and _sre_parse.parse(r["pattern"]).getwidth()[0] == 0):
                    # A match of no characters (a lookahead alone, an optional pattern) has no
                    # span, so the longer-phrase filter could never set it aside (T03 review 18).
                    problems.append(f"{where}: pattern can match without consuming any text, which a v2 "
                                    "table does not allow")
                if (isinstance(r.get("pattern"), str) and r["pattern"] and not _compile_problem(r["pattern"])
                        and _no_backtrack(r["pattern"])):
                    problems.append(f"{where}: pattern uses an atomic group or a possessive repeat, which a "
                                    "v2 table does not allow: the longest match could not be found (D102)")
                if (isinstance(r.get("pattern"), str) and r["pattern"] and not _compile_problem(r["pattern"])
                        and _repeat_crosses_words(r["pattern"])):
                    problems.append(f"{where}: pattern has a repeat that can run across words (such as .* or "
                                    "[A-Z ]+), which a v2 table does not allow: list the phrases as names")
            if "stage" in r and r["stage"] not in STAGES:
                problems.append(f"{where}: stage {r['stage']!r} is not one of {list(STAGES)}")
            if strict:
                # Line-type wording beats every name list, the case profile and firm rules
                # included, so it is protected and comes before any name list.
                if "stage" not in r:
                    problems.append(f"{where}: missing stage (line_type or names)")
                elif r["stage"] == "line_type":
                    if not r.get("protected"):
                        problems.append(f"{where}: a line_type rule must be protected")
                    if any(isinstance(p, dict) and p.get("stage") == "names" for p in rules[:i - 1]):
                        problems.append(f"{where}: a line_type rule must come before every names rule")
            if vi == 0 and isinstance(r.get("id"), str):  # once per rule, as given
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
    if strict:
        flagged = {r["pattern"] for r in rules if isinstance(r, dict) and r.get("bank_code") is True
                   and isinstance(r.get("pattern"), str)}  # a malformed pattern is named by its rule
        for code in sorted(BANK_RETURN_CODES - flagged):
            problems.append(f"the bank's own returned-payment code {code} has no rule with bank_code: true "
                            "(R4: it settles a credit's line type)")
    # Each rule's matchers are built here too, as the loader builds them, so a pattern that
    # compiles alone but cannot be used is named with the table's other faults, not found
    # only once everything else passes (T03 review 27).
    frozen = not strict  # only the pinned v1 builds its matchers as frozen (review 36)
    for i, rule0 in enumerate(rules, start=1):
        if not isinstance(rule0, dict):
            continue
        where = f"rule {i} ({rule0.get('id', '?')})"
        for r in _variants(rule0):  # every value of a repeated field (review 37)
            # Each part is built when its own fields are well typed, so a malformed exception
            # never hides a fault in the rule's own matcher, nor the other way (review 28).
            parts: list[tuple[str, Callable[[], object]]] = []

            def matchers(sub: dict) -> tuple:
                return _rule_pattern(sub, frozen), _span_matchers(sub, frozen), _longest_sources_checked(sub, frozen)

            # The pattern and the names are each built alone, whatever is wrong with the other
            # or with the rule's exceptions, so no field's fault hides another's (review 29);
            # then together, as the loader joins them, when both build alone.
            own_ok = isinstance(r.get("pattern"), str) and bool(r["pattern"]) and not _compile_problem(r["pattern"])
            # A value of a repeated field is compiled here, as a field given once is above,
            # so one that does not compile is named rather than skipped (review 39).
            for key in ("pattern", "unless", "requires"):
                if isinstance(rule0.get(key), _Repeated) and isinstance(r.get(key), str) and r[key]:
                    problem = _compile_problem(r[key])
                    if problem:
                        problems.append(f"{where}: {key} does not compile ({problem})")
            names_ok = "names" in r and _well_typed_text({"names": r["names"]})
            if own_ok:
                parts.append(("its pattern", lambda r=r: matchers({"pattern": r["pattern"]})))
            if names_ok:
                parts.append(("its names", lambda r=r: matchers({"names": r["names"]})))
            if own_ok and names_ok:
                parts.append(("its pattern and names together",
                              lambda r=r: matchers({"pattern": r["pattern"], "names": r["names"]})))
            for key in ("unless", "requires"):
                if isinstance(r.get(key), str) and r[key] and not _compile_problem(r[key]):
                    parts.append((f"its {key}", lambda r=r, key=key: _side_pattern(r[key], frozen)))
            failed = False
            for what, build in parts:
                if failed and what == "its pattern and names together":
                    continue  # already named by the part that fails alone
                try:
                    build()
                except (re.error, OverflowError, RecursionError, MemoryError, TypeError, ValueError) as e:
                    failed = failed or what in ("its pattern", "its names")
                    if not (what == "its pattern and names together"
                            and any(p.startswith(where) and "do not compile together" in p for p in problems)):
                        problems.append(f"{where}: {what} cannot be used ({e.__class__.__name__}: {e})")
    problems += _category_order_problems(data, rules, strict)
    problems = list(dict.fromkeys(problems))  # a check run on each variant names a fault once
    if problems:
        raise RulesetError(f"{source}: " + "; ".join(problems))


def _well_typed_text(r: dict) -> bool:
    """The rule's pattern and names have the types the loader builds its matcher from."""
    if "pattern" not in r and "names" not in r:
        return False
    if "pattern" in r and not (isinstance(r["pattern"], str) and r["pattern"]):
        return False
    return "names" not in r or (isinstance(r["names"], list) and bool(r["names"])
                                and all(isinstance(n, str) and n.strip() for n in r["names"]))


def _span_matchers(r: dict, frozen: bool) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(f"(?=({part}))") for part in _rule_parts(r, frozen))


def _build_matchers(r: dict, frozen: bool) -> tuple:
    """Every pattern a line will meet for this rule, compiled: the rule's own (made
    mark-aware for a v2 table), its exceptions, and the form that finds its matches at every
    position (_spans)."""
    return (_rule_pattern(r, frozen),
            _span_matchers(r, frozen),
            _longest_sources_checked(r, frozen),
            _side_pattern(r["unless"], frozen) if "unless" in r else None,
            _side_pattern(r["requires"], frozen) if "requires" in r else None)


def _category_order_problems(data: dict, rules: list, strict: bool = True) -> list[str]:
    """A maintained table (every table but the pinned v1, whatever its frozen flag says,
    review 35) declares the category order inside every money-in bucket its credit rules
    reach with more than one category, so rule order never picks one."""
    order = data.get("category_order")
    if not strict:
        return ["a frozen table has no category_order"] if "category_order" in data else []
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
    validate_ruleset(data, expected_id, source, sha256)
    assert isinstance(data, dict)
    frozen = bool(data.get("frozen", False))
    rules = []
    for r in data["rules"]:
        # Every pattern a line will meet is compiled here, at load: the rule's own (made
        # mark-aware for a v2 table), its exceptions, and the form that finds its matches
        # at every position (_spans). A pattern that fails any of them refuses the table
        # by rule id, rather than failing on the first line it meets.
        try:
            pattern, spans, longest, unless, requires = _build_matchers(r, frozen)
            ends = _end_forms(longest)
            rule = Rule(r["id"], pattern, r["direction"],
                        Category(r["category"]) if "category" in r else None,
                        protected=r.get("protected", False), stage=r.get("stage"),
                        bank_code=r.get("bank_code", False),
                        span_patterns=spans, span_longest=longest, span_ends=ends,
                        unless=unless, requires=requires)
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


class _DuplicateKey(yaml.constructor.ConstructorError):
    """A repeated key: refused as such, never recovered as an unbuilt value."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """safe_load keeps the last of two equal keys, so a second `category:` inside a rule
    would silently change it with nothing left for the validator to see. Refuse instead."""

    def construct_mapping(self, node, deep=False):
        """A key given twice keeps neither value: it holds an unbuilt value, so validation
        names the rule and field beside the table's other faults (T03 review 35), and a
        second `category:` can never silently win."""
        mapping = super().construct_mapping(node, deep=deep)
        given: dict = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                given.setdefault(key, []).append(value_node)
            except TypeError:  # an unhashable key; the base class has reported it
                continue
        for key, nodes in given.items():
            if len(nodes) > 1:
                values = [self.construct_object(n, deep=deep) for n in nodes]
                mapping[key] = _Repeated(f"{key} given twice", "repeated key", values)
        return mapping

    def construct_object(self, node, deep=False):
        """Every node is built in full where it stands, and one that cannot be built (any
        tag on any kind of node: !!str [1], !!seq x, !!omap with a bad pair, a bad merge,
        an alias that contains itself) becomes an unbuilt value, so validation names its
        rule and field beside the table's other faults (T03 reviews 30 to 34)."""
        try:
            return super().construct_object(node, deep=True)
        except _DuplicateKey:
            raise
        except (yaml.constructor.ConstructorError, ValueError, TypeError, OverflowError, KeyError,
                RecursionError, AttributeError) as e:
            self.recursive_objects.pop(node, None)
            text = node.value if isinstance(node, yaml.ScalarNode) else f"<{node.id}>"
            return _Unbuilt(str(text), node.tag or type(e).__name__)


class _Unbuilt:
    """A scalar the loader could not build (2026-02-30 read as a date, !!int x, !!bool x).
    It is no string, number or flag, so no field accepts it: validation names the rule and
    field it sits in, beside the table's other faults, and it never loads as text (T03
    reviews 30 and 31)."""

    def __init__(self, text: str, tag: str):
        self.text, self.tag = text, tag

    def __repr__(self) -> str:
        return f"{self.tag.rsplit(':', 1)[-1]} value {self.text!r} that YAML cannot build"


class _Repeated(_Unbuilt):
    """A key given more than once: neither value is kept, and each is still checked."""

    def __init__(self, text: str, tag: str, values: list):
        super().__init__(text, tag)
        self.values = values


def _variants(r: dict) -> list[dict]:
    """The rule as given, then once for each value of each field given more than once, that
    field alone set to the value and every other repeated field left unbuilt. Linear, with
    no cap, so every value is checked (T03 review 37): a check that reads several fields
    meets a repeated one as unbuilt, so no exemption (the bank's code) is granted on a guess."""
    out = [r]
    for key, value in r.items():
        if isinstance(value, _Repeated):
            out += [{**r, key: x} for x in value.values]
    return out


def _unbuilt_in(value: object, _seen: set[int] | None = None) -> list[_Unbuilt]:
    """Every unbuilt value inside `value`, each container visited once: a YAML alias can make
    a list contain itself, which must never recurse without end (T03 review 32)."""
    seen = set() if _seen is None else _seen
    if isinstance(value, _Unbuilt):
        return [value]
    if not isinstance(value, (list, dict)) or id(value) in seen:
        return []
    seen.add(id(value))
    items = value if isinstance(value, list) else list(value.values())
    return [b for v in items for b in _unbuilt_in(v, seen)]


def _scalar_or_unbuilt(base):
    def construct(loader, node):
        try:
            return base(loader, node)
        except (ValueError, TypeError, OverflowError, KeyError, yaml.constructor.ConstructorError):
            # A collection tagged as a scalar (!!int [1]) has no scalar text to read back,
            # so it is named by its kind, never constructed again (T03 review 33).
            text = node.value if isinstance(node, yaml.ScalarNode) else f"<{node.id}>"
            return _Unbuilt(str(text), node.tag)
    return construct


def _unknown_tag(loader, node):
    """A tag the safe loader does not know (!foo x) is an unbuilt value, named by rule and
    field, never a stop to the whole load (T03 review 32)."""
    text = node.value if isinstance(node, yaml.ScalarNode) else f"<{node.id}>"
    return _Unbuilt(str(text), node.tag)


_UniqueKeyLoader.add_constructor(None, _unknown_tag)

for _tag, _base in (("tag:yaml.org,2002:timestamp", yaml.SafeLoader.construct_yaml_timestamp),
                    ("tag:yaml.org,2002:int", yaml.SafeLoader.construct_yaml_int),
                    ("tag:yaml.org,2002:float", yaml.SafeLoader.construct_yaml_float),
                    ("tag:yaml.org,2002:bool", yaml.SafeLoader.construct_yaml_bool),
                    ("tag:yaml.org,2002:binary", yaml.SafeLoader.construct_yaml_binary)):
    _UniqueKeyLoader.add_constructor(_tag, _scalar_or_unbuilt(_base))


def _table_bytes(ruleset_id: str) -> bytes:
    return resources.files("cma.rules").joinpath(f"{ruleset_id}.yaml").read_bytes()


def _parse_table(raw: bytes, ruleset_id: str, source: str, digest: str) -> tuple[dict, Ruleset]:
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
    return data, ruleset_from_data(data, ruleset_id, source, digest)


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
    # A pinned frozen table (v1) is judged by its bytes first: any change is refused as a
    # changed frozen table whatever else it breaks, and what else it breaks is named after
    # it (T03 review 25).
    digest = hashlib.sha256(raw).hexdigest()
    pinned = FROZEN_SHA256.get(ruleset_id)
    changed = pinned is not None and digest != pinned
    try:
        data, ruleset = _parse_table(raw, ruleset_id, source, digest)
    except Exception as e:  # whatever else the change breaks, a changed v1 is named as changed (review 26)
        if changed:
            raise RulesetError(f"{source}: frozen table {ruleset_id} has been changed (sha256 {digest}, "
                               f"pinned {pinned}); reinstall the package. It also fails to load: {e}") from e
        if isinstance(e, RulesetError):
            raise
        raise RulesetError(f"{source}: a malformed value could not be checked ({e.__class__.__name__}: {e})") from e
    if changed:
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


@functools.lru_cache(maxsize=256)
def _names_regex(names: tuple[str, ...]) -> re.Pattern[str] | None:
    # A profile value of only spaces, dashes, dots or invisible characters names no one
    # (T03 review 9): it must not settle an ambiguous credit by matching everywhere.
    kept = [n for n in names if _SEPARATOR_RUN.sub("", _fold(n))]
    return re.compile(names_pattern(kept)) if kept else None


def _names_in(names: Sequence[str], description: str) -> bool:
    """`description` folded (_fold). The profile's names are compiled once per case."""
    pattern = _names_regex(tuple(names))
    return pattern is not None and pattern.search(description) is not None


@functools.lru_cache(maxsize=None)
def _at_every_position(pattern: re.Pattern[str]) -> re.Pattern[str]:
    # The pattern inside a lookahead matches at every start position, so a longer listed
    # phrase that starts inside another match of the same rule is still found; plain
    # finditer resumes after each match and would skip it.
    return re.compile(f"(?=({pattern.pattern}))", pattern.flags)


def _may_end_early(pattern: str) -> bool:
    """An alternative, or any repeat of variable length (greedy ones too: a greedy repeat
    can take text a later optional part needed, T03 review 15): the first match at a start
    may not be the longest, so the span scan looks for the longest end."""
    def walk(sub: _sre_parse.SubPattern) -> bool:
        for op, av in sub:
            name = str(op)
            if name == "BRANCH" or (name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT") and av[0] != av[1]):
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


def _longest_sources_checked(r: dict, frozen: bool) -> tuple[str | None, ...]:
    """The longest-match sources, each compiled at load in the form a line's search uses
    (forced to end at a position), so none is first compiled on a transaction (review 35)."""
    sources = _longest_sources(r, frozen)
    _end_forms(sources)
    return sources


def _longest_sources(r: dict, frozen: bool) -> tuple[str | None, ...]:
    """Per part of _rule_parts: the own pattern's source if it may end early. Names lists
    try their longest name first, so their first match is already the longest."""
    own = r.get("pattern")
    out: list[str | None] = []
    for part in _rule_parts(r, frozen):
        is_own = own is not None and part == (own if frozen else _mark_aware(own))
        # Judged on the pattern as written: the mark-aware word edge it is rewritten with
        # has alternatives of its own, which never change where a match can end.
        out.append(part if is_own and _may_end_early(own) else None)
    return tuple(out)


@functools.lru_cache(maxsize=None)
def _widest(source: str) -> int | None:
    """The most characters the pattern can match, or None if it has no bound."""
    widest = _sre_parse.parse(source).getwidth()[1]
    return None if widest >= _sre_parse.MAXREPEAT else widest


def _ends(source: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """The part forced to end at a position, in two forms built once per table at load
    (T03 review 36), never per line: ending at the line's end, and ending one character
    before the end of a window that stops one character after it. A table pattern's edges
    (\\b, \\B, and the mark-aware edges it is rewritten with) look at most one character
    past a match, so that one real character is all the second form needs to see."""
    return re.compile(f"(?:{source})\\Z"), re.compile(f"(?:{source})(?=[\\s\\S]\\Z)")


def _end_forms(sources: tuple[str | None, ...]) -> tuple:
    """Each longest-match source's two end-forced forms and its widest match."""
    return tuple(None if s is None else (*_ends(s), _widest(s)) for s in sources)


def _ends_at(forms: tuple[re.Pattern[str], re.Pattern[str]], description: str, start: int, end: int) -> bool:
    """The part (as its two end-forced forms) matches from `start` to exactly `end`."""
    at_line_end, before_one = forms
    if end == len(description):
        return at_line_end.match(description, start) is not None
    return before_one.match(description, start, end + 1) is not None


def _spans(rule: Rule, description: str) -> list[tuple[int, int]]:
    """Every match of the rule, at every start, the longest at each start across its parts
    and across a part's own alternatives (D102): the engine returns the first alternative
    that matches at a start, which may be the shorter."""
    longest: dict[int, int] = {}
    parts = rule.span_patterns or (_at_every_position(rule.pattern),)
    ends = rule.span_ends or _end_forms(rule.span_longest or (None,) * len(parts))
    for part, end_forms in zip(parts, ends, strict=True):
        for m in part.finditer(description):
            start, end = m.span(1)
            if end_forms is not None:
                widest = end_forms[2]
                top = len(description) if widest is None else min(len(description), start + widest)
                for later in range(top, end, -1):
                    if _ends_at(end_forms[:2], description, start, later):
                        end = later
                        break
            if end > start and end > longest.get(start, -1):
                longest[start] = end
    return sorted(longest.items())


def credit_matches(ruleset: Ruleset, description: str, amount: float) -> list[Rule]:
    """Every rule of `ruleset` that matches a line, in table order, less those whose every
    match lies inside a longer match of another rule (STRIPE inside STRIPE CAPITAL, REFUND
    inside HMRC REFUND, LOAN inside DIRECTOR LOAN): those count only as the longer one."""
    desc = _fold(description)
    hits = [r for r in ruleset.rules if rule_matches(r, desc, amount)]
    if len(hits) < 2:  # nothing for a match to lie inside: no spans needed
        return hits
    matched = [(r, _spans(r, desc)) for r in hits]

    # Each occurrence counts on its own (review 23): an occurrence is set aside when it lies
    # inside a strictly longer occurrence of another rule that itself counts. Cash wording
    # and payment-method wording never set each other aside, whichever list holds the
    # longer phrase (R4; reviews 19 and 21). A rule counts while any occurrence of it does,
    # and the bank's own code always counts (D101).
    occ = [(k, s, e) for k, (_, spans) in enumerate(matched) for s, e in spans]

    def kept(alive: set[int]) -> set[int]:
        out = set()
        for i, (k, s, e) in enumerate(occ):
            rule = matched[k][0]
            hidden = any(j in alive and k2 != k and not _rail_and_cash(rule, matched[k2][0])
                         and a <= s and e <= b and b - a > e - s
                         for j, (k2, a, b) in enumerate(occ))
            if rule.bank_code or not hidden:
                out.add(i)
        return out

    # Which occurrences count depends on which others count. Starting from all of them,
    # every second step keeps a subset of the step before, so the steps settle; the
    # settled set is the larger of the two that alternate, which sets aside the fewest
    # occurrences and so never hides wording that makes a credit ambiguous.
    alive = set(range(len(occ)))
    while True:
        nxt = kept(kept(alive))
        if nxt == alive:
            break
        alive = nxt
    counted = {occ[i][0] for i in alive}
    return [r for k, (r, spans) in enumerate(matched) if k in counted or not spans]


def _is_payment_method(rule: Rule) -> bool:
    return rule.category is None and rule.stage == "line_type"


def _rail_and_cash(a: Rule, b: Rule) -> bool:
    """One is cash paid-in wording and the other a payment-method cue."""
    return ((a.category is Category.CASH_DEPOSIT and _is_payment_method(b))
            or (b.category is Category.CASH_DEPOSIT and _is_payment_method(a)))


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
    # Cash wording in either stage (a table edit may list it as a cue): a payment-method cue
    # with cash wording is ambiguous by R4 whichever list the cash wording is on.
    if rails and any(r.category is Category.CASH_DEPOSIT for r in (*cues, *names)):
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
    # Debits by (amount in pence, payee), in statement order: each returned DD looks only
    # at the debits it could undo, latest first, not back through the whole statement.
    debits: dict[tuple[int, str], list[int]] = {}
    for j, o in enumerate(txns):
        if o.amount < 0 and o.category not in (Category.RETURNED_DD, None):
            debits.setdefault((round(-o.amount * 100), counterparty(o.description)), []).append(j)
    for i, r in enumerate(txns):
        if r.category != Category.RETURNED_DD or r.amount <= 0:
            continue
        candidates = debits.get((round(r.amount * 100), counterparty(r.description)), [])
        for j in reversed(candidates[: bisect.bisect_left(candidates, i)]):
            o = txns[j]
            if o.id not in taken and abs(o.amount + r.amount) < 0.005:
                r.reverses = o.id
                taken.add(o.id)
                break
    return statement


OUTGOING_TRANSFER = re.compile(r"^(?:FPS DEBIT|BACS DEBIT|TFR)\b")


def unmark_round_trips(statement: Statement, ruleset_id: str | None = None, *,
                       overridden: Collection[str] = ()) -> Statement:
    """A "customer" the applicant also sends transfers to is not counted as revenue.

    Money sent out by transfer and received back with an invoice reference is the
    cheapest way to manufacture revenue. Such credits become OTHER (and so show in M16).
    This also catches a genuine customer who is paid a refund by transfer: the cost is
    understated revenue, which a person can add back; the alternative is gameable.

    Only a category the built-in table set is undone: a person's override, a profile name
    or a firm rule ranks above the table and is left alone. Overridden lines are named by
    id (`overridden`), not inferred from their provenance text, which an override's own
    reference could imitate (T03 review 19).
    """
    overridden = set(overridden)
    paid_out = {counterparty(t.description) for t in statement.transactions
                if t.amount < 0 and OUTGOING_TRANSFER.search(t.description.upper())}
    for t in statement.transactions:
        set_by_table = t.id not in overridden and (
            ruleset_id is None or (t.rule_id or "").startswith(f"{ruleset_id}:"))
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
    return link_reversals(unmark_round_trips(statement, ruleset.id, overridden=overrides.keys()))
