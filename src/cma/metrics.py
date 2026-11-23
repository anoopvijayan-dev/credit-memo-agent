"""Deterministic metrics with provenance.

This is the only place money gets added up. Every Metric lists the transaction ids
behind it, and the memo may only quote numbers that appear here. A metric that cannot
be computed from the statement has value None and says why; the policy decides what a
missing figure means (see `when_missing` in the policy file), not this module.

Money is added up in integer pence (R2): each amount is turned into whole pence once,
every sum and comparison is of pence, and a figure is turned back into pounds only when
it is reported. An amount with a fraction of a penny is refused, never rounded. Balances
are compared in pence too. There is no fixed ceiling: a figure is reported in pounds only
when that float reads back as exactly the pence it was computed from, and is otherwise
refused (a sum figure, AmountError) or n/a with the reason (M3), so reported buckets add
up as printed and no figure is ever a penny out.

Money in (R1, R3): every credit is in exactly one bucket (models.CREDIT_BUCKET), M2 is
trading receipts and M19-M25 the other seven buckets, and together they make M17 to the
penny. A case where they do not is refused with ReconciliationError, naming the
difference in pence and the lines involved; no figure is returned for it.

Returned direct debits: the categoriser links each one to the debit it undoes. Sums
here use the *effective* category, so a reversal of a loan repayment reduces debt
service, and a bounced-then-re-presented payment counts once.
"""

from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from .categorise import OUTGOING_TRANSFER, counterparty
from .models import (
    CREDIT_BUCKET,
    REVENUE_CATEGORIES,
    Bucket,
    Category,
    Metric,
    MonthlyCell,
    MonthRow,
    Statement,
    Transaction,
)

# HMRC VAT stagger groups: the months in which a VAT quarter ends.
VAT_STAGGERS: dict[int, tuple[int, ...]] = {1: (3, 6, 9, 12), 2: (1, 4, 7, 10), 3: (2, 5, 8, 11)}
VAT_DD_WORKING_DAYS = 3  # HMRC collects by Direct Debit up to three working days after the due date.
FINANCING = {Category.LOAN_REPAYMENT, Category.LOAN_DRAWDOWN, Category.OWNER_DRAWINGS}
# Money in that is neither trading receipts, a lending drawdown nor a returned DD. Under v1
# every such credit is OTHER; v2 names some of them (director funding, own pots, refunds,
# HMRC, grants), and M9 and M16 treat them all as v1 treats OTHER, so naming a credit
# never moves either figure (R10).
NON_TRADING_IN = {Category.OTHER, Category.OWN_ACCOUNT_TRANSFER, Category.DIRECTOR_FUNDING, Category.REFUND_IN,
                  Category.HMRC_REPAYMENT, Category.OTHER_EXPLAINED_IN}


# The figure each money-in bucket is reported as. Trading receipts are M2 (its id and name
# are kept so signed memos still read); the other seven are new (R1).
# The figures that are sums reconciled to the penny (R1, R3): shown to the penny wherever
# they are shown together, so a displayed breakdown always adds up to the displayed M17
# (T04 review 2).
EXACT_IDS = frozenset({"M2", "M17", "M18", "M19", "M20", "M21", "M22", "M23", "M24", "M25"})

BUCKET_METRICS: dict[Bucket, tuple[str, str, str]] = {
    Bucket.OWN_ACCOUNT_IN: ("M19", "Own-account transfers in", "Credits moved in from the applicant's own pots, savings or accounts."),
    Bucket.DIRECTOR_FUNDING: ("M20", "Director and shareholder funding",
                              "Credits from directors or shareholders, and capital introduced."),
    Bucket.LENDING_DRAWDOWNS: ("M21", "Lending drawdowns", "Loans and merchant cash advances paid in."),
    Bucket.REFUNDS_RETURNED: ("M22", "Refunds and returned payments in",
                              "Refunds, reversals and direct debits returned unpaid."),
    Bucket.HMRC_REPAYMENTS: ("M23", "HMRC repayments", "Credits from HMRC."),
    Bucket.OTHER_EXPLAINED_IN: ("M24", "Other explained money in", "Grants, insurance claims, interest and cashback."),
    Bucket.UNEXPLAINED: ("M25", "Unexplained money in",
                         "Credits no rule, firm rule or override explains, and credits whose wording is ambiguous."),
}
# Left out of the denominator M16 and M26 share: borrowing, money that only undoes a
# payment, and the applicant's own money moving between its accounts.
PROFILE_OWN_ACCOUNT = "profile:own_account"  # the rule a case profile's own-account name records (R6)
PAIRED = "pairing:"  # the rule an own-account pairing between a case's accounts records (R8)
NOT_IN_SHARE = {Category.LOAN_DRAWDOWN, Category.RETURNED_DD, Category.OWN_ACCOUNT_TRANSFER}


# --- Import arithmetic ----------------------------------------------------------------
# The loader and the importers read text and report problems; every sum, difference and
# conversion of money they need is done here, in the figures layer (R2, T04 review 13).

# A statement's amounts, balances, money in and money out are each below £10,000,000,000,000
# (revision 25): fifteen significant digits always round-trip through a float.
MAX_STATEMENT_PENCE = 10**15
AT_LIMIT = "£10,000,000,000,000 or more"  # how every problem at the limit says so


# The largest power of ten text_pence scales by: a decimal written past it (1e5000) is no
# amount or limit anyone writes, and building the integer would take unbounded time.
_MAX_PENCE_EXPONENT = 1000


def text_pence(text: str) -> int | None:
    """A plain decimal's whole pence, read exactly from its text, digit by digit, never through
    Decimal arithmetic, which rounds to its context's precision (28 digits): a limit written
    with a fraction of a penny past the 28th digit is not whole pence (T04 review 60). None if
    it is not whole pence."""
    try:
        d = Decimal(text.strip())
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    sign, digits, exponent = d.as_tuple()
    shift = exponent + 2  # the decimal's exponent once it is counted in pence
    if shift >= 0:
        if shift > _MAX_PENCE_EXPONENT:
            return None
        p = int(Decimal((0, digits, shift)))
    else:
        whole, fraction = digits[:shift], digits[shift:]
        if any(fraction):
            return None
        # From the digits as a Decimal, exactly, never through int() of a string, which
        # refuses more than 4,300 digits (T04 review 61).
        p = int(Decimal((0, whole or (0,), 0)))
    return -p if sign else p


def float_pence(v: float) -> int | None:
    """A float's whole pence, read exactly from the decimal it prints as; None if it is not whole pence."""
    return _whole_pence_exact(v)


def at_limit(p: int) -> bool:
    """Whether an amount or balance in pence is at or past the import limit."""
    return abs(p) >= MAX_STATEMENT_PENCE


def flows_at_limit(amounts) -> list[str]:
    """Which of money in and money out, as totals of these whole-pence amounts, reach the limit."""
    money_in = sum(a for a in amounts if a > 0)
    money_out = sum(-a for a in amounts if a < 0)
    return [name for name, flow in (("money in", money_in), ("money out", money_out)) if flow >= MAX_STATEMENT_PENCE]


def follows(previous: int, amount: int, balance: int) -> bool:
    """Whether a running balance follows from the one before it and the line's amount."""
    return previous + amount == balance


def opening_before(balance: int, amount: int) -> int:
    """The balance before a line, from the balance after it and its amount."""
    return balance - amount


def running_balances(opening: int, amounts) -> list[int]:
    """The balance after each line, walked from the opening balance, in whole pence."""
    out, bal = [], opening
    for a in amounts:
        bal += a
        out.append(bal)
    return out


def limit_pence(limit: float) -> int | None:
    """A money limit's whole pence, read exactly from the decimal it is written as; None if it
    is not a whole number of pence (T04 review 58). A limit read from a policy file carries
    the text it was written as (policy.load_policy), which is read, not the float YAML parsed
    it to, so no fraction of a penny is rounded away before it is checked (T04 review 59)."""
    text = getattr(limit, "text", None)
    if text is None:
        text = repr(limit) if isinstance(limit, float) else str(limit)
    return text_pence(text)


