"""Data models shared across the pipeline.

Everything the memo can say about money comes from a Metric. A Metric carries the
transaction ids it was computed from, so each figure in the memo can be traced back
to the statement lines behind it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, StrictBool, field_validator


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
    OTHER = "other"  # a credit no rule explains: unexplained money in
    CASH_DEPOSIT = "cash_deposit"
    OWN_ACCOUNT_TRANSFER = "own_account_transfer"  # either direction
    DIRECTOR_FUNDING = "director_funding"
    REFUND_IN = "refund_in"
    HMRC_REPAYMENT = "hmrc_repayment"
    OTHER_EXPLAINED_IN = "other_explained_in"  # grants, insurance claims, interest


class Bucket(str, Enum):
    """The money-in buckets. Every credit is in exactly one (R1, R3)."""

    TRADING_RECEIPTS = "trading_receipts"
    OWN_ACCOUNT_IN = "own_account_transfers_in"
    DIRECTOR_FUNDING = "director_shareholder_funding"
    LENDING_DRAWDOWNS = "lending_drawdowns"
    REFUNDS_RETURNED = "refunds_returned_payments_in"
    HMRC_REPAYMENTS = "hmrc_repayments"
    OTHER_EXPLAINED_IN = "other_explained_money_in"
    UNEXPLAINED = "unexplained_money_in"


# The bucket each credit category belongs to. A credit in a category missing from this
# map is a reconciliation failure, never a guess.
CREDIT_BUCKET: dict[Category, Bucket] = {
    Category.REVENUE_CARD: Bucket.TRADING_RECEIPTS,
    Category.REVENUE_TRANSFER: Bucket.TRADING_RECEIPTS,
    Category.CASH_DEPOSIT: Bucket.TRADING_RECEIPTS,  # D31: cash takings are trading receipts
    Category.OWN_ACCOUNT_TRANSFER: Bucket.OWN_ACCOUNT_IN,
    Category.DIRECTOR_FUNDING: Bucket.DIRECTOR_FUNDING,
    Category.LOAN_DRAWDOWN: Bucket.LENDING_DRAWDOWNS,
    Category.REFUND_IN: Bucket.REFUNDS_RETURNED,
    Category.RETURNED_DD: Bucket.REFUNDS_RETURNED,
    Category.HMRC_REPAYMENT: Bucket.HMRC_REPAYMENTS,
    Category.OTHER_EXPLAINED_IN: Bucket.OTHER_EXPLAINED_IN,
    Category.OTHER: Bucket.UNEXPLAINED,
}

REVENUE_CATEGORIES = {Category.REVENUE_CARD, Category.REVENUE_TRANSFER, Category.CASH_DEPOSIT}


class Transaction(BaseModel):
    id: str
    date: date
    description: str
    amount: float = Field(description="Signed. Credits positive, debits negative.")
    balance: float = Field(description="Running balance after this transaction.")
    category: Category | None = Field(
        default=None, description="Set by the categoriser; never read from the input file."
    )
    reverses: str | None = Field(
        default=None,
        description="For a returned direct debit: the id of the debit it undoes. Set by the categoriser.",
    )
    # Optional, with defaults, so a Transaction stored before they existed (a pending
    # review in a checkpoint) still loads.
    account_key: str = Field(default="main", description="Which of the case's accounts the line is from.")
    rule_id: str | None = Field(
        default=None, description="The built-in rule, firm rule or override that set the category."
    )
    provider_hint: str | None = Field(
        default=None, description="The provider's own category. Untrusted; no figure reads it."
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
    value: float | None = Field(
        description="None when the figure cannot be computed from this statement (say why in `method`)."
    )
    unit: Literal["GBP", "pct", "count", "days", "ratio", "months"]
    method: str = Field(description="One sentence saying how it was computed.")
    evidence: list[str] = Field(
        default_factory=list, description="Transaction ids this figure was computed from."
    )

    def display(self) -> str:
        """The figure as the memo should quote it. Negative figures keep their minus sign."""
        return format_value(self.value, self.unit)


# How many decimals each unit is shown with. The memo copies figures as shown, and the
# guard treats a figure written at this precision as exact (see guard._value_fits).
DISPLAY_DECIMALS = {"GBP": 0, "pct": 1, "ratio": 2}


def format_value(v: float | None, unit: str) -> str:
    """One display convention for metrics and policy thresholds alike."""
    if v is None:
        return "n/a"
    d = DISPLAY_DECIMALS.get(unit, 0)
    if unit == "GBP":
        return f"{'-' if v < 0 else ''}£{abs(v):,.{d}f}"
    if unit == "pct":
        return f"{v:.{d}f}%"
    if unit == "ratio":
        return f"{v:.{d}f}x"
    return f"{v:.{d}f}"


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
    op: str = ""
    threshold: float | None = None
    refer_below: float | None = None
    refer_above: float | None = None
    unit: str = Field(default="", description="Unit of the metric the threshold applies to.")

    def citable_values(self) -> list[float]:
        """Values a memo may quote with this rule's [Rn] tag."""
        return [v for v in (self.threshold, self.refer_below, self.refer_above) if v is not None]


class PolicyResult(BaseModel):
    policy_name: str
    overall: RuleOutcome
    rules: list[RuleResult]
    policy_sha256: str = Field(default="", description="Hash of the policy file the rules came from.")


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
            return 0.0 if self.unmatched else 1.0
        return max(0.0, 1 - len(self.unmatched) / self.numbers_checked)


class ReviewDecision(BaseModel):
    """A human decision on a memo. There is no default reviewer: someone must be named.

    `decided_at` is stamped by the system when the decision is accepted; the review
    node refuses a decision that tries to set it.
    """

    approved: StrictBool
    reviewer: str = Field(min_length=1, description="Name of the person who decided.")
    notes: str = ""
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    override_grounding: StrictBool = Field(
        default=False,
        description="True when approving a memo the guard did not ground; requires notes.",
    )
    mode: Literal["interactive", "automated"] = Field(
        default="interactive",
        description="automated: approved by a script (--yes); stamped on the memo as no interactive review.",
    )

    @field_validator("reviewer", "notes", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v
