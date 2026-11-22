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

from .firm import DEFAULT_PAIRING_DAYS
from .models import CREDIT_BUCKET, Category

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


# Opt-in features (R47). With none, generate() is exactly as before: every existing archetype
# and profile gives a byte-identical statement and truth file. With any, the statement gains
# lines that the v2 rule table and the case profile recognise, and the truth gains every
# bucket, every line's category and every indicator, all worked out from the generator's own
# events (P17). Feature truth is the v2 categoriser's with the case profile in truth.case_profile.
FEATURES = ("acquirers", "director", "cash", "lenders", "two_accounts")

# Real acquirer and gateway payouts (v2's card_acquirers and payment_gateways lists) and a
# delivery platform, each with the acquirer it is, as the irregular-acquirer indicator groups.
ACQUIRER_PAYOUTS = (("SUMUP PAYMENTS LTD", "SUMUP"), ("STRIPE PAYMENTS UK", "STRIPE"),
                    ("DELIVEROO UK LTD", None))
PAYOUTS_A_MONTH = {"card": 22, "bacs": 7, "gateway": 9}  # each channel's receipts in a month
DIRECTOR_NAME = "AMARA OKAFOR"  # fictional; named as a director in the case profile
LENDER = "IWOCA LTD"
CASH_DEPOSIT_DESC = "CASH PAID IN AT BRANCH"
CASH_WITHDRAWAL_DESC = "CASH WITHDRAWAL HIGH ST"

# The shipped indicator thresholds (cma/thresholds/indicators.yaml), restated here so the
# truth does not come from the module it checks.
TRUTH_THRESHOLDS = {
    "large_credit_multiple": 5, "single_cash_pence": 250_000, "cash_month_share_pct": 50,
    "round_trip_days": 7, "round_trip_tolerance_pct": 10, "returned_dd_count": 1,
    "overdrawn_run_days": 7, "irregular_multiple": 3, "irregular_min_credits": 3,
}
# Money in that is not trading receipts, a drawdown or a returned DD (M9 and M16 leave it out).
_NON_TRADING_IN = {Category.OTHER, Category.OWN_ACCOUNT_TRANSFER, Category.DIRECTOR_FUNDING, Category.REFUND_IN,
                   Category.HMRC_REPAYMENT, Category.OTHER_EXPLAINED_IN}