def compare(value: float, op: str, limit: float, unit: str) -> bool:
    """`value op limit` for a figure and a policy limit. A money figure is compared in whole
    pence here in the figures layer, never as binary floats or fractions of a penny (R2, T04
    reviews 57 to 59): the limit as the whole pence a policy is checked to give it, the figure
    as the whole pence every money figure is computed in (M3 included). A money figure or
    limit that is not whole pence, as a stored figure from an earlier release can be, is
    refused, never compared. Any other figure is compared as the number it is."""
    if unit == "GBP":
        b = limit_pence(limit)
        if b is None:
            raise ValueError(f"money limit {limit!r} is not a whole number of pence")
        a = float_pence(value)
        if a is None:
            raise ValueError(f"money figure {value!r} is not a whole number of pence")
    else:
        a, b = value, limit
    return {">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b, "==": a == b}[op]


def pounds_text(p: int) -> str:
    """Whole pence written as pounds with two decimals, exactly: -1234 is "-12.34"."""
    sign = "-" if p < 0 else ""
    # Written by Decimal, never str() of an int, which refuses more than 4,300 digits: a
    # balance of thousands of digits is named, not a crash (T04 review 62).
    return f"{sign}{Decimal(abs(p) // 100)}.{abs(p) % 100:02d}"


def pounds(p: int) -> float:
    """Whole pence as the float pounds a result carries, refused if not exact."""
    return _gbp(p)


# 10**15 pence (£10 trillion): every whole number of pence below it has at most 15
# significant digits, which a binary float holds and prints back exactly.
MAX_PENCE = 10**15  # bound the tests draw amounts from; figures are checked for exactness (T04 review 2)


class AmountError(ValueError):
    """An amount, balance or figure that cannot be counted in whole pence or reported
    exactly in pounds; nothing is computed from the statement."""


class ReconciliationError(ValueError):
    """The money-in buckets do not sum to total money in, or a credit is in no bucket or
    in more than one (R3). Carries the difference in pence and the ids of the lines."""

    def __init__(self, difference_pence: int, lines: list[str]):
        self.difference_pence = difference_pence
        self.lines = lines
        super().__init__(
            f"the money-in buckets do not reconcile with total money in: they differ by {difference_pence} "
            f"pence, and {len(lines)} line(s) are in no bucket, in more than one, or counted as trading "
            f"receipts without being money in: {', '.join(lines) or 'none'}. No figures are shown for this case."
        )


def _whole_pence(v: float, what: str) -> int:
    try:
        p = Decimal(repr(v)) * 100
    except InvalidOperation:
        p = None
    if p is None or not p.is_finite() or p != p.to_integral_value():
        raise AmountError(f"{what} is not a whole number of pence")
    return int(p)


def pence(t: Transaction) -> int:
    """The line's amount in whole pence. A fraction of a penny is refused, never rounded."""
    return _whole_pence(t.amount, f"line {t.id}: amount")


def size_pence(t: Transaction) -> int | None:
    """The line's amount in whole pence, without its sign, or None if it is not whole pence:
    the key the categoriser matches a returned payment to the debit it undoes by, computed
    here so no money arithmetic is done outside the figures layer (R2, T04 review 12)."""
    try:
        return abs(pence(t))
    except AmountError:
        return None


def balance_pence(t: Transaction) -> int:
    """The line's running balance in whole pence."""
    return _whole_pence(t.balance, f"line {t.id}: balance")


def _ratio(num: int | float, den: int | float, places: int, scale: int = 1) -> float:
    """num / den * scale, rounded half to even from the exact quotient of the pence, so a
    ratio of whole pence never rounds the wrong way through a binary float."""
    q = Decimal(num) * scale / Decimal(den)
    return float(q.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN))


def _div_half_even(num: int, den: int) -> int:
    """num / den rounded half to even, in integers only."""
    q, r = divmod(num, den)
    if 2 * r > den or (2 * r == den and q % 2):
        q += 1
    return q


def _gbp(p: int) -> float:
    """Pence back to pounds, for reporting only. The float is the one nearest the exact
    pounds; it is refused only when that float does not read back as the same pence, so no
    figure is ever reported a penny out, and no amount the DTO carries exactly is refused
    by a fixed ceiling (T04 review 2)."""
    v = p / 100
    if _whole_pence_exact(v) != p:
        raise AmountError(f"a figure of {p} pence cannot be reported to the penny")
    return v


def _whole_pence_exact(v: float) -> int | None:
    q = Decimal(repr(v)) * 100
    return int(q) if q.is_finite() and q == q.to_integral_value() else None


def _month(d: date) -> str:
    return d.strftime("%Y-%m")


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _add_months(y: int, m: int, n: int) -> tuple[int, int]:
    k = y * 12 + (m - 1) + n
    return k // 12, k % 12 + 1


DAYS_PER_MONTH = 365.25 / 12


def months_covered(start: date, end: date) -> int:
    """The statement period in whole months, to the nearest month: 1 Oct to 30 Sep is
    12, 25 Oct to 30 Sep is 11, 16 Oct to 15 Oct is 12."""
    return round(((end - start).days + 1) / DAYS_PER_MONTH)


def full_months(start: date, end: date) -> list[str]:
    """Calendar months wholly inside the period, oldest first."""
    out, (y, m) = [], (start.year, start.month)
    while (y, m) <= (end.year, end.month):
        if date(y, m, 1) >= start and _month_end(y, m) <= end:
            out.append(f"{y:04d}-{m:02d}")
        y, m = _add_months(y, m, 1)
    return out


def _add_working_days(d: date, n: int) -> date:
    while n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def vat_due_dates(stagger: int, start: date, end: date) -> list[tuple[date, date]]:
    """(quarter end, payment due) for every quarter whose whole payment window lies
    inside the statement. Due is one month and seven days after the quarter ends."""
    out = []
    y, m = _add_months(start.year, start.month, -1)
    while (y, m) <= (end.year, end.month):
        # A quarter outside the calendar's years (before year 1 or after 9999) has no dates:
        # it is skipped, never an error that would stop the reconciliation (T04 review 7).
        if m in VAT_STAGGERS[stagger] and y >= 1 and _add_months(y, m, 2)[0] <= 9999:
            q_end = _month_end(y, m)
            dy, dm = _add_months(y, m, 2)
            due = date(dy, dm, 7)
            # No date arithmetic that can leave the calendar (review 8): "ends no earlier than
            # the day before the start" as a difference, and a due date whose working days run
            # past year 9999 is outside the statement anyway.
            try:
                inside = _add_working_days(due, VAT_DD_WORKING_DAYS) <= end
            except OverflowError:
                inside = False
            if (start - q_end).days <= 1 and inside:
                out.append((q_end, due))
        y, m = _add_months(y, m, 1)
    return out


def end_of_day_balances(txns: list[Transaction]) -> dict[date, tuple[int, str]]:
    """Closing balance in pence for every calendar day from the first transaction to the
    last, carried forward over days with no transactions, with the id of the line that set it."""
    last: dict[date, Transaction] = {}
    for t in txns:  # file order; the last line of a day sets its closing balance
        last[t.date] = t
    out: dict[date, tuple[int, str]] = {}
    d, end = min(last), max(last)
    current = last[d]
    while True:
        current = last.get(d, current)
        out[d] = (balance_pence(current), current.id)
        if d == end:  # never step past the last day: it may be the calendar's last (T04 review 9)
            break
        d += timedelta(days=1)
    return out


# --- a case of more than one account (R41) ---------------------------------------------------

CASE_ID_SEP = ":"  # an account key holds no colon, so "<account>:<line id>" names one line of a case
NO_SHARED_DAYS = ("The case's accounts share no day: their statements' periods do not overlap, so there "
                  "is no combined end-of-day balance.")


