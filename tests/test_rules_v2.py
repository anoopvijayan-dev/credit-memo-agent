"""The v2 rule table (R4) and the categoriser's precedence with per-line provenance.

v2 is packaged data like v1 (src/cma/rules/v2.yaml) and adds the maintained lists of R4.
Every line records the rule that set its category. Precedence, first match wins: a
per-line override, the protected built-in rules, the case profile's names, firm rules,
the rest of the table, the default.
"""

import csv
import gzip
import hashlib
import json
import re
from datetime import date, timedelta
from importlib import resources
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from cma import categorise, ingest, metrics, synth
from cma.categorise import (
    LineOverride,
    ProfileNames,
    Rule,
    RulesetError,
    classify,
    load_ruleset,
    validate_ruleset,
)
from cma.models import CREDIT_BUCKET, Bucket, Category, Statement, Transaction

C = Category
GOLDEN = Path(__file__).parent / "golden" / "p7_v1_baseline.json.gz"


@pytest.fixture(scope="module")
def v2():
    return load_ruleset("v2")


def _t(desc: str, amount: float, tid: str = "T") -> Transaction:
    return Transaction(id=tid, date=date(2026, 1, 1), description=desc, amount=amount, balance=0.0)


# --- one table of cases per maintained list ------------------------------------------
# (description, amount, category, rule id recorded on the line)

