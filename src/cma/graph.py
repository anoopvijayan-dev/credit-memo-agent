"""The memo graph.

    load -> categorise -> metrics -> policy -> draft -> guard -> review -> finalise
                                               ^                 |
                                               +---- redraft ----+  (if ungrounded, max 2)

The model writes prose in `draft`. It never computes anything: it is handed the
metrics table and the policy result and told to quote only from them, with a [Mn]
tag on every figure. `guard` checks that it did. `review` stops the graph and waits
for a person; nothing is final until they say so.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import ValidationError

from . import categorise as cat
from . import guard as grd
from . import ingest
from . import metrics as met
from . import policy as pol
from .llm import LLM, get_llm
from .models import GuardResult, Metric, PolicyResult, ReviewDecision, Statement

MAX_REDRAFTS = 2

# Our pydantic models travel through the checkpointer between the review interrupt and
# the resume, so they must be on the allow-list.
CHECKPOINT_TYPES = [
    ("cma.models", name)
    for name in (
        "Statement", "Transaction", "Category", "Metric", "PolicyResult", "RuleResult", "RuleOutcome",
        "GuardResult", "GuardFinding", "ReviewDecision",
    )
]


def default_checkpointer() -> MemorySaver:
    return MemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES))

SYSTEM_PROMPT = """You are a credit analyst drafting an internal memo for a UK SME working-capital facility.

Rules you must follow:
1. Quote numbers ONLY from the metrics table you are given. Do not compute, estimate, or recall any figure.
2. Every number you quote must be followed by its citation tag in square brackets, e.g. "£22,400 [M3]".
3. Use exactly these headings: Business overview, Revenue, Cash position, Obligations and conduct, Recommendation.
4. The Recommendation must state the policy outcome and which rules were referred or failed. You do not decide; the analyst does.
5. Plain English, no hedging filler, under 400 words."""


class MemoState(TypedDict, total=False):
    statement_path: str
    policy_path: str | None
    statement: Statement
    metrics: list[Metric]
    policy: PolicyResult
    memo: str
    guard: GuardResult
    guard_feedback: str
    redrafts: int
    review: ReviewDecision
    final_memo: str


def _review_problem(decision, grounded: bool) -> str | None:
    """Why this decision cannot be accepted, or None if it can."""
    if not isinstance(decision, dict):
        return "answer with {'approved': bool, 'reviewer': '<name>', 'notes': '...'}"
    try:
        review = ReviewDecision(**decision)
    except ValidationError as e:
        fields = ", ".join(str(err["loc"][0]) for err in e.errors())
        return f"missing or invalid: {fields}; an approval must name the reviewer"
    if review.approved and not grounded and not (review.override_grounding and review.notes.strip()):
        return (
            "this memo failed the grounding check; approving it needs override_grounding=True "
            "and notes explaining why"
        )
    return None


def build(llm: LLM | None = None, checkpointer=None):
    llm = llm or get_llm()

    def load(s: MemoState) -> MemoState:
        return {"statement": ingest.load_csv(Path(s["statement_path"]))}

    def categorise(s: MemoState) -> MemoState:
        return {"statement": cat.categorise(s["statement"])}

    def metrics(s: MemoState) -> MemoState:
        return {"metrics": met.compute(s["statement"])}

    def policy(s: MemoState) -> MemoState:
        p = pol.load_policy(Path(s["policy_path"])) if s.get("policy_path") else None
        return {"policy": pol.evaluate(s["metrics"], p)}

    def draft(s: MemoState) -> MemoState:
        table = met.as_table(s["metrics"])
        rules = "\n".join(
            f"- {r.rule_id} {r.description}: {r.outcome.value} ({r.detail})" for r in s["policy"].rules
        )
        user = (
            f"Account: {s['statement'].account_name}\n"
            f"Period: {s['statement'].start} to {s['statement'].end}\n\n"
            f"Metrics table:\n{table}\n\n"
            f"Policy: {s['policy'].policy_name}\nOverall policy outcome: {s['policy'].overall.value}\n{rules}\n"
        )
        if s.get("guard_feedback"):
            user += f"\nYour previous draft was rejected by the grounding check:\n{s['guard_feedback']}\nRewrite it using only figures from the table, each with its tag.\n"
        return {"memo": llm.complete(SYSTEM_PROMPT, user), "redrafts": s.get("redrafts", 0)}

    def guard(s: MemoState) -> MemoState:
        g = grd.check(s["memo"], s["metrics"])
        feedback = ""
        if not g.grounded:
            parts = [f"- {f.number_text}: {f.reason}" for f in g.unmatched]
            parts += [f"- {mid} quoted without its tag" for mid in g.missing_citations]
            feedback = "\n".join(parts)
        return {"guard": g, "guard_feedback": feedback}

    def route_after_guard(s: MemoState) -> str:
        if s["guard"].grounded or s.get("redrafts", 0) >= MAX_REDRAFTS:
            return "review"
        return "redraft"

    def redraft(s: MemoState) -> MemoState:
        return {"redrafts": s.get("redrafts", 0) + 1}

    def review(s: MemoState) -> MemoState:
        """Stop for a named human. An invalid decision is not an error: ask again, saying why.

        Each refusal is a fresh interrupt, so LangGraph replays earlier answers in order
        and the reviewer can always correct theirs.
        """
        payload = {
            "memo": s["memo"],
            "policy_outcome": s["policy"].overall.value,
            "guard_grounded": s["guard"].grounded,
            "guard_unmatched": [f.number_text or f.reason for f in s["guard"].unmatched],
            "guard_missing_citations": s["guard"].missing_citations,
            "question": "Approve this memo for the credit file?",
            "error": None,
        }
        while True:
            decision = interrupt(payload)
            problem = _review_problem(decision, s["guard"].grounded)
            if problem is None:
                return {"review": ReviewDecision(**decision)}
            payload = {**payload, "error": problem}

    def finalise(s: MemoState) -> MemoState:
        r, g = s["review"], s["guard"]
        if g.grounded:
            grounding = "passed"
        elif r.approved:
            grounding = "overridden by reviewer"
        else:
            grounding = "failed"
        stamp = (
            f"\n\n---\n{'APPROVED' if r.approved else 'NOT APPROVED'} by {r.reviewer}"
            f" at {r.decided_at.strftime('%Y-%m-%d %H:%M UTC')}."
            + (f" Notes: {r.notes}" if r.notes else "")
            + f"\nGrounding check: {grounding} ({g.numbers_checked} numbers checked,"
            f" {len(g.unmatched)} unmatched, {len(g.missing_citations)} missing citations)."
        )
        return {"final_memo": s["memo"] + stamp}

    g = StateGraph(MemoState)
    for name, fn in [
        ("load", load), ("categorise", categorise), ("metrics", metrics), ("policy", policy),
        ("draft", draft), ("guard", guard), ("redraft", redraft), ("review", review), ("finalise", finalise),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "load")
    g.add_edge("load", "categorise")
    g.add_edge("categorise", "metrics")
    g.add_edge("metrics", "policy")
    g.add_edge("policy", "draft")
    g.add_edge("draft", "guard")
    g.add_conditional_edges("guard", route_after_guard, {"review": "review", "redraft": "redraft"})
    g.add_edge("redraft", "draft")
    g.add_edge("review", "finalise")
    g.add_edge("finalise", END)
    return g.compile(checkpointer=checkpointer or default_checkpointer())
