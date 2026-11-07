"""Data models shared across the pipeline.

Everything the memo can say about money comes from a Metric. A Metric carries the
transaction ids it was computed from, so each figure in the memo can be traced back
to the statement lines behind it.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Category(str, Enum):
    REVENUE_CARD = "revenue_card_settlement"
    REVENUE_TRANSFER = "revenue_transfer"
    PAYROLL = "payroll"
    HMRC_VAT = "hmrc_vat"
    HMRC_PAYE = "hmrc_paye"
    RENT = "rent"
    SUPPLIER = "supplier"
    LOAN_REPAYMENT = "loan_repayment"
    LOAN_DRAWDOWN = "loan_drawdown"
    BANK_FEES = "bank_fees"
    OWNER_DRAWINGS = "owner_drawings"
    RETURNED_DD = "returned_dd"
    OTHER = "other"


REVENUE_CATEGORIES = {Category.REVENUE_CARD, Category.REVENUE_TRANSFER}


class Transaction(BaseModel):
    id: str
    date: date
    description: str
    amount: float = Field(description="Signed. Credits positive, debits negative.")
    balance: float = Field(description="Running balance after this transaction.")
    category: Category | None = Field(
        default=None, description="Set by the categoriser; never read from the input file."
    )


class Statement(BaseModel):
    account_name: str
    transactions: list[Transaction]

    @property
    def start(self) -> date:
        return min(t.date for t in self.transactions)

    @property
    def end(self) -> date:
        return max(t.date for t in self.transactions)

    def by_id(self) -> dict[str, Transaction]:
        return {t.id: t for t in self.transactions}


class Metric(BaseModel):
    """A single computed figure with its provenance."""

    id: str = Field(description="Stable citation key, e.g. M4.")
    name: str
    value: float
    unit: Literal["GBP", "pct", "count", "days", "ratio", "months"]
    method: str = Field(description="One sentence saying how it was computed.")
    evidence: list[str] = Field(
        default_factory=list, description="Transaction ids this figure was computed from."
    )

    def display(self) -> str:
        if self.unit == "GBP":
            return f"£{self.value:,.0f}"
        if self.unit == "pct":
            return f"{self.value:.1f}%"
        if self.unit == "ratio":
            return f"{self.value:.2f}"
        return f"{self.value:.0f}"


class RuleOutcome(str, Enum):
    PASS = "pass"
    REFER = "refer"
    FAIL = "fail"


class RuleResult(BaseModel):
    rule_id: str
    description: str
    metric_id: str
    outcome: RuleOutcome
    detail: str


class PolicyResult(BaseModel):
    policy_name: str
    overall: RuleOutcome
    rules: list[RuleResult]


class GuardFinding(BaseModel):
    number_text: str
    reason: str


class GuardResult(BaseModel):
    numbers_checked: int
    unmatched: list[GuardFinding]
    missing_citations: list[str] = Field(
        default_factory=list, description="Metric ids quoted without a [Mn] tag."
    )

    @property
    def grounded(self) -> bool:
        return not self.unmatched and not self.missing_citations

    @property
    def grounding_rate(self) -> float:
        if self.numbers_checked == 0:
            return 1.0
        return 1 - len(self.unmatched) / self.numbers_checked


class ReviewDecision(BaseModel):
    approved: bool
    reviewer: str = "analyst"
    notes: str = ""
