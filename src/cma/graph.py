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

Offline lock: a run reads firm.yaml once, at its start. The CLI reads it before making the
model client and hands that reading to `build`, which uses it for the first run only; any
other run reads firm.yaml in `load`. Nothing in the graph's input can set the run's lock
(T17 review 3). Every hosted request in the run is checked against that value, and the
evidence records it.

Checkpoints: the default MemorySaver lives in memory, so a pending review is lost when
the process exits. Pass a durable checkpointer (`sqlite_checkpointer(path)`, needs the
`durable` extra) to keep pending reviews across restarts.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
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
    run_offline_lock: bool | None  # never read: an input before T17 review 3, kept so old checkpoints load
    offline_lock: bool
    resume_offline_lock: bool | None  # the lock in force when the review was resumed (T17 review 10)
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


def _shipped_rules() -> dict[str, tuple]:
    """The shipped default policy's rules, by id: description, metric, operator, threshold."""
    try:
        def band(v):
            return float(v) if v is not None else None
        return {r["id"]: (r.get("description"), r.get("metric"), r.get("op"), float(r.get("threshold")),
                          band(r.get("refer_below")), band(r.get("refer_above")),
                          str(r.get("on_fail", "")), str(r.get("when_missing", "refer")))
                for r in pol.load_policy().get("rules", []) if isinstance(r, dict) and isinstance(r.get("id"), str)}
    except Exception:  # no shipped rule known means none is sent
        return {}


def _shipped_spellings() -> dict[str, tuple]:
    """Each shipped rule's limits as the shipped policy writes them: a custom rule equal to it
    is sent with these, never with its own spelling of the same number (T17 review 12)."""
    try:
        def spell(v):
            return str(v) if v is not None else None
        return {r["id"]: (spell(r.get("threshold")), spell(r.get("refer_below")), spell(r.get("refer_above")))
                for r in pol.load_policy().get("rules", []) if isinstance(r, dict) and isinstance(r.get("id"), str)}
    except Exception:
        return {}


def _shipped_detail(r, spelt: tuple) -> str:
    """A shipped rule's detail for the prompt, its limits written as the shipped policy writes
    them; a figure that cannot be computed names no limit."""
    head, sep, tail = r.detail.partition("; rule requires ")
    if not sep:
        return r.detail
    req = f"rule requires {r.op} {spelt[0]}"
    if "referred below" in tail:
        req += f", referred below {spelt[1]}"
    if "referred above" in tail:
        req += f", referred above {spelt[2]}"
    return f"{head}; {req}."


def _is_shipped(r, shipped: dict[str, tuple]) -> bool:
    """Whether a policy result's rule is the shipped policy's own rule, whole: its id, text,
    metric and requirement, referral bands and what a breach or a missing figure means
    included. A custom rule that reuses a shipped id is custom (T17 reviews 6, 7 and 8)."""
    want = shipped.get(r.rule_id)

    def num(v):
        return float(v) if v is not None else None
    return want is not None and want == (r.description, r.metric_id, r.op, num(r.threshold),
                                         num(r.refer_below), num(r.refer_above), r.on_fail, r.when_missing)


def rules_table(p: PolicyResult, overall_row: bool = False, for_prompt: bool = False) -> str:
    """The policy table. `for_prompt`: what goes to a hosted model, where a rule's free-text
    description is sent only when it is the shipped policy's own text for that rule; a custom
    policy's description could carry case or customer text, so it is replaced by the rule's
    metric and requirement alone (P5, T17 review 1). A rule id is free text in a custom
    policy too, whatever its spelling (R4242 can be a sentinel), so only an id the shipped
    policy itself uses is sent; any other is replaced (T17 reviews 2 and 3)."""
    shipped = _shipped_rules() if for_prompt else {}
    spellings = _shipped_spellings() if for_prompt else {}
    lines = ["| id | rule | metric | requirement | outcome | detail |", "|---|---|---|---|---|---|"]
    if overall_row:
        lines.append(f"| overall | overall outcome | - | - | {p.overall.value} | - |")
    for r in p.rules:
        req = f"{r.op} {_threshold(r.threshold, r.unit)}"
        if r.refer_below is not None:
            req += f"; referred below {_threshold(r.refer_below, r.unit)}"
        if r.refer_above is not None:
            req += f"; referred above {_threshold(r.refer_above, r.unit)}"
        rule_id, description = r.rule_id, r.description
        detail = r.detail
        if for_prompt and not _is_shipped(r, shipped):
            # A custom rule's limits and detail are its own text too, and a number in them could
            # come from the case: only its metric and outcome are sent (T17 review 10).
            rule_id, description, req, detail = "(custom id)", "(custom rule)", "(custom requirement)", "-"
        elif for_prompt and r.rule_id in spellings:
            detail = _shipped_detail(r, spellings[r.rule_id])
        lines.append(f"| {rule_id} | {description} | {r.metric_id} | {req} | {r.outcome.value} | {detail} |")
    return "\n".join(lines)


