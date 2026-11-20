"""Deterministic metrics with provenance.

This is the only place money gets added up. Every Metric lists the transaction ids
behind it, and the memo may only quote numbers that appear here. A metric that cannot
be computed from the statement has value None and says why; the policy decides what a
missing figure means (see `when_missing` in the policy file), not this module.

Returned direct debits: the categoriser links each one to the debit it undoes. Sums
here use the *effective* category, so a reversal of a loan repayment reduces debt
service, and a bounced-then-re-presented payment counts once.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta

from .categorise import OUTGOING_TRANSFER, counterparty
from .models import REVENUE_CATEGORIES, Category, Metric, Statement, Transaction

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
        if m in VAT_STAGGERS[stagger]:
            q_end = _month_end(y, m)
            dy, dm = _add_months(y, m, 2)
            due = date(dy, dm, 7)
            if q_end >= start - timedelta(days=1) and _add_working_days(due, VAT_DD_WORKING_DAYS) <= end:
                out.append((q_end, due))
        y, m = _add_months(y, m, 1)
    return out


def end_of_day_balances(txns: list[Transaction]) -> dict[date, tuple[float, str]]:
    """Closing balance for every calendar day from the first transaction to the last,
    carried forward over days with no transactions, with the id of the line that set it."""
    last: dict[date, Transaction] = {}
    for t in txns:  # file order; the last line of a day sets its closing balance
        last[t.date] = t
    out: dict[date, tuple[float, str]] = {}
    d, end = min(last), max(last)
    current = last[d]
    while d <= end:
        current = last.get(d, current)
        out[d] = (current.balance, current.id)
        d += timedelta(days=1)
    return out


def compute(statement: Statement, vat_stagger: int | None = None) -> list[Metric]:
    txns = statement.transactions
    if any(t.category is None for t in txns):
        raise ValueError("categorise() must run before compute()")
    if vat_stagger is not None and vat_stagger not in VAT_STAGGERS:
        raise ValueError("vat_stagger must be 1, 2 or 3")

    by_id = statement.by_id()

    def eff(t: Transaction) -> Category:
        """A returned DD counts in the category of the debit it reverses."""
        return by_id[t.reverses].category if t.reverses else t.category

    def of(*cats: Category) -> list[Transaction]:
        return [t for t in txns if eff(t) in cats]

    start, end = statement.start, statement.end
    n_months = months_covered(start, end)
    period_months = ((end - start).days + 1) / DAYS_PER_MONTH

    # Revenue
    rev = [t for t in txns if t.category in REVENUE_CATEGORIES]
    total_revenue = sum(t.amount for t in rev)
    rev_by_month: dict[str, float] = defaultdict(float)
    for t in rev:
        rev_by_month[_month(t.date)] += t.amount
    whole = full_months(start, end)
    trend: float | None = None
    trend_note = (
        "Average revenue of the latest quarter of whole calendar months against the earliest quarter, "
        "as a percentage change. On a year-long statement these are opposite seasons."
    )
    if len(whole) < 6:
        trend_note = "Needs more whole calendar months than this statement has."
    else:
        first3 = sum(rev_by_month.get(m, 0.0) for m in whole[:3]) / 3
        last3 = sum(rev_by_month.get(m, 0.0) for m in whole[-3:]) / 3
        if first3 > 0:
            trend = round((last3 - first3) / first3 * 100, 2)
        else:
            trend_note = "No revenue in the earliest quarter of whole months, so there is no base to compare with."

    # Balances
    lowest = min(txns, key=lambda t: t.balance)
    eod = end_of_day_balances(txns)
    overdrawn = sorted(d for d, (bal, _) in eod.items() if bal < 0)
    pos = {t.id: i for i, t in enumerate(txns)}
    overdrawn_evidence = sorted({eod[d][1] for d in overdrawn}, key=pos.__getitem__)

    # Obligations
    returned = [t for t in txns if t.category == Category.RETURNED_DD]
    loan = of(Category.LOAN_REPAYMENT)
    drawings = of(Category.OWNER_DRAWINGS)
    vat = of(Category.HMRC_VAT)
    paye = of(Category.HMRC_PAYE)
    debt_service = round(-sum(t.amount for t in loan), 2)
    owner_drawings = round(-sum(t.amount for t in drawings), 2)

    # Operating cash flow is trading cash: money out that is not financing, and money in
    # that is revenue or that reverses an operating payment. Money in that nobody has
    # explained (OTHER: capital introduced, savings, refunds) is left out, so it can never
    # lift cover; it shows in M16 instead. A returned DD that matches no debit is left out
    # too, for the same reason.
    reversed_ids = {t.reverses for t in txns if t.reverses}

    def operating_line(t: Transaction) -> bool:
        if eff(t) in FINANCING:
            return False
        if t.amount <= 0:
            return True
        if t.category == Category.RETURNED_DD:
            return t.reverses is not None
        return t.category in REVENUE_CATEGORIES

    operating = [t for t in txns if operating_line(t)]
    # Money sent to someone by transfer and sent back by them (a savings account, a
    # friend, a round trip) nets off: their unexplained credits offset the transfers to
    # them, but never by more than was sent, so the round trip can't add cash.
    sent: dict[str, float] = defaultdict(float)
    for t in txns:
        if t.amount < 0 and eff(t) not in FINANCING and OUTGOING_TRANSFER.search(t.description.upper()):
            sent[counterparty(t.description)] += -t.amount
    back: dict[str, list[Transaction]] = defaultdict(list)
    for t in txns:
        if t.amount > 0 and t.category in NON_TRADING_IN and counterparty(t.description) in sent:
            back[counterparty(t.description)].append(t)
    offset = sum(min(sum(t.amount for t in ts), sent[cp]) for cp, ts in back.items())
    operating += [t for ts in back.values() for t in ts]
    net_operating = round(sum(t.amount for t in operating if not (t.amount > 0 and t.category in NON_TRADING_IN))
                          + offset, 2)
    dscr: float | None = None
    dscr_note = (
        "Historical cover of existing debt only: net operating cash flow after owner drawings, "
        "divided by debt service. It does not include the facility being applied for."
    )
    if debt_service > 0:
        dscr = round((net_operating - owner_drawings) / debt_service, 4)
    else:
        dscr_note = "No loan or finance repayments in the statement, so there is no existing debt to cover."

    # Concentration: only knowable for customers who pay by named transfer.
    transfers = [t for t in txns if t.category == Category.REVENUE_TRANSFER]
    by_payer: dict[str, float] = defaultdict(float)
    for t in transfers:
        by_payer[counterparty(t.description)] += t.amount
    top_share: float | None = None
    top_note = "Largest payer's share of customer transfer revenue, payers named after removing invoice references."
    if by_payer and sum(by_payer.values()) > 0:
        top_share = round(max(by_payer.values()) / sum(by_payer.values()) * 100, 2)
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
                    (t for t in vat if t.id not in used and t.id not in reversed_ids and t.amount < 0
                     and q_end < t.date <= window_end),
                    None,
                )
                if paid:
                    used.add(paid.id)
                else:
                    late_vat += 1

    # Inflows nobody has explained.
    inflows = [t for t in txns if t.amount > 0 and eff(t) not in (Category.LOAN_DRAWDOWN,)
               and t.category != Category.RETURNED_DD]
    unclassified = [t for t in inflows if t.category in NON_TRADING_IN]
    total_in = sum(t.amount for t in inflows)
    unclassified_share = round(sum(t.amount for t in unclassified) / total_in * 100, 2) if total_in else None

    def g(x: float | None) -> float | None:
        return None if x is None else round(x, 2)

    return [
        Metric(id="M1", name="Months covered", value=n_months, unit="months",
               method="Days from the first transaction to the last, in months, to the nearest month.",
               evidence=[txns[0].id, txns[-1].id]),
        Metric(id="M2", name="Total revenue", value=g(total_revenue), unit="GBP",
               method="Card and gateway settlements, transfers in that carry an invoice reference, and cash paid in.",
               evidence=[t.id for t in rev]),
        Metric(id="M3", name="Average monthly revenue",
               value=g(total_revenue / period_months), unit="GBP",
               method="Total revenue divided by the exact length of the period in months (quiet months still count).",
               evidence=[t.id for t in rev]),
        Metric(id="M4", name="Revenue trend", value=trend, unit="pct", method=trend_note,
               evidence=[t.id for t in rev if _month(t.date) in whole[:3] + whole[-3:]] if trend is not None else []),
        Metric(id="M5", name="Lowest balance", value=g(lowest.balance), unit="GBP",
               method="Lowest running balance after any transaction in the period.", evidence=[lowest.id]),
        Metric(id="M6", name="Days overdrawn", value=len(overdrawn), unit="days",
               method="Calendar days that closed overdrawn, carrying the balance over days with no transactions.",
               evidence=overdrawn_evidence),
        Metric(id="M7", name="Returned direct debits", value=len(returned), unit="count",
               method="Lines marked as unpaid direct debits.",
               evidence=[t.id for t in returned]),
        Metric(id="M8", name="Debt service", value=debt_service, unit="GBP",
               method="Loan and finance repayments, net of any that were returned unpaid.",
               evidence=[t.id for t in loan]),
        Metric(id="M9", name="Net operating cash flow", value=net_operating, unit="GBP",
               method=("Revenue in, minus all money out, excluding loans and drawings. Money in that is not "
                       "explained as trading is left out, except that money sent back by someone the "
                       "business paid by transfer nets off what it sent them."),
               evidence=[t.id for t in operating]),
        Metric(id="M10", name="Debt service cover", value=dscr, unit="ratio", method=dscr_note,
               evidence=[t.id for t in operating + drawings + loan] if dscr is not None else []),
        Metric(id="M11", name="Owner drawings", value=owner_drawings, unit="GBP",
               method="Transfers to the owner or directors and dividends.", evidence=[t.id for t in drawings]),
        Metric(id="M12", name="VAT paid", value=round(-sum(t.amount for t in vat), 2), unit="GBP",
               method="Payments to HMRC VAT, net of any returned unpaid.", evidence=[t.id for t in vat]),
        Metric(id="M13", name="Late or missed VAT payments", value=late_vat, unit="count",
               method=vat_note, evidence=[t.id for t in vat] if late_vat is not None else []),
        Metric(id="M14", name="PAYE paid", value=round(-sum(t.amount for t in paye), 2), unit="GBP",
               method="Payments to HMRC PAYE/NI, net of any returned unpaid.", evidence=[t.id for t in paye]),
        Metric(id="M15", name="Top customer share of transfer revenue", value=top_share, unit="pct",
               method=top_note, evidence=[t.id for t in transfers]),
        Metric(id="M16", name="Non-trading money in (share)", value=unclassified_share, unit="pct",
               method="Share of money in (excluding loan drawdowns and returned DDs) that is not revenue; it is left out of operating cash flow.",
               evidence=[t.id for t in inflows]),
    ]


def as_table(metrics: list[Metric]) -> str:
    lines = ["| id | metric | value | method |", "|---|---|---|---|"]
    for m in metrics:
        lines.append(f"| {m.id} | {m.name} | {m.display()} | {m.method} |")
    return "\n".join(lines)
