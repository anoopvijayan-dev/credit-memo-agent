"""Rule-based transaction categoriser.

Deterministic on purpose. Rules are ordered; the first match whose direction also
matches wins. Direction matters: "HOLLOWAY BRIDGE FINANCE LOAN" is a repayment when
money leaves and a drawdown when it arrives, and a drawdown is financing, not revenue
or debt service. The model never overrides a rule match.

Revenue is claimed narrowly. A card or gateway settlement is trading income. A bank
transfer in is only counted as a customer payment when it carries an invoice
reference (INV1234, INVOICE 1234); anything else that arrives by transfer (the owner
putting money back, a capital injection, a VAT repayment, a transfer from savings) is
OTHER and shows up in M16, unclassified inflows, for a person to look at. That costs
some real revenue on statements without invoice references, but the alternative is
that money the owner moves in and out counts as trading income and lifts debt cover.

A returned direct debit ("UNPAID DD ...") is linked to the debit it reverses, so a
bounced and re-presented payment counts once, not twice. An invoice-referenced credit
from someone the applicant also pays by transfer is not revenue (round trip).
"""

from __future__ import annotations

import re
from typing import Literal

from .models import Category, Statement, Transaction

Direction = Literal["in", "out", "any"]

INVOICE_REF = re.compile(r"\bINV(?:OICE)?\s*[-#:]?\s*\d+")
# Money in that is never trading income, whatever reference it carries.
NON_TRADING_IN = re.compile(
    r"\bDIRECTOR\b|\bOWNER\b|\bCAPITAL\b|\bSHAREHOLDER\b|\bHMRC\b|\bREFUND\b|\bREVERSAL\b"
    r"|\bSAVINGS\b|\bPOT TRANSFER\b|\bINTEREST\b|\bGRANT\b|\bINSURANCE CLAIM\b"
)

RULES: list[tuple[re.Pattern[str], Direction, Category]] = [
    (re.compile(r"^UNPAID DD\b"), "any", Category.RETURNED_DD),
    (re.compile(r"UNPAID ITEM FEE|ACCOUNT FEES|BANK CHARGES"), "out", Category.BANK_FEES),
    (re.compile(r"HMRC VAT"), "out", Category.HMRC_VAT),
    (re.compile(r"HMRC PAYE"), "out", Category.HMRC_PAYE),
    (re.compile(r"PAYROLL|SALARIES"), "out", Category.PAYROLL),
    (re.compile(r"\bRENT\b"), "out", Category.RENT),
    (re.compile(r"\bLOAN\b|FINANCE\b"), "out", Category.LOAN_REPAYMENT),
    (re.compile(r"\bLOAN\b|FINANCE\b"), "in", Category.LOAN_DRAWDOWN),
    (re.compile(r"OWNER DRAWINGS|DIRECTOR DRAWINGS|DIVIDEND"), "out", Category.OWNER_DRAWINGS),
    (NON_TRADING_IN, "in", Category.OTHER),
    (re.compile(r"ACQUIRER SETTLEMENT|PAYGATE PAYOUT|CARD SETTLEMENT"), "in", Category.REVENUE_CARD),
    (INVOICE_REF, "in", Category.REVENUE_TRANSFER),
]


def _direction_ok(direction: Direction, amount: float) -> bool:
    if direction == "any":
        return True
    return amount > 0 if direction == "in" else amount < 0


def categorise_one(t: Transaction) -> Category:
    desc = t.description.upper()
    for pattern, direction, cat in RULES:
        if pattern.search(desc) and _direction_ok(direction, t.amount):
            return cat
    if t.amount < 0:
        return Category.SUPPLIER
    return Category.OTHER


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
    for i, r in enumerate(txns):
        if r.category != Category.RETURNED_DD or r.amount <= 0:
            continue
        payee = counterparty(r.description)
        for o in reversed(txns[:i]):
            if (
                o.id not in taken
                and o.amount < 0
                and o.category not in (Category.RETURNED_DD, None)
                and abs(o.amount + r.amount) < 0.005
                and counterparty(o.description) == payee
            ):
                r.reverses = o.id
                taken.add(o.id)
                break
    return statement


OUTGOING_TRANSFER = re.compile(r"^(?:FPS DEBIT|BACS DEBIT|TFR)\b")


def unmark_round_trips(statement: Statement) -> Statement:
    """A "customer" the applicant also sends transfers to is not counted as revenue.

    Money sent out by transfer and received back with an invoice reference is the
    cheapest way to manufacture revenue. Such credits become OTHER (and so show in M16).
    This also catches a genuine customer who is paid a refund by transfer: the cost is
    understated revenue, which a person can add back; the alternative is gameable.
    """
    paid_out = {counterparty(t.description) for t in statement.transactions
                if t.amount < 0 and OUTGOING_TRANSFER.search(t.description.upper())}
    for t in statement.transactions:
        if t.category == Category.REVENUE_TRANSFER and counterparty(t.description) in paid_out:
            t.category = Category.OTHER
    return statement


def categorise(statement: Statement) -> Statement:
    for t in statement.transactions:
        t.category = categorise_one(t)
        t.reverses = None
    return link_reversals(unmark_round_trips(statement))
