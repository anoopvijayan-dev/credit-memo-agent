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


def _filed(tmp_path, on_fail="refer"):
    """A stored result from a policy file, a rule passing on 2.0 whose fail action is `on_fail`."""
    import yaml

    from cma import policy as pol
    from cma.models import Metric

    f = tmp_path / "firm.yaml"
    f.write_text(yaml.safe_dump({"name": "firm", "rules": [
        {"id": "R1", "description": "cover", "metric": "M10", "op": ">=", "threshold": 1.25, "on_fail": on_fail}]}))
    m = Metric(id="M10", name="Debt service cover", value=2.0, unit="ratio", method="m", evidence=[])
    return f, pol.evaluate([m], pol.load_policy(f)), m


def test_a_rule_the_stored_result_cannot_settle_is_settled_by_the_same_policy(tmp_path):
    # T04 review 19: newly failing a rule needs its fail action; the policy the result came
    # from (checked by hash) gives it, so a historical review is not refused for it.
    from cma import policy as pol

    f, stored, m = _filed(tmp_path)
    got = pol.reevaluate(stored, [m], [m.model_copy(update={"value": 1.0})], f)
    assert got.rules[0].outcome.value == "refer" and got.policy_sha256 == stored.policy_sha256


@pytest.mark.parametrize("change", ["edited", "removed"])
def test_a_different_or_missing_policy_file_is_not_used(tmp_path, change):
    from cma import policy as pol

    f, stored, m = _filed(tmp_path)
    if change == "edited":
        f.write_text(f.read_text().replace("refer", "fail"))
    else:
        f.unlink()
    with pytest.raises(pol.OutcomeUnknown, match="M10"):
        pol.reevaluate(stored, [m], [m.model_copy(update={"value": 1.0})], f)



@pytest.mark.parametrize("make", ["directory", "not_utf8"])
def test_an_unreadable_policy_is_a_policy_error_and_resume_refuses_naming_the_figure(tmp_path, make):
    # T04 review 21: a policy path that is a directory, or a file that is not UTF-8, escaped
    # as a raw exception instead of the refusal naming the figure (revision 28).
    from cma import policy as pol

    f, stored, m = _filed(tmp_path)
    f.unlink()
    if make == "directory":
        f.mkdir()
    else:
        f.write_bytes(b"\xff\xfe name: x")
    with pytest.raises(pol.PolicyError):
        pol.load_policy(f)
    with pytest.raises(pol.OutcomeUnknown, match=r"M10.*cannot be read"):
        pol.reevaluate(stored, [m], [m.model_copy(update={"value": 1.0})], f)


@pytest.mark.parametrize("same_policy", [True, False])
def test_a_rule_stored_without_its_comparison_falls_back_to_the_same_policy(tmp_path, same_policy):
    # T04 review 22: an earlier release's rule result holds no operator or threshold; a
    # changed figure raised a raw error instead of using the hash-checked policy or refusing.
    from cma import policy as pol

    f, stored, m = _filed(tmp_path)
    old = stored.model_copy(update={"rules": [r.model_copy(update={"op": "", "threshold": None})
                                              for r in stored.rules]})
    if not same_policy:
        f.write_text(f.read_text().replace("cover", "cover cover"))
    new = m.model_copy(update={"value": 1.0})
    if same_policy:
        assert pol.reevaluate(old, [m], [new], f).rules[0].outcome.value == "refer"
    else:
        with pytest.raises(pol.OutcomeUnknown, match="M10"):
            pol.reevaluate(old, [m], [new], f)


# --- money limits are whole pence (T04 review 58) ---------------------------------------

def _money(value):
    from cma.models import Metric

    return Metric(id="M2", name="Total revenue", value=value, unit="GBP", method="m", evidence=[])


@pytest.mark.contracts
@pytest.mark.parametrize("key, limit", [("threshold", 1000.005), ("refer_below", 1200.001), ("refer_above", 0.0001)])
def test_a_money_limit_that_is_not_whole_pence_is_refused(key, limit):
    """T04 review 58 / R2: a policy could give a money figure a limit in fractions of a penny,
    compared as an exact decimal; money is compared in whole pence, so such a limit is refused."""
    from cma import policy as pol

    rule = {"id": "R1", "description": "revenue", "metric": "M2", "op": ">=", "threshold": 1000.0,
            "on_fail": "refer", key: limit}
    with pytest.raises(pol.PolicyError, match=f"{key} {limit!r} must be a whole number of pence"):
        pol.evaluate([_money(2000.0)], {"name": "p", "rules": [rule]})
    # A limit on a figure that is not money keeps its precision.
    ratio = {"id": "R1", "description": "cover", "metric": "M10", "op": ">=", "threshold": 1.2505, "on_fail": "fail"}
    from cma.models import Metric
    m = Metric(id="M10", name="Debt service cover", value=1.26, unit="ratio", method="m", evidence=[])
    assert pol.evaluate([m], {"name": "p", "rules": [ratio]}).rules[0].outcome.value == "pass"


@pytest.mark.contracts
def test_money_figures_are_compared_with_their_limits_in_pence():
    """T04 review 58 / R2: money is compared in pence in the figures layer; an average holding a
    fraction of a penny is compared exactly, and a limit that is not whole pence never is."""
    from cma import metrics

    assert metrics.compare(1000.0, ">=", 1000, "GBP") and not metrics.compare(999.99, ">=", 1000, "GBP")
    assert metrics.compare(0.1 + 0.2, ">", 0.3, "GBP")  # 30.000000000000004 pence, exactly
    assert metrics.compare(1000.004, "<", 1000.01, "GBP") and metrics.compare(1000.004, ">", 1000, "GBP")
    with pytest.raises(ValueError, match="not a whole number of pence"):
        metrics.compare(1.0, ">=", 0.005, "GBP")


def test_a_stored_money_limit_that_is_not_whole_pence_is_not_replayed():
    """T04 review 58 / R2, R53: an earlier release admitted a money limit in fractions of a penny;
    replaying it would compare money otherwise than in pence, so only the policy can say."""
    from cma import policy as pol
    from cma.models import PolicyResult, RuleOutcome, RuleResult

    rule = RuleResult(rule_id="R1", description="revenue", metric_id="M2", requirement="r", outcome=RuleOutcome.PASS,
                      detail="d", op=">=", threshold=1000.005, unit="GBP")
    stored = PolicyResult(policy_name="p", policy_sha256="", rules=[rule], overall=RuleOutcome.PASS)
    with pytest.raises(pol.OutcomeUnknown, match="not a whole number of pence"):
        pol.reevaluate(stored, [_money(2000.0)], [_money(2001.0)])