_TRADING = {Category.REVENUE_CARD, Category.REVENUE_TRANSFER, Category.CASH_DEPOSIT}


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
    # Set only with features (left out of the truth file otherwise, so it stays byte-identical).
    features: list[str] = field(default_factory=list)
    case_profile: dict[str, list[str]] = field(default_factory=dict)
    total_money_in: float = 0.0
    buckets: dict[str, dict] = field(default_factory=dict)
    cash_deposits: dict = field(default_factory=dict)
    indicators: list[dict] = field(default_factory=list)
    accounts: list[dict] = field(default_factory=list)
    # With "two_accounts": the truth of the accounts as one case (case_truth); empty otherwise.
    case: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        """The truth file's content. A truth with no features has exactly the fields it had
        before features existed."""
        d = dict(self.__dict__)
        if not self.features:
            for k in ("features", "case_profile", "total_money_in", "buckets", "cash_deposits", "indicators",
                      "accounts", "case"):
                d.pop(k)
        else:
            if not self.case:
                d.pop("case")
            d["accounts"] = [{k: v for k, v in a.items() if k != "rows"} for a in self.accounts]
        return d


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
    features: tuple[str, ...] | list[str] | frozenset[str] = (),
) -> tuple[list[dict], Truth]:
    """Return (rows, truth). Rows are plain dicts ready for CSV. `features` (FEATURES) are
    opt-in; with "two_accounts" the second account's rows are in truth.accounts."""
    unknown = sorted(set(features) - set(FEATURES))
    if unknown:
        raise ValueError(f"unknown feature {unknown[0]!r}; choose from {list(FEATURES)}")
    features = [f for f in FEATURES if f in set(features)]
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
        # Director funding under v2, the default ruleset (D48, T11); v1 called it OTHER, which
        # P7 covers. The statement is byte-identical either way; only the truth names it.
        events.append((mid + timedelta(days=14), "FPS CREDIT DIRECTOR CAPITAL INTRODUCED", float(p["capital_in"]),
                       Category.DIRECTOR_FUNDING))

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

    # Opt-in features, after every draw from `rng`, from an rng of their own: with none the
    # statement is exactly as before.
    other = None
    if features:
        acquirer_of, other = _feature_events(events, truth, a, p, starts, features,
                                             random.Random(f"features:{archetype}:{profile}:{seed}"))

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
        if effective in financing or cat in _NON_TRADING_IN:  # director funding is non-trading
            continue
        op += amt
    truth.net_operating = round(op, 2)
    if truth.total_debt_service:
        truth.debt_cover = round((truth.net_operating - truth.total_owner_drawings) / truth.total_debt_service, 4)
    money_in = sum(a for _, _, a, c in events
                   if a > 0 and c not in (Category.RETURNED_DD, Category.LOAN_DRAWDOWN, Category.OWN_ACCOUNT_TRANSFER))
    other_in = sum(a for _, _, a, c in events if a > 0 and c in _NON_TRADING_IN and c != Category.OWN_ACCOUNT_TRANSFER)
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
    if features:
        lines = _lines(rows, truth.category_by_id, {r["id"]: acquirer_of.get(r["description"]) for r in rows})
        _money_in_truth(truth.__dict__, lines)
        truth.indicators = indicator_truth(lines)
        if other is not None:
            truth.accounts = [other]
            truth.case = case_truth(truth, rows, a["account_name"])
    return rows, truth


MAIN_KEY = "main"  # the business account's key in the case to_case makes; the other is its own key


def _case_id(account: str, line_id: str) -> str:
    """A line's id in a case of more than one account: its account key, a colon, its own id (R41)."""
    return f"{account}:{line_id}"


def case_truth(truth: Truth, rows: list[dict], account_name: str) -> dict:
    """The truth of the generated accounts as one case (R8, R41, R47, P11, P17), from the
    generator's own events: every line's category under its case line id; each own-account
    pair, debit first, which is a move the generator made between the accounts (the business
    account's FPS line to or from the reserve account and the reserve account's line naming
    the business account, on the same day, for the same amount); the money-in buckets and the
    cash deposits across the case; the money in from outside the applicant's accounts, every
    credit that is half of no move; and each account's indicators, raised on that account."""
    other = truth.accounts[0]
    reserve = other["account_name"]
    mine = {f"FPS TO {reserve}", f"FPS CREDIT {reserve}"}
    theirs = {f"FPS TO {account_name}", f"FPS CREDIT {account_name}"}
    # A move's two halves share a day and an amount, of opposite signs; no two moves share both.
    halves = {(r["date"], -_pence(r["amount"])): r["id"] for r in other["rows"] if r["description"] in theirs}
    pairs = []
    for r in rows:
        if r["description"] in mine:
            p = _pence(r["amount"])
            ids = (_case_id(MAIN_KEY, r["id"]), _case_id(other["key"], halves.pop((r["date"], p))))
            pairs.append(list(ids if p < 0 else ids[::-1]))
    assert not halves, "every reserve-account move has its half in the business account"
    pairs.sort()
    lines = [dict(x, id=_case_id(key, x["id"])) for key, rs, cats in
             ((MAIN_KEY, rows, truth.category_by_id), (other["key"], other["rows"], other["category_by_id"]))
             for x in _lines(rs, cats, {})]
    case: dict = {"accounts": [MAIN_KEY, other["key"]],
                  "category_by_id": {x["id"]: x["cat"].value for x in lines},
                  "own_account_pairs": pairs}
    _money_in_truth(case, lines)
    in_moves = {i for p in pairs for i in p}
    case["outside_money_in"] = sum(x["pence"] for x in lines if x["pence"] > 0 and x["id"] not in in_moves) / 100
    case["indicators"] = {MAIN_KEY: truth.indicators, other["key"]: other["indicators"]}
    return case


