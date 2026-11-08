import pytest

from cma import guard
from cma.models import Metric


def _metrics():
    return [
        Metric(id="M2", name="Total revenue", value=264_312.5, unit="GBP", method="x"),
        Metric(id="M3", name="Average monthly revenue", value=22_114.0, unit="GBP", method="x"),
        Metric(id="M4", name="Revenue trend", value=-7.3, unit="pct", method="x"),
        Metric(id="M6", name="Days overdrawn", value=25, unit="days", method="x"),
        Metric(id="M7", name="Returned direct debits", value=1, unit="count", method="x"),
        Metric(id="M10", name="Debt service cover", value=1.42, unit="ratio", method="x"),
    ]


def test_grounded_memo_passes():
    memo = (
        "Revenue was £264,313 [M2], down -7.3% [M4]. There was 1 [M7] returned direct debit. "
        "Cover is 1.42 [M10]."
    )
    g = guard.check(memo, _metrics())
    assert g.grounded, g


def test_invented_figure_is_caught():
    memo = "Revenue was £264,313 [M2]. The business also holds stock worth £48,250 according to the owner."
    g = guard.check(memo, _metrics())
    assert not g.grounded
    assert g.unmatched[0].number_text == "£48,250"


def test_missing_citation_is_caught():
    memo = "Revenue was £264,313 over the year, down -7.3% [M4]."
    g = guard.check(memo, _metrics())
    assert "M2" in g.missing_citations


def test_counts_are_checked_like_any_other_figure():
    # Panel run 02, D5: the old 0-31 exemption let "2 returned direct debits" through when M7 = 1.
    g = guard.check("Revenue £264,313 [M2]. There were 2 returned direct debits.", _metrics())
    assert "2" in [f.number_text for f in g.unmatched]
    g = guard.check("Revenue £264,313 [M2]. Three of the 9 rules passed.", _metrics())
    assert {"Three", "9"} <= {f.number_text for f in g.unmatched}


# --- C1: suffixed numbers (panel run 01, 6/6 seats) -------------------------------

@pytest.mark.parametrize(
    "memo, bad",
    [
        ("Revenue £264,313 [M2]. Stock is worth £48k.", "£48k"),
        ("Revenue £264,313 [M2]. The facility is £1.2m.", "£1.2m"),
        ("Revenue £264,313 [M2]. Group turnover is 3bn.", "3bn"),
        ("Revenue £264,313 [M2]. Cover is 3.7x [M10].", "3.7x"),
        ("Revenue £264,313 [M2]. Cover is 2.75x.", "2.75x"),
    ],
)
def test_suffixed_invented_numbers_are_caught(memo, bad):
    g = guard.check(memo, _metrics())
    assert not g.grounded
    assert bad in [f.number_text for f in g.unmatched]


def test_suffixed_true_numbers_are_grounded():
    memo = "Average revenue was £22.1k [M3] a month. Cover is 1.42x [M10]. Revenue £264k [M2]."
    g = guard.check(memo, _metrics())
    assert g.grounded, g


def test_scaled_value_without_suffix_is_not_accepted():
    # Panel run 01 minor: the old /1000 variant let "£264.3 [M2]" through.
    g = guard.check("Revenue was £264.3 [M2].", _metrics())
    assert not g.grounded


# --- C2: year exemption and empty memos (5/6 seats) -------------------------------

def test_money_in_year_range_is_checked():
    g = guard.check("Revenue £264,313 [M2]. Rent is £2,050 a month.", _metrics())
    assert "£2,050" in [f.number_text for f in g.unmatched]


def test_percent_in_year_range_is_checked():
    g = guard.check("Revenue £264,313 [M2]. Margin grew 2050%.", _metrics())
    assert not g.grounded


def test_bare_years_are_not_numbers():
    g = guard.check("In 2026 revenue was £264,313 [M2].", _metrics())
    assert g.grounded, g


def test_empty_memo_is_not_grounded():
    g = guard.check("", _metrics())
    assert not g.grounded


