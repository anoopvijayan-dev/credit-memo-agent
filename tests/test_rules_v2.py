"""The v2 rule table (R4) and the categoriser's precedence with per-line provenance.

v2 is packaged data like v1 (src/cma/rules/v2.yaml) and adds the maintained lists of R4.
Every line records the rule that set its category. A credit gets design C1's verdict
(line-type cues, then name lists; two or more buckets are ambiguous) and the precedence:
a per-line override, a protected verdict, the case profile's names, an ambiguous verdict,
firm rules, an unprotected verdict, the default. Debits keep first-match order.
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
        # whole words in any script: an accented letter is a letter (verify-32)
        ("FPS CREDIT CAFÉWORLDPAY LTD", 50.00, C.OTHER, "v2:default"),
    ],
    "payment_gateways": [
        ("STRIPE PAYMENTS UK LTD", 2300.00, C.REVENUE_CARD, "v2:payment_gateways"),
        ("STRIPE PAYOUT", 2300.00, C.REVENUE_CARD, "v2:payment_gateways"),
        ("PAYPAL PPWDL 7HK2", 145.00, C.REVENUE_CARD, "v2:payment_gateways"),
        ("GOCARDLESS LTD", 600.00, C.REVENUE_CARD, "v2:payment_gateways"),
        ("OPAYO PAYOUT", 60.00, C.REVENUE_CARD, "v2:payment_gateways"),
        ("KLARNA BANK AB", 89.00, C.REVENUE_CARD, "v2:payment_gateways"),
        # a gateway's name with generic finance wording: names in two buckets, never trading
        ("CLEARPAY FINANCE LTD", 89.00, C.OTHER, "v2:conflict:loan_drawdown+payment_gateways"),
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
    "payment_rail": [
        # the rail has no category: the name list decides, and with nothing listed it is unexplained
        ("FPS CREDIT STRIPE PAYMENTS", 900.00, C.REVENUE_CARD, "v2:payment_gateways"),
        ("BACS CREDIT ACME LTD", 900.00, C.OTHER, "v2:default"),
        # bare pot labels are not evidence (D48): no move wording, no own-account transfer
        ("TAX POT", 400.00, C.OTHER, "v2:default"),
        ("VAT POT", -400.00, C.SUPPLIER, "v2:default"),
    ],
    "own_pots_savings": [
        ("POT TRANSFER IN HOLIDAY POT", 200.00, C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"),
        ("POT TRANSFER OUT HOLIDAY POT", -200.00, C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"),
        ("TFR TO SAVINGS", -1000.00, C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"),
        # savings wording with no move is tried after the director wording
        # a bare SAVINGS word is not a move: it stays for a person, unless the profile names the account
        ("FPS CREDIT SAVINGS ACCOUNT", 1000.00, C.OTHER, "v2:default"),
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
        # cash wording on an electronic payment: the rail says it is not cash, so ambiguous
        ("FPS CREDIT CASH DEPOSIT", 200.00, C.OTHER, "v2:conflict:payment_rail+cash_paid_in"),
        ("BACS CREDIT POST OFFICE CASH", 200.00, C.OTHER, "v2:conflict:payment_rail+cash_paid_in"),
        # a cheque is not cash: with cash paid-in wording the credit is ambiguous (D70)
        ("CHEQUE PAID IN AT BRANCH", 900.00, C.OTHER, "v2:conflict:cheque+cash_paid_in"),
        # T03 review 7: a cheque token used to hide explicit cash wording through an
        # exception on the cash list, so the rail + cash check never saw the cash
        ("FPS CASH DEPOSIT CHQ 1234", 900.00, C.OTHER, "v2:conflict:payment_rail+cheque+cash_paid_in"),
        ("CASH DEPOSIT CHEQUE", 900.00, C.OTHER, "v2:conflict:cheque+cash_paid_in"),
        # cash wording with a director or lender name: names in two buckets, a judgement
        ("CASH DEPOSIT DIRECTOR", 3000.00, C.OTHER, "v2:conflict:director_funding+cash_paid_in"),
        ("CASH DEPOSIT IWOCA", 3000.00, C.OTHER, "v2:conflict:lender_drawdown+cash_paid_in"),
        # a cue decides over names: the HMRC cue
        ("HMRC CASH PAID IN", 3000.00, C.HMRC_REPAYMENT, "v2:hmrc_repayment"),
        # a rail anywhere in the line, not only first, makes cash wording ambiguous
        ("CREDIT FPS POST OFFICE CASH", 500.00, C.OTHER, "v2:conflict:payment_rail+cash_paid_in"),
        ("CASH DEPOSIT VIA FASTER PAYMENT", 500.00, C.OTHER, "v2:conflict:payment_rail+cash_paid_in"),
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
        # a returned direct debit in words, not the bank's code: a returned payment
        ("RETURNED DD BRITISH GAS", 75.00, C.RETURNED_DD, "v2:returned_payment"),
        ("FPS CREDIT DIRECT DEBIT RETURNED ACME", 75.00, C.RETURNED_DD, "v2:returned_payment"),
        # returned-payment and refund wording agree on the bucket: the table's category
        # order (D61) makes it a returned payment, which M7 and the indicator count
        ("RETURNED DD REFUND", 75.00, C.RETURNED_DD, "v2:returned_payment"),
    ],
    "explained_non_trading": [
        ("INTEREST PAID", 3.10, C.OTHER_EXPLAINED_IN, "v2:explained_non_trading"),
        ("COUNCIL GRANT", 2500.00, C.OTHER_EXPLAINED_IN, "v2:explained_non_trading"),
        ("AVIVA INSURANCE CLAIM", 1800.00, C.OTHER_EXPLAINED_IN, "v2:explained_non_trading"),
        ("CASHBACK", 5.00, C.OTHER_EXPLAINED_IN, "v2:explained_non_trading"),
        # explained wording with an invoice reference: never a customer by order, a judgement
        ("COUNCIL GRANT INV 12", 2500.00, C.OTHER, "v2:conflict:explained_non_trading+invoice_transfer"),
    ],
    "director_and_shareholder": [
        ("FPS CREDIT DIRECTOR CAPITAL INTRODUCED", 5000.00, C.DIRECTOR_FUNDING, "v2:director_funding"),
        ("FPS CREDIT A DIRECTOR LOAN ACCOUNT", 5000.00, C.DIRECTOR_FUNDING, "v2:director_funding"),
        ("J SMITH SHAREHOLDER", 900.00, C.DIRECTOR_FUNDING, "v2:director_funding"),
        ("FPS CREDIT J SMITH DIRECTOR LOAN", 900.00, C.DIRECTOR_FUNDING, "v2:director_funding"),
        # shareholder wording with an invoice reference: names in two buckets (a profile
        # director with an invoice reference is director funding, R5)
        ("J SMITH SHAREHOLDER INV 1234", 900.00, C.OTHER, "v2:conflict:director_funding+invoice_transfer"),
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
    # (a rule recorded as a candidate of a conflict counts: the rail never sets a category)
    exercised = {i for *_, rid in CASES for i in rid.split(":", 1)[1].removeprefix("conflict:").split("+")}
    assert ids <= exercised, ids - exercised


def test_every_credit_category_v2_can_give_has_a_bucket(v2):
    """A credit landing in a category with no bucket would be a reconciliation failure (R3)."""
    for r in v2.rules:
        if r.direction in ("in", "any"):
            # only a line-type cue may have no category (the rail); it never sets one
            assert r.category in CREDIT_BUCKET or (r.category is None and r.stage == "line_type"), r.id
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


POT_MOVES = ("POT TRANSFER", "TO POT", "FROM POT", "TO SPACE", "FROM SPACE", "TO VAT POT", "FROM TAX POT")


@pytest.mark.contracts
def test_an_explicit_pot_move_beats_protected_wording_in_the_pot_name(v2):
    """A move to or from the applicant's own pot or space is an own-account transfer in
    either direction, even when the pot's name is protected wording (CAPITAL, DIRECTOR,
    HMRC, a lender, LOAN)."""
    protected_words = ["CAPITAL", "DIRECTOR", "DIRECTORS", "OWNER", "SHAREHOLDER", "LOAN", "FINANCE",
                       "DIVIDEND", "OWNER DRAWINGS", *_list("lender_drawdown")]
    for pot in POT_MOVES:
        for word in protected_words:
            for amount in (300.0, -300.0):
                desc = f"TFR {pot} {word} RESERVE"
                assert classify(_t(desc, amount), v2) == (C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"), \
                    (desc, amount)
        # HMRC is a cue of its own: on a debit the first match is the pot move; on a credit
        # the two cues disagree and it is a judgement (R4)
        for word in ("HMRC", "HMRC VAT", "HMRC PAYE", "HM REVENUE"):
            desc = f"TFR {pot} {word} RESERVE"
            assert classify(_t(desc, -300.0), v2) == (C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"), desc
            got = classify(_t(desc, 300.0), v2)
            # the candidates are every rule it matched in either stage: TFR is a payment rail
            assert got[0] is C.OTHER and set(got[1].split(":conflict:")[1].split("+")) == \
                {"own_pots_savings", "hmrc_repayment", "payment_rail"}, (desc, got)
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
                        rail = amt > 0 and "payment_rail" in {r.id for r in categorise.credit_matches(v2, desc, amt)}
                        if list_id == "cash_venues" and rail:
                            # a rail with cash wording is ambiguous whatever cue matches (rev 4),
                            # and every rule it matched is a candidate, the pot move included
                            got = classify(_t(desc, amt), v2, firm_rules=firm)
                            assert got[0] is C.OTHER and {"payment_rail", "cash_paid_in", "own_pots_savings"} <= \
                                set(got[1].split(":conflict:")[1].split("+")), (desc, got)
                            continue
                        assert classify(_t(desc, amt), v2, profile=profile, firm_rules=firm) == \
                            (C.OWN_ACCOUNT_TRANSFER, "v2:own_pots_savings"), (list_id, desc, amt)
            for rail in RAILS:
                for desc in (f"{rail} {name}", f"{name} {rail} REF 12"):
                    got = classify(_t(desc, amount), v2)
                    if list_id == "cash_venues":
                        # electronic money with cash wording: ambiguous, never cash
                        assert got[0] is C.OTHER and got[1].startswith("v2:conflict:payment_rail+"), desc
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
    # line type first, then the protected name lists; cash paid in is not protected (design rev 3)
    assert [r.id for r in v2.protected] == ["returned_dd", "own_pots_savings", "hmrc_vat", "hmrc_paye",
                                            "hmrc_repayment", "returned_payment", "refunds", "payment_rail",
                                            "cheque", "lender_repayment", "lender_drawdown", "loan_repayment",
                                            "director_funding", "loan_drawdown", "owner_drawings"]
    # no rule's exception hides another list's cue or name (D70: the cheque is a cue now)
    unless = {r["id"]: r["unless"] for r in data["rules"] if "unless" in r}
    assert unless == {}


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
              "CREDIT FPS POST OFFICE CASH": ("other", "v2:conflict:payment_rail+cash_paid_in"),
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
    # verify-24/25: a counter cash deposit at a branch called CAPITAL is cash: CAPITAL alone is
    # not director funding, only capital introduced, injected or contributed is
    ("COUNTER DEPOSIT CAPITAL ST BRANCH", 500.00, C.CASH_DEPOSIT, "v2:cash_paid_in"),
    ("CAPITAL INTRODUCED J SMITH", 5000.00, C.DIRECTOR_FUNDING, "v2:director_funding"),
    # verify-25: SAVINGS in an HMRC payment's reference does not stop HMRC recognition
    ("HMRC VAT REPAYMENT REF SAVINGS", 1200.00, C.HMRC_REPAYMENT, "v2:hmrc_repayment"),
    # verify-25: an explicit card refund wins over a generic explained word in the merchant
    ("CARD REFUND GRANT STATIONERY", 25.00, C.REFUND_IN, "v2:refunds"),
    ("REFUND INTEREST BOOKS LTD", 25.00, C.REFUND_IN, "v2:refunds"),
    # verify-25: SAVINGS in an outside payer's name is not the applicant moving money
    ("FPS CREDIT SAVINGS AND LOANS LTD", 900.00, C.OTHER, "v2:default"),
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
               "returned_dd": "UNPAID DD"}
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
                # HMRC REFUND is one longer phrase on the HMRC list, so it is a repayment
                assert got == (C.HMRC_REPAYMENT, "v2:hmrc_repayment"), desc
            else:
                assert got[0] == C.OTHER and got[1].startswith("v2:conflict:"), (desc, got)
            checked += 1
    assert checked >= 4


@pytest.mark.invariants
def test_no_name_list_makes_a_credit_trading_receipts_when_another_says_otherwise(v2):
    from cma.models import CREDIT_BUCKET, Bucket
    non_trading = ["DIRECTOR", "CAPITAL INTRODUCED", "SHAREHOLDER", "IWOCA", "LOAN"]
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


# --- revision 3 (D52): ambiguous wording is a verdict of its own --------------------------------

@pytest.mark.invariants
@pytest.mark.parametrize("desc, amount, category, rule_id", [
    # verify-27: an HMRC credit with explicit savings move wording: HMRC and own-account cues disagree
    # (revision 4: the candidates are every rule matched in either stage, so BACS's rail is one)
    ("BACS CREDIT HMRC VAT REPAYMENT REF TO SAVINGS", 1200.00, C.OTHER,
     "v2:conflict:own_pots_savings+hmrc_repayment+payment_rail"),
    # verify-27: counter cash at a branch whose name holds a generic explained word: names disagree
    ("COUNTER DEPOSIT GRANT ST BRANCH", 500.00, C.OTHER, "v2:conflict:cash_paid_in+explained_non_trading"),
    # verify-27: a bare pot label in an external payment is no move wording: not own-account
    ("FPS CREDIT ACME LTD REF TAX POT", 900.00, C.OTHER, "v2:default"),
    # verify-27: a card refund from a merchant named with savings-transfer wording: cues disagree
    ("CARD REFUND FROM SAVINGS AND LOANS LTD", 25.00, C.OTHER, "v2:conflict:own_pots_savings+refunds"),
])
def test_p_verify27_repros_take_the_r4_outcome(v2, desc, amount, category, rule_id):
    assert classify(_t(desc, amount), v2) == (category, rule_id)


@pytest.mark.contracts
def test_cma_run_under_v2_lists_the_verify27_repros_as_unexplained_with_their_candidates(tmp_path, monkeypatch):
    """End to end: each verify-27 repro is in unexplained money in (M16's evidence), never in
    M2, and its line records the conflict with every candidate rule (or the default)."""
    from cma.cli import app

    monkeypatch.setenv("CMA_RULESET", "v2")
    expect = {"STRIPE PAYMENTS UK LTD": ("revenue_card_settlement", "v2:payment_gateways"),
              "BACS CREDIT HMRC VAT REPAYMENT REF TO SAVINGS":
                  ("other", "v2:conflict:own_pots_savings+hmrc_repayment+payment_rail"),
              "COUNTER DEPOSIT GRANT ST BRANCH": ("other", "v2:conflict:cash_paid_in+explained_non_trading"),
              "FPS CREDIT ACME LTD REF TAX POT": ("other", "v2:default"),
              "CARD REFUND FROM SAVINGS AND LOANS LTD": ("other", "v2:conflict:own_pots_savings+refunds"),
              "CARD TESCO": ("supplier", "v2:default")}
    rows = [(d, a) for d, a in zip(expect, [2000.0, 1200.0, 500.0, 900.0, 25.0, -45.0], strict=True)] * 4
    path = _write(tmp_path / "s.csv", rows)
    r = CliRunner().invoke(app, ["run", str(path), "--yes", "--reviewer", "A", "--out", str(tmp_path / "m.md")])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "m.evidence.json").read_text())
    lines = {d["id"]: d for d in record["lines"]}
    txns = ingest.load_csv(path).transactions
    for t in txns:
        assert (lines[t.id]["category"], lines[t.id]["rule_id"]) == expect[t.description]
    m = {x["id"]: x for x in record["metrics"]}
    stripe = {t.id for t in txns if t.description == "STRIPE PAYMENTS UK LTD"}
    unclear = {t.id for t in txns if t.amount > 0} - stripe
    assert set(m["M2"]["evidence"]) == stripe
    assert unclear <= set(m["M16"]["evidence"])


@pytest.mark.invariants
@pytest.mark.parametrize("desc, amount, category, rule_id", [
    # a match inside a longer listed phrase counts only as the longer phrase
    ("STRIPE CAPITAL", 5000.00, C.LOAN_DRAWDOWN, "v2:lender_drawdown"),
    ("HMRC REFUND", 300.00, C.HMRC_REPAYMENT, "v2:hmrc_repayment"),
    ("HMRC VAT REFUND", 300.00, C.HMRC_REPAYMENT, "v2:hmrc_repayment"),
    ("FPS CREDIT J SMITH DIRECTOR LOAN", 2000.00, C.DIRECTOR_FUNDING, "v2:director_funding"),
    # agreeing cues outrank names that point elsewhere
    ("PAYPAL REFUND", 40.00, C.REFUND_IN, "v2:refunds"),
    ("HMRC CASH PAID IN", 300.00, C.HMRC_REPAYMENT, "v2:hmrc_repayment"),
    # the bank's returned-payment code settles the line alone
    ("UNPAID DD HMRC VAT", 900.00, C.RETURNED_DD, "v2:returned_dd"),
    # a rail has no bucket: the acquirer decides; with cash wording it is ambiguous
    ("FPS CREDIT STRIPE PAYMENTS", 900.00, C.REVENUE_CARD, "v2:payment_gateways"),
    ("FPS CREDIT CASH DEPOSIT", 200.00, C.OTHER, "v2:conflict:payment_rail+cash_paid_in"),
    # names in two buckets, no cue: ambiguous, never trading receipts
    ("CASH DEPOSIT DIRECTOR", 3000.00, C.OTHER, "v2:conflict:director_funding+cash_paid_in"),
    ("COUNTER DEPOSIT IWOCA", 3000.00, C.OTHER, "v2:conflict:lender_drawdown+cash_paid_in"),
    ("COUNTER DEPOSIT AVIVA INSURANCE CLAIM", 500.00, C.OTHER, "v2:conflict:cash_paid_in+explained_non_trading"),
    ("COUNCIL GRANT INV 12", 2500.00, C.OTHER, "v2:conflict:explained_non_trading+invoice_transfer"),
])
def test_p_verdict_follows_design_c1(v2, desc, amount, category, rule_id):
    assert classify(_t(desc, amount), v2) == (category, rule_id)


@pytest.mark.invariants
@pytest.mark.parametrize("desc", ["COUNTER DEPOSIT GRANT ST BRANCH", "CARD REFUND FROM SAVINGS AND LOANS LTD",
                                  "BACS CREDIT HMRC VAT REPAYMENT REF TO SAVINGS", "CASH DEPOSIT DIRECTOR"])
def test_p_only_an_override_settles_an_ambiguous_credit_never_a_firm_rule_or_a_profile_name(v2, desc):
    conflict = classify(_t(desc, 500.0), v2)
    assert conflict[0] is C.OTHER and ":conflict:" in conflict[1]
    firm = [_firm("f", ".", "in", C.REVENUE_TRANSFER)]
    assert classify(_t(desc, 500.0), v2, firm_rules=firm) == conflict
    assert classify(_t(desc, 500.0), v2, override=LineOverride(C.CASH_DEPOSIT, "override:1")) == \
        (C.CASH_DEPOSIT, "override:1")
    # D78: a profile name the wording matches is one more candidate, not a settlement
    profile = ProfileNames(own_accounts=(desc.split()[-1],))
    assert classify(_t(desc, 500.0), v2, profile=profile) == (C.OTHER, conflict[1] + "+profile:own_account")


@pytest.mark.invariants
def test_p_a_profile_director_outranks_cash_wording(v2):
    profile = ProfileNames(directors=("JANE DOE",))
    assert classify(_t("CASH PAID IN JANE DOE", 900.0), v2, profile=profile) == (C.DIRECTOR_FUNDING, "profile:director")
    assert classify(_t("CASH PAID IN JANE DOE", 900.0), v2) == (C.CASH_DEPOSIT, "v2:cash_paid_in")


def _credit_samples() -> list[str]:
    """Every credit description the tests in this module use, plus every listed name alone."""
    data = yaml.safe_load(resources.files("cma.rules").joinpath("v2.yaml").read_bytes())
    names = [n for r in data["rules"] if "names" in r and r["direction"] != "out" for n in r["names"]]
    descs = [c[1] for c in CASES if c[2] > 0] + names
    for a in ("TO SAVINGS", "REFUND", "HMRC", "FPS", "COUNTER DEPOSIT", "IWOCA", "CAPITAL INTRODUCED", "LOAN",
              "GRANT", "WORLDPAY", "STRIPE", "ETSY", "DELIVEROO", "INV 12"):
        for b in names:
            descs.append(f"{a} | {b}")
    return descs


def _verdict_key(category: Category, rule_id: str) -> tuple:
    """The verdict, which must not depend on list order: the category (and so the money-in
    bucket), and for an ambiguous credit the set of candidates. Among agreeing rules of one
    category the rule recorded is the first in table order (design C1 step 6), so the rule
    id is not compared."""
    if ":conflict:" in rule_id:
        return category, frozenset(rule_id.split(":conflict:", 1)[1].split("+"))
    return category, None


@pytest.mark.invariants
def test_p_shuffling_lists_within_a_stage_gives_the_same_verdict_on_every_credit(v2):
    """Design Rollout: the verdict on a credit does not depend on the order of the lists
    within a stage (D52), and neither does the category inside a bucket (D61: the table's
    declared order). Every stage of the table is shuffled with several seeds."""
    import random

    descs = _credit_samples()
    want = [_verdict_key(*classify(_t(d, 100.0), v2)) for d in descs]
    for seed in range(8):
        rng = random.Random(seed)
        cues = [r for r in v2.rules if r.stage == "line_type"]
        names = [r for r in v2.rules if r.stage == "names"]
        rng.shuffle(cues)
        rng.shuffle(names)
        shuffled = categorise.Ruleset(id="v2", sha256=v2.sha256, rules=tuple(cues + names),
                                      category_order=v2.category_order)
        got = [_verdict_key(*classify(_t(d, 100.0), shuffled)) for d in descs]
        assert got == want, [(d, a, b) for d, a, b in zip(descs, got, want, strict=True) if a != b][:5]


# D61, written out again here: inside a bucket, the category with more checks first.
CATEGORY_ORDER = [C.CASH_DEPOSIT, C.REVENUE_TRANSFER, C.REVENUE_CARD, C.RETURNED_DD, C.REFUND_IN]


def _expected_verdict(parts: list[list[Rule]]) -> tuple[Category, frozenset[str] | None]:
    """R4's ambiguity policy (design C1 revision 4), written out again from the rules each
    part matches alone."""
    rules = list({r.id: r for p in parts for r in p}.values())
    every = frozenset(r.id for r in rules)
    if any(r.id == "returned_dd" for r in rules):
        return C.RETURNED_DD, None
    cues = [r for r in rules if r.stage == "line_type" and r.category is not None]
    names = [r for r in rules if r.stage == "names"]
    rails = [r for r in rules if r.stage == "line_type" and r.category is None]
    if rails and any(r.category is C.CASH_DEPOSIT for r in names):
        return C.OTHER, every
    deciding = cues or names
    if not deciding:
        return C.OTHER, None
    if len({CREDIT_BUCKET[r.category] for r in deciding}) > 1:
        return C.OTHER, every
    cats = {r.category for r in deciding}
    return min(cats, key=lambda c: CATEGORY_ORDER.index(c) if c in CATEGORY_ORDER else -1), None


@pytest.mark.invariants
def test_p_pairwise_matrix_every_two_credit_lists_combine_by_the_policy_alone(v2):
    """Every listed credit name (and rail) crossed with one name from every other credit
    list: what the pair matches is exactly what each part matches alone (no exception in
    one list hides another list's wording), and the verdict is R4's policy on that union."""
    data = yaml.safe_load(resources.files("cma.rules").joinpath("v2.yaml").read_bytes())
    credit_lists = {r["id"]: r["names"] for r in data["rules"] if "names" in r and r["direction"] != "out"}
    samples = {rid: names[0] for rid, names in credit_lists.items()}
    samples.update({"card_settlement": "CARD SETTLEMENT", "invoice_transfer": "INV 12", "loan_drawdown": "LOAN",
                    "returned_dd": "UNPAID DD"})
    checked = 0
    for rid, names in [*credit_lists.items(), ("returned_dd", ["UNPAID DD"])]:
        for name in names:
            for other, sample in samples.items():
                if other == rid:
                    continue
                first, second = (sample, name) if other == "returned_dd" else (name, sample)
                # "|" is not a separator names skip (D71), so two wordings never fuse into
                # one listed phrase across the join; "/" would (HMRC / REFUND = HMRC REFUND)
                desc = f"{first} | {second}"
                a = categorise.credit_matches(v2, first, 100.0)
                b = categorise.credit_matches(v2, second, 100.0)
                assert {r.id for r in categorise.credit_matches(v2, desc, 100.0)} == {r.id for r in a + b}, desc
                got = classify(_t(desc, 100.0), v2)
                assert _verdict_key(*got) == _expected_verdict([a, b]), (desc, got)
                checked += 1
    assert checked > 1000


@pytest.mark.contracts
def test_only_a_line_type_cue_of_a_v2_table_may_have_no_category():
    rail = {"id": "rail", "names": ["FPS"], "direction": "in", "protected": True, "stage": "line_type"}
    name = {**GOOD, "stage": "names"}
    validate_ruleset({"id": "v2", "frozen": False, "rules": [rail, name]}, expected_id="v2")
    with pytest.raises(RulesetError, match="missing category"):
        validate_ruleset({"id": "v2", "frozen": False,
                          "rules": [rail, {k: v for k, v in name.items() if k != "category"}]}, expected_id="v2")
    with pytest.raises(RulesetError, match="missing category"):
        validate_ruleset({"id": "v2", "frozen": True, "rules": [rail]}, expected_id="v2")
    rs = categorise.ruleset_from_data({"id": "v2", "frozen": False, "rules": [rail, name]}, "v2", "t", "0")
    assert rs.rules[0].category is None
    # the cue alone never sets a category: a debit or a credit with only the rail is the default
    assert classify(_t("FPS ACME", 5.0), rs) == (C.OTHER, "v2:default")
    assert classify(_t("FPS ACME", -5.0), rs) == (C.SUPPLIER, "v2:default")


@pytest.mark.contracts
def test_only_the_banks_returned_payment_code_settles_a_credit_alone(v2):
    """The bank-code exception is an explicit flag (T03 review 12), allowed only on the
    returned-payment rule anchored at the start of the line; anchoring another cue in a
    table edit gains nothing (design C1)."""
    assert {r.id for r in v2.rules if r.bank_code} == {"returned_dd"}
    # payer wording cannot take the bank code's place: UNPAID DD later in the line is not anchored
    got = classify(_t("REFUND REF UNPAID DD", 50.0), v2)
    assert got == (C.REFUND_IN, "v2:refunds")


# --- revision 4 (D58, D60, D61): the fifth review's findings ----------------------------------

def _conflict(rule_id: str) -> list[str]:
    assert ":conflict:" in rule_id, rule_id
    return rule_id.split(":conflict:", 1)[1].split("+")


@pytest.mark.invariants
@pytest.mark.parametrize("desc, rule_id", [
    # verify-32 finding 1: a bucketed cue no longer returns before the rail-plus-cash check
    ("BACS CREDIT HMRC CASH PAID IN", "v2:conflict:hmrc_repayment+payment_rail+cash_paid_in"),
    ("FPS CREDIT REFUND CASH DEPOSIT", "v2:conflict:refunds+payment_rail+cash_paid_in"),
    ("FPS CREDIT FROM SAVINGS COUNTER CREDIT", "v2:conflict:own_pots_savings+payment_rail+cash_paid_in"),
    ("FPS CREDIT RETURNED DD CASH DEPOSIT", "v2:conflict:returned_payment+payment_rail+cash_paid_in"),
])
def test_p_r4_a_rail_with_cash_wording_is_ambiguous_whatever_cue_matches(v2, desc, rule_id):
    assert classify(_t(desc, 500.0), v2) == (C.OTHER, rule_id)


@pytest.mark.invariants
def test_p_r4_rail_plus_cash_beats_every_bucketed_cue_in_a_full_matrix(v2):
    """Every rail, crossed with every credit cue with a bucket (not the bank's own code) and every
    cash paid-in phrase: ambiguous, with the rail, the cue and the cash wording among the
    candidates, and never trading receipts."""
    data = yaml.safe_load(resources.files("cma.rules").joinpath("v2.yaml").read_bytes())
    cues = {r["id"]: r["names"] for r in data["rules"]
            if r["stage"] == "line_type" and "names" in r and "category" in r and r["direction"] != "out"}
    assert set(cues) == {"own_pots_savings", "hmrc_repayment", "returned_payment", "refunds"}
    checked = 0
    for rail in RAILS:
        for cue_names in cues.values():
            for cue in cue_names:
                for cash in _list("cash_paid_in"):
                    desc = f"{rail} {cue} {cash}"
                    matched = {r.id for r in categorise.credit_matches(v2, desc, 500.0)}
                    if "payment_rail" not in matched or "cash_paid_in" not in matched:
                        continue  # a rail word swallowed by a longer listed phrase (POT TRANSFER) is no rail
                    got = classify(_t(desc, 500.0), v2)
                    assert got[0] is C.OTHER, (desc, got)
                    assert set(_conflict(got[1])) == matched, (desc, got)
                    checked += 1
    assert checked > 1000


@pytest.mark.invariants
@pytest.mark.parametrize("desc, rule_id", [
    # verify-32 finding 2: the gateway and the rail, outranked by the cues, are still candidates
    ("FPS CREDIT HMRC TO SAVINGS STRIPE", "v2:conflict:own_pots_savings+hmrc_repayment+payment_rail+payment_gateways"),
    ("CARD REFUND FROM SAVINGS STRIPE", "v2:conflict:own_pots_savings+refunds+payment_gateways"),
    ("HMRC TO SAVINGS DIRECTOR INV 4", "v2:conflict:own_pots_savings+hmrc_repayment+director_funding+invoice_transfer"),
    # names decide, the rail is still recorded
    ("FPS CREDIT CLEARPAY FINANCE LTD", "v2:conflict:payment_rail+loan_drawdown+payment_gateways"),
    # a match inside a longer listed phrase still counts only as the longer phrase
    ("TO SAVINGS HMRC REFUND STRIPE CAPITAL", "v2:conflict:own_pots_savings+hmrc_repayment+lender_drawdown"),
])
def test_p_r4_candidates_are_every_rule_matched_in_either_stage(v2, desc, rule_id):
    assert classify(_t(desc, 500.0), v2) == (C.OTHER, rule_id)


@pytest.mark.invariants
def test_p_r4_every_ambiguous_credit_records_exactly_its_matches_in_table_order(v2):
    """Over every sample credit: an ambiguous credit's candidates are exactly the rules it
    matched (after the longer-phrase filter), from both stages, written in table order."""
    position = {r.id: i for i, r in enumerate(v2.rules)}
    seen = 0
    for d in _credit_samples():
        cat, rid = classify(_t(d, 100.0), v2)
        if ":conflict:" not in rid:
            continue
        got = _conflict(rid)
        assert cat is C.OTHER
        assert got == [r.id for r in categorise.credit_matches(v2, d, 100.0)], d
        assert got == sorted(got, key=position.__getitem__), d
        seen += 1
    assert seen > 100


@pytest.mark.invariants
@pytest.mark.parametrize("desc", [
    "FPS CREDIT CAFÉSTRIPE LTD",            # verify-32 finding 3
    "FPS CREDIT CAFÉSTRIPE LTD",      # the same, with the accent as a combining mark
    "FPS CREDIT STRIPEÉ LTD",
    "FPS CREDIT \u03b1\u03b2\u03b3STRIPE".upper(),  # Greek letters
    "FPS CREDIT STRIPEЖ",                   # Cyrillic
    "FPS CREDIT ٣STRIPE",                   # an Arabic-Indic digit
    "FPS CREDIT ÉWORLDPAY",
    "FPS CREDIT DELIVEROOÑ",
])
def test_p_r4_a_listed_name_inside_a_longer_word_in_any_script_is_not_that_name(v2, desc):
    assert classify(_t(desc, 500.0), v2) == (C.OTHER, "v2:default")


@pytest.mark.invariants
@pytest.mark.parametrize("word", ["ACQUIRER SETTLEMENT", "PAYGATE PAYOUT", "CARD SETTLEMENT"])
def test_p_r4_v1_acquirer_wording_is_whole_words_in_any_script_too(v2, word):
    """Hardener: the one trading rule written as a regex (v1's synthetic acquirer wording)
    follows the same whole-word rule as the name lists, so a Unicode letter glued to it does
    not make a credit trading receipts; the wording alone, and inside v1's own lines, still does."""
    for desc in (f"FPS CREDIT CAFÉ{word}", f"FPS CREDIT {word}É", f"FPS CREDIT Ж{word}"):
        assert classify(_t(desc, 500.0), v2) == (C.OTHER, "v2:default"), desc
    for desc in (word, f"CARD {word} 0412", f"FPS CREDIT CAFÉ {word}"):
        assert classify(_t(desc, 500.0), v2) == (C.REVENUE_CARD, "v2:card_settlement"), desc


@pytest.mark.invariants
def test_p_r4_unicode_neighbours_that_are_not_letters_still_separate_words(v2):
    assert classify(_t("FPS CREDIT CAFÉ STRIPE LTD", 500.0), v2) == (C.REVENUE_CARD, "v2:payment_gateways")
    assert classify(_t("café stripe ltd", 500.0), v2) == (C.REVENUE_CARD, "v2:payment_gateways")
    assert classify(_t("FPS CREDIT «STRIPE»", 500.0), v2) == (C.REVENUE_CARD, "v2:payment_gateways")
    assert classify(_t("FPS CREDIT STRIPE\u2013PAYOUT", 500.0), v2) == (C.REVENUE_CARD, "v2:payment_gateways")


@pytest.mark.invariants
def test_p_r4_profile_names_share_the_unicode_whole_word_rule(v2):
    profile = ProfileNames(directors=("JANE DOE",), own_accounts=("ACME",))
    assert classify(_t("FPS CREDIT ÉJANE DOE", 500.0), v2, profile=profile) == (C.OTHER, "v2:default")
    assert classify(_t("FPS CREDIT JANE DOE", 500.0), v2, profile=profile) == (C.DIRECTOR_FUNDING, "profile:director")
    assert classify(_t("FPS CREDIT ACMEÉ", 500.0), v2, profile=profile) == (C.OTHER, "v2:default")
    assert classify(_t("FPS CREDIT ACME", 500.0), v2, profile=profile) == \
        (C.OWN_ACCOUNT_TRANSFER, "profile:own_account")
    # an accented name in the profile matches its accented word, composed or not
    accented = ProfileNames(directors=("JOSÉ",))
    assert classify(_t("FPS CREDIT JOSÉ", 500.0), v2, profile=accented) == \
        (C.DIRECTOR_FUNDING, "profile:director")


# D61: the category inside one bucket comes from the table's declared order.

@pytest.mark.invariants
@pytest.mark.parametrize("desc, category, rule_id", [
    ("FPS CREDIT STRIPE INV 17", C.REVENUE_TRANSFER, "v2:invoice_transfer"),
    ("WORLDPAY INV 17", C.REVENUE_TRANSFER, "v2:invoice_transfer"),
    ("CASH DEPOSIT INV 12", C.CASH_DEPOSIT, "v2:cash_paid_in"),
    ("CASH DEPOSIT WORLDPAY", C.CASH_DEPOSIT, "v2:cash_paid_in"),
    ("RETURNED DD REFUND", C.RETURNED_DD, "v2:returned_payment"),
    ("REFUND DIRECT DEBIT RETURNED", C.RETURNED_DD, "v2:returned_payment"),
])
def test_p_r4_category_inside_a_bucket_follows_the_declared_order(v2, desc, category, rule_id):
    assert classify(_t(desc, 100.0), v2) == (category, rule_id)


@pytest.mark.contracts
def test_v2_declares_its_category_order_in_the_table(v2):
    data = yaml.safe_load(resources.files("cma.rules").joinpath("v2.yaml").read_bytes())
    assert data["category_order"] == [c.value for c in CATEGORY_ORDER]
    assert v2.category_order == tuple(CATEGORY_ORDER)


@pytest.mark.contracts
@pytest.mark.parametrize("mutate, message", [
    (lambda d: d.pop("category_order"), "bucket trading_receipts has categories"),
    (lambda d: d["category_order"].remove("refund_in"), "bucket refunds_returned_payments_in"),
    (lambda d: d["category_order"].append("cash_deposit"), "listed twice"),
    (lambda d: d["category_order"].append("nonsense"), "'nonsense' is not a category"),
    (lambda d: d.update(category_order="cash_deposit"), "must be a list"),
])
def test_a_v2_table_must_order_every_bucket_with_two_categories(mutate, message):
    import copy
    data = copy.deepcopy(yaml.safe_load(resources.files("cma.rules").joinpath("v2.yaml").read_bytes()))
    mutate(data)
    with pytest.raises(RulesetError, match=message):
        validate_ruleset(data, "v2")


@pytest.mark.contracts
def test_a_frozen_table_has_no_category_order():
    with pytest.raises(RulesetError, match="frozen table has no category_order"):
        validate_ruleset({"id": "v1", "frozen": True, "category_order": [], "rules": [GOOD]}, "v1")


def _shuffled(v2, rng) -> categorise.Ruleset:
    """v2 with its rules shuffled inside each stage. Debits read the protected rules first,
    so the shuffle keeps protected before unprotected within a stage (as the validator does)."""
    groups: list[list[Rule]] = []
    for stage in ("line_type", "names"):
        for protected in (True, False):
            g = [r for r in v2.rules if r.stage == stage and r.protected is protected]
            rng.shuffle(g)
            groups.append(g)
    return categorise.Ruleset(id="v2", sha256=v2.sha256, rules=tuple(r for g in groups for r in g),
                              category_order=v2.category_order)


def _figures(s: Statement) -> list[tuple]:
    return [(m.id, m.value, tuple(m.evidence)) for m in metrics.compute(s)]


def _statement(rows: list[tuple[str, float]]) -> Statement:
    bal, txns = 1000.0, []
    for i, (desc, amount) in enumerate(rows, start=1):
        bal = round(bal + amount, 2)
        txns.append(Transaction(id=f"T{i:04d}", date=date(2026, 1, 1) + timedelta(days=i % 180), description=desc,
                                amount=amount, balance=bal))
    return Statement(account_name="shuffle", transactions=txns)


@pytest.mark.invariants
def test_p_r4_round_trip_statement_keeps_its_category_and_m2_when_rules_are_reordered(v2):
    """verify-32 minor: with the invoice rule before the gateway rule, the credit used to
    become a customer transfer and leave M2 through the round-trip check. The declared
    order makes it a customer transfer whatever the rule order, so M2 cannot move."""
    rows = [("FPS DEBIT STRIPE", -100.0), ("FPS CREDIT STRIPE INV 17", 100.0)]
    names = [r for r in v2.rules if r.stage == "names"]
    inv = next(r for r in names if r.id == "invoice_transfer")
    swapped = categorise.Ruleset(
        id="v2", sha256=v2.sha256, category_order=v2.category_order,
        rules=tuple([r for r in v2.rules if r.stage == "line_type"] + [inv] + [r for r in names if r is not inv]))
    want = categorise.categorise(_statement(rows), v2)
    got = categorise.categorise(_statement(rows), swapped)
    assert [(t.category, t.rule_id) for t in want.transactions] == [(t.category, t.rule_id) for t in got.transactions]
    assert want.transactions[1].category is C.OTHER and want.transactions[1].rule_id == "v2:round_trip"
    assert _figures(want) == _figures(got)


@pytest.mark.invariants
def test_p_r4_shuffling_rules_within_a_stage_changes_no_category_or_figure_on_whole_statements(v2, tmp_path):
    """Design Rollout (revision 4): shuffle the rules within each stage and recategorise
    whole statements (round-trip and reversal passes included): every line's category and
    every figure are unchanged. Statements: every sample credit with matching debits (so
    round trips fire), and the committed synthetic and example corpus."""
    import random

    credits = _credit_samples()
    rows = [(d, 100.0 + i % 7) for i, d in enumerate(credits)]
    rows += [("FPS DEBIT STRIPE", -50.0), ("FPS CREDIT STRIPE INV 17", 50.0), ("FPS DEBIT WORLDPAY", -40.0),
             ("WORLDPAY INV 3", 40.0), ("DD BRITISH GAS", -75.0), ("RETURNED DD BRITISH GAS REFUND", 75.0),
             ("IWOCA LOAN", -10.0), ("RENT LOAN", -10.0), ("TFR TO SAVINGS RENT", -10.0), ("CASH WITHDRAWAL", -60.0)]
    corpus = [_statement(rows)] + [s for _, s, _, _ in _corpus_statements(tmp_path)[:6]]
    want = []
    for s in corpus:
        c = categorise.categorise(s.model_copy(deep=True), v2)
        want.append(([(t.category, t.reverses) for t in c.transactions], _figures(c)))
    for seed in range(6):
        shuffled = _shuffled(v2, random.Random(seed))
        for s, (cats, figs) in zip(corpus, want, strict=True):
            c = categorise.categorise(s.model_copy(deep=True), shuffled)
            got = [(t.category, t.reverses) for t in c.transactions]
            assert got == cats, [(t.description, a, b) for t, a, b in zip(c.transactions, got, cats, strict=True)
                                 if a != b][:5]
            assert _figures(c) == figs


@pytest.mark.contracts
def test_cma_run_under_v2_records_the_verify32_repros_with_every_candidate(tmp_path, monkeypatch):
    """End to end through cma run: each verify-32 repro takes R4's outcome in the evidence
    lines (the conflict form with every candidate from either stage), and only the
    genuine gateway payout is in M2; the ambiguous and unmatched credits are in M16."""
    from cma.cli import app

    monkeypatch.setenv("CMA_RULESET", "v2")
    expect = {
        "STRIPE PAYMENTS UK LTD": ("revenue_card_settlement", "v2:payment_gateways"),
        "BACS CREDIT HMRC CASH PAID IN": ("other", "v2:conflict:hmrc_repayment+payment_rail+cash_paid_in"),
        "FPS CREDIT HMRC TO SAVINGS STRIPE":
            ("other", "v2:conflict:own_pots_savings+hmrc_repayment+payment_rail+payment_gateways"),
        "FPS CREDIT CAFÉSTRIPE LTD": ("other", "v2:default"),
        "CARD TESCO": ("supplier", "v2:default"),
    }
    amounts = [2000.0, 500.0, 500.0, 500.0, -45.0]
    path = tmp_path / "s.csv"
    bal = 1000.0
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "date", "description", "amount", "balance"])
        for i, (desc, amount) in enumerate(list(zip(expect, amounts, strict=True)) * 4, start=1):
            bal = round(bal + amount, 2)
            w.writerow([f"T{i:03d}", (date(2026, 1, 1) + timedelta(days=5 * i)).isoformat(), desc,
                        f"{amount:.2f}", f"{bal:.2f}"])
    r = CliRunner().invoke(app, ["run", str(path), "--yes", "--reviewer", "A", "--out", str(tmp_path / "m.md")])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "m.evidence.json").read_text(encoding="utf-8"))
    lines = {d["id"]: d for d in record["lines"]}
    txns = ingest.load_csv(path).transactions
    for t in txns:
        assert (lines[t.id]["category"], lines[t.id]["rule_id"]) == expect[t.description], t.description
    m = {x["id"]: x for x in record["metrics"]}
    stripe = {t.id for t in txns if t.description == "STRIPE PAYMENTS UK LTD"}
    assert set(m["M2"]["evidence"]) == stripe
    assert {t.id for t in txns if t.amount > 0} - stripe <= set(m["M16"]["evidence"])