def case_line_id(account: str, line_id: str) -> str:
    """A line's id within a case of more than one account: its own id prefixed with its
    account's key, so that ids are unique across the case (R41)."""
    return f"{account}{CASE_ID_SEP}{line_id}"


def split_case_line_id(case_id: str) -> tuple[str, str]:
    """The account key and the line's own id that case_line_id joined."""
    account, _, line_id = case_id.partition(CASE_ID_SEP)
    return account, line_id


def case_statement(statements: dict[str, Statement]) -> Statement:
    """Every line of the case's accounts as one statement, each id (and the id of the debit a
    returned payment undoes) prefixed with its account key, in date order, each account's own
    order kept within a day and the accounts in the order given (R41). Each line keeps its own
    account's running balance: a balance figure of the case reads case_closes(), never these."""
    lines = []
    for n, (account, s) in enumerate(statements.items()):
        for i, t in enumerate(s.transactions):
            lines.append(((t.date, n, i), t.model_copy(update={
                "id": case_line_id(account, t.id), "account_key": account,
                "reverses": case_line_id(account, t.reverses) if t.reverses else None})))
    return Statement(account_name="+".join(statements), transactions=[t for _, t in sorted(lines, key=lambda x: x[0])])


@dataclass(frozen=True)
class CaseCloses:
    """The combined end-of-day balance of a case's accounts (R41): on each day every account
    covers (from the latest first line to the earliest last line), the sum of every account's
    close, each carried over days with no lines, with the line of each account that set it.
    When those days do not exist, no closes and the reason, which every balance figure of the
    case gives as n/a."""

    closes: dict[date, tuple[int, tuple[str, ...]]]
    reason: str | None = None


def case_closes(statements: dict[str, Statement]) -> CaseCloses:
    """The combined end-of-day balance over the days every account of the case covers, the
    setting lines named by their case ids (case_line_id)."""
    eods = {a: end_of_day_balances(s.transactions) for a, s in statements.items()}
    first, last = max(min(e) for e in eods.values()), min(max(e) for e in eods.values())
    if first > last:
        return CaseCloses({}, NO_SHARED_DAYS)
    out: dict[date, tuple[int, tuple[str, ...]]] = {}
    d = first
    while True:
        out[d] = (sum(e[d][0] for e in eods.values()),
                  tuple(case_line_id(a, e[d][1]) for a, e in eods.items()))
        if d == last:
            break
        d += timedelta(days=1)
    return CaseCloses(out)


def _closes_balance_figures(closes: CaseCloses, pos: dict[str, int]):
    """M5, M6 and M27 of a case of more than one account, from its combined closes (R41):
    each derived, citing the lines that set the closes it reads (P2); n/a with the reason when
    the accounts share no day, or when no float carries a combined balance to the penny (R2)."""
    def order(ids) -> list[str]:
        return sorted(set(ids), key=pos.__getitem__)

    if closes.reason is not None:
        why = closes.reason
        return ((None, why, []), (None, why, []), (None, why, []))
    days = sorted(closes.closes)
    low_day = min(days, key=lambda d: closes.closes[d][0])
    low = closes.closes[low_day][0]
    m5 = ((low / 100 if _whole_pence_exact(low / 100) == low else None),
          ("Lowest combined end-of-day balance of the case's accounts, over the days every account covers."
           if _whole_pence_exact(low / 100) == low else "The lowest combined end-of-day balance is too large "
           "to report to the penny."), order(closes.closes[low_day][1]))
    over = [d for d in days if closes.closes[d][0] < 0]
    m6 = (len(over), ("Calendar days, of those every account covers, whose combined close was overdrawn, each "
                      "account's balance carried over days with no transactions."),
          order(i for d in over for i in closes.closes[d][1]))
    avg = _div_half_even(sum(closes.closes[d][0] for d in days), len(days))
    exact = _whole_pence_exact(avg / 100) == avg
    m27 = ((avg / 100 if exact else None),
           ("The combined closing balance of the case's accounts on every calendar day every account covers, "
            "each carried over days with no transactions, averaged over those days." if exact
            else "The average combined end-of-day balance is too large to report to the penny."),
           order(i for d in days for i in closes.closes[d][1]))
    return m5, m6, m27


def compute_case(statements: dict[str, Statement], vat_stagger: int | None = None, proposed_repayment=None
                 ) -> tuple[Statement, CaseCloses, list[Metric], list[MonthRow]]:
    """The figures of a case of more than one account (R41), each account's statement already
    categorised and its own-account pairs settled (overrides.case_figures,
    categorise.pair_own_accounts): line ids prefixed with the account key, flow figures across every account
    (own-account transfers, from the profile or a pairing, out of trading receipts as for one
    account), and balance figures on the combined end-of-day balance over the days every
    account covers, n/a with the reason when they share none."""
    statement = case_statement(statements)
    closes = case_closes(statements)
    return (statement, closes, compute(statement, vat_stagger, proposed_repayment, closes=closes),
            monthly_summary(statement, closes=closes))


def proposed_repayment_pence(proposed) -> int:
    """The proposed facility's monthly repayment in whole pence, read exactly from the text it
    is written as (a float from the repr it prints as), as a policy limit is (R2). Refused,
    never rounded, unless it is a positive whole number of pence below the import limit."""
    p = limit_pence(proposed)
    if p is None or p <= 0 or at_limit(p):
        raise ValueError(f"proposed monthly repayment {proposed!r} is not a positive whole number of pence "
                         "below £10,000,000,000,000")
    return p


# --- Shared calculations ------------------------------------------------------------------
# Each is the one place its figure's calculation lives: compute() reads it for the figure,
# and derived_inputs() for the inputs the drill-down shows beside it (R16), so the inputs
# shown are always the ones the figure was computed from.


def revenue_quarters(rev: list[Transaction], start: date, end: date
                     ) -> tuple[tuple[list[str], int], tuple[list[str], int]] | None:
    """M4's two quarters: the earliest and the latest three whole calendar months, each with
    its trading receipts in pence; None when the statement has fewer than six whole months."""
    whole = full_months(start, end)
    if len(whole) < 6:
        return None
    by_month: dict[str, int] = defaultdict(int)
    for t in rev:
        by_month[_month(t.date)] += pence(t)
    first, last = whole[:3], whole[-3:]
    return (first, sum(by_month.get(m, 0) for m in first)), (last, sum(by_month.get(m, 0) for m in last))


@dataclass
class OperatingCash:
    """M9's parts. operating: its lines, statement order apart from the sent-back credits at
    the end; trading: the total of its lines other than money in that is not trading
    receipts; offset: what those sent-back credits net off, never more than was sent to each
    payer; counted: how much of each sent-back credit counts, in pence; capped: the credit
    that counts only in part, at most one per payer."""

    operating: list[Transaction]
    trading: int
    offset: int
    counted: dict[str, int]
    capped: list[str]

    @property
    def net(self) -> int:
        return self.trading + self.offset


def _eff(t: Transaction, by_id: dict[str, Transaction]) -> Category:
    """A returned DD counts in the category of the debit it reverses. One whose debit is not
    in the statement counts as itself (it is refused after the reconciliation)."""
    return by_id[t.reverses].category if t.reverses in by_id else t.category


