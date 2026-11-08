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
import re
from collections import defaultdict
from datetime import date, timedelta

from .models import REVENUE_CATEGORIES, Category, Metric, Statement, Transaction

# HMRC VAT stagger groups: the months in which a VAT quarter ends.
VAT_STAGGERS: dict[int, tuple[int, ...]] = {1: (3, 6, 9, 12), 2: (1, 4, 7, 10), 3: (2, 5, 8, 11)}
VAT_DD_WORKING_DAYS = 3  # HMRC collects by Direct Debit up to three working days after the due date.
MIN_DAYS_FOR_BOUNDARY_MONTH = 15
FINANCING = {Category.LOAN_REPAYMENT, Category.LOAN_DRAWDOWN, Category.OWNER_DRAWINGS}


def _month(d: date) -> str:
    return d.strftime("%Y-%m")


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _add_months(y: int, m: int, n: int) -> tuple[int, int]:
    k = y * 12 + (m - 1) + n
    return k // 12, k % 12 + 1


def span_months(start: date, end: date) -> list[str]:
    """Every calendar month from start to end, including months with no transactions."""
    out, (y, m) = [], (start.year, start.month)
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = _add_months(y, m, 1)
    return out


def covered_months(start: date, end: date) -> list[str]:
    """Months the statement covers: every month in the span, except a first or last month
    with fewer than 15 days inside the period. A statement from 25 Oct to 30 Sep covers
    11 months, not 12 or 13."""
    months = span_months(start, end)
    keep = []
    for key in months:
        y, m = int(key[:4]), int(key[5:])
        lo = max(start, date(y, m, 1))
        hi = min(end, _month_end(y, m))
        days = (hi - lo).days + 1
        boundary = key in (months[0], months[-1])
        if not boundary or days >= MIN_DAYS_FOR_BOUNDARY_MONTH:
            keep.append(key)
    return keep


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


INV_TOKEN = re.compile(r"\bINV(?:OICE)?\s*[-#:]?\s*\d+\b|\b\w*\d\w*\b")
RAIL_PREFIX = re.compile(r"^(FPS CREDIT|BACS CREDIT|FPS|BACS|TFR|BGC)\s+")