# --- T03 review 6 (D64): overlapping phrases and Unicode marks --------------------------

@pytest.mark.contracts
def test_a_longer_phrase_starting_inside_another_match_still_hides_the_words_inside_it():
    """A plain scan resumes after ACME PAY and never sees PAY FASTER PAYMENT LTD, which
    starts inside it; the rail inside that longer phrase then survived as a cue."""
    rail = {"id": "rail", "names": ["FASTER PAYMENT"], "direction": "in", "protected": True,
            "stage": "line_type"}
    acme = {"id": "acme", "names": ["ACME PAY", "PAY FASTER PAYMENT LTD"], "direction": "in",
            "category": "revenue_card_settlement", "stage": "names"}
    rs = categorise.ruleset_from_data({"id": "v2", "frozen": False, "rules": [rail, acme]}, "v2", "t", "0")
    assert [r.id for r in categorise.credit_matches(rs, "ACME PAY FASTER PAYMENT LTD", 100.0)] == ["acme"]
    # the rail on its own, outside any longer phrase, is still a cue
    assert [r.id for r in categorise.credit_matches(rs, "FASTER PAYMENT ACME PAY", 100.0)] == ["rail", "acme"]


# Combining marks from several blocks, including ones a hand-kept list of the Latin and
# symbol blocks missed: Devanagari, Cyrillic, Hebrew, Thai, and a variation selector.
MARKS = ["́", "ँ", "҃", "ַ", "ั", "\U000e0100"]


