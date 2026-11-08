"""Synthetic UK SME business current-account statements.

There is no open dataset of UK small-business bank statements, so this module makes
them. Each statement comes with a ground-truth file so the metrics layer can be tested
against known answers rather than eyeballed.

Three archetypes (cafe, trades, ecommerce) x three risk profiles (good, stretched, poor).
Everything is seeded, so the same arguments always produce the same statement, and
the default end date is fixed (30 September 2026) rather than "last month", so a
generated example does not change from one day to the next.

The ground truth is worked out from the generator's own events, not by calling the
metrics module, so a bug there cannot hide by agreeing with itself.
"""

from __future__ import annotations

import calendar
import csv
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .models import Category

DEFAULT_END = date(2026, 9, 30)
VAT_STAGGER = 3  # quarters end Feb, May, Aug, Nov; due the 7th of Apr, Jul, Oct, Jan.

# Counterparties are fictional. Any resemblance to a real business is accidental.
ARCHETYPES: dict[str, dict] = {
    "cafe": {
        "account_name": "HARBOURSIDE COFFEE LTD",
        "channel": "card",
        "monthly_revenue": 22_000,
        "seasonality": [0.80, 0.82, 0.90, 0.98, 1.05, 1.12, 1.18, 1.15, 1.02, 0.98, 0.95, 1.05],
        "suppliers": [("FRESHFIELD FOODS LTD DD", 4_600), ("BREW SUPPLY CO DD", 1_500), ("CITY WASTE SERVICES DD", 180), ("LANTERN ENERGY DD", 950)],
        "rent": 2_400,
        "payroll": 7_200,
        "loan": ("HOLLOWAY BRIDGE FINANCE LOAN", 1_150),
        "drawings": 2_500,
    },
    "trades": {
        "account_name": "KESTREL PLUMBING & HEATING LTD",
        "channel": "bacs",
        "monthly_revenue": 31_000,
        "seasonality": [1.15, 1.10, 1.00, 0.92, 0.88, 0.85, 0.85, 0.88, 0.98, 1.08, 1.15, 1.16],
        "suppliers": [("PIPEWELL TRADE SUPPLIES DD", 9_800), ("ROADLINE FUEL CARD DD", 980), ("ABBEY VAN LEASE DD", 620), ("TRADESURE INSURANCE DD", 260)],
        "rent": 900,
        "payroll": 9_800,
        "loan": ("ASSETLINE VAN FINANCE", 1_900),
        "drawings": 3_200,
    },
    "ecommerce": {
        "account_name": "MOSS & FERN HOMEWARE LTD",
        "channel": "gateway",
        "monthly_revenue": 41_000,
        "seasonality": [0.85, 0.80, 0.88, 0.92, 0.95, 0.90, 0.88, 0.92, 1.00, 1.15, 1.45, 1.30],
        "suppliers": [("STOCKHAVEN WHOLESALE", 15_500), ("PARCELWAY COURIERS DD", 3_100), ("SHOPFRONT PLATFORM DD", 290), ("ADSPEND MEDIA", 4_900)],
        "rent": 1_600,
        "payroll": 8_400,
        "loan": ("GROWTHCAP REVENUE LOAN", 1_900),
        "drawings": 3_000,
    },
}

PROFILES: dict[str, dict] = {
    # revenue_mult scales revenue; returned_dds is how many direct debits bounce in the
    # year; hmrc_late delays VAT payments; opening_balance sets how much headroom exists.
    # Tuned so that, under the default policy, good passes (or is referred only for
    # seasonality), stretched is referred and poor fails, with cover measured after
    # owner drawings.
    # capital_in is money the owner puts in mid-year: not revenue, not operating cash.
    "good": {"revenue_mult": 1.30, "returned_dds": 0, "hmrc_late": False, "opening_balance": 14_000, "capital_in": 0},
    "stretched": {"revenue_mult": 1.18, "returned_dds": 1, "hmrc_late": False, "opening_balance": 3_500, "capital_in": 8_000},
    "poor": {"revenue_mult": 0.85, "returned_dds": 4, "hmrc_late": True, "opening_balance": 600, "capital_in": 15_000},
}

