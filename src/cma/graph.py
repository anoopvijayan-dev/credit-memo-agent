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

Prompts: `draft_prompt` is the only thing that reaches the model. It carries the metric
and rule tables, the period and a placeholder account name, never a description, a
payer, the account's real name, a case profile field, a file path or the policy's name
(ADR 001, R30); the overall policy outcome is a row of the rule table. A redraft adds one
line naming the metric and rule ids the guard flagged, and nothing else: never the
rejected draft, the guard's reasons or any number it quoted.

Offline lock: a run reads firm.yaml once, at its start (the CLI reads it before making the
model client, and passes the same reading in). Every hosted request in the run is checked
against that value, and the evidence records it.

Checkpoints: the default MemorySaver lives in memory, so a pending review is lost when
the process exits. Pass a durable checkpointer (`sqlite_checkpointer(path)`, needs the
`durable` extra) to keep pending reviews across restarts.
"""

from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import ValidationError

from . import categorise as cat
from . import firm, ingest
from . import guard as grd
from . import metrics as met
from . import policy as pol
from .llm import LLM, FakeLLM, HostedLLM, get_llm
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
PROMPT_ACCOUNT_NAME = "Applicant"
# A redraft prompt's only addition: this label and the metric and rule ids the guard flagged.
# The guard's findings quote the model's own output, so they stay out of the prompt (R30);
# they go to the reviewer.
REDRAFT_LABEL = "Redraft ids:"

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
9. Plain English, no hedging filler, under 400 words.
10. A "Redraft ids:" line means your previous draft failed the check. It lists the metric and rule ids whose figures were misquoted, untagged or not introduced as a limit ("none" if the fault was a number with no tag or a missing heading). Write the memo again from the tables."""


class MemoState(TypedDict, total=False):
    statement_path: str
    policy_path: str | None
    vat_stagger: int | None
    model: str
    run_offline_lock: bool | None  # input: the run's one reading of firm.yaml, if the caller made it
    offline_lock: bool
    egress: str
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


def _shipped_descriptions() -> dict[str, str]:
    """The rule descriptions of the shipped default policy, by rule id."""
    try:
        return {r["id"]: r["description"] for r in pol.load_policy().get("rules", [])
                if isinstance(r, dict) and isinstance(r.get("id"), str) and isinstance(r.get("description"), str)}
    except Exception:  # no shipped text known means none is sent
        return {}


def rules_table(p: PolicyResult, overall_row: bool = False, for_prompt: bool = False) -> str:
    """The policy table. `for_prompt`: what goes to a hosted model, where a rule's free-text
    description is sent only when it is the shipped policy's own text for that rule; a custom
    policy's description could carry case or customer text, so it is replaced by the rule's
    metric and requirement alone (P5, T17 review 1)."""
    shipped = _shipped_descriptions() if for_prompt else {}
    lines = ["| id | rule | metric | requirement | outcome | detail |", "|---|---|---|---|---|---|"]
    if overall_row:
        lines.append(f"| overall | overall outcome | - | - | {p.overall.value} | - |")
    for r in p.rules:
        req = f"{r.op} {_threshold(r.threshold, r.unit)}"
        if r.refer_below is not None:
            req += f"; referred below {_threshold(r.refer_below, r.unit)}"
        if r.refer_above is not None:
            req += f"; referred above {_threshold(r.refer_above, r.unit)}"
        description = r.description
        if for_prompt and shipped.get(r.rule_id) != r.description:
            description = "(custom rule)"
        lines.append(f"| {r.rule_id} | {description} | {r.metric_id} | {req} | {r.outcome.value} | {r.detail} |")
    return "\n".join(lines)


def redraft_ids(g: GuardResult, metrics: list[Metric], policy: PolicyResult) -> list[str]:
    """The metric and rule ids the guard flagged, and only ids that are in the tables, so
    nothing the model wrote (a made-up tag included) goes back into a prompt."""
    known = {m.id for m in metrics} | {r.rule_id for r in policy.rules}
    found = set(g.missing_citations)
    for f in g.unmatched:
        found.update(re.findall(r"\b[MR]\d+\b", f.reason))
    return sorted(found & known, key=lambda i: (i[0], int(i[1:])))


def draft_prompt(statement: Statement, metrics: list[Metric], policy: PolicyResult,
                 redraft: list[str] | None = None) -> str:
    """The user prompt: the tables (the overall outcome as a rule-table row), the period and
    the placeholder name. A redraft adds one line of metric and rule ids, nothing else."""
    user = (
        f"Account: {PROMPT_ACCOUNT_NAME}\n"
        f"Period: {statement.start} to {statement.end}\n\n"
        f"Metrics table:\n{met.as_table(metrics)}\n\n"
        f"Policy table:\n{rules_table(policy, overall_row=True, for_prompt=True)}\n"
    )
    if redraft is not None:
        user += f"\n{REDRAFT_LABEL} {', '.join(redraft) or 'none'}\n"
    return user


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
def _egress(s: MemoState) -> dict:
    """Where the draft's data went. A review drafted by the version before the offline lock
    resumes with no lock fields in its checkpoint; that version had no lock, and its model
    name says whether the draft left the machine."""
    if s.get("egress") is not None and s.get("offline_lock") is not None:
        return {"offline_lock": s["offline_lock"], "destination": s["egress"],
                "recorded": "at run start; every hosted request in the run was checked against this value"}
    model = s.get("model") or "-"
    return {"offline_lock": False, "destination": "offline" if model.startswith("fake:") else model,
            "recorded": "at resume; the draft predates the offline lock"}


def evidence_record(s: MemoState) -> dict:
    """Everything needed to audit the memo later, as plain JSON."""
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "statement": {"path": s["statement_path"], "sha256": s.get("statement_sha256"),
                      "start": str(s["statement"].start), "end": str(s["statement"].end),
                      "transactions": len(s["statement"].transactions)},
        "model": s.get("model"),
        "egress": _egress(s),
        "vat_stagger": s.get("vat_stagger"),
        "ruleset": ruleset_used(s),
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
        lock = s.get("run_offline_lock")
        path = Path(s["statement_path"])
        statement = ingest.load_csv(path)
        return {
            "statement": statement,
            "statement_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "model": f"{llm.name}:{getattr(llm, 'model', '-')}",
            # The run's one reading of firm.yaml: the caller's, or read now. Every hosted
            # request in this run is checked against it, and the evidence records it.
            "offline_lock": lock if lock is not None else firm.load().offline_lock,
            "run_offline_lock": None,
            "egress": "offline" if isinstance(llm, FakeLLM) else f"{llm.name}:{getattr(llm, 'model', '-')}",
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
        g = s.get("guard")
        ids = redraft_ids(g, s["metrics"], s["policy"]) if s.get("guard_feedback") and g else None
        user = draft_prompt(s["statement"], s["metrics"], s["policy"], redraft=ids)
        if isinstance(llm, HostedLLM):
            memo = llm.complete(SYSTEM_PROMPT, user, offline_lock=s["offline_lock"])
        else:
            memo = llm.complete(SYSTEM_PROMPT, user)
        return {"memo": memo, "redrafts": s.get("redrafts", 0)}

    def guard(s: MemoState) -> MemoState:
        g = grd.check(s["memo"], s["metrics"], s["policy"], required_headings=grd.REQUIRED_HEADINGS)
        # The findings stay in `guard` for the reviewer; the model is told only that it failed.
        return {"guard": g, "guard_feedback": "" if g.grounded else "redraft"}

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