@pytest.mark.contracts
@pytest.mark.parametrize("mark", MARKS)
def test_a_listed_name_joined_to_other_letters_by_any_mark_is_not_that_name(v2, mark):
    trading = {c for c, b in CREDIT_BUCKET.items() if b is Bucket.TRADING_RECEIPTS}
    assert classify(_t("PAYOUT STRIPE", 100.0), v2)[0] in trading  # the control
    for desc in (f"PAYOUT X{mark}STRIPE", f"PAYOUT STRIPE{mark}X"):
        matched = categorise.credit_matches(v2, desc, 100.0)
        assert not any(r.category in trading for r in matched), (desc, [r.id for r in matched])
        assert classify(_t(desc, 100.0), v2)[0] not in trading, desc


@pytest.mark.contracts
@pytest.mark.parametrize("mark", MARKS)
def test_a_table_pattern_word_edge_is_mark_aware_too(v2, mark):
    """\\b in a v2 pattern counts a mark as part of the word, like the name lists do."""
    invoice = next(r for r in v2.rules if r.id == "invoice_transfer")
    assert invoice.pattern.search("PAYMENT INV 1234")
    assert not invoice.pattern.search(f"PAYMENT X{mark}INV 1234")


@pytest.mark.contracts
def test_a_frozen_table_keeps_its_patterns_exactly():
    """v1 reproduces its history (R54): its patterns are compiled as written."""
    from importlib import resources as res
    raw = yaml.safe_load(res.files("cma.rules").joinpath("v1.yaml").read_text(encoding="utf-8"))
    v1 = load_ruleset("v1")
    written = {r["id"]: r["pattern"] for r in raw["rules"] if "pattern" in r}
    assert written and all(r.pattern.pattern == written[r.id] for r in v1.rules if r.id in written)