def operating_cash(txns: list[Transaction], by_id: dict[str, Transaction]) -> OperatingCash:
    """Net operating cash flow (M9) and the parts it is computed from."""
    pos = {t.id: i for i, t in enumerate(txns)}

    def total(lines) -> int:
        return sum(pence(t) for t in lines)

    # Operating cash flow is trading cash: money out that is not financing, and money in
    # that is revenue or that reverses an operating payment. Money in that nobody has
    # explained (OTHER: capital introduced, savings, refunds) is left out, so it can never
    # lift cover; it shows in M16 instead. A returned DD that matches no debit is left out
    # too, for the same reason.

    # A line the case profile names as one of the applicant's other accounts, or that an
    # own-account pairing between a case's accounts settled (R8), is the applicant's own money
    # moving, not operating cash, in either direction (R6): it is never an operating line, and never sent or sent back in the round trip below. A move to or
    # from an own pot that the built-in table recognises keeps the treatment v1 gives the same
    # line, so M1 to M16 do not move under v2 (R10, P7); and with no case profile no line is
    # named, so M9 is the previous release's (R54).
    # A returned DD that undoes such a line is the same money coming back, so it is named too.
    def own_account_named(t: Transaction) -> bool:
        u = by_id.get(t.reverses, t) if t.reverses else t
        return u.category == Category.OWN_ACCOUNT_TRANSFER and (
            u.rule_id == PROFILE_OWN_ACCOUNT or (u.rule_id or "").startswith(PAIRED))

    def operating_line(t: Transaction) -> bool:
        if _eff(t, by_id) in FINANCING or own_account_named(t):
            return False
        if pence(t) <= 0:
            return True
        if t.category == Category.RETURNED_DD:
            return t.reverses is not None
        return t.category in REVENUE_CATEGORIES

    operating = [t for t in txns if operating_line(t)]
    # Money sent to someone by transfer and sent back by them (a savings account, a
    # friend, a round trip) nets off: their unexplained credits offset the transfers to
    # them, but never by more than was sent, so the round trip can't add cash.
    sent: dict[str, int] = defaultdict(int)
    for t in txns:
        if pence(t) < 0 and operating_line(t) and OUTGOING_TRANSFER.search(t.description.upper()):
            sent[counterparty(t.description)] += -pence(t)
    back: dict[str, list[Transaction]] = defaultdict(list)
    for t in txns:
        if (pence(t) > 0 and t.category in NON_TRADING_IN and not own_account_named(t)
                and counterparty(t.description) in sent):
            back[counterparty(t.description)].append(t)
    offset = sum(min(total(ts), sent[cp]) for cp, ts in back.items())
    # A derived figure (P2): the cap is allocated to a counterparty's credits in statement
    # order, so one credit at most counts in part, and only that one is named in the evidence
    # note; the ones before it count in full and the ones after it not at all (T04 review 6).
    capped: list[str] = []
    counted: dict[str, int] = {}
    for cp, ts in back.items():
        left = sent[cp]
        for t in sorted(ts, key=lambda x: pos[x.id]):
            if 0 < left < pence(t):
                capped.append(t.id)
            counted[t.id] = min(left, pence(t))
            left = max(0, left - pence(t))
    capped.sort(key=pos.__getitem__)
    operating += [t for ts in back.values() for t in ts]
    trading = total(t for t in operating if not (pence(t) > 0 and t.category in NON_TRADING_IN))
    return OperatingCash(operating, trading, offset, counted, capped)


@dataclass
class VatQuarter:
    """One VAT quarter M13 judged: its end, the payment's due date, the last day of the Direct
    Debit collection window, and the payment that stuck within it (None: late or missed)."""

    quarter_end: date
    due: date
    window_end: date
    paid: str | None


def vat_quarters(vat: list[Transaction], reversed_ids: set[str], stagger: int, start: date, end: date
                 ) -> list[VatQuarter]:
    """Every VAT quarter of the stagger group whose payment window lies in the statement, and
    the payment counted for it, as M13 counts them."""
    used: set[str] = set()
    out: list[VatQuarter] = []
    for q_end, d in vat_due_dates(stagger, start, end):
        window_end = _add_working_days(d, VAT_DD_WORKING_DAYS)
        paid = next(
            (t for t in vat if t.id not in used and t.id not in reversed_ids and pence(t) < 0
             and q_end < t.date <= window_end),
            None,
        )
        if paid:
            used.add(paid.id)
        out.append(VatQuarter(q_end, d, window_end, paid.id if paid else None))
    return out


