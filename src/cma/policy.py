"""Apply a YAML lending policy to computed metrics.

The default policy ships inside the package (cma/policies/uk_sme_default.yaml), so a
plain `pip install` works. A policy file is validated before use: an unknown operator,
metric or outcome is an error, never a silent pass.
"""

from __future__ import annotations

import hashlib
import operator
from importlib import resources
from pathlib import Path

import yaml

from .models import Metric, PolicyResult, RuleOutcome, RuleResult

OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt, "==": operator.eq}
ON_FAIL = {"refer", "fail"}
WHEN_MISSING = {"pass", "refer", "fail"}
DEFAULT_POLICY_NAME = "uk_sme_default.yaml"


class PolicyError(ValueError):
    """The policy file is malformed; no decision should be made from it."""


def default_policy_text() -> str:
    return resources.files("cma.policies").joinpath(DEFAULT_POLICY_NAME).read_text()


def load_policy(path: Path | None = None) -> dict:
    text = Path(path).read_text() if path else default_policy_text()
    policy = yaml.safe_load(text)
    validate(policy, source=str(path or f"cma/policies/{DEFAULT_POLICY_NAME}"))
    policy["_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    return policy


def validate(policy: object, source: str = "policy", metric_ids: set[str] | None = None) -> None:
    problems: list[str] = []
    if not isinstance(policy, dict):
        raise PolicyError(f"{source}: not a mapping")
    if not isinstance(policy.get("name"), str) or not policy["name"].strip():
        problems.append("missing name")
    rules = policy.get("rules")
    if not isinstance(rules, list) or not rules:
        raise PolicyError(f"{source}: no rules")
    seen: set[str] = set()
    for i, r in enumerate(rules, start=1):
        where = f"rule {i} ({r.get('id', '?') if isinstance(r, dict) else '?'})"
        if not isinstance(r, dict):
            problems.append(f"{where}: not a mapping")
            continue
        for key in ("id", "description", "metric", "op", "threshold", "on_fail"):
            if key not in r:
                problems.append(f"{where}: missing {key}")
        if r.get("id") in seen:
            problems.append(f"{where}: duplicate id")
        seen.add(r.get("id"))
        if r.get("op") not in OPS:
            problems.append(f"{where}: op {r.get('op')!r} is not one of {sorted(OPS)}")
        if r.get("on_fail") not in ON_FAIL:
            problems.append(f"{where}: on_fail {r.get('on_fail')!r} must be refer or fail")
        if r.get("when_missing", "refer") not in WHEN_MISSING:
            problems.append(f"{where}: when_missing must be pass, refer or fail")
        for key in ("threshold", "refer_below", "refer_above"):
            if key in r and (isinstance(r[key], bool) or not isinstance(r[key], int | float)):
                problems.append(f"{where}: {key} must be a number")
        if metric_ids is not None and r.get("metric") not in metric_ids:
            problems.append(f"{where}: unknown metric {r.get('metric')!r}")
    if problems:
        raise PolicyError(f"{source}: " + "; ".join(problems))


def evaluate(metrics: list[Metric], policy: dict | None = None) -> PolicyResult:
    policy = policy or load_policy()
    by_id = {m.id: m for m in metrics}
    validate(policy, metric_ids=set(by_id))
    results: list[RuleResult] = []
    for rule in policy["rules"]:
        m = by_id[rule["metric"]]
        req = f"rule requires {rule['op']} {rule['threshold']}"
        if m.value is None:
            outcome = RuleOutcome(rule.get("when_missing", "refer"))
            detail = f"{m.name} cannot be computed ({m.method}); policy says {outcome.value} when missing."
        else:
            passed = OPS[rule["op"]](m.value, rule["threshold"])
            if passed:
                outcome = RuleOutcome.PASS
                # Soft bands: close to the threshold still gets a human look.
                if "refer_below" in rule and m.value < rule["refer_below"]:
                    outcome = RuleOutcome.REFER
                    req += f", referred below {rule['refer_below']}"
                if "refer_above" in rule and m.value > rule["refer_above"]:
                    outcome = RuleOutcome.REFER
                    req += f", referred above {rule['refer_above']}"
            else:
                outcome = RuleOutcome(rule["on_fail"])
            detail = f"{m.name} is {m.display()}; {req}."
        results.append(
            RuleResult(
                rule_id=rule["id"], description=rule["description"], metric_id=m.id,
                outcome=outcome, detail=detail, op=rule["op"], threshold=float(rule["threshold"]),
                refer_below=rule.get("refer_below"), refer_above=rule.get("refer_above"), unit=m.unit,
            )
        )
    if any(r.outcome == RuleOutcome.FAIL for r in results):
        overall = RuleOutcome.FAIL
    elif any(r.outcome == RuleOutcome.REFER for r in results):
        overall = RuleOutcome.REFER
    else:
        overall = RuleOutcome.PASS
    return PolicyResult(
        policy_name=policy["name"], overall=overall, rules=results, policy_sha256=policy.get("_sha256", "")
    )