# --- T03 review 7 (D71): spelling variants of listed names -----------------------------

def _listed_names():
    raw = yaml.safe_load(resources.files("cma.rules").joinpath("v2.yaml").read_text(encoding="utf-8"))
    return [(r["id"], n) for r in raw["rules"] for n in r.get("names", [])]


def _spellings(name: str) -> set[str]:
    words = name.split()
    out = {"".join(words), "-".join(words), ".".join(words), " - ".join(words), "/".join(words)}
    compact = "".join(words)
    if len(compact) >= 4:  # a space inside a run-together name: SUMUP as SUM UP
        out.add(compact[:2] + " " + compact[2:])
    return out


@pytest.mark.contracts
def test_every_listed_name_matches_its_rule_whatever_the_spacing(v2):
    """D71: a list cannot hold every spelling, so names ignore spaces, hyphens, dots and
    slashes between their characters. Each spelling still hits the name's own rule."""
    rules = {r.id: r for r in v2.rules}
    checked = 0
    for rule_id, name in _listed_names():
        for spelling in _spellings(name.upper()):
            desc = f"REF {spelling} 01"
            assert rules[rule_id].pattern.search(categorise._fold(desc)), (rule_id, name, spelling)
            checked += 1
    assert checked > 500


@pytest.mark.contracts
@pytest.mark.parametrize(("desc", "category", "rule"), [
    # the reviewer's cases
    ("FASTER-PAYMENT CASH DEPOSIT", C.OTHER, "v2:conflict:payment_rail+cash_paid_in"),
    ("SUM UP CASH ADVANCE", C.LOAN_DRAWDOWN, "v2:lender_drawdown"),
    # an advance from a provider no list names as a lender is lending, never trading
    ("SQUARE CASH ADVANCE", C.LOAN_DRAWDOWN, "v2:loan_drawdown"),
    # a listed card provider with advance wording: trading and lending, so ambiguous
    ("ZETTLE MERCHANT ADVANCE", C.OTHER, "v2:conflict:loan_drawdown+card_acquirers"),
    # a customer's advance payment is not advance wording
    ("ADVANCE PAYMENT INV 123", C.REVENUE_TRANSFER, "v2:invoice_transfer"),
])
def test_spelling_and_advance_wording_never_reach_trading_by_accident(v2, desc, category, rule):
    assert classify(_t(desc, 100.0), v2) == (category, rule)