def test_memo_without_any_citation_is_not_grounded():
    g = guard.check("The business looks healthy and trades well.", _metrics())
    assert not g.grounded


# --- citation handling for small integers (clean-code major) ----------------------

def test_small_integer_metric_needs_its_tag():
    g = guard.check("Revenue £264,313 [M2]. The account was overdrawn on 25 days.", _metrics())
    assert "M6" in g.missing_citations


def test_citation_digits_are_not_counted_as_numbers():
    g = guard.check("Revenue £264,313 [M2]. Cover 1.42 [M10].", _metrics())
    assert g.numbers_checked == 2


# --- panel run 02 ------------------------------------------------------------------

def _with_negatives():
    return [*_metrics(), Metric(id="M5", name="Lowest balance", value=-3200.0, unit="GBP", method="x"),
            Metric(id="M1", name="Months covered", value=12, unit="months", method="x")]


@pytest.mark.parametrize("memo, reason", [
    ("Revenue £264,313 [M2]. A healthy £3,200 [M5] in credit.", "sign"),     # D2: sign
    ("Revenue £264,313 [M2]. Statements cover 12% [M1].", "unit"),           # D2: unit
    ("Revenue £264,313 [M2]. Cover is £1.42 [M10].", "unit"),
    ("Revenue $264,313 [M2].", "unit"),
])
def test_sign_and_unit_must_fit_the_tagged_metric(memo, reason):
    g = guard.check(memo, _with_negatives())
    assert not g.grounded
    assert any(reason in f.reason for f in g.unmatched), g.unmatched


def test_negative_figures_written_with_minus_are_grounded():
    for memo in ["Lowest balance -£3,200 [M5].", "Lowest balance £-3,200 [M5].", "Lowest balance \u2212£3,200 [M5]."]:
        g = guard.check(memo, _with_negatives())
        assert g.grounded, (memo, g)


@pytest.mark.parametrize("memo, bad", [
    ("Revenue £264,313 [M2]. Stock is 48250GBP.", "48250GBP"),                # D3: glued units
    ("Revenue £264,313 [M2]. The facility is GBP1.2m.", "GBP1.2m"),
    ("Revenue £264,313 [M2]. Stock is c.48k.", "48k"),
    ("Revenue £264,313 [M2]. Stock is worth 48 thousand pounds.", "48 thousand"),
    ("Revenue £264,313 [M2]. Stock is forty-eight thousand pounds.", "forty-eight"),
    ("Revenue £264,313 [M2]. There were five returned direct debits.", "five"),
])
def test_glued_units_and_number_words_are_read(memo, bad):
    g = guard.check(memo, _metrics())
    assert not g.grounded
    assert any(bad in f.number_text for f in g.unmatched), g.unmatched


def test_rounding_cannot_drift_more_than_half_a_percent():
    ms = [Metric(id="M2", name="Total revenue", value=48_400.0, unit="GBP", method="x")]
    assert not guard.check("Revenue £48k [M2].", ms).grounded
    assert guard.check("Revenue £48.4k [M2].", ms).grounded


def test_tag_must_follow_its_figure_and_name_the_right_metric():
    assert "M3" in guard.check("Revenue averaged £22,114 a month [M3].", _metrics()).missing_citations
    g = guard.check("Revenue averaged £22,114 [M2].", _metrics())  # right number, wrong tag
    assert not g.grounded and "M2" in g.unmatched[0].reason


def _policy():
    from cma.models import PolicyResult, RuleOutcome, RuleResult
    return PolicyResult(policy_name="p", overall=RuleOutcome.REFER, rules=[
        RuleResult(rule_id="R2", description="Debt service cover of at least 1.25x", metric_id="M10",
                   outcome=RuleOutcome.REFER, detail="", op=">=", threshold=1.25, refer_below=1.5, unit="ratio"),
        RuleResult(rule_id="R4", description="Fewer than 15 days overdrawn", metric_id="M6",
                   outcome=RuleOutcome.REFER, detail="", op="<", threshold=15, unit="days"),
    ])