def payer_key(description: str) -> str:
    """Who paid: the description without the payment rail, invoice numbers or other
    references with digits in them."""
    d = RAIL_PREFIX.sub("", description.upper())
    d = INV_TOKEN.sub(" ", d)
    return " ".join(d.split()) or description.upper()


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
    months = covered_months(start, end)
    n_months = len(months)

    # Revenue
    rev = [t for t in txns if t.category in REVENUE_CATEGORIES]
    total_revenue = sum(t.amount for t in rev)
    rev_by_month: dict[str, float] = defaultdict(float)
    for t in rev:
        rev_by_month[_month(t.date)] += t.amount
    monthly = [rev_by_month.get(m, 0.0) for m in months]
    trend: float | None = None
    trend_note = "Average monthly revenue of the last three covered months versus the first three, as a percentage."
    if n_months < 6:
        trend_note = "Needs at least six covered months; this statement has fewer."
    else:
        first3, last3 = sum(monthly[:3]) / 3, sum(monthly[-3:]) / 3
        if first3 > 0:
            trend = round((last3 - first3) / first3 * 100, 1)
        else:
            trend_note = "No revenue in the first three months, so there is no base to compare with."

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

    operating = [t for t in txns if eff(t) not in FINANCING]
    net_operating = round(sum(t.amount for t in operating), 2)
    dscr: float | None = None
    dscr_note = "Net operating cash flow after owner drawings, divided by debt service."
    if debt_service > 0:
        dscr = round((net_operating - owner_drawings) / debt_service, 2)
    else:
        dscr_note = "No loan or finance repayments in the statement, so there is nothing to cover."

    # Concentration: only knowable for customers who pay by named transfer.
    transfers = [t for t in txns if t.category == Category.REVENUE_TRANSFER]
    by_payer: dict[str, float] = defaultdict(float)
    for t in transfers:
        by_payer[payer_key(t.description)] += t.amount
    top_share: float | None = None
    top_note = "Largest payer's share of customer transfer revenue, payers named after removing invoice references."
    if by_payer and sum(by_payer.values()) > 0:
        top_share = round(max(by_payer.values()) / sum(by_payer.values()) * 100, 1)
    else:
        top_note = "No customer transfers: revenue is card or gateway settled, which hides who the customers are."

    # VAT timeliness against the business's own stagger group.
    late_vat: int | None = None
    vat_note = (
        f"Quarters (stagger {vat_stagger}) with no VAT payment between the quarter end and "
        f"{VAT_DD_WORKING_DAYS} working days after the due date."
    )
    if vat_stagger is None:
        vat_note = "VAT stagger group not given (1, 2 or 3, from the VAT certificate), so due dates are unknown."
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
                    (t for t in vat if t.id not in used and t.amount < 0 and q_end < t.date <= window_end),
                    None,
                )
                if paid:
                    used.add(paid.id)
                else:
                    late_vat += 1

    # Inflows nobody has explained.
    inflows = [t for t in txns if t.amount > 0 and eff(t) not in (Category.LOAN_DRAWDOWN,)
               and t.category != Category.RETURNED_DD]
    unclassified = [t for t in inflows if t.category == Category.OTHER]
    total_in = sum(t.amount for t in inflows)
    unclassified_share = round(sum(t.amount for t in unclassified) / total_in * 100, 1) if total_in else None

    def g(x: float | None) -> float | None:
        return None if x is None else round(x, 2)

    return [
        Metric(id="M1", name="Months covered", value=n_months, unit="months",
               method=(f"Calendar months from {start} to {end}; a first or last month counts only if "
                       f"{MIN_DAYS_FOR_BOUNDARY_MONTH}+ days of it are inside the period."),
               evidence=[txns[0].id, txns[-1].id]),
        Metric(id="M2", name="Total revenue", value=g(total_revenue), unit="GBP",
               method="Card and gateway settlements, plus transfers in that carry an invoice reference.",
               evidence=[t.id for t in rev]),
        Metric(id="M3", name="Average monthly revenue",
               value=g(total_revenue / n_months) if n_months else None, unit="GBP",
               method="Total revenue divided by months covered (months with no revenue still count).",
               evidence=[t.id for t in rev]),
        Metric(id="M4", name="Revenue trend", value=trend, unit="pct", method=trend_note,
               evidence=[t.id for t in rev if _month(t.date) in months[:3] + months[-3:]]),
        Metric(id="M5", name="Lowest balance", value=g(lowest.balance), unit="GBP",
               method="Lowest running balance after any transaction in the period.", evidence=[lowest.id]),
        Metric(id="M6", name="Days overdrawn", value=len(overdrawn), unit="days",
               method="Calendar days that closed below zero, carrying the balance over days with no transactions.",
               evidence=overdrawn_evidence),
        Metric(id="M7", name="Returned direct debits", value=len(returned), unit="count",
               method="Lines marked as unpaid direct debits.",
               evidence=[t.id for t in returned]),
        Metric(id="M8", name="Debt service", value=debt_service, unit="GBP",
               method="Loan and finance repayments, net of any that were returned unpaid.",
               evidence=[t.id for t in loan]),
        Metric(id="M9", name="Net operating cash flow", value=net_operating, unit="GBP",
               method="All money in minus all money out, excluding loan repayments, drawdowns and owner drawings.",
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
        Metric(id="M16", name="Unclassified inflows", value=unclassified_share, unit="pct",
               method="Share of money in (excluding loan drawdowns and returned DDs) that is neither revenue nor known financing.",
               evidence=[t.id for t in unclassified]),
    ]


def as_table(metrics: list[Metric]) -> str:
    lines = ["| id | metric | value | method |", "|---|---|---|---|"]
    for m in metrics:
        lines.append(f"| {m.id} | {m.name} | {m.display()} | {m.method} |")
    return "\n".join(lines)