# --- T03 review 9: typographic dashes, invisible characters, empty profile names ------

@pytest.mark.contracts
@pytest.mark.parametrize("gap", ["–", "—", "−", "­", "​", "﻿", "‑"])  # noqa: RUF001 - the look-alike dashes are the test
def test_a_typographic_dash_or_invisible_character_hides_no_listed_name(v2, gap):
    """En and em dash, minus, soft hyphen, zero-width space, byte order mark and the
    non-breaking hyphen all separate like a space: the rail is still the rail."""
    desc = f"FASTER{gap}PAYMENT CASH DEPOSIT"
    assert classify(_t(desc, 200.0), v2) == (C.OTHER, "v2:conflict:payment_rail+cash_paid_in"), repr(desc)
    assert classify(_t(f"SUM{gap}UP CASH ADVANCE", 200.0), v2) == (C.LOAN_DRAWDOWN, "v2:lender_drawdown")


@pytest.mark.contracts
@pytest.mark.parametrize("value", ["-", " . ", "–", "​", "/ /", " "])  # noqa: RUF001
def test_a_profile_value_of_only_separators_names_no_one(v2, value):
    """A director or own-account value that is all separators matches nothing, so it can
    neither settle an ambiguous credit nor move any line."""
    profile = ProfileNames(directors=(value,), own_accounts=(value,))
    for desc in ("FPS CREDIT CASH DEPOSIT", "CASH DEPOSIT DIRECTOR", "STRIPE PAYMENTS UK"):
        assert classify(_t(desc, 300.0), v2, profile=profile) == classify(_t(desc, 300.0), v2), (value, desc)
    assert categorise.names_pattern([value]) == "(?!)"