CUSTOMER_NAMES = [
    "MRS P ADEYEMI", "J & L CARTER", "OAKFIELD PROPERTIES", "DUNMORE HOUSING ASSOC",
    "T WHITTAKER", "ELMS DENTAL PRACTICE", "RIVERSIDE LETTINGS", "S & K PATEL",
]


@dataclass
class Truth:
    """What the generator knows that the agent must rediscover."""

    archetype: str
    profile: str
    seed: int
    months: list[str]
    monthly_revenue: dict[str, float] = field(default_factory=dict)
    total_revenue: float = 0.0
    total_debt_service: float = 0.0
    total_owner_drawings: float = 0.0
    total_hmrc_vat: float = 0.0
    returned_dd_count: int = 0
    lowest_balance: float = 0.0
    days_overdrawn: int = 0
    top_customer_share: float | None = None
    average_monthly_revenue: float = 0.0
    net_operating: float = 0.0
    debt_cover: float | None = None
    unclassified_share: float | None = None
    vat_stagger: int = VAT_STAGGER
    late_vat_count: int = 0
    category_by_id: dict[str, str] = field(default_factory=dict)


def _month_starts(end: date, months: int) -> list[date]:
    y, m = end.year, end.month
    out = []
    for _ in range(months):
        out.append(date(y, m, 1))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(out))


def _last_working_day(d: date) -> date:
    last = date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
    while last.weekday() >= 5:
        last -= timedelta(days=1)
    return last