def compute(statement: Statement, vat_stagger: int | None = None, proposed_repayment=None,
            closes: CaseCloses | None = None) -> list[Metric]:
    """Every figure for the statement. `proposed_repayment` is the proposed facility's monthly
    repayment from the case profile (R13), in pounds, as text, a Decimal, an int or a float;
    `cma run` has no case profile and gives none, so M28 is n/a with the reason there.
    `closes`, for a case of more than one account (compute_case), is the combined end-of-day
    balance the balance figures M5, M6 and M27 are read from instead of the lines' own (R41)."""
    txns = statement.transactions
    # A debit with no category cannot be placed in any figure: refused, naming it. A credit
    # with none is in no bucket, so it is left to the reconciliation below, which names it
    # with every other line that is not in exactly one bucket and the whole difference
    # (R3, T04 review 4).
    # A line with no category: a credit is in no bucket and is named by the reconciliation
    # below with every other unplaced credit and the whole difference; a debit is refused
    # after that, so it never hides the credits' report (R3, T04 reviews 4 and 5).
    loose_debits = [t for t in txns if t.category is None and (_whole_pence_exact(t.amount) or 0) <= 0]
    if vat_stagger is not None and vat_stagger not in VAT_STAGGERS:
        raise ValueError("vat_stagger must be 1, 2 or 3")
    proposed = None if proposed_repayment is None else proposed_repayment_pence(proposed_repayment)

    by_id = statement.by_id()
    for t in txns:  # refuse a fraction of a penny before any figure is computed
        pence(t)
        balance_pence(t)

    def total(lines) -> int:
        return sum(pence(t) for t in lines)

    dangling = [t for t in txns if t.reverses and t.reverses not in by_id]

    def eff(t: Transaction) -> Category:
        """A returned DD counts in the category of the debit it reverses. One whose debit is
        not in the statement counts as itself, and is refused after the reconciliation, so it
        never hides an unplaced credit's report (T04 review 6)."""
        return _eff(t, by_id)

    def of(*cats: Category) -> list[Transaction]:
        return [t for t in txns if eff(t) in cats]

    start, end = statement.start, statement.end
    n_months = months_covered(start, end)
    period_days = (end - start).days + 1  # in months: period_days / DAYS_PER_MONTH = period_days * 48 / 1461

    # Revenue
    rev = [t for t in txns if t.category in REVENUE_CATEGORIES]
    total_revenue = total(rev)
    whole = full_months(start, end)
    trend: float | None = None
    trend_note = (
        "Average revenue of the latest quarter of whole calendar months against the earliest quarter, "
        "as a percentage change. On a year-long statement these are opposite seasons."
    )
    quarters = revenue_quarters(rev, start, end)
    if quarters is None:
        trend_note = "Needs more whole calendar months than this statement has."
    else:
        (_, first3), (_, last3) = quarters  # quarters of equal length: the averages' 1/3 cancels
        if first3 > 0:
            trend = _ratio(last3 - first3, first3, 2, 100)
        else:
            trend_note = "No revenue in the earliest quarter of whole months, so there is no base to compare with."

    # Balances
    lowest = min(txns, key=balance_pence)
    eod = end_of_day_balances(txns)
    overdrawn = sorted(d for d, (bal, _) in eod.items() if bal < 0)
    pos = {t.id: i for i, t in enumerate(txns)}
    # M1-M16 keep the evidence the previous release gave (dual run, R54): M6 cites the line
    # that closed each overdrawn day (P2 revision 25).
    overdrawn_evidence = sorted({eod[d][1] for d in overdrawn}, key=pos.__getitem__)

    # Obligations
    returned = [t for t in txns if t.category == Category.RETURNED_DD]
    loan = of(Category.LOAN_REPAYMENT)
    drawings = of(Category.OWNER_DRAWINGS)
    vat = of(Category.HMRC_VAT)
    paye = of(Category.HMRC_PAYE)
    debt_service = -total(loan)
    owner_drawings = -total(drawings)

    reversed_ids = {t.reverses for t in txns if t.reverses}
    cash_flow = operating_cash(txns, by_id)
    operating, capped, net_operating = cash_flow.operating, cash_flow.capped, cash_flow.net
    dscr: float | None = None
    dscr_note = (
        "Historical cover of existing debt only: net operating cash flow after owner drawings, "
        "divided by debt service. It does not include the facility being applied for."
    )
    if debt_service > 0:
        dscr = _ratio(net_operating - owner_drawings, debt_service, 4)
    else:
        dscr_note = "No loan or finance repayments in the statement, so there is no existing debt to cover."

    # M28 (R13): the proposed monthly repayment for each month of the period, counted in the
    # exact length of the period in months as M3 counts it (period_days * 48 / 1461), added to
    # existing debt service. Kept in integers: cover = cash * 1461 / (debt * 1461 + proposed *
    # period_days * 48), rounded half to even from the exact quotient (P2: a derived figure).
    cover_lines = sorted(operating + drawings + loan, key=lambda t: pos[t.id])
    cover: float | None = None
    if proposed is None:
        cover_note = ("No proposed monthly repayment was given: it comes from the case profile, and this "
                      "statement has none, so cover including the proposed facility cannot be computed.")
    else:
        cover_den = debt_service * 1461 + proposed * period_days * 48
        cover_note = ("Net operating cash flow after owner drawings, divided by debt service plus the proposed "
                      "monthly repayment for each month of the period (its exact length in months).")
        if cover_den > 0:
            cover = _ratio((net_operating - owner_drawings) * 1461, cover_den, 4)
        else:
            cover_note = "Debt service with the proposed repayment is not above zero, so there is nothing to cover."

    # Concentration: only knowable for customers who pay by named transfer.
    transfers = [t for t in txns if t.category == Category.REVENUE_TRANSFER]
    by_payer: dict[str, int] = defaultdict(int)
    for t in transfers:
        by_payer[counterparty(t.description)] += pence(t)
    top_share: float | None = None
    top_note = "Largest payer's share of customer transfer revenue, payers named after removing invoice references."
    if by_payer and sum(by_payer.values()) > 0:
        top_share = _ratio(max(by_payer.values()), sum(by_payer.values()), 2, 100)
    else:
        top_note = "No customer transfers: revenue is card or gateway settled, which hides who the customers are."

    # VAT timeliness against the business's own stagger group.
    late_vat: int | None = None
    vat_note = (
        "VAT quarters in the business's stagger group with no payment that stuck (not returned "
        "unpaid) between the quarter end and the Direct Debit collection days after the due date."
    )
    if vat_stagger is None:
        vat_note = "VAT stagger group not given (it is on the VAT certificate), so due dates are unknown."
    else:
        checked = vat_quarters(vat, reversed_ids, vat_stagger, start, end)
        if not checked:
            vat_note = "No VAT quarter falls wholly inside the statement period."
        else:
            late_vat = sum(1 for q in checked if q.paid is None)

    # Money in, by bucket (R1). Every credit goes to the bucket its category maps to; M2's
    # lines (REVENUE_CATEGORIES) are the trading receipts bucket.
    credits = [t for t in txns if pence(t) > 0]
    buckets: dict[Bucket, list[Transaction]] = {b: [] for b in BUCKET_METRICS}
    for t in credits:
        b = CREDIT_BUCKET.get(t.category)
        if b in buckets:
            buckets[b].append(t)
    money_in = total(credits)
    _reconcile(credits, rev, buckets, money_in, total_revenue, total, pos)
    if dangling:
        raise AmountError(f"line {dangling[0].id} reverses {dangling[0].reverses}, which is not in the statement")
    if loose_debits:
        raise AmountError(f"line {loose_debits[0].id} has no category: categorise() must run before compute()")
    cash = [t for t in rev if t.category == Category.CASH_DEPOSIT]

    # M16 and M26 share one denominator: money in less lending drawdowns, returned DDs and
    # own-account transfers. A credit an ambiguous verdict left unexplained is in both numerators.
    inflows = [t for t in credits if eff(t) not in NOT_IN_SHARE and t.category not in NOT_IN_SHARE]
    unclassified = [t for t in inflows if t.category in NON_TRADING_IN]
    unexplained = [t for t in inflows if t.category == Category.OTHER]
    total_in = total(inflows)
    unclassified_share = _ratio(total(unclassified), total_in, 2, 100) if total_in else None
    unexplained_share = _ratio(total(unexplained), total_in, 2, 100) if total_in else None

    def g(p: int | None) -> float | None:
        return None if p is None else _gbp(p)

    # M3 in whole pence from the exact quotient (never through a float), and n/a with the
    # reason when no float carries those pence exactly: it is derived, so it never withholds
    # the sum figures (verify-128).
    avg_pence = _div_half_even(total_revenue * 1461, period_days * 48)
    avg_revenue: float | None = avg_pence / 100
    avg_note = "Total revenue divided by the exact length of the period in months (quiet months still count)."
    if _whole_pence_exact(avg_revenue) != avg_pence:
        avg_revenue = None
        avg_note = "Average monthly revenue is too large to report to the penny."

    # M27 (R12): the close of every calendar day from the first line to the last, a day with no
    # lines carrying the close before it, averaged in whole pence (half to even), citing the line
    # that set each of those closes (P2: a derived figure). n/a with the reason when no float
    # carries those pence (R2).
    day_closes = list(eod.values())
    eod_avg_pence = _div_half_even(sum(bal for bal, _ in day_closes), len(day_closes))
    eod_avg: float | None = eod_avg_pence / 100
    eod_note = ("The closing balance of every calendar day in the period, carried over days with no "
                "transactions, averaged over those days.")
    if _whole_pence_exact(eod_avg) != eod_avg_pence:
        eod_avg = None
        eod_note = "The average end-of-day balance is too large to report to the penny."
    eod_setters = sorted({line_id for _, line_id in day_closes}, key=pos.__getitem__)

    m5 = (_gbp(balance_pence(lowest)), "Lowest running balance after any transaction in the period.", [lowest.id])
    m6 = (len(overdrawn), "Calendar days that closed overdrawn, carrying the balance over days with no transactions.",
          overdrawn_evidence)
    m27 = (eod_avg, eod_note, eod_setters)
    if closes is not None:  # a case of more than one account: the combined closes (R41)
        m5, m6, m27 = _closes_balance_figures(closes, pos)

    return [
        Metric(id="M1", name="Months covered", value=n_months, unit="months",
               method="Days from the first transaction to the last, in months, to the nearest month.",
               evidence=[txns[0].id, txns[-1].id]),
        Metric(id="M2", name="Total revenue", value=g(total_revenue), unit="GBP",
               method="Card and gateway settlements, transfers in that carry an invoice reference, and cash paid in.",
               evidence=[t.id for t in rev]),
        Metric(id="M3", name="Average monthly revenue", value=avg_revenue, unit="GBP", method=avg_note,
               evidence=[t.id for t in rev]),
        Metric(id="M4", name="Revenue trend", value=trend, unit="pct", method=trend_note,
               evidence=[t.id for t in rev if _month(t.date) in whole[:3] + whole[-3:]] if trend is not None else []),
        Metric(id="M5", name="Lowest balance", value=m5[0], unit="GBP", method=m5[1], evidence=m5[2]),
        Metric(id="M6", name="Days overdrawn", value=m6[0], unit="days", method=m6[1], evidence=m6[2]),
        Metric(id="M7", name="Returned direct debits", value=len(returned), unit="count",
               method="Lines marked as unpaid direct debits.",
               evidence=[t.id for t in returned]),
        Metric(id="M8", name="Debt service", value=g(debt_service), unit="GBP",
               method="Loan and finance repayments, net of any that were returned unpaid.",
               evidence=[t.id for t in loan]),
        Metric(id="M9", name="Net operating cash flow", value=g(net_operating), unit="GBP",
               method=("Revenue in, minus all money out, excluding loans and drawings. Money in that is not "
                       "explained as trading is left out, except that money sent back by someone the "
                       "business paid by transfer nets off what it sent them."),
               evidence=[t.id for t in operating], evidence_note=capped or None),
        Metric(id="M10", name="Debt service cover", value=dscr, unit="ratio", method=dscr_note,
               evidence=[t.id for t in operating + drawings + loan] if dscr is not None else []),
        Metric(id="M11", name="Owner drawings", value=g(owner_drawings), unit="GBP",
               method="Transfers to the owner or directors and dividends.", evidence=[t.id for t in drawings]),
        Metric(id="M12", name="VAT paid", value=g(-total(vat)), unit="GBP",
               method="Payments to HMRC VAT, net of any returned unpaid.", evidence=[t.id for t in vat]),
        Metric(id="M13", name="Late or missed VAT payments", value=late_vat, unit="count",
               method=vat_note, evidence=[t.id for t in vat] if late_vat is not None else []),
        Metric(id="M14", name="PAYE paid", value=g(-total(paye)), unit="GBP",
               method="Payments to HMRC PAYE/NI, net of any returned unpaid.", evidence=[t.id for t in paye]),
        Metric(id="M15", name="Top customer share of transfer revenue", value=top_share, unit="pct",
               method=top_note, evidence=[t.id for t in transfers]),
        Metric(id="M16", name="Non-trading money in (share)", value=unclassified_share, unit="pct",
               method=("Share of money in, less lending drawdowns, returned direct debits and own-account "
                       "transfers, that is not trading receipts; it is left out of operating cash flow."),
               evidence=[t.id for t in inflows]),
        Metric(id="M17", name="Total money in", value=g(money_in), unit="GBP",
               method="Every credit in the period; the money-in buckets (M2, M19-M25) add up to it.",
               evidence=[t.id for t in credits]),
        Metric(id="M18", name="Cash deposits within trading receipts", value=g(total(cash)), unit="GBP",
               method="Cash paid in at a counter or machine. Part of M2, not added to it again.",
               evidence=[t.id for t in cash]),
        *[Metric(id=fid, name=name, value=g(total(buckets[b])), unit="GBP", method=method,
                 evidence=[t.id for t in buckets[b]])
          for b, (fid, name, method) in BUCKET_METRICS.items()],
        Metric(id="M26", name="Unexplained share of money in", value=unexplained_share, unit="pct",
               method=("Unexplained money in (M25) as a share of money in less lending drawdowns, returned "
                       "direct debits and own-account transfers, the denominator M16 uses."),
               evidence=[t.id for t in inflows]),
        Metric(id="M27", name="Average end-of-day balance", value=m27[0], unit="GBP", method=m27[1],
               evidence=m27[2]),
        Metric(id="M28", name="Debt service cover including the proposed facility", value=cover, unit="ratio",
               method=cover_note, evidence=[t.id for t in cover_lines] if cover is not None else []),
    ]