def _reserve_name(account_name: str) -> str:
    """The applicant's second account: a reserve account in the business's name."""
    return f"{account_name.split()[0]} BUSINESS RESERVE"


def _feature_events(events: list, truth: Truth, a: dict, p: dict, starts: list[date], features: list[str],
                    frng: random.Random) -> tuple[dict[str, str | None], dict | None]:
    """Add the lines of each feature to `events`, keep the truth's revenue and debt service in
    step, and return (the acquirer each acquirer payout is, the second account or None)."""
    truth.features = list(features)
    profile: dict[str, list[str]] = {"directors": [], "own_accounts": []}
    acquirer_of: dict[str, str | None] = {}
    mid = starts[len(starts) // 2]
    # A payout about the size of the archetype's own typical receipt.
    base = a["monthly_revenue"] * p["revenue_mult"] / PAYOUTS_A_MONTH[a["channel"]]

    def month_days(s: date, weekday: int) -> list[date]:
        n = calendar.monthrange(s.year, s.month)[1]
        return [s + timedelta(i) for i in range(n) if (s + timedelta(i)).weekday() == weekday]

    def trading(d: date, desc: str, amt: float, cat: Category) -> None:
        events.append((d, desc, amt, cat))
        key = d.strftime("%Y-%m")
        truth.monthly_revenue[key] += amt
        truth.total_revenue += amt

    if "acquirers" in features:
        # Fortnightly payouts from two real acquirers and, in the other weeks, a delivery
        # platform; one SumUp payout four times the usual, which the irregular-acquirer
        # indicator flags.
        for desc, acquirer in ACQUIRER_PAYOUTS:
            acquirer_of[desc] = acquirer
        for s in starts:
            for i, d in enumerate(month_days(s, 2)):
                for desc, _ in (ACQUIRER_PAYOUTS[:2] if i % 2 == 0 else ACQUIRER_PAYOUTS[2:]):
                    trading(d, desc, round(base * frng.uniform(0.8, 1.2), 2), Category.REVENUE_CARD)
        trading(mid + timedelta(days=12), ACQUIRER_PAYOUTS[0][0], round(base * 4, 2), Category.REVENUE_CARD)

    if "director" in features:
        # A director pays in by name (the case profile names them, R5) and lends by director
        # loan wording (the v2 table, R4).
        profile["directors"].append(DIRECTOR_NAME)
        events.append((starts[min(2, len(starts) - 1)] + timedelta(days=9), f"FPS CREDIT {DIRECTOR_NAME}", 6000.0,
                       Category.DIRECTOR_FUNDING))
        events.append((starts[-1] + timedelta(days=16), "DIRECTOR LOAN A OKAFOR", 2500.0, Category.DIRECTOR_FUNDING))

    if "cash" in features:
        # Cash takings paid in every Monday, below the single-deposit limit; one deposit at or
        # above it; and cash withdrawn and paid back in two days later, within 10% (R47, D31).
        for s in starts:
            for d in month_days(s, 0):
                trading(d, CASH_DEPOSIT_DESC, round(frng.uniform(250, 900), 2), Category.CASH_DEPOSIT)
        trading(mid + timedelta(days=11), CASH_DEPOSIT_DESC, 3200.0, Category.CASH_DEPOSIT)
        # The limit itself: a deposit of exactly the limit is flagged, a penny under it is not.
        trading(starts[-1] + timedelta(days=20), CASH_DEPOSIT_DESC, 2500.0, Category.CASH_DEPOSIT)
        trading(starts[0] + timedelta(days=20), CASH_DEPOSIT_DESC, 2499.99, Category.CASH_DEPOSIT)
        out = starts[max(0, len(starts) // 2 - 1)] + timedelta(days=17)
        events.append((out, CASH_WITHDRAWAL_DESC, -1500.0, Category.SUPPLIER))
        trading(out + timedelta(days=2), CASH_DEPOSIT_DESC, 1450.0, Category.CASH_DEPOSIT)

    if "lenders" in features:
        # A named lender pays a loan in and takes monthly repayments from the month after.
        at = len(starts) // 3
        events.append((starts[at] + timedelta(days=3), LENDER, 15000.0, Category.LOAN_DRAWDOWN))
        for s in starts[at + 1:]:
            events.append((s + timedelta(days=14), f"DD {LENDER}", -1375.0, Category.LOAN_REPAYMENT))
            truth.total_debt_service += 1375.0

    other = None
    if "two_accounts" in features:
        # Money moved to and from the applicant's reserve account, which the case profile names
        # as an own account (R6); the reserve account's own statement names the main account.
        reserve = _reserve_name(a["account_name"])
        profile["own_accounts"] += [reserve, a["account_name"]]
        mirror: list[tuple[date, str, float, Category]] = []
        before = list(events)  # the business account's lines that are no move

        def move(d: date, amt: float) -> float:
            # A move's amount, as the business account's line has it: raised a penny at a time
            # while another line of the business account has the same amount within the default
            # pairing days, so its other half has one candidate and the pair is the truth (R8,
            # R9, P11). A clash is rare, so almost every move keeps its round amount.
            pence = round(amt * 100)
            step = 1 if pence > 0 else -1
            while any(round(e[2] * 100) == pence and abs((e[0] - d).days) <= DEFAULT_PAIRING_DAYS for e in before):
                pence += step
            return pence / 100

        for i, s in enumerate(starts):
            d = s + timedelta(days=19)
            amt = move(d, -1000.0)
            events.append((d, f"FPS TO {reserve}", amt, Category.OWN_ACCOUNT_TRANSFER))
            mirror.append((d, f"FPS CREDIT {a['account_name']}", -amt, Category.OWN_ACCOUNT_TRANSFER))
            if i % 3 == 2:
                d = s + timedelta(days=5)
                amt = move(d, 2500.0)
                events.append((d, f"FPS CREDIT {reserve}", amt, Category.OWN_ACCOUNT_TRANSFER))
                mirror.append((d, f"FPS TO {a['account_name']}", -amt, Category.OWN_ACCOUNT_TRANSFER))
            mirror.append((s + timedelta(days=26), "GROSS INTEREST", round(frng.uniform(3, 9), 2),
                           Category.OTHER_EXPLAINED_IN))
        mirror.sort(key=lambda e: (e[0], e[2] < 0, e[1]))
        rows, cats = _walk(mirror, RESERVE_OPENING)
        other = {"key": "reserve", "account_name": reserve, "opening_balance": float(RESERVE_OPENING),
                 "rows": rows, "category_by_id": cats}
        lines = _lines(rows, cats, {})
        _money_in_truth(other, lines)
        other["indicators"] = indicator_truth(lines)
    truth.case_profile = profile
    return acquirer_of, other


RESERVE_OPENING = 20_000


def _walk(events: list[tuple[date, str, float, Category]], opening: float) -> tuple[list[dict], dict[str, str]]:
    """Rows and each line's category for events already in statement order."""
    balance, rows, cats = float(opening), [], {}
    for i, (d, desc, amt, cat) in enumerate(events, start=1):
        balance = round(balance + amt, 2)
        tid = f"T{i:05d}"
        rows.append({"id": tid, "date": d.isoformat(), "description": desc, "amount": f"{amt:.2f}",
                     "balance": f"{balance:.2f}"})
        cats[tid] = cat.value
    return rows, cats


def _pence(text: str) -> int:
    whole, _, frac = text.lstrip("-").partition(".")
    p = int(whole) * 100 + int((frac + "00")[:2])
    return -p if text.startswith("-") else p


def _lines(rows: list[dict], cats: dict[str, str], acquirer: dict[str, str | None]) -> list[dict]:
    return [{"id": r["id"], "date": date.fromisoformat(r["date"]), "desc": r["description"],
             "pence": _pence(r["amount"]), "balance": _pence(r["balance"]), "cat": Category(cats[r["id"]]),
             "acquirer": acquirer.get(r["id"])} for r in rows]


def _money_in_truth(target: dict, lines: list[dict]) -> None:
    """Total money in, every money-in bucket and the cash within trading receipts, each with
    its lines (R1, R55), from the generator's own categories."""
    credits = [x for x in lines if x["pence"] > 0]
    buckets: dict[str, dict] = {}
    for b in dict.fromkeys(CREDIT_BUCKET.values()):
        ids = [x["id"] for x in credits if CREDIT_BUCKET[x["cat"]] == b]
        buckets[b.value] = {"amount": sum(x["pence"] for x in credits if x["id"] in set(ids)) / 100, "ids": ids}
    target["total_money_in"] = sum(x["pence"] for x in credits) / 100
    target["buckets"] = buckets
    cash = [x for x in credits if x["cat"] == Category.CASH_DEPOSIT]
    target["cash_deposits"] = {"amount": sum(x["pence"] for x in cash) / 100, "ids": [x["id"] for x in cash]}


def _median2(xs: list[int]) -> int:
    """Twice the median, so it stays an integer."""
    s, n = sorted(xs), len(xs)
    return 2 * s[n // 2] if n % 2 else s[n // 2 - 1] + s[n // 2]


def indicator_truth(lines: list[dict]) -> list[dict]:
    """Every indicator the shipped thresholds raise on these lines, as [{kind, lines}], in the
    order of the thresholds file and then of the statement. The generator writes no gambling
    payment, so none is raised."""
    th = TRUTH_THRESHOLDS
    pos = {x["id"]: i for i, x in enumerate(lines)}
    out: list[dict] = []

    def fire(kind: str, ids: list[str]) -> None:
        out.append({"kind": kind, "lines": sorted(dict.fromkeys(ids), key=pos.__getitem__)})

    credits = [x for x in lines if x["pence"] > 0]
    trading = [x for x in credits if x["cat"] in _TRADING]
    cash = [x for x in credits if x["cat"] == Category.CASH_DEPOSIT]
    if trading:
        typical2 = _median2([x["pence"] for x in trading])
        for x in credits:
            if 2 * x["pence"] >= th["large_credit_multiple"] * typical2:
                fire("large_credit", [x["id"]])
    for x in cash:
        if x["pence"] >= th["single_cash_pence"]:
            fire("single_cash_deposit", [x["id"]])
    months: dict[tuple[int, int], list[dict]] = {}
    for x in trading:
        months.setdefault((x["date"].year, x["date"].month), []).append(x)
    for _, xs in sorted(months.items()):
        month_cash = [x for x in xs if x["cat"] == Category.CASH_DEPOSIT]
        c, tr = sum(x["pence"] for x in month_cash), sum(x["pence"] for x in xs)
        if c > 0 and c * 100 > th["cash_month_share_pct"] * tr:
            fire("cash_heavy_month", [x["id"] for x in month_cash])
    withdrawals = [x for x in lines if x["desc"] == CASH_WITHDRAWAL_DESC and x["pence"] < 0]
    for d in cash:
        matches = [w for w in withdrawals
                   if 0 <= (d["date"] - w["date"]).days <= th["round_trip_days"] and pos[w["id"]] < pos[d["id"]]
                   and abs(d["pence"] + w["pence"]) * 100 <= th["round_trip_tolerance_pct"] * -w["pence"]]
        if matches:
            fire("cash_round_trip", [*(w["id"] for w in matches), d["id"]])
    returned = [x for x in lines if x["cat"] == Category.RETURNED_DD]
    if len(returned) >= th["returned_dd_count"]:
        for x in returned:
            fire("returned_dd", [x["id"]])
    closes: dict[date, dict] = {}
    for x in lines:
        closes[x["date"]] = x
    day, last, current, run = min(closes), max(closes), None, []
    while True:
        current = closes.get(day, current)
        if current["balance"] < 0:
            run.append(current["id"])
        else:
            if len(run) >= th["overdrawn_run_days"]:
                fire("overdrawn_run", run)
            run = []
        if day == last:
            break
        day += timedelta(days=1)
    if len(run) >= th["overdrawn_run_days"]:
        fire("overdrawn_run", run)
    by_acquirer: dict[str, list[dict]] = {}
    for x in credits:
        if x["acquirer"]:
            by_acquirer.setdefault(x["acquirer"], []).append(x)
    irregular = []
    for xs in by_acquirer.values():
        if len(xs) < th["irregular_min_credits"]:
            continue
        med2 = _median2([x["pence"] for x in xs])
        m = th["irregular_multiple"]
        irregular += [x for x in xs if 2 * x["pence"] >= m * med2 or 2 * x["pence"] * m <= med2]
    for x in sorted(irregular, key=lambda x: pos[x["id"]]):
        fire("irregular_acquirer", [x["id"]])
    return out


def _working_days_after(d: date, n: int) -> date:
    while n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "date", "description", "amount", "balance"])
        w.writeheader()
        w.writerows(rows)


def write(rows: list[dict], truth: Truth, out_dir: Path, stem: str | None = None) -> tuple[Path, Path]:
    """Write STEM.csv and STEM.truth.json, and each further account's statement as
    STEM_KEY.csv (its truth is in STEM.truth.json, under accounts)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or f"{truth.archetype}_{truth.profile}"
    csv_path = out_dir / f"{stem}.csv"
    truth_path = out_dir / f"{stem}.truth.json"
    _write_csv(csv_path, rows)
    for account in truth.accounts:
        _write_csv(out_dir / f"{stem}_{account['key']}.csv", account["rows"])
    truth_path.write_text(json.dumps(truth.to_json(), indent=2, default=str))
    return csv_path, truth_path


def to_case(rows: list[dict], truth: Truth, work: Path, *, label: str | None = None,
            root: Path | None = None, warn=None) -> tuple[str, Path]:
    """Make one demo case in the case store from a generated statement (R47, R48): every
    account imported under its key (the business account as "main"), and the case profile
    the generator gives (its directors and own accounts) with the VAT stagger it used. The
    statements are written to `work` first. `warn` is new_case's sync-folder warning (R32).
    If anything after the case is made fails, the case this call made is removed, so no
    half-imported demo case is left behind. Returns (case id, case folder)."""
    import shutil

    from . import store

    csv_path, _ = write(rows, truth, Path(work))
    case_id = store.new_case(label=label or f"synthetic {truth.archetype} {truth.profile}", demo=True, root=root,
                             warn=warn)
    folder = store.case_folder(case_id, root)
    try:
        names = truth.case_profile or {"directors": [], "own_accounts": []}
        store.update_profile(folder, lambda old: {**old, "directors": list(names["directors"]),
                                                  "own_accounts": list(names["own_accounts"]),
                                                  "vat_stagger": truth.vat_stagger})
        store.import_file(folder, csv_path, "csv", account=MAIN_KEY)
        for account in truth.accounts:
            store.import_file(folder, csv_path.with_name(f"{csv_path.stem}_{account['key']}.csv"), "csv",
                              account=account["key"])
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)  # the case this call made, never another
        raise
    return case_id, folder