def generate(
    archetype: str,
    profile: str,
    seed: int = 7,
    months: int = 12,
    end: date | None = None,
) -> tuple[list[dict], Truth]:
    """Return (rows, truth). Rows are plain dicts ready for CSV."""
    if archetype not in ARCHETYPES:
        raise ValueError(f"unknown archetype {archetype!r}; choose from {list(ARCHETYPES)}")
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; choose from {list(PROFILES)}")

    rng = random.Random(f"{archetype}:{profile}:{seed}")
    a, p = ARCHETYPES[archetype], PROFILES[profile]
    end = end or DEFAULT_END
    starts = _month_starts(end, months)
    truth = Truth(archetype, profile, seed, [s.strftime("%Y-%m") for s in starts])

    events: list[tuple[date, str, float, Category]] = []
    by_customer: dict[str, float] = {}

    # Revenue
    for s in starts:
        key = s.strftime("%Y-%m")
        base = a["monthly_revenue"] * a["seasonality"][s.month - 1] * p["revenue_mult"]
        base *= rng.uniform(0.93, 1.07)
        truth.monthly_revenue[key] = 0.0
        if a["channel"] == "card":
            # Acquirer settles every weekday, so a month has 20-23 payouts.
            d = s
            days = [d + timedelta(i) for i in range(calendar.monthrange(s.year, s.month)[1])]
            days = [x for x in days if x.weekday() < 5]
            for x in days:
                amt = round(base / len(days) * rng.uniform(0.6, 1.4), 2)
                events.append((x, "CARD ACQUIRER SETTLEMENT", amt, Category.REVENUE_CARD))
                truth.monthly_revenue[key] += amt
        elif a["channel"] == "gateway":
            # Payment gateway pays out twice a week.
            d = s
            days = [d + timedelta(i) for i in range(calendar.monthrange(s.year, s.month)[1])]
            days = [x for x in days if x.weekday() in (1, 4)]
            for x in days:
                amt = round(base / len(days) * rng.uniform(0.7, 1.3), 2)
                events.append((x, "PAYGATE PAYOUT", amt, Category.REVENUE_CARD))
                truth.monthly_revenue[key] += amt
        else:
            # Invoices paid by bank transfer, 5-9 a month, lumpy.
            n = rng.randint(5, 9)
            weights = [rng.uniform(0.5, 2.0) for _ in range(n)]
            tot = sum(weights)
            for w in weights:
                x = s + timedelta(days=rng.randint(0, calendar.monthrange(s.year, s.month)[1] - 1))
                amt = round(base * w / tot, 2)
                who = rng.choice(CUSTOMER_NAMES)
                events.append((x, f"FPS CREDIT {who} INV{rng.randint(1000, 9999)}", amt, Category.REVENUE_TRANSFER))
                truth.monthly_revenue[key] += amt
                by_customer[who] = by_customer.get(who, 0.0) + amt
        truth.total_revenue += truth.monthly_revenue[key]

    # Fixed outgoings
    for s in starts:
        events.append((s + timedelta(days=0), "SO RENT PARKGATE ESTATES", -a["rent"], Category.RENT))
        pay_day = _last_working_day(s)
        payroll = a["payroll"] * rng.uniform(0.97, 1.03)
        events.append((pay_day, "BACS PAYROLL", -round(payroll, 2), Category.PAYROLL))
        paye = round(payroll * 0.22, 2)
        events.append((s + timedelta(days=21), "HMRC PAYE NI", -paye, Category.HMRC_PAYE))
        loan_name, loan_amt = a["loan"]
        events.append((s + timedelta(days=4), f"DD {loan_name}", -loan_amt, Category.LOAN_REPAYMENT))
        truth.total_debt_service += loan_amt
        drawings = a["drawings"]
        events.append((s + timedelta(days=9), "TFR OWNER DRAWINGS", -drawings, Category.OWNER_DRAWINGS))
        truth.total_owner_drawings += drawings
        fee = round(rng.uniform(18, 42), 2)
        events.append((pay_day, "ACCOUNT FEES", -fee, Category.BANK_FEES))
        for name, amt in a["suppliers"]:
            day = s + timedelta(days=rng.randint(2, 24))
            events.append((day, name, -round(amt * rng.uniform(0.85, 1.15), 2), Category.SUPPLIER))

    # VAT for stagger group 3: the quarter ending Nov is due 7 Jan, Feb due 7 Apr, and so
    # on. A good payer pays on the due date; a late one three weeks after it. A quarter
    # partly before the statement is estimated from the months that are inside it.
    avg_month = sum(truth.monthly_revenue.values()) / len(truth.monthly_revenue)
    for q in [s for s in starts if s.month in (1, 4, 7, 10)]:
        qy, qm = (q.year, q.month - 2) if q.month > 2 else (q.year - 1, q.month + 10)
        quarter = [f"{qy if m > 0 else qy - 1:04d}-{(m - 1) % 12 + 1:02d}" for m in (qm - 2, qm - 1, qm)]
        vat = round(sum(truth.monthly_revenue.get(m, avg_month) for m in quarter) * 0.12, 2)
        pay = q + timedelta(days=6 + (23 if p["hmrc_late"] else 0))
        events.append((pay, "HMRC VAT", -vat, Category.HMRC_VAT))
        truth.total_hmrc_vat += vat

    # The owner props up the account once, half way through.
    if p["capital_in"]:
        mid = starts[len(starts) // 2]
        events.append((mid + timedelta(days=14), "FPS CREDIT DIRECTOR CAPITAL INTRODUCED", float(p["capital_in"]), Category.OTHER))

    # Returned direct debits: a scheduled DD bounces, the bank returns it two days later
    # and charges a fee, and the payee re-presents it a week after the original.
    bounceable = [i for i, e in enumerate(events)
                  if e[3] in (Category.SUPPLIER, Category.LOAN_REPAYMENT)
                  and (e[1].endswith(" DD") or e[1].startswith("DD ")) and e[0] >= starts[min(3, len(starts) - 1)]]
    for i in rng.sample(bounceable, min(p["returned_dds"], len(bounceable))):
        d, desc, amt, cat = events[i]
        payee = desc[3:] if desc.startswith("DD ") else desc
        events.append((d + timedelta(days=2), f"UNPAID DD {payee}", -amt, Category.RETURNED_DD))
        events.append((d + timedelta(days=2), "UNPAID ITEM FEE", -15.0, Category.BANK_FEES))
        events.append((d + timedelta(days=7), desc, amt, cat))
        truth.returned_dd_count += 1

    # Order, then walk the balance.
    events.sort(key=lambda e: (e[0], e[2] < 0, e[1]))
    balance = float(p["opening_balance"])
    rows: list[dict] = []
    lowest: float | None = None
    closing: dict[date, float] = {}
    for i, (d, desc, amt, cat) in enumerate(events, start=1):
        balance = round(balance + amt, 2)
        lowest = balance if lowest is None else min(lowest, balance)
        closing[d] = balance
        tid = f"T{i:05d}"
        rows.append(
            {"id": tid, "date": d.isoformat(), "description": desc, "amount": f"{amt:.2f}", "balance": f"{balance:.2f}"}
        )
        truth.category_by_id[tid] = cat.value

    # Days that closed overdrawn, carrying each day's closing balance forward.
    day, last_day, carried, overdrawn = events[0][0], events[-1][0], 0.0, 0
    while day <= last_day:
        carried = closing.get(day, carried)
        overdrawn += carried < 0
        day += timedelta(days=1)

    # Which VAT quarters fall wholly inside the statement, and were they paid late?
    first_day = events[0][0]
    for q in [s for s in starts if s.month in (1, 4, 7, 10)]:
        q_end = date(q.year, q.month, 1) - timedelta(days=1)
        q_end = date(q_end.year, q_end.month, 1) - timedelta(days=1)
        window = _working_days_after(q + timedelta(days=6), 3)  # due on the 7th, plus DD collection
        if q_end >= first_day - timedelta(days=1) and window <= last_day and p["hmrc_late"]:
            truth.late_vat_count += 1

    # The figures that decide the fail rules, from the generator's own events: operating
    # cash is everything except financing and the owner's capital; a returned DD and its
    # re-presentation net to one payment.
    financing = {Category.LOAN_REPAYMENT, Category.LOAN_DRAWDOWN, Category.OWNER_DRAWINGS}
    returned_cat = {f"UNPAID DD {d[3:] if d.startswith('DD ') else d}": c
                    for _, d, _, c in events if c in (Category.SUPPLIER, Category.LOAN_REPAYMENT)}
    op = 0.0
    for _, desc, amt, cat in events:
        effective = returned_cat.get(desc, cat) if cat == Category.RETURNED_DD else cat
        if effective in financing or cat == Category.OTHER:
            continue
        op += amt
    truth.net_operating = round(op, 2)
    if truth.total_debt_service:
        truth.debt_cover = round((truth.net_operating - truth.total_owner_drawings) / truth.total_debt_service, 4)
    money_in = sum(a for _, _, a, c in events if a > 0 and c not in (Category.RETURNED_DD, Category.LOAN_DRAWDOWN))
    other_in = sum(a for _, _, a, c in events if a > 0 and c == Category.OTHER)
    truth.unclassified_share = round(other_in / money_in * 100, 2) if money_in else None
    period_days = (events[-1][0] - events[0][0]).days + 1
    truth.average_monthly_revenue = round(truth.total_revenue / (period_days / (365.25 / 12)), 2)

    truth.lowest_balance = lowest
    truth.days_overdrawn = overdrawn
    truth.total_revenue = round(truth.total_revenue, 2)
    truth.total_hmrc_vat = round(truth.total_hmrc_vat, 2)
    truth.monthly_revenue = {k: round(v, 2) for k, v in truth.monthly_revenue.items()}
    if by_customer:
        truth.top_customer_share = round(max(by_customer.values()) / sum(by_customer.values()) * 100, 2)
    return rows, truth


def _working_days_after(d: date, n: int) -> date:
    while n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def write(rows: list[dict], truth: Truth, out_dir: Path, stem: str | None = None) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or f"{truth.archetype}_{truth.profile}"
    csv_path = out_dir / f"{stem}.csv"
    truth_path = out_dir / f"{stem}.truth.json"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "date", "description", "amount", "balance"])
        w.writeheader()
        w.writerows(rows)
    truth_path.write_text(json.dumps(truth.__dict__, indent=2, default=str))
    return csv_path, truth_path
