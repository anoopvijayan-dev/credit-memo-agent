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
    source = str(path or f"cma/policies/{DEFAULT_POLICY_NAME}")
    try:
        text = Path(path).read_text() if path else default_policy_text()
        policy = yaml.safe_load(text)
    except FileNotFoundError as e:
        raise PolicyError(f"{source}: no such file") from e
    except (OSError, UnicodeDecodeError) as e:  # a directory, no permission, not UTF-8 (T04 review 21)
        raise PolicyError(f"{source}: cannot be read ({e.__class__.__name__})") from e
    except yaml.YAMLError as e:
        raise PolicyError(f"{source}: not valid YAML ({e.__class__.__name__})") from e
    validate(policy, source=source)
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


class OutcomeUnknown(PolicyError):
    """A stored policy result cannot say a rule's outcome for a changed figure."""


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def _band(r: RuleResult, v: float | None) -> tuple:
    """What decides a rule's outcome for a value: missing, failing, or passing with which
    referral bands it falls in."""
    if v is None:
        return ("missing",)
    if not OPS[r.op](v, r.threshold):
        return ("fail",)
    return ("pass", r.refer_below is not None and v < r.refer_below, r.refer_above is not None and v > r.refer_above)


def reevaluate(stored: PolicyResult, before: list[Metric], now: list[Metric],
               policy_path: Path | None = None) -> PolicyResult:
    """The stored result for figures that changed. Where the stored result cannot say a
    rule's outcome, the policy (the file at `policy_path`, or the packaged default) is read
    once and used only if it is the policy the result came from, by its hash; it is then
    evaluated in full on today's figures. Otherwise OutcomeUnknown, naming why (T04 review
    19). A review whose stored result settles every rule never reopens the policy."""
    try:
        return _from_stored(stored, before, now)
    except OutcomeUnknown as e:
        try:
            policy = load_policy(policy_path)
        except PolicyError as pe:
            raise OutcomeUnknown(f"{e}, and the policy it came from cannot be read ({pe})") from None
        if not stored.policy_sha256 or policy["_sha256"] != stored.policy_sha256:
            raise OutcomeUnknown(f"{e}, and the policy file is not the one the review was drafted with") from None
        return evaluate(now, policy)


def _from_stored(stored: PolicyResult, before: list[Metric], now: list[Metric]) -> PolicyResult:
    """The stored result for figures that changed, without reopening the policy: each rule
    keeps its operator, threshold and referral bands, so its outcome for a new value is
    known when the value passes (with or without a referral), or fails a rule it already
    failed (its fail action is the stored outcome). A rule whose figure is now missing, or
    newly fails, needs its when-missing or fail action, which the stored result does not
    hold: OutcomeUnknown (T04 review 18)."""
    was_by, now_by = {m.id: m for m in before}, {m.id: m for m in now}
    rules: list[RuleResult] = []
    for r in stored.rules:
        old, new = was_by.get(r.metric_id), now_by.get(r.metric_id)
        if new is None:
            raise OutcomeUnknown(f"{r.metric_id}, which rule {r.rule_id} reads, is no longer computed")
        if old is not None and old.value == new.value:
            rules.append(r)
            continue
        if r.op not in OPS or r.threshold is None:
            # A rule stored without its comparison (an earlier release's result): only the
            # policy can say its outcome (T04 review 22).
            raise OutcomeUnknown(f"{r.metric_id}, which rule {r.rule_id} reads, changed and its stored "
                                 "result does not hold the rule")
        was, will = _band(r, old.value if old else None), _band(r, new.value)
        if will[0] == "missing" or (will[0] == "fail" and was[0] != "fail"):
            raise OutcomeUnknown(f"{r.metric_id}, which rule {r.rule_id} reads, now "
                                 f"{'cannot be computed' if will[0] == 'missing' else 'fails it'}")
        outcome = r.outcome if will[0] == "fail" else (
            RuleOutcome.REFER if will[1] or will[2] else RuleOutcome.PASS)
        prefix = f"{old.name} is {old.display()}; " if old is not None and old.value is not None else None
        if was == will and prefix and r.detail.startswith(prefix):
            req = r.detail[len(prefix):].removesuffix(".")
        else:
            req = f"rule requires {r.op} {_num(r.threshold)}"
            if will[0] == "pass" and will[1]:
                req += f", referred below {_num(r.refer_below)}"
            if will[0] == "pass" and will[2]:
                req += f", referred above {_num(r.refer_above)}"
        rules.append(r.model_copy(update={"outcome": outcome, "detail": f"{new.name} is {new.display()}; {req}."}))
    overall = (RuleOutcome.FAIL if any(r.outcome == RuleOutcome.FAIL for r in rules)
               else RuleOutcome.REFER if any(r.outcome == RuleOutcome.REFER for r in rules) else RuleOutcome.PASS)
    return stored.model_copy(update={"rules": rules, "overall": overall})


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
