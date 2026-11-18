"""The memo graph.

    load -> categorise -> metrics -> policy -> draft -> guard -> review -> finalise
                                               ^                 |
                                               +---- redraft ----+  (if ungrounded, max 2)

The model writes prose in `draft`. It never computes anything: it is handed the
metrics table and the policy table and told to quote only from them, each figure
followed by its [Mn] or [Rn] tag. `guard` checks that it did. `review` stops the graph
and waits for a named person; nothing is final until they say so. `finalise` stamps
the decision and appends the figures, rules, model and file hashes the memo rests on,
so the written memo can answer "where did this figure come from" on its own.

Checkpoints: the default MemorySaver lives in memory, so a pending review is lost when
the process exits. Pass a durable checkpointer (`sqlite_checkpointer(path)`, needs the
`durable` extra) to keep pending reviews across restarts.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
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
from .models import (
    EVIDENCE_SCHEMA_VERSION,
    GuardResult,
    Metric,
    PolicyResult,
    ReviewDecision,
    Statement,
    format_value,
)

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


def _serde() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)


def default_checkpointer() -> MemorySaver:
    return MemorySaver(serde=_serde())


@contextmanager
def sqlite_checkpointer(path: Path):
    """A checkpointer that survives restarts, so a memo waiting for review is not lost."""
    try:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as e:  # pragma: no cover - depends on the optional extra
        raise RuntimeError("durable checkpoints need: pip install 'credit-memo-agent[durable]'") from e
    conn = sqlite3.connect(str(path), check_same_thread=False)
    try:
        yield SqliteSaver(conn, serde=_serde())
    finally:
        conn.close()


REVIEW_KEYS = {"approved", "reviewer", "notes", "override_grounding", "mode"}

SYSTEM_PROMPT = """You are a credit analyst drafting an internal memo for a UK SME working-capital facility.