def monthly_summary(statement: Statement, closes: CaseCloses | None = None) -> list[MonthRow]:
    """One row per calendar month in the period, oldest first (R11).

    Money in is every credit of the month and trading receipts the month's M2 lines, cash
    deposits included (R55): both are sum figures, so the months add up to M17 and M2 to the
    penny (P12). Money out is an outflow figure, reported as a positive amount whose lines
    total it negated (P2, D220). The average and lowest end-of-day balance and the days
    overdrawn are derived from the close of every day of the month in the period, carried
    over days with no lines, and cite the line that set each of those closes (P2, D225).

    For a case of more than one account (`closes`, compute_case), the balance and overdrawn-day
    cells read the combined closes of the month's days every account covers, citing every
    account's line that set them, and are n/a with the reason in a month with none (R41)."""
    txns = statement.transactions
    for t in txns:  # a fraction of a penny is refused, as in compute()
        pence(t)
        balance_pence(t)
    pos = {t.id: i for i, t in enumerate(txns)}
    eod = end_of_day_balances(txns)
    start, end = statement.start, statement.end

    def flow(lines: list[Transaction], kind: str) -> MonthlyCell:
        signed = sum(pence(t) for t in lines)
        value = -signed if kind == "outflow" else signed
        return MonthlyCell(value=_gbp(value), unit="GBP", kind=kind, evidence=[t.id for t in lines],
                           total=_gbp(signed))

    rows: list[MonthRow] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        key = f"{y:04d}-{m:02d}"
        first, last = max(date(y, m, 1), start), min(_month_end(y, m), end)
        # By (year, month), not by _month's text: strftime does not pad a year below 1000.
        lines = [t for t in txns if (t.date.year, t.date.month) == (y, m)]
        balance_cells = _month_balance_cells(eod, closes, first, last, pos)
        rows.append(MonthRow(
            month=key,
            part_month=first != date(y, m, 1) or last != _month_end(y, m),
            start=first,
            end=last,
            money_in=flow([t for t in lines if pence(t) > 0], "sum"),
            money_out=flow([t for t in lines if pence(t) < 0], "outflow"),
            trading_receipts=flow([t for t in lines if t.category in REVENUE_CATEGORIES], "sum"),
            **balance_cells,
        ))
        y, m = _add_months(y, m, 1)
    return rows


NO_SHARED_DAYS_IN_MONTH = ("No day of this month is covered by every account of the case, so there is no "
                           "combined end-of-day balance.")


def _month_balance_cells(eod, closes: CaseCloses | None, first: date, last: date, pos) -> dict[str, MonthlyCell]:
    """A month's average and lowest end-of-day balance and days overdrawn (derived cells):
    from the account's own closes, or, for a case of more than one account, from the combined
    closes of the month's days every account covers (R41)."""
    if closes is None:
        days = [(bal, (line_id,)) for d, (bal, line_id) in sorted(eod.items()) if first <= d <= last]
    else:
        days = [closes.closes[d] for d in sorted(closes.closes) if first <= d <= last]
    if not days:
        why = (closes.reason if closes is not None else None) or NO_SHARED_DAYS_IN_MONTH
        return {key: MonthlyCell(value=None, unit=unit, kind="derived", note=why)
                for key, unit in (("average_balance", "GBP"), ("lowest_balance", "GBP"), ("days_overdrawn", "days"))}
    bals = [bal for bal, _ in days]
    setters = sorted({i for _, ids in days for i in ids}, key=pos.__getitem__)
    avg_pence = _div_half_even(sum(bals), len(bals))
    avg: float | None = avg_pence / 100
    avg_note = None
    if _whole_pence_exact(avg) != avg_pence:  # a derived figure: n/a with the reason (R2)
        avg, avg_note = None, "The month's average end-of-day balance is too large to report to the penny."
    low_pence = min(bals)
    if closes is None:
        low, low_note = _gbp(low_pence), None
    elif _whole_pence_exact(low_pence / 100) == low_pence:
        low, low_note = low_pence / 100, None
    else:  # a combined balance no float carries to the penny: n/a with the reason (R2, R41)
        low, low_note = None, "The month's lowest combined end-of-day balance is too large to report to the penny."
    return {
        "average_balance": MonthlyCell(value=avg, unit="GBP", kind="derived", evidence=setters, note=avg_note),
        "lowest_balance": MonthlyCell(value=low, unit="GBP", kind="derived", evidence=setters, note=low_note),
        "days_overdrawn": MonthlyCell(value=sum(1 for b in bals if b < 0), unit="days", kind="derived",
                                      evidence=setters),
    }


