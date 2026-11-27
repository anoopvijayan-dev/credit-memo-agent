"""The open owner items, settled with the recommended option (standing approval of
2026-11-19; recorded in docs/statement-review/owner-decisions.md). Each test fails on the code
before the decision."""

from datetime import date

import pytest

from cma import categorise, metrics
from cma.models import Category, Statement, Transaction


def _cat(desc: str, amount: float, ruleset: str = "v2") -> Category:
    s = Statement(account_name="t", transactions=[Transaction(id="L1", date=date(2026, 3, 2), description=desc,
                                                                amount=amount, balance=amount)])
    return categorise.categorise(s, ruleset=categorise.load_ruleset(ruleset)).transactions[0].category


@pytest.mark.contracts
def test_the_comparison_shows_a_ratio_as_the_memo_does():
    # T41: the tool's figure was shown as 1.7543859649122806x
    d = metrics.comparison(1.7543859649122806, "ratio", "1.75")
    assert d["ours_display"] == "1.75x" and d["difference_display"] == "0x"
    assert d["ours"] == "1.7543859649122806"  # the exact value is kept beside it
    assert metrics.comparison(42.18571, "pct", "40")["ours_display"] == "42.2%"


@pytest.mark.contracts
@pytest.mark.parametrize("desc, amount, category", [
    ("SHARE CAPITAL ACME LTD", 1000.00, Category.DIRECTOR_FUNDING),
    ("UNPAID CHEQUE 000123", 250.00, Category.RETURNED_DD),
    ("BOUNCED DD BRITISH GAS", 80.00, Category.RETURNED_DD),
])
def test_wordings_no_built_in_pattern_recognised_are_now_protected(desc, amount, category):
    assert _cat(desc, amount) is category
    rules = categorise.load_ruleset("v2").rules
    assert any(r.protected and categorise.rule_matches(r, desc.upper(), amount) for r in rules)


@pytest.mark.contracts
@pytest.mark.parametrize("desc", ["CAPITAL-INJECTED ACME", "CAPITAL_INTRODUCED ACME", "CAPITAL/CONTRIBUTION X"])
def test_v1_protects_capital_wording_written_with_a_separator(desc):
    # Codex re-review of T36: the protection needed a literal space after CAPITAL
    assert categorise._V1_PROTECTED_IN.search(desc)
