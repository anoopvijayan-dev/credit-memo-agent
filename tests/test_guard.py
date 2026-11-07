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


def test_small_prose_integers_are_allowed():
    memo = "Three of the 9 rules passed over the period [M7]."
    g = guard.check(memo, _metrics())
    assert g.unmatched == []


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
