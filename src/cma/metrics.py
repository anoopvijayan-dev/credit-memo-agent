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
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from .categorise import OUTGOING_TRANSFER, counterparty
from .models import (
    CREDIT_BUCKET,
    REVENUE_CATEGORIES,
    Bucket,
    Category,
    Metric,
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


def compute(statement: Statement, vat_stagger: int | None = None) -> list[Metric]:
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
        return by_id[t.reverses].category if t.reverses in by_id else t.category

    def of(*cats: Category) -> list[Transaction]:
        return [t for t in txns if eff(t) in cats]

    start, end = statement.start, statement.end
    n_months = months_covered(start, end)
    period_days = (end - start).days + 1  # in months: period_days / DAYS_PER_MONTH = period_days * 48 / 1461

    # Revenue
    rev = [t for t in txns if t.category in REVENUE_CATEGORIES]
    total_revenue = total(rev)
    rev_by_month: dict[str, int] = defaultdict(int)
    for t in rev:
        rev_by_month[_month(t.date)] += pence(t)
    whole = full_months(start, end)
    trend: float | None = None
    trend_note = (
        "Average revenue of the latest quarter of whole calendar months against the earliest quarter, "
        "as a percentage change. On a year-long statement these are opposite seasons."
    )
    if len(whole) < 6:
        trend_note = "Needs more whole calendar months than this statement has."
    else:
        first3 = sum(rev_by_month.get(m, 0) for m in whole[:3])  # quarters of equal length: the
        last3 = sum(rev_by_month.get(m, 0) for m in whole[-3:])  # averages' 1/3 cancels
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

    # Operating cash flow is trading cash: money out that is not financing, and money in
    # that is revenue or that reverses an operating payment. Money in that nobody has
    # explained (OTHER: capital introduced, savings, refunds) is left out, so it can never
    # lift cover; it shows in M16 instead. A returned DD that matches no debit is left out
    # too, for the same reason.
    reversed_ids = {t.reverses for t in txns if t.reverses}

    def operating_line(t: Transaction) -> bool:
        if eff(t) in FINANCING:
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
        if pence(t) < 0 and eff(t) not in FINANCING and OUTGOING_TRANSFER.search(t.description.upper()):
            sent[counterparty(t.description)] += -pence(t)
    back: dict[str, list[Transaction]] = defaultdict(list)
    for t in txns:
        if pence(t) > 0 and t.category in NON_TRADING_IN and counterparty(t.description) in sent:
            back[counterparty(t.description)].append(t)
    offset = sum(min(total(ts), sent[cp]) for cp, ts in back.items())
    # A derived figure (P2): the cap is allocated to a counterparty's credits in statement
    # order, so one credit at most counts in part, and only that one is named in the evidence
    # note; the ones before it count in full and the ones after it not at all (T04 review 6).
    capped: list[str] = []
    for cp, ts in back.items():
        left = sent[cp]
        for t in sorted(ts, key=lambda x: pos[x.id]):
            if 0 < left < pence(t):
                capped.append(t.id)
            left = max(0, left - pence(t))
    capped.sort(key=pos.__getitem__)
    operating += [t for ts in back.values() for t in ts]
    net_operating = total(t for t in operating if not (pence(t) > 0 and t.category in NON_TRADING_IN)) + offset
    dscr: float | None = None
    dscr_note = (
        "Historical cover of existing debt only: net operating cash flow after owner drawings, "
        "divided by debt service. It does not include the facility being applied for."
    )
    if debt_service > 0:
        dscr = _ratio(net_operating - owner_drawings, debt_service, 4)
    else:
        dscr_note = "No loan or finance repayments in the statement, so there is no existing debt to cover."

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
        due = vat_due_dates(vat_stagger, start, end)
        if not due:
            vat_note = "No VAT quarter falls wholly inside the statement period."
        else:
            used: set[str] = set()
            late_vat = 0
            for q_end, d in due:
                window_end = _add_working_days(d, VAT_DD_WORKING_DAYS)
                paid = next(
                    (t for t in vat if t.id not in used and t.id not in reversed_ids and pence(t) < 0
                     and q_end < t.date <= window_end),
                    None,
                )
                if paid:
                    used.add(paid.id)
                else:
                    late_vat += 1

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
        Metric(id="M5", name="Lowest balance", value=_gbp(balance_pence(lowest)), unit="GBP",
               method="Lowest running balance after any transaction in the period.", evidence=[lowest.id]),
        Metric(id="M6", name="Days overdrawn", value=len(overdrawn), unit="days",
               method="Calendar days that closed overdrawn, carrying the balance over days with no transactions.",
               evidence=overdrawn_evidence),
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
    ]


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
