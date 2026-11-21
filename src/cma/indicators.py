"""Risk indicators (R14), raised from thresholds in an editable file.

This is part of the figures layer (ADR 001): every comparison of money here is of integer
pence (R2), and a share or a multiple is compared exactly, as a fraction, never through a
binary float. The thresholds ship in cma/thresholds/indicators.yaml, a firm can give its own
file in the same form, and a threshold still at its shipped value is labelled illustrative
wherever it fires.

A thresholds file is validated before any indicator is raised from it. A malformed file is
refused with ThresholdsError naming the line of the file the problem is on: YAML that does
not parse, a section or key that is missing, unknown or given twice, and a value that is not
of its kind (an amount with a fraction of a penny, a share above 100, a count below one).

Each indicator lists the lines that made it fire and the thresholds it fired at, and says
why in one line that never quotes a line's description (R34).
"""

from __future__ import annotations

import hashlib
import re
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction
from functools import lru_cache
from importlib import resources
from pathlib import Path

import yaml

from .categorise import _SEPARATOR_RUN, _fold, names_pattern
from .metrics import at_limit, balance_pence, end_of_day_balances, pence, pounds_text, text_pence
from .models import (
    REVENUE_CATEGORIES,
    Category,
    Indicator,
    IndicatorThreshold,
    Statement,
    Transaction,
)

DEFAULT_THRESHOLDS_NAME = "indicators.yaml"
DEFAULT_SOURCE = f"cma/thresholds/{DEFAULT_THRESHOLDS_NAME}"

# Every section of the file, its keys and each key's kind, in the order indicators are raised.
SPEC: dict[str, dict[str, str]] = {
    "large_credit": {"multiple_of_typical": "times"},
    "single_cash_deposit": {"amount_at_least": "GBP"},
    "cash_heavy_month": {"share_above_pct": "pct"},
    "cash_round_trip": {"within_days": "days", "tolerance_pct": "pct"},
    "gambling": {"amount_at_least": "GBP", "operators": "names"},
    "returned_dd": {"count_at_least": "count"},
    "overdrawn_run": {"days_at_least": "count"},
    "irregular_acquirer": {"multiple_of_median": "times", "min_credits": "count"},
}
# The rule-table lists a named acquirer is read from, whatever category its line took.
ACQUIRER_RULES = ("card_acquirers", "payment_gateways")
WITHDRAWAL_RULE = "cash_withdrawal"  # v2's rule for cash taken out, read by the round trip

_DECIMAL = re.compile(r"\d+(?:\.\d+)?")
_INTEGER = re.compile(r"\d+")


class ThresholdsError(ValueError):
    """The thresholds file is malformed; no indicator is raised from it."""


@dataclass(frozen=True)
class Thresholds:
    source: str
    sha256: str
    values: dict[tuple[str, str], object]  # pence (int), Decimal, int or a tuple of names

    def value(self, section: str, key: str):
        return self.values[(section, key)]

    def fields(self) -> list[tuple[str, str]]:
        return list(self.values)

    def illustrative(self, section: str, key: str) -> bool:
        """Whether the threshold is still at its shipped value (compared as a value, so 2500
        and 2500.00 are the same pence)."""
        return self.values[(section, key)] == shipped().values[(section, key)]


def default_thresholds_text() -> str:
    return resources.files("cma.thresholds").joinpath(DEFAULT_THRESHOLDS_NAME).read_text()


@lru_cache(maxsize=1)
def shipped() -> Thresholds:
    """The packaged thresholds, the values R14 calls illustrative."""
    return parse_thresholds(default_thresholds_text(), DEFAULT_SOURCE)


def read_thresholds(path: Path | None = None) -> tuple[str, Thresholds]:
    """The text of the file at `path`, or of the packaged one, and its thresholds, validated."""
    if path is None:
        return default_thresholds_text(), shipped()
    source = str(path)
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise ThresholdsError(f"{source}: no such file") from e
    except (OSError, UnicodeDecodeError) as e:  # a directory, no permission, not UTF-8
        raise ThresholdsError(f"{source}: cannot be read ({e.__class__.__name__})") from e
    return text, parse_thresholds(text, source)


def load_thresholds(path: Path | None = None) -> Thresholds:
    """The file at `path`, or the packaged one, validated."""
    return read_thresholds(path)[1]