LISTS = {
    "card_acquirers": [
        ("SUMUP PAYMENTS LTD", 412.30, C.REVENUE_CARD, "v2:card_acquirers"),
        ("BGC SUM UP PAYOUT", 80.00, C.REVENUE_CARD, "v2:card_acquirers"),
        ("WORLDPAY UK LTD", 1250.00, C.REVENUE_CARD, "v2:card_acquirers"),
        ("ZETTLE BY PAYPAL", 55.10, C.REVENUE_CARD, "v2:card_acquirers"),
        ("IZETTLE PAYOUT", 55.10, C.REVENUE_CARD, "v2:card_acquirers"),
        ("SQUARE EUROPE LTD", 300.00, C.REVENUE_CARD, "v2:card_acquirers"),
        ("BARCLAYCARD MERCHANT SERVICES", 990.00, C.REVENUE_CARD, "v2:card_acquirers"),
        ("ELAVON FINANCIAL SERVICES", 210.00, C.REVENUE_CARD, "v2:card_acquirers"),
        ("GLOBAL PAYMENTS UK", 120.00, C.REVENUE_CARD, "v2:card_acquirers"),
        ("LLOYDS CARDNET SETTLEMENT", 640.00, C.REVENUE_CARD, "v2:card_acquirers"),
        ("DOJO PAYOUT", 400.00, C.REVENUE_CARD, "v2:card_acquirers"),
        ("TEYA UK LTD", 400.00, C.REVENUE_CARD, "v2:card_acquirers"),
        ("CHECKOUT.COM PAYOUT", 75.00, C.REVENUE_CARD, "v2:card_acquirers"),
        ("ADYEN NV", 75.00, C.REVENUE_CARD, "v2:card_acquirers"),
        ("CARD ACQUIRER SETTLEMENT", 100.00, C.REVENUE_CARD, "v2:card_settlement"),  # v1's wording stays
        # a debit to the same names is not money in; a card repayment is a supplier
        ("BARCLAYCARD", -120.00, C.SUPPLIER, "v2:default"),
        ("SUMUP FEES", -4.50, C.SUPPLIER, "v2:default"),
        # whole words only: a payer who merely contains an acquirer's letters is not one
        ("DOJOMATIC LTD", 50.00, C.OTHER, "v2:default"),
    ],
    "payment_gateways": [
        ("STRIPE PAYMENTS UK LTD", 2300.00, C.REVENUE_CARD, "v2:payment_gateways"),
        ("STRIPE PAYOUT", 2300.00, C.REVENUE_CARD, "v2:payment_gateways"),
        ("PAYPAL PPWDL 7HK2", 145.00, C.REVENUE_CARD, "v2:payment_gateways"),
        ("GOCARDLESS LTD", 600.00, C.REVENUE_CARD, "v2:payment_gateways"),
        ("OPAYO PAYOUT", 60.00, C.REVENUE_CARD, "v2:payment_gateways"),
        ("KLARNA BANK AB", 89.00, C.REVENUE_CARD, "v2:payment_gateways"),
        ("CLEARPAY FINANCE LTD", 89.00, C.LOAN_DRAWDOWN, "v2:loan_drawdown"),  # lender wording wins, never trading
    ],
    "marketplaces": [
        ("AMAZON PAYMENTS UK", 830.00, C.REVENUE_CARD, "v2:marketplaces"),
        ("AMAZON SERVICES EUROPE SARL", 830.00, C.REVENUE_CARD, "v2:marketplaces"),
        ("EBAY COMMERCE UK LTD", 210.00, C.REVENUE_CARD, "v2:marketplaces"),
        ("ETSY UK", 33.00, C.REVENUE_CARD, "v2:marketplaces"),
        ("NOTHS PAYOUT", 70.00, C.REVENUE_CARD, "v2:marketplaces"),
        ("SHOPIFY PAYMENTS", 70.00, C.REVENUE_CARD, "v2:marketplaces"),
        # a plain Amazon credit is usually a refund on a purchase: left for a person
        ("AMAZON.CO.UK", 25.00, C.OTHER, "v2:default"),
    ],
    "delivery_platforms": [
        ("DELIVEROO PLC", 510.00, C.REVENUE_CARD, "v2:delivery_platforms"),
        ("ROOFOODS LTD", 510.00, C.REVENUE_CARD, "v2:delivery_platforms"),
        ("JUST EAT PAYOUT", 310.00, C.REVENUE_CARD, "v2:delivery_platforms"),
        ("JUST-EAT.CO.UK", 310.00, C.REVENUE_CARD, "v2:delivery_platforms"),
        ("UBER EATS UK", 150.00, C.REVENUE_CARD, "v2:delivery_platforms"),
        ("PORTIER EATS UK LTD", 150.00, C.REVENUE_CARD, "v2:delivery_platforms"),
        ("UBER TRIP", -12.00, C.SUPPLIER, "v2:default"),
    ],
    "lenders": [
        ("IWOCA LTD", 10000.00, C.LOAN_DRAWDOWN, "v2:lender_drawdown"),
        ("DD IWOCA LTD", -450.00, C.LOAN_REPAYMENT, "v2:lender_repayment"),
        ("FUNDING CIRCLE", 25000.00, C.LOAN_DRAWDOWN, "v2:lender_drawdown"),
        ("DD FUNDING CIRCLE", -812.40, C.LOAN_REPAYMENT, "v2:lender_repayment"),
        ("YOULEND LTD", 8000.00, C.LOAN_DRAWDOWN, "v2:lender_drawdown"),
        ("LIBERIS LTD", 8000.00, C.LOAN_DRAWDOWN, "v2:lender_drawdown"),
        ("365 BUSINESS FINANCE", 6000.00, C.LOAN_DRAWDOWN, "v2:lender_drawdown"),
        ("ESME LOANS", 15000.00, C.LOAN_DRAWDOWN, "v2:lender_drawdown"),
        # merchant cash advances from acquirers and gateways are drawdowns, not takings
        ("STRIPE CAPITAL", 5000.00, C.LOAN_DRAWDOWN, "v2:lender_drawdown"),
        ("PAYPAL WORKING CAPITAL", 3000.00, C.LOAN_DRAWDOWN, "v2:lender_drawdown"),
        ("SHOPIFY CAPITAL", 3000.00, C.LOAN_DRAWDOWN, "v2:lender_drawdown"),
        ("SUMUP CASH ADVANCE", 2000.00, C.LOAN_DRAWDOWN, "v2:lender_drawdown"),
        ("BOOST CAPITAL", 4000.00, C.LOAN_DRAWDOWN, "v2:lender_drawdown"),  # a lender, not capital introduced
        ("MERCHANT CASH ADVANCE", 4000.00, C.LOAN_DRAWDOWN, "v2:lender_drawdown"),
        ("HOLLOWAY BRIDGE FINANCE LOAN", -300.00, C.LOAN_REPAYMENT, "v2:loan_repayment"),  # v1 wording stays
        ("HOLLOWAY BRIDGE FINANCE LOAN", 9000.00, C.LOAN_DRAWDOWN, "v2:loan_drawdown"),
    ],
    "pot_names": [
        ("TAX POT", 400.00, C.OWN_ACCOUNT_TRANSFER, "v2:pot_names"),
        ("VAT POT", -400.00, C.OWN_ACCOUNT_TRANSFER, "v2:pot_names"),
    ],
    "own_pots_savings": [
        ("POT TRANSFER IN HOLIDAY POT", 200.00, C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"),
        ("POT TRANSFER OUT HOLIDAY POT", -200.00, C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"),
        ("TFR TO SAVINGS", -1000.00, C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"),
        # savings wording with no move is tried after the director wording
        ("FPS CREDIT SAVINGS ACCOUNT", 1000.00, C.OWN_ACCOUNT_TRANSFER, "v2:savings_wording"),
        # an explicit move from the applicant's savings beats protected wording in its name
        ("TFR FROM SAVINGS CAPITAL RESERVE", 300.00, C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"),
        ("TRANSFER TO SAVINGS DIRECTOR", -300.00, C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"),
        ("TRANSFER FROM BUSINESS SAVER", 750.00, C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"),
        ("TO SPACE VAT", -300.00, C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"),
        ("FROM SPACE VAT", 300.00, C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"),
        ("TEAPOT SUPPLIES", -18.00, C.SUPPLIER, "v2:default"),
        # an explicit move to a pot is an own-account transfer, whatever the pot is for
        ("TFR TO POT RENT", -300.00, C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"),
        ("POT TRANSFER PAYROLL POT", -3000.00, C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"),
        ("FROM POT RENT", 300.00, C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"),
    ],
    "cash_paid_in": [
        ("CASH PAID IN", 640.00, C.CASH_DEPOSIT, "v2:cash_paid_in"),
        ("CASH DEPOSIT BRANCH 30-11-22", 1200.00, C.CASH_DEPOSIT, "v2:cash_paid_in"),
        ("COUNTER CREDIT", 380.00, C.CASH_DEPOSIT, "v2:cash_paid_in"),
        ("PAID IN AT HIGH ST BRANCH", 380.00, C.CASH_DEPOSIT, "v2:cash_paid_in"),
        ("CASH DEPOSIT MACHINE 0412", 250.00, C.CASH_DEPOSIT, "v2:cash_paid_in"),
        ("POST OFFICE CASH DEPOSIT", 500.00, C.CASH_DEPOSIT, "v2:cash_paid_in"),
        ("CASH DEP POST OFFICE 0412", 500.00, C.CASH_DEPOSIT, "v2:cash_paid_in"),
        ("POST OFFICE COUNTER CREDIT", 500.00, C.CASH_DEPOSIT, "v2:cash_paid_in"),
        ("PAYPOINT CASH", 90.00, C.CASH_DEPOSIT, "v2:cash_paid_in"),
        # a provider's name alone is not cash: Post Office Ltd and PayPoint also pay by
        # transfer, and a transfer is left for a person, never counted as cash takings
        ("BACS CREDIT POST OFFICE LTD", 500.00, C.OTHER, "v2:default"),
        ("POST OFFICE COUNTERS", 500.00, C.OTHER, "v2:default"),
        ("PAYPOINT PLC", 90.00, C.OTHER, "v2:default"),
        # cash wording a payer typed into an electronic payment's reference is not cash
        ("FPS CREDIT CASH DEPOSIT", 200.00, C.OTHER, "v2:default"),
        ("BACS CREDIT POST OFFICE CASH", 200.00, C.OTHER, "v2:default"),
        # a cheque is not cash; it stays unexplained for a person
        ("CHEQUE PAID IN AT BRANCH", 900.00, C.OTHER, "v2:default"),
        # protected wording wins over cash wording: never cash or trading receipts
        # cash wording with a director or lender name: never trading receipts, a judgement
        ("CASH DEPOSIT DIRECTOR", 3000.00, C.OTHER, "v2:conflict:cash_paid_in+director_funding"),
        ("CASH DEPOSIT IWOCA", 3000.00, C.OTHER, "v2:conflict:cash_paid_in+lender_drawdown"),
        ("HMRC CASH PAID IN", 3000.00, C.HMRC_REPAYMENT, "v2:hmrc_repayment"),
        # an electronic rail anywhere in the line, not only first, means it is not cash
        ("CREDIT FPS POST OFFICE CASH", 500.00, C.OTHER, "v2:default"),
        ("CASH DEPOSIT VIA FASTER PAYMENT", 500.00, C.OTHER, "v2:default"),
    ],
    "cash_withdrawal": [
        # cash out keeps the category an unmatched debit gets; the rule id marks it for
        # the round-trip indicator
        ("CASH WITHDRAWAL LLOYDS", -60.00, C.SUPPLIER, "v2:cash_withdrawal"),
        ("ATM LINK 1234", -100.00, C.SUPPLIER, "v2:cash_withdrawal"),
        ("POST OFFICE CASH", -50.00, C.SUPPLIER, "v2:cash_withdrawal"),
        ("CASHPOINT HIGH ST", -40.00, C.SUPPLIER, "v2:cash_withdrawal"),
        ("ATM REFUND", 40.00, C.REFUND_IN, "v2:refunds"),  # money in is never a withdrawal
    ],
    "hmrc": [
        ("HMRC VAT REPAYMENT", 1840.00, C.HMRC_REPAYMENT, "v2:hmrc_repayment"),
        ("BACS CREDIT HMRC VAT REPAYMENT", 1840.00, C.HMRC_REPAYMENT, "v2:hmrc_repayment"),
        ("HM REVENUE & CUSTOMS", 220.00, C.HMRC_REPAYMENT, "v2:hmrc_repayment"),
        ("HMRC VAT", -2400.00, C.HMRC_VAT, "v2:hmrc_vat"),
        ("HMRC PAYE", -900.00, C.HMRC_PAYE, "v2:hmrc_paye"),
        ("HMRC INV 1234", 500.00, C.HMRC_REPAYMENT, "v2:hmrc_repayment"),  # never a customer
    ],
    "refunds": [
        ("CARD REFUND TESCO", 12.00, C.REFUND_IN, "v2:refunds"),
        ("DD REFUND COUNCIL", 140.00, C.REFUND_IN, "v2:refunds"),
        ("PAYPAL REFUND", 40.00, C.REFUND_IN, "v2:refunds"),  # a refund, not gateway takings
        ("AMAZON REFUND", 25.00, C.REFUND_IN, "v2:refunds"),
        ("REVERSAL OF PAYMENT", 75.00, C.REFUND_IN, "v2:refunds"),
        ("RETURNED PAYMENT ACME", 75.00, C.REFUND_IN, "v2:refunds"),
        # explicit refund wording beats generic finance or loan wording: no debt was drawn
        ("CARD REFUND ACME RETAIL FINANCE", 75.00, C.REFUND_IN, "v2:refunds"),
        ("LOAN REPAYMENT REVERSAL", 450.00, C.REFUND_IN, "v2:refunds"),
        # a named lender giving money back is a refund, not new debt; still protected (R21)
        ("IWOCA REFUND", 50.00, C.REFUND_IN, "v2:refunds"),
        ("DD REPAYMENT REVERSAL IWOCA LTD", 450.00, C.REFUND_IN, "v2:refunds"),
        ("BOOST CAPITAL REFUND", 60.00, C.REFUND_IN, "v2:refunds"),  # not director capital
        # explicit refund wording beats director or capital wording in the merchant's name
        ("CARD REFUND CAPITAL OFFICE SUPPLIES", 75.00, C.REFUND_IN, "v2:refunds"),
        ("REFUND DIRECTOR TRAINING LTD", 75.00, C.REFUND_IN, "v2:refunds"),
        ("IWOCA REFUND", -50.00, C.LOAN_REPAYMENT, "v2:lender_repayment"),  # money out stays debt service
        ("UNPAID DD BRITISH GAS", 75.00, C.RETURNED_DD, "v2:returned_dd"),
    ],
    "explained_non_trading": [
        ("INTEREST PAID", 3.10, C.OTHER_EXPLAINED_IN, "v2:explained_non_trading"),
        ("COUNCIL GRANT", 2500.00, C.OTHER_EXPLAINED_IN, "v2:explained_non_trading"),
        ("AVIVA INSURANCE CLAIM", 1800.00, C.OTHER_EXPLAINED_IN, "v2:explained_non_trading"),
        ("CASHBACK", 5.00, C.OTHER_EXPLAINED_IN, "v2:explained_non_trading"),
        ("COUNCIL GRANT INV 12", 2500.00, C.OTHER_EXPLAINED_IN, "v2:explained_non_trading"),  # not a customer
    ],
    "director_and_shareholder": [
        ("FPS CREDIT DIRECTOR CAPITAL INTRODUCED", 5000.00, C.DIRECTOR_FUNDING, "v2:director_funding"),
        ("FPS CREDIT A DIRECTOR LOAN ACCOUNT", 5000.00, C.DIRECTOR_FUNDING, "v2:director_funding"),
        ("J SMITH SHAREHOLDER INV 1234", 900.00, C.DIRECTOR_FUNDING, "v2:director_funding"),
        ("CAPITAL INJECTION", 900.00, C.DIRECTOR_FUNDING, "v2:director_funding"),
        # an explicit move from the applicant's own pot is not director money, whatever the pot is called
        ("TFR FROM POT CAPITAL RESERVE", 300.00, C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"),
        # savings wording alone is not a pot move: a director's savings paid in is director funding
        ("FPS CREDIT DIRECTOR SAVINGS", 300.00, C.DIRECTOR_FUNDING, "v2:director_funding"),
        ("DIRECTOR DRAWINGS", -900.00, C.OWNER_DRAWINGS, "v2:owner_drawings"),
    ],
    "defaults_and_invoices": [
        ("FPS CREDIT ACME LTD INV1234", 1200.00, C.REVENUE_TRANSFER, "v2:invoice_transfer"),
        ("FPS CREDIT ACME LTD", 1200.00, C.OTHER, "v2:default"),
        ("CARD TESCO", -45.00, C.SUPPLIER, "v2:default"),
        ("PAYROLL", -3000.00, C.PAYROLL, "v2:payroll"),
        ("RENT", -1500.00, C.RENT, "v2:rent"),
        ("BANK CHARGES", -15.00, C.BANK_FEES, "v2:bank_fees"),
    ],
}

CASES = [(name, *case) for name, cases in LISTS.items() for case in cases]


@pytest.mark.parametrize("name, desc, amount, category, rule_id", CASES,
                         ids=[f"{c[0]}:{c[1]}:{'in' if c[2] > 0 else 'out'}" for c in CASES])
def test_each_list_categorises_its_lines_and_records_the_rule(v2, name, desc, amount, category, rule_id):
    s = categorise.categorise(Statement(account_name="a", transactions=[_t(desc, amount)]), v2)
    line = s.transactions[0]
    assert (line.category, line.rule_id) == (category, rule_id)
    assert classify(_t(desc, amount), v2) == (category, rule_id)
    assert categorise.categorise_one(_t(desc, amount), v2) == category


def test_every_list_in_the_task_has_a_rule_in_v2(v2):
    ids = {r.id for r in v2.rules}
    assert {"card_acquirers", "payment_gateways", "marketplaces", "delivery_platforms", "lender_drawdown",
            "lender_repayment", "own_pots_savings", "cash_paid_in", "cash_withdrawal", "hmrc_repayment",
            "refunds", "explained_non_trading"} <= ids
    # each list rule is exercised above, in the direction it applies to
    exercised = {rid.split(":", 1)[1] for *_, rid in CASES}
    assert ids <= exercised, ids - exercised


def test_every_credit_category_v2_can_give_has_a_bucket(v2):
    """A credit landing in a category with no bucket would be a reconciliation failure (R3)."""
    for r in v2.rules:
        if r.direction in ("in", "any"):
            assert r.category in CREDIT_BUCKET, r.id
    assert CREDIT_BUCKET[C.CASH_DEPOSIT] is Bucket.TRADING_RECEIPTS


def test_ambiguous_wording_never_resolves_to_trading_receipts(v2):
    """A refund, pot, grant or lender word with an acquirer name or an invoice reference is
    not trading receipts: the rules that need judgement come first."""
    trading = {C.REVENUE_CARD, C.REVENUE_TRANSFER}
    for desc in ["STRIPE REFUND", "PAYPAL REVERSAL", "SUMUP INTEREST", "DELIVEROO GRANT", "EBAY TO SAVINGS",
                 "STRIPE CAPITAL INV 1234", "AMAZON LENDING", "ACME LTD REFUND INV 99", "HMRC STRIPE"]:
        assert classify(_t(desc, 100.0), v2)[0] not in trading, desc


def _list(rule_id: str) -> list[str]:
    data = yaml.safe_load(resources.files("cma.rules").joinpath("v2.yaml").read_bytes())
    return next(r["names"] for r in data["rules"] if r["id"] == rule_id)


@pytest.mark.contracts
def test_overlaps_from_the_review_hold_for_every_listed_word(v2):
    """The three review findings, generalised from one example to every word in the list:
    an explicit pot move beats what the pot is for; explicit refund wording beats generic
    loan or finance wording; a Post Office or PayPoint name without cash wording, or any
    cash wording on an electronic payment, is never cash takings."""
    for pot in _list("own_pots_savings"):
        for purpose in ("RENT", "PAYROLL", "BANK CHARGES"):
            assert classify(_t(f"TFR {pot} {purpose}", -100.0), v2) == \
                (C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"), (pot, purpose)
    for refund in _list("refunds"):
        for generic in ("ACME RETAIL FINANCE", "LOAN"):
            assert classify(_t(f"{refund} {generic}", 75.0), v2) == (C.REFUND_IN, "v2:refunds"), (refund, generic)
    for provider in ("POST OFFICE LTD", "POST OFFICE COUNTERS", "POST OFFICE", "PAYPOINT PLC", "PAYPOINT"):
        for rail in ("", "BACS CREDIT ", "FPS CREDIT "):
            assert classify(_t(f"{rail}{provider}", 500.0), v2)[0] is C.OTHER, (rail, provider)
    for cash in _list("cash_paid_in"):
        # FPI, FP and BGC are rail codes UK banks print (BGC is in categorise.RAIL_PREFIX too)
        for rail in ("FPS", "FPI", "FP", "BGC", "FASTER PAYMENT", "FASTER PAYMENTS", "BACS", "CHAPS", "TFR",
                     "TRANSFER", "SO", "STO", "STANDING ORDER", "DIRECT CREDIT"):
            # wherever the rail sits in the line
            for desc in (f"{rail} {cash}", f"CREDIT {rail} {cash}", f"{cash} {rail}", f"{cash} BY {rail} REF 12"):
                assert classify(_t(desc, 200.0), v2)[0] is not C.CASH_DEPOSIT, desc
        assert classify(_t(f"{cash} HIGH ST", 200.0), v2) == (C.CASH_DEPOSIT, "v2:cash_paid_in"), cash


@pytest.mark.contracts
def test_a_named_lender_refund_is_a_protected_refund_for_every_lender_and_refund_word(v2):
    """A named lender's explicit refund or reversal is money given back, not a new
    drawdown, and stays protected: a profile name or firm rule cannot reclassify it (R21).
    Money out to a lender is debt service whatever the wording."""
    firm = [_firm("f", ".", "any", C.REVENUE_TRANSFER)]
    profile = ProfileNames(directors=("IWOCA",), own_accounts=("IWOCA",))
    for lender in _list("lender_drawdown"):
        for refund in _list("refunds"):
            for desc in (f"{refund} {lender}", f"{lender} {refund}", f"DD REPAYMENT {refund} {lender} LTD"):
                assert classify(_t(desc, 75.0), v2, profile=profile, firm_rules=firm) == \
                    (C.REFUND_IN, "v2:refunds"), desc
                assert classify(_t(desc, -75.0), v2) == (C.LOAN_REPAYMENT, "v2:lender_repayment"), desc
        assert classify(_t(lender, 75.0), v2) == (C.LOAN_DRAWDOWN, "v2:lender_drawdown"), lender


POT_MOVES = ("POT TRANSFER", "TO POT", "FROM POT", "TO SPACE", "FROM SPACE", "VAT POT", "TAX POT")


@pytest.mark.contracts
def test_an_explicit_pot_move_beats_protected_wording_in_the_pot_name(v2):
    """A move to or from the applicant's own pot or space is an own-account transfer in
    either direction, even when the pot's name is protected wording (CAPITAL, DIRECTOR,
    HMRC, a lender, LOAN)."""
    protected_words = ["CAPITAL", "DIRECTOR", "DIRECTORS", "OWNER", "SHAREHOLDER", "HMRC", "HMRC VAT",
                       "HMRC PAYE", "HM REVENUE", "LOAN", "FINANCE", "DIVIDEND", "OWNER DRAWINGS",
                       *_list("lender_drawdown")]
    for pot in POT_MOVES:
        for word in protected_words:
            for amount in (300.0, -300.0):
                desc = f"TFR {pot} {word} RESERVE"
                assert classify(_t(desc, amount), v2) == (C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"), \
                    (desc, amount)
    # only a returned direct debit is tried before an explicit pot move
    assert [r.id for r in v2.rules].index("own_pots_savings") == 1


RAILS = ("FPS", "FPI", "FP", "BGC", "FASTER PAYMENT", "FASTER PAYMENTS", "BACS", "CHAPS", "TFR", "TRANSFER",
         "SO", "STO", "STANDING ORDER", "DIRECT CREDIT")


def _name_lists() -> dict[str, tuple[list[str], float]]:
    """Every name list the second stage reads, with the direction it applies to."""
    return {
        "card_acquirers": (_list("card_acquirers"), 100.0),
        "payment_gateways": (_list("payment_gateways"), 100.0),
        "marketplaces": (_list("marketplaces"), 100.0),
        "delivery_platforms": (_list("delivery_platforms"), 100.0),
        "lenders": (_list("lender_drawdown"), 100.0),
        "director_or_capital": (_list("director_funding"), 100.0),
        "cash_venues": (_list("cash_paid_in"), 100.0),
        "rent": (["RENT"], -100.0),
    }


@pytest.mark.contracts
def test_line_type_cue_beats_every_name_list_in_a_full_matrix(v2):
    """Two stages: line-type wording first (a refund or reversal; a move to or from the
    applicant's own pot or savings; a payment rail anywhere in the line), then the name
    lists. Every cue is crossed with every name on every list, the cue before and after
    the name, and the cue wins. A refund or a pot move is protected, so a profile name or a
    firm rule for the same name does not take it either. A rail has no category of its
    own: it rules out cash, and otherwise the name list decides."""
    firm = [_firm("f", ".", "any", C.REVENUE_TRANSFER)]
    for list_id, (names, amount) in _name_lists().items():
        for name in names:
            alone = classify(_t(name, amount), v2)
            assert alone[0] is not C.OTHER, (list_id, name)  # the name alone is recognised
            profile = ProfileNames(directors=(name,), own_accounts=(name,))
            for refund in _list("refunds"):
                for desc in (f"{refund} {name}", f"{name} {refund}", f"CARD {refund} {name} LTD"):
                    assert classify(_t(desc, 75.0), v2, profile=profile, firm_rules=firm) == \
                        (C.REFUND_IN, "v2:refunds"), (list_id, desc)
            for pot in _list("own_pots_savings"):
                for desc in (f"TFR {pot} {name}", f"{name} {pot}"):
                    for amt in (300.0, -300.0):
                        assert classify(_t(desc, amt), v2, profile=profile, firm_rules=firm) == \
                            (C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"), (list_id, desc, amt)
            for rail in RAILS:
                for desc in (f"{rail} {name}", f"{name} {rail} REF 12"):
                    got = classify(_t(desc, amount), v2)
                    if list_id == "cash_venues":
                        assert got == (C.OTHER, "v2:default"), desc
                    else:
                        assert got == alone, (desc, got, alone)


@pytest.mark.contracts
def test_returned_dd_cue_beats_every_name_on_every_list_either_direction(v2):
    """The first line-type cue, a returned direct debit, crossed with every name the
    table lists (and the same name as a profile director and own account): it is always
    a returned DD, in either direction."""
    data = yaml.safe_load(resources.files("cma.rules").joinpath("v2.yaml").read_bytes())
    names = {n for r in data["rules"] if "names" in r for n in r["names"]}
    assert len(names) > 100
    for name in sorted(names):
        profile = ProfileNames(directors=(name,), own_accounts=(name,))
        for amount in (75.0, -75.0):
            assert classify(_t(f"UNPAID DD {name}", amount), v2, profile=profile) == \
                (C.RETURNED_DD, "v2:returned_dd"), (name, amount)


# --- precedence ----------------------------------------------------------------------


def _firm(rid: str, pattern: str, direction: str, category: Category) -> Rule:
    return Rule(rid, re.compile(pattern), direction, category)


PROFILE = ProfileNames(directors=("Jane Doe",), own_accounts=("ACME HOLDINGS",))


@pytest.mark.parametrize("desc, amount, kw, expected", [
    # 1. an override beats everything, protected rules included
    ("HMRC VAT REPAYMENT", 100.0, {"override": LineOverride(C.REVENUE_TRANSFER, "override:3")},
     (C.REVENUE_TRANSFER, "override:3")),
    # 2. a protected rule beats the profile and firm rules (R21)
    ("IWOCA ACME HOLDINGS", 100.0, {"profile": PROFILE}, (C.LOAN_DRAWDOWN, "v2:lender_drawdown")),
    ("HMRC", 100.0, {"firm_rules": [_firm("f1", "HMRC", "in", C.REVENUE_TRANSFER)]},
     (C.HMRC_REPAYMENT, "v2:hmrc_repayment")),
    ("FPS CREDIT DIRECTOR", 100.0, {"firm_rules": [_firm("f1", "DIRECTOR", "in", C.REVENUE_TRANSFER)]},
     (C.DIRECTOR_FUNDING, "v2:director_funding")),
    ("UNPAID DD ACME", 100.0, {"firm_rules": [_firm("f1", "ACME", "any", C.REVENUE_TRANSFER)]},
     (C.RETURNED_DD, "v2:returned_dd")),
    # 3. a profile director beats firm rules and an invoice reference (R5)
    ("FPS CREDIT JANE DOE INV 1234", 100.0,
     {"profile": PROFILE, "firm_rules": [_firm("f1", "JANE", "in", C.REVENUE_TRANSFER)]},
     (C.DIRECTOR_FUNDING, "profile:director")),
    # a director name on a debit is not funding; the table decides
    ("FPS DEBIT JANE DOE", -100.0, {"profile": PROFILE}, (C.SUPPLIER, "v2:default")),
    # own accounts, either direction (R6)
    ("TFR ACME HOLDINGS", -100.0, {"profile": PROFILE}, (C.OWN_ACCOUNT_TRANSFER, "profile:own_account")),
    ("TFR ACME HOLDINGS", 100.0, {"profile": PROFILE}, (C.OWN_ACCOUNT_TRANSFER, "profile:own_account")),
    # a name matches whole words only
    ("FPS CREDIT JANE DOERING INV 1", 100.0, {"profile": PROFILE}, (C.REVENUE_TRANSFER, "v2:invoice_transfer")),
    # 4. a firm rule beats the rest of the table
    ("ACME TRUST INV 99", 100.0, {"firm_rules": [_firm("trust", "ACME TRUST", "in", C.OTHER_EXPLAINED_IN)]},
     (C.OTHER_EXPLAINED_IN, "firm:trust")),
    ("STRIPE PAYOUT", 100.0, {"firm_rules": [_firm("s", "STRIPE", "out", C.SUPPLIER)]},
     (C.REVENUE_CARD, "v2:payment_gateways")),  # a firm rule for the other direction does not apply
    # 5. the default
    ("SOMETHING ELSE", 100.0, {"profile": PROFILE}, (C.OTHER, "v2:default")),
    ("SOMETHING ELSE", -100.0, {}, (C.SUPPLIER, "v2:default")),
])
def test_precedence(v2, desc, amount, kw, expected):
    assert classify(_t(desc, amount), v2, **kw) == expected


def test_categorise_applies_overrides_by_line_and_records_each_rule(v2):
    s = Statement(account_name="a", transactions=[
        _t("CASH PAID IN", 900.0, "T1"), _t("CASH PAID IN", 50.0, "T2"), _t("STRIPE PAYOUT", 10.0, "T3")])
    categorise.categorise(s, v2, overrides={"T1": LineOverride(C.DIRECTOR_FUNDING, "override:1")})
    assert [(t.category, t.rule_id) for t in s.transactions] == [
        (C.DIRECTOR_FUNDING, "override:1"), (C.CASH_DEPOSIT, "v2:cash_paid_in"),
        (C.REVENUE_CARD, "v2:payment_gateways")]


def test_round_trip_records_its_pass_and_leaves_higher_precedence_alone(v2):
    lines = [_t("FPS DEBIT ACME LTD", -500.0, "T1"), _t("FPS CREDIT ACME LTD INV 7", 500.0, "T2"),
             _t("FPS DEBIT BETA LTD", -10.0, "T3"), _t("FPS CREDIT BETA LTD INV 8", 10.0, "T4")]
    s = categorise.categorise(Statement(account_name="a", transactions=lines), v2,
                              overrides={"T4": LineOverride(C.REVENUE_TRANSFER, "override:9")})
    by = s.by_id()
    assert (by["T2"].category, by["T2"].rule_id) == (C.OTHER, "v2:round_trip")
    assert (by["T4"].category, by["T4"].rule_id) == (C.REVENUE_TRANSFER, "override:9")


def test_returned_dd_links_under_v2_and_keeps_its_rule(v2):
    s = categorise.categorise(Statement(account_name="a", transactions=[
        _t("DD IWOCA LTD", -450.0, "T1"), _t("UNPAID DD IWOCA LTD", 450.0, "T2")]), v2)
    assert s.transactions[1].reverses == "T1" and s.transactions[1].rule_id == "v2:returned_dd"
    assert s.transactions[0].category == C.LOAN_REPAYMENT


def test_v1_lines_record_v1_rule_ids():
    v1 = load_ruleset("v1")
    s = categorise.categorise(Statement(account_name="a", transactions=[
        _t("CARD ACQUIRER SETTLEMENT", 10.0, "T1"), _t("STRIPE PAYOUT", 10.0, "T2"), _t("CASH PAID IN", 9.0, "T3")]), v1)
    assert [(t.category, t.rule_id) for t in s.transactions] == [
        (C.REVENUE_CARD, "v1:card_settlement"), (C.OTHER, "v1:default"), (C.OTHER, "v1:default")]


# --- the table as data -----------------------------------------------------------------


@pytest.mark.contracts
def test_v2_is_packaged_with_an_id_and_the_hash_of_its_file(v2):
    raw = resources.files("cma.rules").joinpath("v2.yaml").read_bytes()
    assert v2.id == "v2" and v2.sha256 == hashlib.sha256(raw).hexdigest()
    data = yaml.safe_load(raw)
    assert data["frozen"] is False
    # the protected rules are the ones the design names, and they come first
    flags = [r.protected for r in v2.rules]
    assert flags == sorted(flags, reverse=True)
    # line type first, then the protected name lists
    assert [r.id for r in v2.protected] == ["returned_dd", "own_pots_savings", "hmrc_vat", "hmrc_paye",
                                            "hmrc_repayment", "refunds", "explained_non_trading", "cash_paid_in",
                                            "lender_repayment", "lender_drawdown", "loan_repayment",
                                            "director_funding", "loan_drawdown", "owner_drawings"]


GOOD = {"id": "card", "names": ["STRIPE"], "direction": "in", "category": "revenue_card_settlement"}


@pytest.mark.contracts
@pytest.mark.parametrize("rules, match", [
    ([{k: v for k, v in GOOD.items() if k != "names"}], "missing pattern"),
    ([{**GOOD, "names": []}], "names must be"),
    ([{**GOOD, "names": "STRIPE"}], "names must be"),
    ([{**GOOD, "names": ["STRIPE", " "]}], "names must be"),
    ([{**GOOD, "names": ["STRIPE", "stripe"]}], "listed twice"),
    ([{**GOOD, "unless": "("}], "unless does not compile"),
    ([{**GOOD, "unless": 3}], "unless 3 is not"),
    ([{**GOOD, "requires": "("}], "requires does not compile"),
    ([{**GOOD, "requires": ["REFUND"]}], "requires \\['REFUND'\\] is not"),
    ([{**GOOD, "protected": "yes"}], "protected must be"),
    ([GOOD, {**GOOD, "id": "p", "protected": True}], "protected rule must come before"),
    # each part compiles alone, but a global flag is not allowed once the parts are joined
    ([{**GOOD, "pattern": "(?i)stripe"}], "pattern and names do not compile together"),
])
def test_a_malformed_v2_rule_is_refused_naming_the_problem(rules, match):
    with pytest.raises(RulesetError, match=match):
        validate_ruleset({"id": "v2", "frozen": False, "rules": rules}, expected_id="v2", source="v2.yaml")


def test_names_match_whole_words_with_any_spacing():
    p = re.compile(categorise.names_pattern(["CHECKOUT.COM", "JUST EAT"]))
    assert p.search("PAYOUT CHECKOUT.COM LTD") and p.search("JUST   EAT")
    assert not p.search("CHECKOUTXCOM") and not p.search("JUSTEATS") and not p.search("XJUST EAT")


@pytest.mark.contracts
def test_cma_ruleset_v2_chooses_the_v2_table(monkeypatch):
    monkeypatch.setenv("CMA_RULESET", "v2")
    assert categorise.active_ruleset().id == "v2"


# --- cma run under CMA_RULESET=v2 ----------------------------------------------------------


def _write(path: Path, rows: list[tuple[str, float]]) -> Path:
    bal = 1000.0
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "date", "description", "amount", "balance"])
        for i, (desc, amount) in enumerate(rows, start=1):
            bal = round(bal + amount, 2)
            w.writerow([f"T{i:03d}", (date(2026, 1, 1) + timedelta(days=5 * i)).isoformat(), desc,
                        f"{amount:.2f}", f"{bal:.2f}"])
    return path


@pytest.mark.contracts
def test_cma_run_under_v2_categorises_with_v2_and_records_each_lines_rule(tmp_path, monkeypatch):
    from cma.cli import app

    monkeypatch.setenv("CMA_RULESET", "v2")
    rows = [("STRIPE PAYMENTS UK LTD", 2000.0), ("CASH PAID IN", 640.0), ("IWOCA LTD", 5000.0),
            ("DD IWOCA LTD", -450.0), ("POT TRANSFER OUT TAX", -300.0), ("CARD TESCO", -45.0)] * 6
    path = _write(tmp_path / "s.csv", rows)
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", str(path), "--yes", "--reviewer", "A", "--out", str(out)])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    assert record["ruleset"] == {"id": "v2", "sha256": load_ruleset("v2").sha256}
    lines = {d["id"]: d for d in record["lines"]}
    s = ingest.load_csv(path)
    assert len(lines) == len(s.transactions)
    expect = {"STRIPE PAYMENTS UK LTD": ("revenue_card_settlement", "v2:payment_gateways"),
              "CASH PAID IN": ("cash_deposit", "v2:cash_paid_in"),
              "IWOCA LTD": ("loan_drawdown", "v2:lender_drawdown"),
              "DD IWOCA LTD": ("loan_repayment", "v2:lender_repayment"),
              "POT TRANSFER OUT TAX": ("own_account_transfer", "v2:own_pots_savings"),
              "CARD TESCO": ("supplier", "v2:default")}
    for t in s.transactions:
        assert (lines[t.id]["category"], lines[t.id]["rule_id"]) == expect[t.description]
    # the figures cma run reports use those categories: Stripe and cash are trading receipts
    m = {x["id"]: x for x in record["metrics"]}
    assert m["M2"]["value"] == pytest.approx(6 * (2000.0 + 640.0))
    assert m["M8"]["value"] == pytest.approx(6 * 450.0)


@pytest.mark.contracts
def test_cma_run_under_v2_keeps_a_post_office_transfer_out_of_trading_receipts(tmp_path, monkeypatch):
    """The review's repro: a BACS credit from Post Office Ltd is not cash paid in, so it is
    not in M2 (or M18's cash) and stays unexplained for a person."""
    from cma.cli import app

    monkeypatch.setenv("CMA_RULESET", "v2")
    rows = [("STRIPE PAYMENTS UK LTD", 2000.0), ("BACS CREDIT POST OFFICE LTD", 500.0), ("CARD TESCO", -45.0)] * 4
    path = _write(tmp_path / "s.csv", rows)
    r = CliRunner().invoke(app, ["run", str(path), "--yes", "--reviewer", "A", "--out", str(tmp_path / "m.md")])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "m.evidence.json").read_text())
    ids = {t.id for t in ingest.load_csv(path).transactions if t.description == "BACS CREDIT POST OFFICE LTD"}
    lines = {d["id"]: d for d in record["lines"]}
    assert {(lines[i]["category"], lines[i]["rule_id"]) for i in ids} == {("other", "v2:default")}
    m = {x["id"]: x for x in record["metrics"]}
    assert m["M2"]["value"] == pytest.approx(4 * 2000.0)
    assert not ids & set(m["M2"]["evidence"])


@pytest.mark.contracts
def test_cma_run_under_v2_records_the_second_review_repros_as_their_buckets(tmp_path, monkeypatch):
    """The second review's repros end to end: a named lender's repayment reversal is a
    refund, not a drawdown; a transfer from a pot called CAPITAL is an own-account
    transfer, not director funding; a Faster Payment that names Post Office cash is not
    cash and stays out of M2."""
    from cma.cli import app

    monkeypatch.setenv("CMA_RULESET", "v2")
    rows = [("STRIPE PAYMENTS UK LTD", 2000.0), ("DD REPAYMENT REVERSAL IWOCA LTD", 450.0),
            ("TFR FROM POT CAPITAL RESERVE", 300.0), ("CREDIT FPS POST OFFICE CASH", 500.0),
            ("CARD TESCO", -45.0)] * 4
    path = _write(tmp_path / "s.csv", rows)
    r = CliRunner().invoke(app, ["run", str(path), "--yes", "--reviewer", "A", "--out", str(tmp_path / "m.md")])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "m.evidence.json").read_text())
    lines = {d["id"]: d for d in record["lines"]}
    expect = {"STRIPE PAYMENTS UK LTD": ("revenue_card_settlement", "v2:payment_gateways"),
              "DD REPAYMENT REVERSAL IWOCA LTD": ("refund_in", "v2:refunds"),
              "TFR FROM POT CAPITAL RESERVE": ("own_account_transfer", "v2:own_pots_savings"),
              "CREDIT FPS POST OFFICE CASH": ("other", "v2:default"),
              "CARD TESCO": ("supplier", "v2:default")}
    txns = ingest.load_csv(path).transactions
    for t in txns:
        assert (lines[t.id]["category"], lines[t.id]["rule_id"]) == expect[t.description]
    m = {x["id"]: x for x in record["metrics"]}
    assert m["M2"]["value"] == pytest.approx(4 * 2000.0)
    assert set(m["M2"]["evidence"]) == {t.id for t in txns if t.description == "STRIPE PAYMENTS UK LTD"}


@pytest.mark.contracts
def test_cma_run_under_v2_records_the_third_review_repros_as_their_buckets(tmp_path, monkeypatch):
    """The third review's repros end to end: a transfer from the applicant's savings named
    CAPITAL is an own-account transfer, and a card refund from a merchant named CAPITAL is
    a refund; neither is director funding."""
    from cma.cli import app

    monkeypatch.setenv("CMA_RULESET", "v2")
    rows = [("STRIPE PAYMENTS UK LTD", 2000.0), ("TFR FROM SAVINGS CAPITAL RESERVE", 300.0),
            ("CARD REFUND CAPITAL OFFICE SUPPLIES", 75.0), ("CARD TESCO", -45.0)] * 4
    path = _write(tmp_path / "s.csv", rows)
    r = CliRunner().invoke(app, ["run", str(path), "--yes", "--reviewer", "A", "--out", str(tmp_path / "m.md")])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "m.evidence.json").read_text())
    lines = {d["id"]: d for d in record["lines"]}
    expect = {"STRIPE PAYMENTS UK LTD": ("revenue_card_settlement", "v2:payment_gateways"),
              "TFR FROM SAVINGS CAPITAL RESERVE": ("own_account_transfer", "v2:own_pots_savings"),
              "CARD REFUND CAPITAL OFFICE SUPPLIES": ("refund_in", "v2:refunds"),
              "CARD TESCO": ("supplier", "v2:default")}
    for t in ingest.load_csv(path).transactions:
        assert (lines[t.id]["category"], lines[t.id]["rule_id"]) == expect[t.description]
    assert not any(d["category"] == "director_funding" for d in record["lines"])


@pytest.mark.contracts
def test_cma_run_under_v1_records_v1_rule_ids_and_keeps_old_evidence_keys(tmp_path, monkeypatch):
    from cma.cli import app

    monkeypatch.setenv("CMA_RULESET", "v1")
    path = _write(tmp_path / "s.csv", [("CARD ACQUIRER SETTLEMENT", 100.0), ("STRIPE PAYOUT", 50.0)] * 3)
    r = CliRunner().invoke(app, ["run", str(path), "--yes", "--reviewer", "A", "--out", str(tmp_path / "m.md")])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "m.evidence.json").read_text())
    assert {d["rule_id"] for d in record["lines"]} == {"v1:card_settlement", "v1:default"}
    assert {"schema_version", "statement", "model", "ruleset", "metrics", "policy", "guard", "review"} <= set(record)


# --- R10 / P7 under v2: M1-M16 move only on the lines v2 newly recognises ---------------------

# Every line in the committed corpus (synthetic cases, the example, the test fixtures and
# the single lines in the P7 golden) whose category v2 changes. The list is the change R10
# asks for. On every committed statement M1-M16 stay exactly as v1 computed them: the lines
# v2 names here are non-trading either way (metrics.NON_TRADING_IN). The director loan is
# only a single line in the corpus, never part of a statement.
NEWLY_RECOGNISED = {
    ("BACS CREDIT HMRC VAT REPAYMENT", "in", "other", "hmrc_repayment"),
    ("FPS CREDIT A DIRECTOR LOAN ACCOUNT", "in", "loan_drawdown", "director_funding"),
    ("FPS CREDIT CAPITAL INJECTION", "in", "other", "director_funding"),
    ("FPS CREDIT DIRECTOR CAPITAL INJECTION", "in", "other", "director_funding"),
    ("FPS CREDIT DIRECTOR CAPITAL INTRODUCED", "in", "other", "director_funding"),
    ("FPS CREDIT J SMITH CAPITAL INTRODUCED", "in", "other", "director_funding"),
    ("FPS CREDIT MY SAVINGS POT", "in", "other", "own_account_transfer"),
    ("FPS CREDIT SAVINGS ACCOUNT", "in", "other", "own_account_transfer"),
    ("FPS DEBIT MY SAVINGS POT", "out", "supplier", "own_account_transfer"),
    ("HMRC VAT REPAYMENT", "in", "other", "hmrc_repayment"),
    ("POT TRANSFER OUT HOLIDAY POT", "out", "supplier", "own_account_transfer"),
}


def _golden() -> dict:
    return json.loads(gzip.decompress(GOLDEN.read_bytes()))


def _corpus_statements(tmp_path) -> list[tuple[str, Statement, object, list]]:
    """(label, statement, vat_stagger, expected v1 metric rows) for every golden case."""
    out = []
    g = _golden()
    for c in g["synthetic"]:
        rows, truth = synth.generate(c["archetype"], c["profile"], seed=c["seed"], months=c["months"],
                                     end=date.fromisoformat(c["end"]))
        csv_path, _ = synth.write(rows, truth, tmp_path, stem=f"s{len(out)}")
        for key, expected in c["metrics"].items():
            out.append((f"{c['archetype']}-{c['profile']}", ingest.load_csv(csv_path),
                        None if key == "None" else int(key), expected))
    for e in g["examples"]:
        for key, expected in e["metrics"].items():
            out.append((e["path"], ingest.load_csv(Path(__file__).parents[1] / e["path"]),
                        None if key == "None" else int(key), expected))
    for f in g["fixtures"]:
        s = Statement(account_name=f["account_name"], transactions=[
            Transaction(id=i, date=date.fromisoformat(d), description=desc, amount=a, balance=b)
            for i, d, desc, a, b, _, _ in f["lines"]])
        out.append((f"fixture-{f['lines'][0][2]}", s, f["vat_stagger"], f["metrics"]))
    return out


def _rows(s: Statement, vat_stagger) -> list[dict]:
    return [{"id": m.id, "unit": m.unit, "value": m.value, "evidence_n": len(m.evidence),
             "evidence_sha256": hashlib.sha256("\n".join(m.evidence).encode()).hexdigest()}
            for m in metrics.compute(s, vat_stagger=vat_stagger) if int(m.id[1:]) <= 16]


@pytest.mark.dual_run
@pytest.mark.invariants
def test_p7_v2_changes_only_the_listed_lines_and_m1_m16_only_where_listed(tmp_path):
    v1, v2 = load_ruleset("v1"), load_ruleset("v2")
    changed: set[tuple] = set()
    for label, s, stagger, expected in _corpus_statements(tmp_path):
        a = categorise.categorise(s.model_copy(deep=True), v1)
        b = categorise.categorise(s.model_copy(deep=True), v2)
        diff = {(x.description, "in" if x.amount > 0 else "out", x.category.value, y.category.value)
                for x, y in zip(a.transactions, b.transactions, strict=True) if x.category != y.category}
        changed |= diff
        assert _rows(a, stagger) == expected, f"{label}: v1 drifted from the baseline"
        assert _rows(b, stagger) == expected, f"{label}: M1-M16 moved under v2"
    for desc, amount, _ in _golden()["lines"]:
        x, y = classify(_t(desc, amount), v1)[0], classify(_t(desc, amount), v2)[0]
        if x != y:
            changed.add((desc, "in" if amount > 0 else "out", x.value, y.value))
    assert changed == NEWLY_RECOGNISED


# --- hardening: idempotency, rollback, no descriptions in evidence, size ---------------


def _mixed_lines(n: int) -> list[Transaction]:
    descs = ["STRIPE PAYOUT", "CASH PAID IN", "FPS CREDIT ACME LTD INV 12", "CARD TESCO", "DD IWOCA LTD",
             "FPS DEBIT ACME LTD", "UNPAID DD IWOCA LTD", "SOMETHING ELSE", "TFR ACME HOLDINGS", "FPS CREDIT JANE DOE"]
    return [_t(descs[i % len(descs)], (1 if i % 3 else -1) * (10.0 + i % 97), f"T{i}") for i in range(n)]


@pytest.mark.contracts
def test_categorise_is_idempotent_under_v2_with_every_source(v2):
    """Re-categorising a statement (as every recompute does) gives the same category, rule
    id and reversal link on every line: nothing from the previous pass leaks in."""
    s = Statement(account_name="a", transactions=_mixed_lines(200))
    kw = {"profile": PROFILE, "overrides": {"T5": LineOverride(C.OTHER_EXPLAINED_IN, "override:1")},
          "firm_rules": [_firm("f1", "SOMETHING ELSE", "in", C.OTHER_EXPLAINED_IN)]}
    categorise.categorise(s, v2, **kw)
    first = [(t.category, t.rule_id, t.reverses) for t in s.transactions]
    categorise.categorise(s, v2, **kw)
    assert [(t.category, t.rule_id, t.reverses) for t in s.transactions] == first
    assert all(t.rule_id for t in s.transactions)


@pytest.mark.dual_run
def test_rollback_to_v1_after_v2_restores_v1_categories_and_rule_ids(v2):
    """Switching CMA_RULESET back to v1 on a statement already categorised under v2 gives
    exactly what v1 gives on a fresh copy: the rollback needs no data repair."""
    v1 = load_ruleset("v1")
    lines = _mixed_lines(100)
    fresh = categorise.categorise(Statement(account_name="a", transactions=[t.model_copy() for t in lines]), v1)
    s = categorise.categorise(Statement(account_name="a", transactions=lines), v2)
    categorise.categorise(s, v1)
    assert [(t.category, t.rule_id, t.reverses) for t in s.transactions] == \
        [(t.category, t.rule_id, t.reverses) for t in fresh.transactions]


@pytest.mark.contracts
def test_evidence_lines_carry_ids_only_never_descriptions(tmp_path, monkeypatch):
    """Payer-written text stays in the statement: the per-line provenance in the evidence
    file has exactly id, category, rule_id and reverses."""
    from cma.cli import app

    monkeypatch.setenv("CMA_RULESET", "v2")
    path = _write(tmp_path / "s.csv", [("STRIPE PAYMENTS UK LTD", 2000.0), ("CASH PAID IN", 640.0),
                                       ("CARD TESCO", -45.0)] * 4)
    r = CliRunner().invoke(app, ["run", str(path), "--yes", "--reviewer", "A", "--out", str(tmp_path / "m.md")])
    assert r.exit_code == 0, r.output
    raw = (tmp_path / "m.evidence.json").read_text()
    record = json.loads(raw)
    assert all(set(d) == {"id", "category", "rule_id", "reverses"} for d in record["lines"])
    assert "lines" in record and "STRIPE PAYMENTS" not in json.dumps(record["lines"])


def test_categorise_10000_lines_under_v2_with_a_profile_is_fast(v2):
    """The design's recompute target is 2 seconds at 10,000 lines; categorising is part of
    it. This bounds the categoriser alone, not the whole recompute (figures, indicators,
    policy check), which belongs to the workspace's own test (R52)."""
    import time

    s = Statement(account_name="a", transactions=_mixed_lines(10_000))
    t0 = time.perf_counter()
    categorise.categorise(s, v2, profile=ProfileNames(directors=("JANE DOE", "JOHN SMITH"),
                                                      own_accounts=("ACME HOLDINGS",)))
    assert time.perf_counter() - t0 < 2.0


# --- stages: line-type cues, then name lists (T03 review verify-24) -----------------------------

@pytest.mark.invariants
@pytest.mark.parametrize("desc, amount, category, rule_id", [
    # verify-24: a counter cash deposit at a branch called CAPITAL is not director funding
    ("COUNTER DEPOSIT CAPITAL ST BRANCH", 500.00, C.OTHER, "v2:conflict:cash_paid_in+director_funding"),
    # verify-24: a bare pot name in the merchant on an explicit card refund is a refund
    ("CARD REFUND TAX POT STATIONERY", 25.00, C.REFUND_IN, "v2:refunds"),
    ("CARD REFUND VAT POT SUPPLIES", 25.00, C.REFUND_IN, "v2:refunds"),
    # verify-24: third-party compensation with SAVINGS in its wording is explained money in
    ("FPS CREDIT ACME INSURANCE COMPENSATION SAVINGS PLAN", 900.00, C.OTHER_EXPLAINED_IN, "v2:explained_non_trading"),
])
def test_p_review_repros_land_in_their_buckets(v2, desc, amount, category, rule_id):
    assert classify(_t(desc, amount), v2) == (category, rule_id)


@pytest.mark.invariants
def test_two_line_type_cues_in_different_buckets_are_a_judgement_unless_one_is_the_bank_code(v2):
    from cma.models import CREDIT_BUCKET
    cues = [r for r in v2.rules if r.stage == "line_type" and r.direction in ("in", "any")]
    samples = {"own_pots_savings": "FROM POT", "hmrc_repayment": "HMRC", "refunds": "REFUND",
               "explained_non_trading": "COMPENSATION", "returned_dd": "UNPAID DD"}
    checked = 0
    for a in cues:
        for b in cues:
            if a.id >= b.id or a.id not in samples or b.id not in samples:
                continue
            if CREDIT_BUCKET[a.category] == CREDIT_BUCKET[b.category]:
                continue
            # the bank code is anchored at the start of the line, so it is written first
            pair = sorted((a, b), key=lambda r: r.id != "returned_dd")
            desc = f"{samples[pair[0].id]} {samples[pair[1].id]}"
            got = classify(_t(desc, 100.0), v2)
            if "returned_dd" in (a.id, b.id):
                assert got == (C.RETURNED_DD, "v2:returned_dd"), desc
            elif {a.id, b.id} == {"hmrc_repayment", "refunds"}:
                assert got == (C.HMRC_REPAYMENT, "v2:hmrc_repayment"), desc   # an HMRC refund is a repayment
            elif {a.id, b.id} == {"hmrc_repayment", "own_pots_savings"}:
                assert got == (C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"), desc   # a pot named HMRC
            else:
                assert got[0] == C.OTHER and got[1].startswith("v2:conflict:"), (desc, got)
            checked += 1
    assert checked >= 6


@pytest.mark.invariants
def test_no_name_list_makes_a_credit_trading_receipts_when_another_says_otherwise(v2):
    from cma.models import CREDIT_BUCKET, Bucket
    non_trading = ["DIRECTOR", "CAPITAL", "SHAREHOLDER", "IWOCA", "LOAN"]
    trading = ["CASH DEPOSIT", "COUNTER DEPOSIT"]
    for tr in trading:
        for nt in non_trading:
            cat, rid = classify(_t(f"{tr} {nt}", 500.0), v2)
            assert CREDIT_BUCKET[cat] != Bucket.TRADING_RECEIPTS, (tr, nt, rid)


@pytest.mark.contracts
@pytest.mark.parametrize("mutate, message", [
    (lambda rules: rules[0].pop("stage"), "missing stage"),
    (lambda rules: rules[0].update(protected=False), "a line_type rule must be protected"),
    (lambda rules: rules.insert(0, rules.pop(next(i for i, r in enumerate(rules) if r["stage"] == "names"))),
     "a line_type rule must come before every names rule"),
    (lambda rules: rules[0].update(stage="other"), "is not one of"),
])
def test_a_v2_table_must_keep_its_stages(mutate, message):
    import copy
    from importlib import resources

    import yaml

    from cma.categorise import RulesetError, validate_ruleset
    data = yaml.safe_load(resources.files("cma.rules").joinpath("v2.yaml").read_text(encoding="utf-8"))
    data = copy.deepcopy(data)
    mutate(data["rules"])
    with pytest.raises(RulesetError, match=message):
        validate_ruleset(data, "v2")
