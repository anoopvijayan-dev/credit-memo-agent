"""Metrics must agree with the generator's ground truth, not with each other."""

from datetime import date

import pytest

from cma import categorise, ingest, metrics, policy, synth
from cma.models import RuleOutcome

CASES = [(a, p) for a in synth.ARCHETYPES for p in synth.PROFILES]


@pytest.fixture
def statement_for(tmp_path):
    def _make(archetype, profile):
        rows, truth = synth.generate(archetype, profile, end=date(2026, 9, 30))
        csv_path, _ = synth.write(rows, truth, tmp_path, stem=f"{archetype}_{profile}")
        return categorise.categorise(ingest.load_csv(csv_path)), truth

    return _make


def _metrics(s, truth=None):
    return {x.id: x for x in metrics.compute(s, vat_stagger=synth.VAT_STAGGER)}



@pytest.mark.parametrize("archetype,profile", CASES)
def test_categoriser_matches_truth(statement_for, archetype, profile):
    s, truth = statement_for(archetype, profile)
    wrong = [t.id for t in s.transactions if t.category.value != truth.category_by_id[t.id]]
    assert wrong == [], f"{len(wrong)} miscategorised, e.g. {wrong[:3]}"


@pytest.mark.parametrize("archetype,profile", CASES)
def test_metrics_match_truth(statement_for, archetype, profile):
    s, truth = statement_for(archetype, profile)
    m = _metrics(s)
    assert m["M1"].value == 12
    assert m["M2"].value == pytest.approx(truth.total_revenue, abs=0.01)
    assert m["M5"].value == pytest.approx(truth.lowest_balance, abs=0.01)
    assert m["M6"].value == truth.days_overdrawn
    assert m["M7"].value == truth.returned_dd_count
    assert m["M8"].value == pytest.approx(truth.total_debt_service, abs=0.01)
    assert m["M11"].value == pytest.approx(truth.total_owner_drawings, abs=0.01)
    assert m["M12"].value == pytest.approx(truth.total_hmrc_vat, abs=0.01)
    assert m["M13"].value == truth.late_vat_count
    assert m["M15"].value == truth.top_customer_share


@pytest.mark.parametrize("archetype,profile", CASES)
def test_every_metric_cites_evidence(statement_for, archetype, profile):
    s, _ = statement_for(archetype, profile)
    ids = s.by_id()
    for m in metrics.compute(s, vat_stagger=3):
        for tid in m.evidence:
            assert tid in ids


def test_policy_separates_profiles(statement_for):
    outcomes = {}
    for p in synth.PROFILES:
        s, _ = statement_for("cafe", p)
        outcomes[p] = policy.evaluate(list(_metrics(s).values())).overall
    assert outcomes["good"] == RuleOutcome.PASS
    assert outcomes["poor"] == RuleOutcome.FAIL
    assert outcomes["stretched"] == RuleOutcome.REFER


# --- C4: evidence is complete enough to recompute the figure (panel run 01, 6/6 seats)

@pytest.mark.parametrize("archetype,profile", CASES)
def test_evidence_reproduces_each_figure(statement_for, archetype, profile):
    s, _ = statement_for(archetype, profile)
    by_id = s.by_id()
    m = _metrics(s)

    def total(mid):
        return round(sum(by_id[t].amount for t in m[mid].evidence), 2)

    assert total("M2") == pytest.approx(m["M2"].value, abs=0.01)
    assert by_id[m["M5"].evidence[0]].balance == pytest.approx(m["M5"].value, abs=0.01)
    assert all(by_id[t].balance < 0 for t in m["M6"].evidence)
    assert len(m["M7"].evidence) == m["M7"].value
    assert -total("M8") == pytest.approx(m["M8"].value, abs=0.01)
    assert total("M9") == pytest.approx(m["M9"].value, abs=0.01)
    assert -total("M11") == pytest.approx(m["M11"].value, abs=0.01)
    assert -total("M12") == pytest.approx(m["M12"].value, abs=0.01)
    assert -total("M14") == pytest.approx(m["M14"].value, abs=0.01)
    # M10 cites its numerator (operating cash flow less drawings) and its denominator.
    assert set(m["M8"].evidence) <= set(m["M10"].evidence)
    assert set(m["M9"].evidence) <= set(m["M10"].evidence)
    assert set(m["M11"].evidence) <= set(m["M10"].evidence)
    assert m["M10"].value == pytest.approx((m["M9"].value - m["M11"].value) / m["M8"].value, abs=0.01)
