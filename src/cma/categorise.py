"""Rule-based transaction categoriser.

Deterministic on purpose. Rules are ordered; the first match whose direction also
matches wins. Direction matters: "BRIDGEPOINT FINANCE LOAN" is a repayment when money
leaves and a drawdown when it arrives, and a drawdown is financing, not revenue or
debt service. Anything that falls through is OTHER (credits) or SUPPLIER (debits).
The model never overrides a rule match.
"""

from __future__ import annotations

import re
from typing import Literal

from .models import Category, Statement, Transaction

Direction = Literal["in", "out", "any"]

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
    (re.compile(r"ACQUIRER SETTLEMENT|PAYGATE PAYOUT|CARD SETTLEMENT"), "in", Category.REVENUE_CARD),
    (re.compile(r"^FPS CREDIT|^BACS CREDIT|\bINV\d+"), "in", Category.REVENUE_TRANSFER),
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


def categorise(statement: Statement) -> Statement:
    for t in statement.transactions:
        t.category = categorise_one(t)
    return statement