@pytest.mark.invariants
def test_p_a_director_named_in_a_payment_reference_does_not_settle_an_ambiguous_credit(v2):
    """T03 review 9 / D78: the description cannot tell the payer from a reference that
    names someone else, so a profile name only adds its bucket as a candidate."""
    profile = ProfileNames(directors=("JANE DOE",))
    desc = "FPS CREDIT CASH DEPOSIT REF JANE DOE"
    assert classify(_t(desc, 900.0), v2) == (C.OTHER, "v2:conflict:payment_rail+cash_paid_in")
    assert classify(_t(desc, 900.0), v2, profile=profile) == \
        (C.OTHER, "v2:conflict:payment_rail+cash_paid_in+profile:director")
    # a credit the table does not find ambiguous still follows R5
    assert classify(_t("FPS CREDIT JANE DOE", 900.0), v2, profile=profile) == (C.DIRECTOR_FUNDING, "profile:director")


@pytest.mark.invariants
def test_p_an_ambiguous_credit_records_every_kind_of_profile_name_it_matches(v2):
    """T03 review 10 / R4: a credit naming both a director and an own account keeps both
    as candidates; the director match used to hide the own-account one."""
    profile = ProfileNames(directors=("JANE DOE",), own_accounts=("ACME HOLDINGS",))
    desc = "FPS CREDIT CASH DEPOSIT JANE DOE ACME HOLDINGS"
    assert classify(_t(desc, 900.0), v2, profile=profile) == \
        (C.OTHER, "v2:conflict:payment_rail+cash_paid_in+profile:director+profile:own_account")


