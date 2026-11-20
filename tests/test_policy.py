"""Policy evaluation: re-evaluating a stored result for changed figures."""

import pytest

# --- re-evaluating a stored result for changed figures (T04 review 18) -----------------

def _stored(value, op=">=", threshold=1.25, on_fail="fail", **bands):
    from cma import policy as pol
    from cma.models import Metric

    m = Metric(id="M10", name="Debt service cover", value=value, unit="ratio", method="m", evidence=[])
    rule = {"id": "R1", "description": "cover", "metric": "M10", "op": op, "threshold": threshold,
            "on_fail": on_fail, **bands}
    return pol.evaluate([m], {"name": "p", "rules": [rule]}), m


@pytest.mark.parametrize("old, new, bands, outcome", [
    (2.0, 2.0000001, {}, "pass"),                     # same band: same outcome, today's figure shown
    (1.4, 1.6, {"refer_below": 1.5}, "pass"),         # out of a referral band
    (1.6, 1.4, {"refer_below": 1.5}, "refer"),        # into one
    (1.0, 1.1, {}, "fail"),                           # failing still: the stored fail action
    (1.0, 2.0, {}, "pass"),                           # now passing
])
def test_a_stored_policy_result_is_reevaluated_without_the_policy_file(old, new, bands, outcome):
    from cma import policy as pol

    stored, m = _stored(old, **bands)
    fresh = m.model_copy(update={"value": new})
    got = pol.reevaluate(stored, [m], [fresh])
    assert got.rules[0].outcome.value == outcome and got.overall.value == outcome
    assert got.rules[0].detail.startswith(f"{fresh.name} is {fresh.display()}; rule requires >= 1.25")
    assert got == pol.evaluate([fresh], {"name": "p", "rules": [{"id": "R1", "description": "cover", "metric": "M10",
                                                                 "op": ">=", "threshold": 1.25, "on_fail": "fail",
                                                                 **bands}]}).model_copy(
        update={"policy_sha256": stored.policy_sha256})


@pytest.mark.parametrize("old, new", [(2.0, 1.0), (2.0, None)])
def test_a_rule_whose_action_only_the_policy_holds_is_not_guessed(old, new):
    from cma import policy as pol

    stored, m = _stored(old)
    with pytest.raises(pol.OutcomeUnknown, match="M10"):
        pol.reevaluate(stored, [m], [m.model_copy(update={"value": new})])
