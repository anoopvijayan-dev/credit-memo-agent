from cma import guard
from cma.models import Metric


def _metrics():
    return [
        Metric(id="M2", name="Total revenue", value=264_312.5, unit="GBP", method="x"),
        Metric(id="M4", name="Revenue trend", value=-7.3, unit="pct", method="x"),
        Metric(id="M7", name="Returned direct debits", value=1, unit="count", method="x"),
        Metric(id="M10", name="Debt service cover", value=1.42, unit="ratio", method="x"),
    ]


def test_grounded_memo_passes():
    memo = "Revenue was £264,313 [M2], down -7.3% [M4]. There was 1 [M7] returned direct debit. Cover is 1.42 [M10]."
    g = guard.check(memo, _metrics())
    assert g.grounded, g


def test_invented_figure_is_caught():
    memo = "Revenue was £264,313 [M2]. The business also holds stock worth £48,250 according to the owner."
    g = guard.check(memo, _metrics())
    assert not g.grounded
    assert g.unmatched[0].number_text == "£48,250"


def test_missing_citation_is_caught():
    memo = "Revenue was £264,313 over the year, which is healthy."
    g = guard.check(memo, _metrics())
    assert "M2" in g.missing_citations


def test_small_prose_integers_are_allowed():
    memo = "Three of the 7 rules passed over 12 months [M1]."
    g = guard.check(memo, _metrics())
    assert g.unmatched == []