def _line(node) -> int:
    return node.start_mark.line + 1


def parse_thresholds(text: str, source: str) -> Thresholds:
    """Validate a thresholds file's text, refusing it naming the line of the first problem."""
    def refuse(line: int, problem: str):
        raise ThresholdsError(f"{source}: line {line}: {problem}")

    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.MarkedYAMLError as e:
        mark = e.problem_mark or e.context_mark
        # A problem met at the end of the stream is on the file's last line, not past it.
        line = min(mark.line + 1, max(1, len(text.splitlines()))) if mark else 1
        refuse(line, f"not valid YAML ({e.problem or e.__class__.__name__})")
    except yaml.YAMLError as e:
        refuse(1, f"not valid YAML ({e.__class__.__name__})")
    if root is None:
        refuse(1, "empty: every section of the thresholds file is required")
    if not isinstance(root, yaml.MappingNode):
        refuse(_line(root), "not a mapping of sections")

    sections = _mapping(root, set(SPEC), "section", refuse)
    for name in SPEC:
        if name not in sections:
            refuse(_line(root), f"missing section {name}")
    values: dict[tuple[str, str], object] = {}
    for name, keys in SPEC.items():
        key_node, node = sections[name]
        if not isinstance(node, yaml.MappingNode):
            refuse(_line(node), f"{name} must be a mapping of {', '.join(keys)}")
        given = _mapping(node, set(keys), f"key of {name}", refuse)
        for key, kind in keys.items():
            if key not in given:
                refuse(_line(key_node), f"{name}: missing {key}")
            values[(name, key)] = _value(name, key, kind, given[key][1], refuse)
    return Thresholds(source=source, sha256=hashlib.sha256(text.encode()).hexdigest(), values=values)


def _mapping(node, allowed: set[str], what: str, refuse) -> dict[str, tuple]:
    out: dict[str, tuple] = {}
    for k, v in node.value:
        if not isinstance(k, yaml.ScalarNode):
            refuse(_line(k), f"a {what} must be a name")
        if k.value in out:
            refuse(_line(k), f"{k.value} is given more than once")
        if k.value not in allowed:
            refuse(_line(k), f"unknown {what} {k.value!r}; expected one of {', '.join(sorted(allowed))}")
        out[k.value] = (k, v)
    return out


def _value(section: str, key: str, kind: str, node, refuse) -> object:
    where = f"{section}.{key}"
    if kind == "names":
        if not isinstance(node, yaml.SequenceNode) or not node.value:
            refuse(_line(node), f"{where} must be a list of one or more names")
        names = []
        for item in node.value:
            if not isinstance(item, yaml.ScalarNode) or not _SEPARATOR_RUN.sub("", _fold(item.value)):
                refuse(_line(item), f"{where}: each name must be text with a letter or digit in it")
            names.append(item.value)
        return tuple(names)
    if not isinstance(node, yaml.ScalarNode):
        refuse(_line(node), f"{where} must be a single value")
    text = node.value.strip()
    if kind == "GBP":
        if not _DECIMAL.fullmatch(text):
            refuse(_line(node), f"{where} {text!r} must be an amount in pounds")
        p = text_pence(text)
        if p is None:
            refuse(_line(node), f"{where} {text!r} must be a whole number of pence")
        if p <= 0 or at_limit(p):
            refuse(_line(node), f"{where} {text!r} must be above zero and below £10,000,000,000,000")
        return p
    if kind in ("pct", "times"):
        if not _DECIMAL.fullmatch(text):
            refuse(_line(node), f"{where} {text!r} must be a plain decimal number")
        d = Decimal(text)
        if kind == "pct" and d > 100:
            refuse(_line(node), f"{where} {text!r} must be a percentage from 0 to 100")
        if kind == "times" and d < 1:
            refuse(_line(node), f"{where} {text!r} must be a multiple of at least 1")
        return d
    if not _INTEGER.fullmatch(text):
        refuse(_line(node), f"{where} {text!r} must be a whole number")
    try:
        n = int(text)
    except ValueError:  # more digits than Python converts (sys.get_int_max_str_digits)
        refuse(_line(node), f"{where} has too many digits to be a whole number")
    if kind == "count" and n < 1:
        refuse(_line(node), f"{where} {text!r} must be at least 1")
    return n


# --- Raising the indicators ------------------------------------------------------------


