"""Rule-based transaction categoriser.

Deterministic on purpose. Rules are ordered; the first match wins. Anything that
falls through is OTHER, and the memo graph can route OTHER lines to a model for a
second opinion later. The model never overrides a rule match.
"""

from __future__ import annotations

import re

from .models import Category, Statement, Transaction

RULES: list[tuple[re.Pattern[str], Category]] = [
    (re.compile(r"^UNPAID DD\b"), Category.RETURNED_DD),
    (re.compile(r"UNPAID ITEM FEE|ACCOUNT FEES|BANK CHARGES"), Category.BANK_FEES),
    (re.compile(r"HMRC VAT"), Category.HMRC_VAT),
    (re.compile(r"HMRC PAYE"), Category.HMRC_PAYE),
    (re.compile(r"PAYROLL|SALARIES"), Category.PAYROLL),
    (re.compile(r"\bRENT\b"), Category.RENT),
    (re.compile(r"\bLOAN\b|FINANCE\b"), Category.LOAN_REPAYMENT),
    (re.compile(r"OWNER DRAWINGS|DIRECTOR DRAWINGS|DIVIDEND"), Category.OWNER_DRAWINGS),
    (re.compile(r"ACQUIRER SETTLEMENT|PAYGATE PAYOUT|CARD SETTLEMENT"), Category.REVENUE_CARD),
    (re.compile(r"^FPS CREDIT|^BACS CREDIT|\bINV\d+"), Category.REVENUE_TRANSFER),
]


def categorise_one(t: Transaction) -> Category:
    desc = t.description.upper()
    for pattern, cat in RULES:
        if pattern.search(desc):
            return cat
    if t.amount < 0:
        return Category.SUPPLIER
    return Category.OTHER


def categorise(statement: Statement) -> Statement:
    for t in statement.transactions:
        t.category = categorise_one(t)
    return statement