def test_policy_thresholds_are_citable_with_rule_tags():
    # Panel run 02, D6: the model was asked to explain rules but every threshold was flagged.
    memo = ("Cover is 1.42x [M10], above the 1.25x [R2] minimum but below the 1.5x [R2] band. "
            "The account was overdrawn on 25 [M6] days against a limit of 15 [R4].")
    assert guard.check(memo, _metrics(), _policy()).grounded


def test_wrong_threshold_under_a_rule_tag_is_caught():
    g = guard.check("Cover is 1.42x [M10] against a 1.1x [R2] minimum.", _metrics(), _policy())
    assert not g.grounded and "R2" in g.unmatched[0].reason


def test_iso_dates_are_not_figures():
    memo = "Period 2025-10-01 to 2026-09-30. Revenue £264,313 [M2]."
    assert guard.check(memo, _metrics()).grounded


# --- panel run 03 ------------------------------------------------------------------

@pytest.mark.parametrize("memo, bad", [
    ("Revenue £264,313 [M2]. The account was overdrawn on 40 may be a concern.", "40"),   # E2: "may" the verb
    ("Revenue £264,313 [M2]. These 9 may recur.", "9"),
    ("Revenue £264,313 [M2]. Arrears of £75 may follow.", "£75"),
    ("Revenue £264,313 [M2]. In March 62% of transfer revenue came from one payer.", "62%"),
    ("Revenue £264,313 [M2]. By September 85 days had closed overdrawn.", "85"),
    ("Revenue £264,313 [M2]. Last paid 7 January 2026.", "7"),
    ("Revenue £264,313 [M2]. Arrears are 12/34/5678 pounds.", "12"),
    ("Revenue £264,313 [M2] and the business also owes HMRC\n45000. That is a concern.", "45000"),
    ("Revenue £264,313 [M2].\n1. Cash is tight.", "1"),
])
def test_only_iso_dates_are_exempt(memo, bad):
    g = guard.check(memo, _metrics())
    flagged = {f.number_text for f in g.unmatched}
    assert not g.grounded and (bad in flagged or g.missing_citations), g


@pytest.mark.parametrize("phrase", [
    "Revenue roughly halved.", "The top customer is a third of income.", "Revenue doubled.",
    "Drawings were twice the loan repayments.", "There was one returned direct debit.", "Takings fell by half.",
])
def test_fractions_multiples_and_one_are_flagged(phrase):
    # E14
    g = guard.check(f"Revenue £264,313 [M2]. {phrase}", _metrics())
    assert not g.grounded and any("words" in f.reason for f in g.unmatched)


def test_rule_tag_needs_a_limit_word():
    # E15: an applicant figure dressed up as the threshold.
    g = guard.check("Revenue £264,313 [M2]. Debt service cover is 1.25x [R2].", _metrics(), _policy())
    assert not g.grounded and any("policy limit" in f.reason for f in g.unmatched)
    assert guard.check("Cover 1.42x [M10] is above the 1.25x [R2] minimum.", _metrics(), _policy()).grounded


def test_truncated_memo_is_not_grounded():
    # E24: a reply cut off before the Recommendation.
    memo = "## Business overview\nRevenue £264,313 [M2].\n## Revenue\nTrend -7.3% [M4]."
    g = guard.check(memo, _metrics(), required_headings=guard.REQUIRED_HEADINGS)
    assert not g.grounded
    assert "missing heading: Recommendation (truncated or off-format)" in [f.reason for f in g.unmatched]


def test_identifiers_are_not_figures():
    assert guard.check("Rules R2 and R5 were referred; see M10 and INV1234. Revenue £264,313 [M2].", _metrics()).grounded


def test_missing_metric_cannot_be_quoted():
    ms = [*_metrics(), Metric(id="M13", name="Late VAT", value=None, unit="count", method="unknown stagger")]
    g = guard.check("Revenue £264,313 [M2]. Late VAT payments: 0 [M13].", ms)
    assert not g.grounded and "no value" in g.unmatched[0].reason
    assert guard.check("Revenue £264,313 [M2]. Late VAT payments: n/a [M13].", ms).grounded
