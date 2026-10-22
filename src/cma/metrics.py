"""Deterministic metrics with provenance.

This is the only place money gets added up. Every Metric lists the transaction ids
behind it, and the memo may only quote numbers that appear here.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from .models import REVENUE_CATEGORIES, Category, Metric, Statement


def _month(t) -> str:
    return t.date.strftime("%Y-%m")


def compute(statement: Statement) -> list[Metric]:
    txns = statement.transactions
    if any(t.category is None for t in txns):
        raise ValueError("categorise() must run before compute()")

    months = sorted({_month(t) for t in txns})
    rev = [t for t in txns if t.category in REVENUE_CATEGORIES]
    rev_by_month: dict[str, float] = defaultdict(float)
    for t in rev:
        rev_by_month[_month(t)] += t.amount
    monthly = [rev_by_month.get(m, 0.0) for m in months]

    total_revenue = sum(t.amount for t in rev)
    first3 = sum(monthly[:3]) / max(1, len(monthly[:3]))
    last3 = sum(monthly[-3:]) / max(1, len(monthly[-3:]))
    trend = ((last3 - first3) / first3 * 100) if first3 else 0.0

    lowest = min(txns, key=lambda t: t.balance)
    overdrawn_days = {t.date for t in txns if t.balance < 0}

    returned = [t for t in txns if t.category == Category.RETURNED_DD]
    loan = [t for t in txns if t.category == Category.LOAN_REPAYMENT]
    drawings = [t for t in txns if t.category == Category.OWNER_DRAWINGS]
    vat = [t for t in txns if t.category == Category.HMRC_VAT]
    paye = [t for t in txns if t.category == Category.HMRC_PAYE]

    # Operating cash flow before debt service and drawings.
    excluded = {Category.LOAN_REPAYMENT, Category.OWNER_DRAWINGS}
    operating = [t for t in txns if t.category not in excluded]
    net_operating = sum(t.amount for t in operating)
    debt_service = -sum(t.amount for t in loan)
    dscr = (net_operating / debt_service) if debt_service else float("inf")

    # Concentration is only knowable for revenue paid by named customers. Card and
    # gateway settlements arrive from one acquirer and hide the customers behind them.
    transfers = [t for t in txns if t.category == Category.REVENUE_TRANSFER]
    by_counterparty: dict[str, float] = defaultdict(float)
    for t in transfers:
        key = " ".join(t.description.split()[2:5])
        by_counterparty[key] += t.amount
    top_share = (max(by_counterparty.values()) / sum(by_counterparty.values()) * 100) if transfers else 0.0

    # VAT regularity: expect a payment in each quarter-start month plus a week.
    quarter_months = [m for m in months if int(m[5:]) in (1, 4, 7, 10)]
    late_vat = 0
    for t in vat:
        on_time = any(
            t.date >= _first_day(m) and t.date <= _first_day(m) + timedelta(days=10)
            for m in quarter_months
        )
        if not on_time:
            late_vat += 1

    return [
        Metric(id="M1", name="Months of statements", value=len(months), unit="months",
               method="Distinct calendar months with at least one transaction.",
               evidence=[]),
        Metric(id="M2", name="Total revenue", value=round(total_revenue, 2), unit="GBP",
               method="Sum of credits categorised as card settlements or customer transfers.",
               evidence=[t.id for t in rev]),
        Metric(id="M3", name="Average monthly revenue", value=round(total_revenue / len(months), 2),
               unit="GBP", method="Total revenue divided by months covered.",
               evidence=[t.id for t in rev]),
        Metric(id="M4", name="Revenue trend", value=round(trend, 1), unit="pct",
               method="Average of the last three months versus the first three, as a percentage.",
               evidence=[t.id for t in rev if _month(t) in months[:3] + months[-3:]]),
        Metric(id="M5", name="Lowest balance", value=round(lowest.balance, 2), unit="GBP",
               method="Minimum running balance across the period.", evidence=[lowest.id]),
        Metric(id="M6", name="Days overdrawn", value=len(overdrawn_days), unit="days",
               method="Distinct days on which the closing balance was below zero.",
               evidence=[t.id for t in txns if t.balance < 0][:50]),
        Metric(id="M7", name="Returned direct debits", value=len(returned), unit="count",
               method="Count of lines marked as unpaid direct debits.",
               evidence=[t.id for t in returned]),
        Metric(id="M8", name="Debt service", value=round(debt_service, 2), unit="GBP",
               method="Sum of loan and finance repayments.", evidence=[t.id for t in loan]),
        Metric(id="M9", name="Net operating cash flow", value=round(net_operating, 2), unit="GBP",
               method="All inflows minus all outflows, excluding loan repayments and owner drawings.",
               evidence=[t.id for t in operating][:200]),
        Metric(id="M10", name="Debt service cover", value=round(dscr, 2) if dscr != float("inf") else 99.0,
               unit="ratio", method="Net operating cash flow divided by debt service.",
               evidence=[t.id for t in loan]),
        Metric(id="M11", name="Owner drawings", value=round(-sum(t.amount for t in drawings), 2),
               unit="GBP", method="Sum of transfers to the owner.", evidence=[t.id for t in drawings]),
        Metric(id="M12", name="VAT paid", value=round(-sum(t.amount for t in vat), 2), unit="GBP",
               method="Sum of payments to HMRC VAT.", evidence=[t.id for t in vat]),
        Metric(id="M13", name="Late VAT payments", value=late_vat, unit="count",
               method="VAT payments made more than ten days after the quarter-start month began.",
               evidence=[t.id for t in vat]),
        Metric(id="M14", name="PAYE paid", value=round(-sum(t.amount for t in paye), 2), unit="GBP",
               method="Sum of payments to HMRC PAYE/NI.", evidence=[t.id for t in paye]),
        Metric(id="M15", name="Top customer share of transfer revenue", value=round(top_share, 1), unit="pct",
               method="Largest named payer's share of customer transfers; 0 when revenue is card-settled.",
               evidence=[t.id for t in transfers][:200]),
    ]


def _first_day(month: str):
    from datetime import date

    y, m = month.split("-")
    return date(int(y), int(m), 1)


def as_table(metrics: list[Metric]) -> str:
    lines = ["| id | metric | value | method |", "|---|---|---|---|"]
    for m in metrics:
        lines.append(f"| {m.id} | {m.name} | {m.display()} | {m.method} |")
    return "\n".join(lines)
