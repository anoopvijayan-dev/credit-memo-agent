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


@pytest.mark.parametrize("archetype,profile", CASES)
def test_categoriser_matches_truth(statement_for, archetype, profile):
    s, truth = statement_for(archetype, profile)
    wrong = [t.id for t in s.transactions if t.category.value != truth.category_by_id[t.id]]
    assert wrong == [], f"{len(wrong)} miscategorised, e.g. {wrong[:3]}"


@pytest.mark.parametrize("archetype,profile", CASES)
def test_metrics_match_truth(statement_for, archetype, profile):
    s, truth = statement_for(archetype, profile)
    m = {x.id: x for x in metrics.compute(s)}
    assert m["M1"].value == 12
    assert m["M2"].value == pytest.approx(truth.total_revenue, abs=0.01)
    assert m["M5"].value == pytest.approx(truth.lowest_balance, abs=0.01)
    assert m["M6"].value == truth.days_overdrawn
    assert m["M7"].value == truth.returned_dd_count
    assert m["M8"].value == pytest.approx(truth.total_debt_service, abs=0.01)
    assert m["M11"].value == pytest.approx(truth.total_owner_drawings, abs=0.01)
    assert m["M12"].value == pytest.approx(truth.total_hmrc_vat, abs=0.01)


@pytest.mark.parametrize("archetype,profile", CASES)
def test_every_metric_cites_evidence(statement_for, archetype, profile):
    s, _ = statement_for(archetype, profile)
    ids = s.by_id()
    for m in metrics.compute(s):
        if m.id == "M1":
            continue
        for tid in m.evidence:
            assert tid in ids


def test_policy_separates_profiles(statement_for):
    outcomes = {}
    for p in synth.PROFILES:
        s, _ = statement_for("cafe", p)
        outcomes[p] = policy.evaluate(metrics.compute(s)).overall
    assert outcomes["good"] == RuleOutcome.PASS
    assert outcomes["poor"] == RuleOutcome.FAIL
    assert outcomes["stretched"] in (RuleOutcome.REFER, RuleOutcome.PASS)
