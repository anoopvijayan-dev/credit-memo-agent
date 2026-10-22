"""Synthetic UK SME business current-account statements.

There is no open dataset of UK small-business bank statements, so this module makes
them. Each statement comes with a ground-truth file so the metrics layer can be tested
against known answers rather than eyeballed.

Three archetypes (cafe, trades, ecommerce) x three risk profiles (good, stretched, poor).
Everything is seeded, so the same arguments always produce the same statement.
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

# Counterparties are fictional. Any resemblance to a real business is accidental.
ARCHETYPES: dict[str, dict] = {
    "cafe": {
        "account_name": "HARBOURSIDE COFFEE LTD",
        "channel": "card",
        "monthly_revenue": 22_000,
        "seasonality": [0.80, 0.82, 0.90, 0.98, 1.05, 1.12, 1.18, 1.15, 1.02, 0.98, 0.95, 1.05],
        "suppliers": [("FRESHFIELD FOODS LTD DD", 4_600), ("BREW SUPPLY CO DD", 1_500), ("CITY WASTE SERVICES DD", 180), ("NORTHGRID ENERGY DD", 950)],
        "rent": 2_400,
        "payroll": 7_200,
        "loan": ("BRIDGEPOINT FINANCE LOAN", 1_150),
        "drawings": 2_500,
    },
    "trades": {
        "account_name": "KESTREL PLUMBING & HEATING LTD",
        "channel": "bacs",
        "monthly_revenue": 31_000,
        "seasonality": [1.15, 1.10, 1.00, 0.92, 0.88, 0.85, 0.85, 0.88, 0.98, 1.08, 1.15, 1.16],
        "suppliers": [("PLUMBWORLD TRADE DD", 9_800), ("FLEETCARD FUEL DD", 980), ("ABBEY VAN LEASE DD", 620), ("TRADESURE INSURANCE DD", 260)],
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
        "suppliers": [("STOCKHAVEN WHOLESALE", 15_500), ("PARCELFORCE WORLDWIDE DD", 3_100), ("SHOPFRONT PLATFORM DD", 290), ("ADSPEND MEDIA", 4_900)],
        "rent": 1_600,
        "payroll": 8_400,
        "loan": ("GROWTHCAP REVENUE LOAN", 1_900),
        "drawings": 3_000,
    },
}

PROFILES: dict[str, dict] = {
    # revenue_mult scales revenue; returned_dds is how many direct debits bounce in the
    # year; hmrc_late delays VAT payments; opening_balance sets how much headroom exists.
    "good": {"revenue_mult": 1.10, "returned_dds": 0, "hmrc_late": False, "opening_balance": 14_000},
    "stretched": {"revenue_mult": 1.00, "returned_dds": 1, "hmrc_late": False, "opening_balance": 3_500},
    "poor": {"revenue_mult": 0.82, "returned_dds": 4, "hmrc_late": True, "opening_balance": 600},
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
    end = end or (date.today().replace(day=1) - timedelta(days=1))
    starts = _month_starts(end, months)
    truth = Truth(archetype, profile, seed, [s.strftime("%Y-%m") for s in starts])

    events: list[tuple[date, str, float, Category]] = []

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

    # VAT, quarterly, paid on the 7th of the month after the quarter ends.
    quarters = [s for s in starts if s.month in (1, 4, 7, 10)]
    for q in quarters:
        prev3 = [m for m in truth.monthly_revenue if m < q.strftime("%Y-%m")][-3:]
        if len(prev3) < 3:
            continue
        vat = round(sum(truth.monthly_revenue[m] for m in prev3) * 0.12, 2)
        pay = q + timedelta(days=6 + (23 if p["hmrc_late"] else 0))
        events.append((pay, "HMRC VAT", -vat, Category.HMRC_VAT))
        truth.total_hmrc_vat += vat

    # Returned direct debits: the original DD bounces, bank charges a fee, supplier
    # re-presents a week later.
    for _ in range(p["returned_dds"]):
        s = rng.choice(starts[3:])
        name, amt = rng.choice(a["suppliers"])
        day = s + timedelta(days=rng.randint(10, 20))
        events.append((day, f"UNPAID DD {name}", 0.0, Category.RETURNED_DD))
        events.append((day, "UNPAID ITEM FEE", -15.0, Category.BANK_FEES))
        truth.returned_dd_count += 1

    # Order, then walk the balance.
    events.sort(key=lambda e: (e[0], e[2] < 0, e[1]))
    balance = float(p["opening_balance"])
    rows: list[dict] = []
    lowest = balance
    overdrawn_days: set[date] = set()
    for i, (d, desc, amt, cat) in enumerate(events, start=1):
        balance = round(balance + amt, 2)
        lowest = min(lowest, balance)
        if balance < 0:
            overdrawn_days.add(d)
        tid = f"T{i:05d}"
        rows.append(
            {"id": tid, "date": d.isoformat(), "description": desc, "amount": f"{amt:.2f}", "balance": f"{balance:.2f}"}
        )
        truth.category_by_id[tid] = cat.value
    truth.lowest_balance = lowest
    truth.days_overdrawn = len(overdrawn_days)
    truth.total_revenue = round(truth.total_revenue, 2)
    truth.monthly_revenue = {k: round(v, 2) for k, v in truth.monthly_revenue.items()}
    return rows, truth


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