@pytest.mark.invariants
@pytest.mark.parametrize("desc", ["FPS CREDIT JANE DOE ACME HOLDINGS", "JANE DOE ACME HOLDINGS"])
def test_p_a_credit_naming_both_a_director_and_an_own_account_is_ambiguous(v2, desc):
    """D86: R5 and R6 would both claim it, so it goes to the queue with both candidates;
    either name alone still decides by R5 or R6."""
    profile = ProfileNames(directors=("JANE DOE",), own_accounts=("ACME HOLDINGS",))
    category, rule = classify(_t(desc, 900.0), v2, profile=profile)
    assert category is C.OTHER and rule.startswith("v2:conflict:")
    assert rule.endswith("profile:director+profile:own_account")
    assert classify(_t("FPS CREDIT JANE DOE", 900.0), v2, profile=profile) == (C.DIRECTOR_FUNDING, "profile:director")
    assert classify(_t("FPS CREDIT ACME HOLDINGS", 900.0), v2, profile=profile) == \
        (C.OWN_ACCOUNT_TRANSFER, "profile:own_account")


@pytest.mark.invariants
def test_p_a_credit_ambiguous_only_by_two_profile_names_records_every_rule_it_matched(v2):
    """D90: the candidates term is every rule matched in either stage, then both profile
    kinds, even when the table alone would have decided the credit."""
    profile = ProfileNames(directors=("JANE DOE",), own_accounts=("ACME HOLDINGS",))
    desc = "FPS CREDIT STRIPE INV 17 JANE DOE ACME HOLDINGS"
    category, rule = classify(_t(desc, 900.0), v2, profile=profile)
    matched = [r.id for r in categorise.credit_matches(v2, categorise._fold(desc), 900.0)]
    assert category is C.OTHER
    assert rule == "v2:conflict:" + "+".join([*matched, "profile:director", "profile:own_account"])
    assert {"payment_rail", "invoice_transfer"} <= set(matched)