def redraft_ids(g: GuardResult, metrics: list[Metric], policy: PolicyResult) -> list[str]:
    """The metric and rule ids the guard flagged, and only ids the prompt's tables show, so
    nothing the model wrote (a made-up tag included) goes back into a prompt, nor a custom
    rule id the rule table masked (T17 review 3)."""
    shipped = _shipped_rules()
    known = {m.id for m in metrics} | {r.rule_id for r in policy.rules if _is_shipped(r, shipped)}
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
        out = {"offline_lock": s["offline_lock"], "destination": s["egress"],
               "recorded": "at run start; every hosted request in the run was checked against this value"}
        if s.get("resume_offline_lock") is not None:
            # The lock in force when the review was resumed and finalised, which may differ
            # from the draft's (T17 review 10). An added field; the others are unchanged.
            out["resume_offline_lock"] = s["resume_offline_lock"]
        return out
    model = s.get("model") or "-"
    out = {"offline_lock": False, "destination": "offline" if model.startswith("fake:") else model,
           "recorded": "at resume; the draft predates the offline lock"}
    if s.get("resume_offline_lock") is not None:
        # The lock in force as the review resumed, recorded at finalisation, for a draft from
        # before the lock too (T17 review 11).
        out["resume_offline_lock"] = s["resume_offline_lock"]
    return out


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