Rules you must follow. An automatic check enforces 1-7 and rejects the draft if any figure breaks them.
1. Quote numbers ONLY from the metrics table or the policy table you are given. Do not compute, estimate, round differently, or recall any figure.
2. Write each figure in digits exactly as the table shows it, followed immediately by its tag: "£22,400 [M3]", "1.42x [M10]", "-12.3% [M4]", "25 [M6] days". A policy threshold takes its rule's tag: "at least 1.25x [R2]".
3. Keep minus signs. A negative figure is written with its minus, as the table shows it ("-£3,200 [M5]"), never as a positive with words like "deficit".
4. Write no other number, in digits or in words (not "one", "half", "doubled"). Refer to rules by id ("R2 and R5 were referred"), not by counting them. Write dates as YYYY-MM-DD. Use "-" bullets, never numbered lists.
5. A metric shown as n/a could not be computed. Say so with its tag and give the reason from the method column: "Debt service cover is n/a [M10]: there are no loan repayments."
6. Use exactly these headings, as "## " markdown headings: Business overview, Revenue, Cash position, Obligations and conduct, Recommendation.
7. A policy limit takes its rule tag and is introduced as a limit: "above the 1.25x [R2] minimum", never "cover is 1.25x [R2]".
8. The Recommendation states the policy outcome and names each rule that was referred or failed. You do not decide; the analyst does.
9. Plain English, no hedging filler, under 400 words."""


class MemoState(TypedDict, total=False):
    statement_path: str
    policy_path: str | None
    vat_stagger: int | None
    model: str
    statement_sha256: str
    statement: Statement
    ruleset: dict  # {"id", "sha256"} of the categorisation table used (CMA_RULESET)
    metrics: list[Metric]
    policy: PolicyResult
    memo: str
    guard: GuardResult
    guard_feedback: str
    redrafts: int
    review: ReviewDecision | None
    final_memo: str | None


def _review_problem(decision, grounded: bool) -> str | None:
    """Why this decision cannot be accepted, or None if it can."""
    if not isinstance(decision, dict):
        return "answer with {'approved': bool, 'reviewer': '<name>', 'notes': '...'}"
    unknown = sorted(set(decision) - REVIEW_KEYS)
    if unknown:
        return f"unexpected field(s): {', '.join(unknown)}; the time of the decision is recorded by the system"
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


def _threshold(value: float | None, unit: str) -> str:
    return "" if value is None else format_value(value, unit)


def rules_table(p: PolicyResult) -> str:
    lines = ["| id | rule | metric | requirement | outcome | detail |", "|---|---|---|---|---|---|"]
    for r in p.rules:
        req = f"{r.op} {_threshold(r.threshold, r.unit)}"
        if r.refer_below is not None:
            req += f"; referred below {_threshold(r.refer_below, r.unit)}"
        if r.refer_above is not None:
            req += f"; referred above {_threshold(r.refer_above, r.unit)}"
        lines.append(f"| {r.rule_id} | {r.description} | {r.metric_id} | {req} | {r.outcome.value} | {r.detail} |")
    return "\n".join(lines)


def appendix(s: MemoState) -> str:
    """What the memo rests on, printed under it, so the file stands on its own."""
    st, p = s["statement"], s["policy"]
    lines = [
        "## Appendix: the figures behind this memo",
        "",
        f"Statement: {Path(s['statement_path']).name}, {len(st.transactions)} transactions, "
        f"{st.start} to {st.end}, sha256 {s.get('statement_sha256', '')[:16]}",
        f"Model: {s.get('model', '-')}. Policy: {p.policy_name}, sha256 {p.policy_sha256[:16]}, "
        f"overall {p.overall.value}. VAT stagger: {s.get('vat_stagger') or 'not given'}.",
        "Evidence: every transaction id behind each figure is in the .evidence.json file written beside this memo.",
        "",
        "| id | metric | value | method | lines |",
        "|---|---|---|---|---|",
    ]
    lines += [f"| {m.id} | {m.name} | {m.display()} | {m.method} | {len(m.evidence)} |" for m in s["metrics"]]
    lines += ["", rules_table(p)]
    return "\n".join(lines)


def ruleset_used(s: MemoState) -> dict:
    """The categorisation table the memo's figures came from. A review drafted before the
    table moved to data (a pending checkpoint from an older version) has none in its state;
    its lines were categorised by the table now packaged as v1, frozen and compared with
    the old code (tests/test_rules.py, P7), so it is named as v1 and marked as inferred,
    never null. That lookup reads the packaged table and can raise RulesetError, so the
    CLI calls this before a decision is recorded, not after."""
    if s.get("ruleset"):
        return s["ruleset"]
    v1 = cat.load_ruleset("v1")
    return {"id": v1.id, "sha256": v1.sha256, "inferred": "pre-ruleset checkpoint"}


def line_rule_ids(s: MemoState) -> list[str | None]:
    """Each line's rule id. A checkpoint from before provenance holds none; its lines were
    categorised by the table that is now v1 (P7), so v1 is replayed on a copy and its rule
    id taken for each line whose saved category it reproduces. A line it does not
    reproduce keeps None rather than a rule that did not set it."""
    txns = s["statement"].transactions
    recorded = s.get("ruleset")
    if (recorded and not recorded.get("inferred")) or all(t.rule_id for t in txns):
        return [t.rule_id for t in txns]
    replay = cat.categorise(s["statement"].model_copy(deep=True), cat.load_ruleset("v1")).transactions
    return [t.rule_id or (r.rule_id if r.category == t.category else None) for t, r in zip(txns, replay, strict=True)]


def evidence_record(s: MemoState) -> dict:
    """Everything needed to audit the memo later, as plain JSON."""
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "statement": {"path": s["statement_path"], "sha256": s.get("statement_sha256"),
                      "start": str(s["statement"].start), "end": str(s["statement"].end),
                      "transactions": len(s["statement"].transactions)},
        "model": s.get("model"),
        "vat_stagger": s.get("vat_stagger"),
        "ruleset": ruleset_used(s),
        # Each line's category and the rule that set it: ids only, no descriptions.
        "lines": [{"id": t.id, "category": t.category.value if t.category else None, "rule_id": rule_id,
                   "reverses": t.reverses} for t, rule_id in zip(s["statement"].transactions, line_rule_ids(s),
                                                                 strict=True)],
        "metrics": [m.model_dump() for m in s["metrics"]],
        "policy": s["policy"].model_dump(mode="json"),
        "guard": {**s["guard"].model_dump(), "grounded": s["guard"].grounded},
        "redrafts": s.get("redrafts", 0),
        "review": s["review"].model_dump(mode="json") if s.get("review") else None,
        "memo": s.get("memo"),
    }


def build(llm: LLM | None = None, checkpointer=None):
    llm = llm or get_llm()

    def load(s: MemoState) -> MemoState:
        path = Path(s["statement_path"])
        statement = ingest.load_csv(path)
        return {
            "statement": statement,
            "statement_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "model": f"{llm.name}:{getattr(llm, 'model', '-')}",
            # A run starts clean, even on a thread that has run before.
            "redrafts": 0,
            "guard_feedback": "",
            "review": None,
            "final_memo": None,
        }

    def categorise(s: MemoState) -> MemoState:
        rules = cat.active_ruleset()
        return {"statement": cat.categorise(s["statement"], rules),
                "ruleset": {"id": rules.id, "sha256": rules.sha256}}

    def metrics(s: MemoState) -> MemoState:
        return {"metrics": met.compute(s["statement"], vat_stagger=s.get("vat_stagger"))}

    def policy(s: MemoState) -> MemoState:
        p = pol.load_policy(Path(s["policy_path"])) if s.get("policy_path") else None
        return {"policy": pol.evaluate(s["metrics"], p)}

    def draft(s: MemoState) -> MemoState:
        table = met.as_table(s["metrics"])
        user = (
            f"Account: {s['statement'].account_name}\n"
            f"Period: {s['statement'].start} to {s['statement'].end}\n\n"
            f"Metrics table:\n{table}\n\n"
            f"Policy: {s['policy'].policy_name}\nOverall policy outcome: {s['policy'].overall.value}\n"
            f"Policy table:\n{rules_table(s['policy'])}\n"
        )
        if s.get("guard_feedback"):
            user += f"\nYour previous draft was rejected by the grounding check:\n{s['guard_feedback']}\nRewrite it using only figures from the table, each with its tag.\n"
        return {"memo": llm.complete(SYSTEM_PROMPT, user), "redrafts": s.get("redrafts", 0)}

    def guard(s: MemoState) -> MemoState:
        g = grd.check(s["memo"], s["metrics"], s["policy"], required_headings=grd.REQUIRED_HEADINGS)
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
        how = " (automated, no interactive review)" if r.mode == "automated" else ""
        stamp = (
            f"\n\n---\n{'APPROVED' if r.approved else 'NOT APPROVED'} by {r.reviewer}{how}"
            f" at {r.decided_at.strftime('%Y-%m-%d %H:%M UTC')}."
            + (f" Notes: {r.notes}" if r.notes else "")
            + f"\nGrounding check: {grounding} ({g.numbers_checked} numbers checked,"
            f" {len(g.unmatched)} unmatched, {len(g.missing_citations)} missing citations)."
        )
        return {"final_memo": s["memo"] + stamp + "\n\n" + appendix(s)}

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