@pytest.mark.contracts
def test_a_pattern_that_cannot_be_matched_at_every_position_is_refused_at_load():
    """T03 review 11: a back-reference compiles alone but not inside the form that finds
    matches at every position; it is refused when the table loads, by rule id, not on
    the first credit it meets."""
    rule = {"id": "echo", "pattern": r"(AB)\1", "direction": "in", "category": "revenue_card_settlement",
            "stage": "names"}
    with pytest.raises(RulesetError, match=r"echo.*back-reference"):
        categorise.ruleset_from_data({"id": "v2", "frozen": False, "rules": [rule]}, "v2", "t", "0")



@pytest.mark.contracts
def test_an_anchored_cue_without_the_flag_gets_no_bank_code_exception():
    """T03 review 12: anchoring an ordinary cue with ^ used to grant it the exception."""
    refund = {"id": "refund", "pattern": "^REFUND", "direction": "in", "category": "refund_in",
              "protected": True, "stage": "line_type"}
    pot = {"id": "pot", "names": ["TO SAVINGS"], "direction": "in", "category": "own_account_transfer",
           "protected": True, "stage": "line_type"}
    rs = categorise.ruleset_from_data({"id": "v2", "frozen": False, "rules": [refund, pot]}, "v2", "t", "0")
    assert classify(_t("REFUND TO SAVINGS", 50.0), rs) == (C.OTHER, "v2:conflict:refund+pot")


@pytest.mark.contracts
def test_the_bank_code_flag_is_refused_on_any_other_rule():
    refund = {"id": "refund", "pattern": "^REFUND", "direction": "in", "category": "refund_in",
              "protected": True, "stage": "line_type", "bank_code": True}
    with pytest.raises(RulesetError, match="bank_code is only for the returned-payment rule"):
        validate_ruleset({"id": "v2", "frozen": False, "rules": [refund]}, expected_id="v2")
