"""Apply a YAML lending policy to computed metrics."""

from __future__ import annotations

import operator
from pathlib import Path

import yaml

from .models import Metric, PolicyResult, RuleOutcome, RuleResult

OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt, "==": operator.eq}

DEFAULT_POLICY = Path(__file__).resolve().parents[2] / "policy" / "uk_sme_default.yaml"


def load_policy(path: Path | None = None) -> dict:
    return yaml.safe_load(Path(path or DEFAULT_POLICY).read_text())


def evaluate(metrics: list[Metric], policy: dict | None = None) -> PolicyResult:
    policy = policy or load_policy()
    by_id = {m.id: m for m in metrics}
    results: list[RuleResult] = []
    for rule in policy["rules"]:
        m = by_id[rule["metric"]]
        passed = OPS[rule["op"]](m.value, rule["threshold"])
        if passed:
            outcome = RuleOutcome.PASS
            # Soft bands: close to the threshold still gets a human look.
            if "refer_below" in rule and m.value < rule["refer_below"]:
                outcome = RuleOutcome.REFER
            if "refer_above" in rule and m.value > rule["refer_above"]:
                outcome = RuleOutcome.REFER
        else:
            outcome = RuleOutcome(rule["on_fail"])
        results.append(
            RuleResult(
                rule_id=rule["id"],
                description=rule["description"],
                metric_id=m.id,
                outcome=outcome,
                detail=f"{m.name} is {m.display()}; rule requires {rule['op']} {rule['threshold']}.",
            )
        )
    if any(r.outcome == RuleOutcome.FAIL for r in results):
        overall = RuleOutcome.FAIL
    elif any(r.outcome == RuleOutcome.REFER for r in results):
        overall = RuleOutcome.REFER
    else:
        overall = RuleOutcome.PASS
    return PolicyResult(policy_name=policy["name"], overall=overall, rules=results)