def _money(p: int) -> str:
    return f"£{format(Decimal(abs(p)).scaleb(-2), ',.2f')}"


def _median(xs: list[int]) -> Fraction:
    s, n = sorted(xs), len(xs)
    return Fraction(s[n // 2]) if n % 2 else Fraction(s[n // 2 - 1] + s[n // 2], 2)


@lru_cache(maxsize=1)
def _acquirer_names() -> tuple[tuple[str, re.Pattern[str]], ...]:
    """(acquirer, pattern) for each name on v2's acquirer and gateway lists, longest first. An
    acquirer is its name without separators, so SUMUP and SUM UP are one acquirer."""
    table = yaml.safe_load(resources.files("cma.rules").joinpath("v2.yaml").read_text())
    names = [n for r in table["rules"] if r.get("id") in ACQUIRER_RULES for n in r.get("names", [])]
    names.sort(key=lambda n: (-len(n), n))
    return tuple((_SEPARATOR_RUN.sub("", _fold(n)), re.compile(names_pattern([n]))) for n in names)


@lru_cache(maxsize=16)
def _names_regex(names: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(names_pattern(names))


def _is_withdrawal(t: Transaction, amount: int) -> bool:
    return (t.rule_id or "").split(":", 1)[-1] == WITHDRAWAL_RULE and amount < 0


def raise_indicators(statement: Statement, thresholds: Thresholds | None = None) -> list[Indicator]:
    """Every indicator the categorised statement raises, in the order of SPEC and then of the
    statement. The statement is read, never changed."""
    t_ = thresholds or shipped()
    txns = statement.transactions
    for t in txns:  # a fraction of a penny is refused, as in metrics.compute()
        balance_pence(t)
    amt = {t.id: pence(t) for t in txns}  # each line's pence, converted once
    pos = {t.id: i for i, t in enumerate(txns)}
    found: list[tuple[str, list[tuple[str, str]], list[str], str]] = []

    def fire(kind: str, keys: list[tuple[str, str]], lines: list[Transaction] | list[str], reason: str):
        ids = [x if isinstance(x, str) else x.id for x in lines]
        found.append((kind, keys, sorted(dict.fromkeys(ids), key=pos.__getitem__), reason))

    credits = [t for t in txns if amt[t.id] > 0]
    trading = [t for t in credits if t.category in REVENUE_CATEGORIES]
    cash = [t for t in credits if t.category == Category.CASH_DEPOSIT]

    # A single credit large against the typical (median) trading receipt.
    k = Fraction(t_.value("large_credit", "multiple_of_typical"))
    if trading:
        typical = _median([amt[t.id] for t in trading])
        for t in credits:
            if amt[t.id] >= k * typical:
                fire("large_credit", [("large_credit", "multiple_of_typical")], [t],
                     f"A credit of {_money(amt[t.id])} is at least {k} times the median of the period's "
                     f"{len(trading)} trading receipts.")

    # Each cash deposit at or above the single-deposit limit.
    limit = t_.value("single_cash_deposit", "amount_at_least")
    for t in cash:
        if amt[t.id] >= limit:
            fire("single_cash_deposit", [("single_cash_deposit", "amount_at_least")], [t],
                 f"A cash deposit of {_money(amt[t.id])} is at or above the single-deposit limit of {_money(limit)}.")

    # A calendar month whose cash is above a share of its trading receipts.
    share = t_.value("cash_heavy_month", "share_above_pct")
    by_month: dict[tuple[int, int], list[Transaction]] = defaultdict(list)
    for t in trading:
        by_month[(t.date.year, t.date.month)].append(t)
    for (y, m), ts in sorted(by_month.items()):
        month_cash = [t for t in ts if t.category == Category.CASH_DEPOSIT]
        c, tr = sum(amt[t.id] for t in month_cash), sum(amt[t.id] for t in ts)
        if c > 0 and c * 100 > share * tr:
            fire("cash_heavy_month", [("cash_heavy_month", "share_above_pct")], month_cash,
                 f"In {y:04d}-{m:02d} cash deposits of {_money(c)} are above {share}% of the month's "
                 f"trading receipts of {_money(tr)}.")

    # Cash paid in within a window after a similar cash withdrawal.
    days = t_.value("cash_round_trip", "within_days")
    tol = t_.value("cash_round_trip", "tolerance_pct")
    # Withdrawals in (day, position) order, so each deposit's window is found by bisection
    # rather than by reading every withdrawal (R52: 10,000 lines recompute in about 2 seconds).
    withdrawals = sorted((t for t in txns if _is_withdrawal(t, amt[t.id])),
                         key=lambda w: (w.date.toordinal(), pos[w.id]))
    keys = [(w.date.toordinal(), pos[w.id]) for w in withdrawals]
    for d in cash:
        here = d.date.toordinal()
        window = withdrawals[bisect_left(keys, (here - days, -1)):bisect_left(keys, (here, pos[d.id]))]
        matches = sorted((w for w in window if abs(amt[d.id] + amt[w.id]) * 100 <= tol * -amt[w.id]),
                         key=lambda w: pos[w.id])
        if matches:
            fire("cash_round_trip", [("cash_round_trip", "within_days"), ("cash_round_trip", "tolerance_pct")],
                 [*matches, d],
                 f"A cash deposit of {_money(amt[d.id])} was paid in within {days} days after a cash withdrawal "
                 f"within {tol}% of its amount.")

    # Payments to gambling operators.
    least = t_.value("gambling", "amount_at_least")
    operators = _names_regex(t_.value("gambling", "operators"))
    for t in txns:
        if amt[t.id] < 0 and -amt[t.id] >= least and operators.search(_fold(t.description)):
            fire("gambling", [("gambling", "amount_at_least")], [t],
                 f"A payment of {_money(amt[t.id])} to a listed gambling operator.")

    # Returned direct debits.
    returned = [t for t in txns if t.category == Category.RETURNED_DD]
    if len(returned) >= t_.value("returned_dd", "count_at_least"):
        for t in returned:
            fire("returned_dd", [("returned_dd", "count_at_least")], [t],
                 f"A direct debit returned unpaid; the period has {len(returned)}.")

    # Runs of consecutive overdrawn days.
    run_at_least = t_.value("overdrawn_run", "days_at_least")
    eod = end_of_day_balances(txns)
    run: list[date] = []
    for d in [*sorted(eod), None]:
        if d is not None and eod[d][0] < 0:
            run.append(d)
            continue
        if len(run) >= run_at_least:
            fire("overdrawn_run", [("overdrawn_run", "days_at_least")], [eod[x][1] for x in run],
                 f"{len(run)} consecutive days closed overdrawn, from {run[0]} to {run[-1]}.")
        run = []

    # Irregular credits from a named acquirer, read from the acquirer and gateway lists.
    mult = Fraction(t_.value("irregular_acquirer", "multiple_of_median"))
    least_credits = t_.value("irregular_acquirer", "min_credits")
    by_acquirer: dict[str, list[Transaction]] = defaultdict(list)
    for t in credits:
        desc = _fold(t.description)
        acquirer = next((a for a, p in _acquirer_names() if p.search(desc)), None)
        if acquirer:
            by_acquirer[acquirer].append(t)
    irregular = []
    for ts in by_acquirer.values():
        if len(ts) < least_credits:
            continue
        med = _median([amt[t.id] for t in ts])
        irregular += [(t, len(ts)) for t in ts if amt[t.id] >= mult * med or amt[t.id] * mult <= med]
    for t, n in sorted(irregular, key=lambda x: pos[x[0].id]):
        fire("irregular_acquirer", [("irregular_acquirer", "multiple_of_median"), ("irregular_acquirer", "min_credits")],
             [t], f"A credit of {_money(amt[t.id])} from a named acquirer is at least {mult} times above or below "
                  f"the median of its {n} credits in the period.")

    out = []
    for n, (kind, keys, evidence, reason) in enumerate(found, start=1):
        ths = [IndicatorThreshold(key=f"{s}.{k}", value=_shown(t_.value(s, k), SPEC[s][k]), unit=SPEC[s][k],
                                  illustrative=t_.illustrative(s, k)) for s, k in keys]
        out.append(Indicator(id=f"I{n}", kind=kind, thresholds=ths, evidence=evidence, reason=reason,
                             illustrative=any(th.illustrative for th in ths)))
    return out


def _shown(v, unit: str) -> str:
    if unit == "GBP":
        return pounds_text(v)
    if isinstance(v, Decimal):
        return format(v, "f")
    return str(v)