def _reconcile(credits, rev, buckets, money_in, trading, total, pos) -> None:
    """R3: the buckets sum to total money in to the penny, and every credit is in exactly
    one bucket's evidence. Otherwise refuse, naming the difference and the lines."""
    placed = [*rev, *(t for ts in buckets.values() for t in ts)]
    counts = Counter(t.id for t in placed)
    credit_ids = Counter(t.id for t in credits)
    involved = {t.id for t in credits if counts[t.id] != 1 or credit_ids[t.id] != 1}
    involved |= {t.id for t in placed if pence(t) <= 0}
    difference = money_in - (trading + sum(total(ts) for ts in buckets.values()))
    if difference or involved:
        raise ReconciliationError(difference, sorted(involved, key=lambda i: pos.get(i, len(pos))))


def as_table(metrics: list[Metric]) -> str:
    lines = ["| id | metric | value | method |", "|---|---|---|---|"]
    for m in metrics:
        lines.append(f"| {m.id} | {m.name} | {m.display()} | {m.method} |")
    return "\n".join(lines)


# --- Drill-down (R16) -------------------------------------------------------------------
# What the workspace shows beside a figure, a bucket or a monthly cell when the underwriter
# selects it. Every total and every input is computed here, from the lines' whole pence, so
# the workspace only renders (R2). Each figure is one of three kinds (P2): a sum figure's
# lines' signed amounts total it; an outflow figure is a positive amount its lines total
# negated; a derived figure is not a total of its lines, and shows its inputs and its method.

SUM_FIGURES = EXACT_IDS  # M2, M17, M18 and the money-in buckets M19 to M25
OUTFLOW_FIGURES = frozenset({"M8", "M11", "M12", "M14"})
# The figures a derived figure is computed from, shown as its inputs beside its own lines.
DERIVED_FROM: dict[str, tuple[str, ...]] = {
    "M3": ("M2",), "M10": ("M9", "M11", "M8"), "M26": ("M25",), "M28": ("M9", "M11", "M8"),
}
# The figures read from the close of every day of the period (P2): their inputs are those closes.
FROM_CLOSES = frozenset({"M6", "M27"})


def figure_kind(metric_id: str) -> str:
    """'sum', 'outflow' or 'derived', as P2 names each figure."""
    if metric_id in SUM_FIGURES:
        return "sum"
    if metric_id in OUTFLOW_FIGURES:
        return "outflow"
    return "derived"


def money_text(p: int) -> str:
    """Whole pence as pounds to the penny with thousands separators: -123456 is "-£1,234.56"."""
    return f"{'-' if p < 0 else ''}£{Decimal(abs(p)) / 100:,.2f}"


def lines_total(statement: Statement, ids: list[str]) -> int:
    """The signed total, in pence, of the amounts of the lines named."""
    by_id = statement.by_id()
    return sum(pence(by_id[i]) for i in ids)


def daily_closes(statement: Statement, first: date | None = None, last: date | None = None
                 ) -> list[tuple[date, int, str]]:
    """The close of every day from `first` to `last` (the whole period by default), carried
    over days with no lines, with the id of the line that set it: what a figure or cell read
    from end-of-day balances reads."""
    eod = end_of_day_balances(statement.transactions)
    return [(d, bal, line) for d, (bal, line) in sorted(eod.items())
            if (first is None or d >= first) and (last is None or d <= last)]


def _share_parts(statement: Statement, metric_id: str, evidence: list[str]) -> tuple[str, int, list[str]] | None:
    """A share's numerator, in pence, and the lines counted in it; its denominator is its
    evidence lines' total."""
    by_id = statement.by_id()
    lines = [by_id[i] for i in evidence]
    if metric_id == "M16":
        num = [t for t in lines if t.category in NON_TRADING_IN]
        return "Money in that is not trading receipts", sum(pence(t) for t in num), [t.id for t in num]
    if metric_id == "M26":
        num = [t for t in lines if t.category == Category.OTHER]
        return "Unexplained money in", sum(pence(t) for t in num), [t.id for t in num]
    if metric_id == "M15":
        by_payer: dict[str, list[Transaction]] = defaultdict(list)
        for t in lines:
            by_payer[counterparty(t.description)].append(t)
        if not by_payer:
            return "The largest payer's customer transfers (there are none)", 0, []
        top = max(by_payer.values(), key=lambda ts: sum(pence(t) for t in ts))
        return "The largest payer's customer transfers", sum(pence(t) for t in top), [t.id for t in top]
    return None


def _case_daily_closes(closes: CaseCloses, first: date | None = None, last: date | None = None
                       ) -> list[tuple[date, int, str]]:
    """daily_closes() for a case of more than one account: each combined close, with the
    lines of every account that set it."""
    return [(d, bal, ", ".join(ids)) for d, (bal, ids) in sorted(closes.closes.items())
            if (first is None or d >= first) and (last is None or d <= last)]


def derived_inputs(statement: Statement, metrics: list[Metric], metric_id: str,
                   proposed_repayment=None, vat_stagger: int | None = None,
                   closes: CaseCloses | None = None) -> dict:
    """What a derived figure is computed from, for the drill-down (R16): the figures it reads,
    the period, a share's numerator and denominator, and, for a figure read from end-of-day
    balances, the close of every day it reads. Values are written as text, money to the penny.
    For a case of more than one account (`closes`), the balance figures read the combined
    closes (R41)."""
    by = {m.id: m for m in metrics}
    m = by[metric_id]
    inputs: list[dict] = []
    start, end = statement.start, statement.end
    days = (end - start).days + 1
    for fid in DERIVED_FROM.get(metric_id, ()):
        f = by[fid]
        inputs.append({"label": f.name, "figure": f.id, "display": f.display_exact()})
    if metric_id in ("M1", "M3", "M28"):
        inputs.append({"label": "Period", "figure": None,
                       "display": f"{start.isoformat()} to {end.isoformat()}, {days} days"})
    if metric_id == "M28":
        shown = ("not given" if proposed_repayment is None
                 else money_text(proposed_repayment_pence(proposed_repayment)))
        inputs.append({"label": "Proposed monthly repayment (case profile)", "figure": None, "display": shown})
    if metric_id == "M5" and m.evidence and closes is not None:
        day = min(closes.closes, key=lambda d: (closes.closes[d][0], d))
        inputs.append({"label": f"Combined close on {day.isoformat()} (the lines below set each account's close)",
                       "figure": None, "display": money_text(closes.closes[day][0])})
    elif metric_id == "M5" and m.evidence:
        t = statement.by_id()[m.evidence[0]]
        inputs.append({"label": f"Running balance after line {t.id}", "figure": None,
                       "display": money_text(balance_pence(t))})
    if metric_id in ("M15", "M16", "M26"):
        parts = _share_parts(statement, metric_id, m.evidence)
        if parts is not None:
            label, num, num_lines = parts
            inputs.append({"label": label, "figure": None, "display": money_text(num), "lines": num_lines})
            inputs.append({"label": "Of which it is a share (the lines below)", "figure": None,
                           "display": money_text(lines_total(statement, m.evidence))})
    if metric_id == "M4":
        inputs += _trend_inputs(statement)
    if metric_id == "M7":
        inputs.append({"label": "Lines marked as returned direct debits (the lines below)", "figure": None,
                       "display": str(len(m.evidence))})
    if metric_id == "M9":
        inputs += _cash_flow_inputs(statement)
    if metric_id == "M13":
        inputs += _vat_inputs(statement, vat_stagger)
    read = None
    if metric_id in FROM_CLOSES:
        read = daily_closes(statement) if closes is None else _case_daily_closes(closes)
    if closes is not None and closes.reason is not None and metric_id in FROM_CLOSES | {"M5"}:
        inputs.append({"label": "Days every account covers", "figure": None, "display": "none"})
    return {"inputs": inputs, "closes": _closes_out(read)}