def build(llm: LLM | None = None, checkpointer=None, firm_config: firm.FirmConfig | None = None):
    """`firm_config`: the caller's reading of firm.yaml, made just before this build (the CLI
    makes it before the model client). It is the first run's reading and is used once; every
    later run of this graph reads firm.yaml at its own start."""
    llm = llm or get_llm()
    handed = [firm_config] if firm_config is not None else []

    def load(s: MemoState, config: RunnableConfig) -> MemoState:
        reading = _run_reading_of(config) or (handed.pop() if handed else firm.load())
        path = Path(s["statement_path"])
        statement = ingest.load_csv(path)
        return {
            "statement": statement,
            "statement_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "model": f"{llm.name}:{getattr(llm, 'model', '-')}",
            # The run's one reading of firm.yaml: the one handed to build() for its first run,
            # or read now; never the graph's input. Every hosted request in this run is
            # checked against it, and the evidence records it.
            "offline_lock": reading.offline_lock is not False,
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

    def draft(s: MemoState, config: RunnableConfig) -> MemoState:
        g = s.get("guard")
        ids = redraft_ids(g, s["metrics"], s["policy"]) if s.get("guard_feedback") and g else None
        user = draft_prompt(s["statement"], s["metrics"], s["policy"], redraft=ids)
        if isinstance(llm, HostedLLM):
            # A resume's own lock, which its reading at resume found (or the draft's, if that was
            # on), decides its hosted requests, as its evidence records (T17 review 13).
            resumed = ((config or {}).get("configurable") or {}).get(_RESUME_KEY)
            lock = s["offline_lock"] if resumed is None else resumed
            memo = llm.complete(SYSTEM_PROMPT, user, offline_lock=lock, reading=_run_reading_of(config))
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

    def finalise(s: MemoState, config: RunnableConfig) -> MemoState:
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
        resumed = ((config or {}).get("configurable") or {}).get(_RESUME_KEY)
        return {"final_memo": s["memo"] + stamp + "\n\n" + appendix(s),
                "resume_offline_lock": resumed}

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
    compiled = g.compile(checkpointer=checkpointer or default_checkpointer())

    def run_reading(graph_input, config):
        """The run's one reading of firm.yaml, taken as it starts: the one handed to build() for
        its first run, or read now; it travels with the run's own config, never shared by
        thread, so concurrent runs keep their own (T17 review 5). A resumed run is under the
        lock if its first reading was, or if the lock is on now: by this graph's latest reading,
        or firm.yaml read now (T17 review 5)."""
        if graph_input is None or isinstance(graph_input, Command):
            try:
                stored = compiled_get_state(config).values.get("offline_lock")
            except Exception:
                stored = None
            # A resume is a run of its own and reads firm.yaml as it resumes, never reusing the
            # draft's reading or one handed to build(), even with firm.yaml unchanged, and for a
            # draft from before the lock (stored None) too (R29 revision 32, T17 reviews 11, 12).
            now = _read_or_locked()
            if now is not None and not firm.take_for_run(now):
                now = None
            # The resume's reading travels with it, so a hosted request it makes (a draft or a
            # redraft, for a review paused before drafting) is checked against it (T17 review 13).
            return now, stored is True or now is None or now.offline_lock is not False
        reading = handed.pop() if handed else None
        if reading is None or not firm.take_for_run(reading):
            reading = firm.load()  # a reading drives one run only (T17 review 6)
            firm.take_for_run(reading)
        return reading, reading.offline_lock is not False

    compiled_get_state = compiled.get_state
    _untraced_under_lock(compiled, run_reading)
    return compiled


_READING_KEY = "__cma_reading"  # LangGraph never saves a configurable key that starts with "__"
_RESUME_KEY = "__cma_resume_lock"


def _without_network_tracers(callbacks):
    """The run's callbacks without any LangSmith tracer: a list, or a callback manager's own
    and inherited handlers."""
    from langchain_core.callbacks import BaseCallbackManager
    from langchain_core.tracers.langchain import LangChainTracer

    def keep(handlers):
        return [h for h in (handlers or []) if not isinstance(h, LangChainTracer)]

    if isinstance(callbacks, BaseCallbackManager):
        manager = callbacks.copy()
        manager.handlers = keep(manager.handlers)
        manager.inheritable_handlers = keep(manager.inheritable_handlers)
        return manager
    return keep(callbacks) if callbacks else callbacks


def _untraced_inherited_config():
    """Under the lock, the runnable context the graph is called from loses its LangSmith
    tracers for the run: LangGraph merges a caller's inherited callbacks with the run's own,
    so a tracer inherited from the calling runnable would trace the run (R29, T17 review 13).
    Returns the token to restore it, or None when there is nothing to take out."""
    from langchain_core.runnables.config import var_child_runnable_config

    inherited = var_child_runnable_config.get()
    callbacks = (inherited or {}).get("callbacks")
    if not callbacks:
        return None
    return var_child_runnable_config.set({**inherited, "callbacks": _without_network_tracers(callbacks)})


def _reset_inherited_config(token) -> None:
    from langchain_core.runnables.config import var_child_runnable_config

    # A stream finished in another context (closed by the garbage collector): the context it
    # changed is gone with it.
    with contextlib.suppress(ValueError):
        var_child_runnable_config.reset(token)


def _run_reading_of(config) -> firm.FirmConfig | None:
    return ((config or {}).get("configurable") or {}).get(_READING_KEY)


def _read_or_locked() -> firm.FirmConfig | None:
    try:
        return firm.load()
    except Exception:
        return None  # a firm.yaml that cannot be read counts as the lock


# Set while a run started through one of the wrappers below is going: LangGraph's invoke
# runs through its own stream, which must not take a second reading of firm.yaml.
_IN_RUN: ContextVar[tuple | None] = ContextVar("cma_in_run", default=None)
# Set by invoke and ainvoke for the one call LangGraph makes to its own stream, and taken by
# that call: any later call on the same graph object, from a node or a callback, is a run of
# its own, with its own reading and its own tracer filtering (T17 review 11).
_DELEGATE: ContextVar[int | None] = ContextVar("cma_delegate", default=None)


def _untraced_under_lock(graph, run_reading) -> None:
    """Every way of running the graph (invoke and stream, and their async forms) takes the
    run's one reading of firm.yaml as it starts (run_reading keeps it for the run's steps by
    thread), and under the offline lock runs with
    LangSmith tracing off, whatever the environment says: a trace would send statement data
    off the machine (R29, T17 review 4)."""
    import langsmith

    real_invoke, real_stream = graph.invoke, graph.stream
    real_ainvoke, real_astream = graph.ainvoke, graph.astream

    def config_of(a, k):
        return k.get("config", a[1] if len(a) > 1 else None)

    def start(a, k, mark: bool = True) -> tuple[bool, object, tuple, dict]:
        """Whether this run is under the lock, the token to end it, and the call's arguments
        with the run's reading in its config; a call nested in a run already going (invoke's
        own stream) keeps that run's reading. Only invoke and ainvoke mark a run as going,
        since they run to the end before returning: a stream suspended between items marks
        nothing, so no other run started meanwhile takes its decision (T17 review 8)."""
        outer = _IN_RUN.get()
        if outer is not None and outer[0] == id(graph) and _DELEGATE.get() == id(graph):
            # Only invoke's own call to its stream keeps the run's decision, once; a separate
            # run inside it, on this graph or another, takes its own reading and its own tracer
            # filtering (T17 reviews 10 and 11).
            _DELEGATE.set(None)
            return outer[1], None, a, k
        graph_input = a[0] if a else k.get("input")
        reading, locked = run_reading(graph_input, config_of(a, k))
        resumed = graph_input is None or isinstance(graph_input, Command)
        if reading is not None or locked or resumed:
            config = dict(config_of(a, k) or {})
            if reading is not None:
                config["configurable"] = {**(config.get("configurable") or {}), _READING_KEY: reading}
            if resumed:
                # The lock in force for this resume, which the evidence records beside the
                # draft's (T17 review 10).
                config["configurable"] = {**(config.get("configurable") or {}), _RESUME_KEY: locked}
            if locked:
                # A LangSmith tracer handed in with the run's callbacks would send the run off
                # the machine whatever the environment says: under the lock it is taken out
                # (R29, T17 review 9). Local callbacks stay.
                config["callbacks"] = _without_network_tracers(config.get("callbacks"))
            if "config" in k or len(a) < 2:
                k = {**k, "config": config}
            else:
                a = (a[0], config, *a[2:])
            if reading is not None:
                firm.begin_run(reading)
        inherited = _untraced_inherited_config() if locked else None
        marks = (_IN_RUN.set((id(graph), locked)), _DELEGATE.set(id(graph))) if mark else None
        return locked, (marks, reading, inherited), a, k

    def end(token) -> None:
        if token is not None:
            if token[2] is not None:
                _reset_inherited_config(token[2])
            if token[0] is not None:
                _DELEGATE.reset(token[0][1])
                _IN_RUN.reset(token[0][0])
            if token[1] is not None:
                firm.end_run(token[1])

    def invoke(*a, **k):
        locked, token, a, k = start(a, k)
        try:
            if locked:
                with langsmith.tracing_context(enabled=False):
                    return real_invoke(*a, **k)
            return real_invoke(*a, **k)
        finally:
            end(token)

    def stream(*a, **k):
        def run():
            locked, token, args, kwargs = start(a, k, mark=False)
            try:
                if locked:
                    with langsmith.tracing_context(enabled=False):
                        yield from real_stream(*args, **kwargs)
                else:
                    yield from real_stream(*args, **kwargs)
            finally:
                end(token)
        return run()

    async def ainvoke(*a, **k):
        locked, token, a, k = start(a, k)
        try:
            if locked:
                with langsmith.tracing_context(enabled=False):
                    return await real_ainvoke(*a, **k)
            return await real_ainvoke(*a, **k)
        finally:
            end(token)

    def astream(*a, **k):
        async def run():
            locked, token, args, kwargs = start(a, k, mark=False)
            try:
                if locked:
                    with langsmith.tracing_context(enabled=False):
                        async for item in real_astream(*args, **kwargs):
                            yield item
                else:
                    async for item in real_astream(*args, **kwargs):
                        yield item
            finally:
                end(token)
        return run()

    graph.invoke, graph.stream, graph.ainvoke, graph.astream = invoke, stream, ainvoke, astream