def _trend_inputs(statement: Statement) -> list[dict]:
    """M4's inputs: the trading receipts of the two quarters it compares, from the same
    calculation (revenue_quarters) the figure is computed from."""
    rev = [t for t in statement.transactions if t.category in REVENUE_CATEGORIES]
    quarters = revenue_quarters(rev, statement.start, statement.end)
    if quarters is None:
        whole = full_months(statement.start, statement.end)
        return [{"label": "Whole calendar months in the period", "figure": None,
                 "display": f"{len(whole)} (6 needed)"}]
    out = []
    for name, (months, p) in zip(("Earliest", "Latest"), quarters, strict=True):
        out.append({"label": f"{name} quarter of whole months ({months[0]} to {months[-1]}), trading receipts",
                    "figure": None, "display": money_text(p),
                    "lines": [t.id for t in rev if _month(t.date) in months]})
    return out


def _cash_flow_inputs(statement: Statement) -> list[dict]:
    """M9's inputs: its operating lines' total, money in that is not trading receipts left
    out; what money sent back by someone the business paid by transfer nets off; and how much
    of each such credit counts. The two money inputs add up to the figure."""
    cash = operating_cash(statement.transactions, statement.by_id())
    back = [t for t in cash.operating if t.id in cash.counted]
    out = [{"label": "Operating lines, other than money sent back (total of their signed amounts)",
            "figure": None, "display": money_text(cash.trading),
            "lines": [t.id for t in cash.operating if t.id not in cash.counted]},
           {"label": "Money sent back by someone the business paid by transfer, netted off "
                     "(never more than was sent to them)", "figure": None, "display": money_text(cash.offset),
            "lines": [t.id for t in back]}]
    out += [{"label": f"Line {t.id} counts", "figure": None,
             "display": f"{money_text(cash.counted[t.id])} of {money_text(pence(t))}"} for t in back]
    return out


def _vat_inputs(statement: Statement, vat_stagger: int | None) -> list[dict]:
    """M13's inputs: the stagger group, and each quarter it judged, with its due date, the end
    of its collection window and the payment counted for it, from vat_quarters."""
    group = {"label": "VAT stagger group (case profile)", "figure": None, "display": "not given"}
    if vat_stagger is None:
        return [group]
    ends = ", ".join(calendar.month_name[m] for m in VAT_STAGGERS[vat_stagger])
    group["display"] = f"{vat_stagger} (quarters end {ends})"
    txns, by_id = statement.transactions, statement.by_id()
    vat = [t for t in txns if _eff(t, by_id) == Category.HMRC_VAT]
    reversed_ids = {t.reverses for t in txns if t.reverses}
    return [group] + [
        {"label": f"Quarter ended {q.quarter_end.isoformat()}: due {q.due.isoformat()}, "
                  f"collected by {q.window_end.isoformat()}", "figure": None,
         "display": f"paid by line {q.paid}" if q.paid else "late or missed"}
        for q in vat_quarters(vat, reversed_ids, vat_stagger, statement.start, statement.end)]


def cell_inputs(statement: Statement, row: MonthRow, closes: CaseCloses | None = None) -> dict:
    """A monthly balance or overdrawn-day cell's inputs: the close of every day of the month in
    the period, with the line that set it (P2, R11); for a case of more than one account, the
    combined close of every day of the month every account covers (R41)."""
    if closes is None:
        return {"inputs": [{"label": "Days of the month in the period", "figure": None,
                            "display": f"{row.start.isoformat()} to {row.end.isoformat()}"}],
                "closes": _closes_out(daily_closes(statement, row.start, row.end))}
    read = _case_daily_closes(closes, row.start, row.end)
    shown = f"{read[0][0].isoformat()} to {read[-1][0].isoformat()}" if read else "none"
    return {"inputs": [{"label": "Days of the month every account covers", "figure": None, "display": shown}],
            "closes": _closes_out(read)}


def _closes_out(closes) -> list[dict] | None:
    if closes is None:
        return None
    return [{"date": d.isoformat(), "display": money_text(bal), "overdrawn": bal < 0, "line": line}
            for d, bal, line in closes]


# --- the comparison entry (R51) -------------------------------------------------------------

UNIT_MARKS = {"pct": "%", "ratio": "x"}  # a mark the underwriter may write after a figure of that unit
MAX_GIVEN_CHARS = 40  # an underwriter's figure is short; a longer text is refused, never parsed


class ComparisonError(ValueError):
    """An underwriter's figure that cannot be compared. Its message quotes nothing but the unit."""


def _plain(text: str, unit: str) -> tuple[bool, str]:
    """The underwriter's figure as a plain decimal: a leading minus, then for money an optional
    £ and thousands commas, or for a share or ratio the unit's own mark after the number."""
    s = text.strip()
    negative = s.startswith("-")
    if negative:
        s = s[1:].lstrip()
    if unit == "GBP":
        s = s[1:] if s.startswith("£") else s
        if "," in s:
            whole, _, rest = s.partition(".")
            groups = whole.split(",")
            if not (groups[0] and len(groups[0]) <= 3 and all(len(g) == 3 for g in groups[1:])):
                raise ComparisonError("Write money as pounds and pence, such as 55,000 or 55000.00")
            s = "".join(groups) + ("." + rest if "." in s else "")
    elif unit in UNIT_MARKS and s.endswith(UNIT_MARKS[unit]):
        s = s[:-1].rstrip()
    if not s or not all(c in "0123456789." for c in s) or s.count(".") > 1 or s == ".":
        raise ComparisonError("Write the figure as a number" + (", such as 55,000 or 55000.00" if unit == "GBP" else ""))
    return negative, s


def given_figure(text: str, unit: str) -> int | Decimal:
    """The underwriter's own figure, read exactly from what they typed: whole pence for money,
    refused with a fraction of a penny or at the import limit (R2); the number itself, as a
    decimal, for any other unit."""
    if not isinstance(text, str) or not text.strip():
        raise ComparisonError("Enter your figure")
    if len(text) > MAX_GIVEN_CHARS:
        raise ComparisonError("The figure is too long")
    negative, s = _plain(text, unit)
    if unit == "GBP":
        p = text_pence(s)
        if p is None:
            raise ComparisonError("Money is compared to the penny: write at most two decimals")
        if at_limit(p):
            raise ComparisonError(f"An amount of {AT_LIMIT} cannot be compared to the penny")
        return -p if negative else p
    d = Decimal(s)
    return -d if negative else d


UNIT_WORDS = {"days": " days", "months": " months"}


def _unit_text(d: Decimal, unit: str) -> str:
    """A figure that is not money, as typed back: trailing zeros after the point dropped."""
    text = format(d, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    text = "0" if text in ("", "-0") else text
    return text + UNIT_MARKS.get(unit, UNIT_WORDS.get(unit, ""))


def comparison(value: float | None, unit: str, given_text: str) -> dict:
    """The underwriter's figure beside the tool's, and the difference, the underwriter's less the
    tool's, computed here in the figures layer: in whole pence for money (R2), and exactly, as
    decimals, for any other unit. When the tool's figure is n/a there is no difference."""
    given = given_figure(given_text, unit)
    if unit == "GBP":
        ours = None if value is None else float_pence(value)
        if value is not None and ours is None:
            raise ComparisonError("The tool's figure is not a whole number of pence")
        diff = None if ours is None else given - ours
        return {"given": pounds_text(given), "given_display": money_text(given),
                "ours": None if ours is None else pounds_text(ours),
                "ours_display": "n/a" if ours is None else money_text(ours),
                "difference_pence": diff,
                "difference": None if diff is None else pounds_text(diff),
                "difference_display": "n/a" if diff is None else money_text(diff)}
    ours_d = None if value is None else Decimal(repr(value))
    diff_d = None if ours_d is None else given - ours_d
    return {"given": str(given), "given_display": _unit_text(given, unit),
            "ours": None if ours_d is None else str(ours_d),
            "ours_display": "n/a" if ours_d is None else _unit_text(ours_d, unit),
            "difference_pence": None,
            "difference": None if diff_d is None else str(diff_d),
            "difference_display": "n/a" if diff_d is None else _unit_text(diff_d, unit)}
