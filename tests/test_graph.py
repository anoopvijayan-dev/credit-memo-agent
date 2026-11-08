"""End to end with the offline model: draft, guard, stop for a human, resume."""

from datetime import date

from langgraph.types import Command

from cma import synth
from cma.graph import build
from cma.llm import FakeLLM


def _statement(tmp_path, profile="stretched"):
    rows, truth = synth.generate("cafe", profile, end=date(2026, 9, 30))
    csv_path, _ = synth.write(rows, truth, tmp_path)
    return csv_path


def test_graph_stops_for_review_then_finalises(tmp_path):
    graph = build(FakeLLM())
    cfg = {"configurable": {"thread_id": "t-approve"}}
    state = graph.invoke({"statement_path": str(_statement(tmp_path))}, cfg)

    assert "__interrupt__" in state
    payload = state["__interrupt__"][0].value
    assert payload["guard_grounded"] is True
    assert "## Recommendation" in payload["memo"]
    assert "final_memo" not in state

    final = graph.invoke(Command(resume={"approved": True, "reviewer": "A. Analyst", "notes": "ok"}), cfg)
    assert "APPROVED by A. Analyst at " in final["final_memo"]
    assert "Grounding check: passed" in final["final_memo"]


def test_rejection_is_recorded(tmp_path):
    graph = build(FakeLLM())
    cfg = {"configurable": {"thread_id": "t-reject"}}
    graph.invoke({"statement_path": str(_statement(tmp_path))}, cfg)
    final = graph.invoke(Command(resume={"approved": False, "reviewer": "A. Analyst", "notes": "want 24 months"}), cfg)
    assert "NOT APPROVED" in final["final_memo"]
    assert "want 24 months" in final["final_memo"]


def test_hallucinating_model_is_caught_and_redrafted(tmp_path):
    llm = FakeLLM(hallucinate=True)
    graph = build(llm)
    cfg = {"configurable": {"thread_id": "t-halluc"}}
    state = graph.invoke({"statement_path": str(_statement(tmp_path))}, cfg)
    payload = state["__interrupt__"][0].value
    # The fake keeps hallucinating, so after MAX_REDRAFTS the graph still stops for a
    # human but tells them the memo is not grounded.
    assert payload["guard_grounded"] is False
    assert "£48,250" in payload["guard_unmatched"]
    assert state["redrafts"] == 2
    final = graph.invoke(Command(resume={"approved": False, "reviewer": "A. Analyst"}), cfg)
    assert "Grounding check: failed" in final["final_memo"]


# --- C6: a named human approves (panel run 01, 6/6 seats; requirement R5) ---------

import pytest  # noqa: E402


def _stopped(tmp_path, llm=None, tid="t"):
    graph = build(llm or FakeLLM())
    cfg = {"configurable": {"thread_id": tid}}
    state = graph.invoke({"statement_path": str(_statement(tmp_path))}, cfg)
    return graph, cfg, state


def _asked_again(state, match):
    payload = state["__interrupt__"][0].value
    assert "final_memo" not in state
    assert match in payload["error"]


def test_bare_boolean_resume_is_asked_again(tmp_path):
    graph, cfg, _ = _stopped(tmp_path, tid="t-bool")
    _asked_again(graph.invoke(Command(resume=True), cfg), "answer with")


@pytest.mark.parametrize("decision", [{"approved": True}, {"approved": True, "reviewer": ""}])
def test_approval_without_a_name_is_asked_again(tmp_path, decision):
    graph, cfg, _ = _stopped(tmp_path, tid=f"t-noname-{len(decision)}")
    _asked_again(graph.invoke(Command(resume=decision), cfg), "reviewer")


def test_ungrounded_memo_needs_explicit_override_with_reason(tmp_path):
    graph, cfg, _ = _stopped(tmp_path, FakeLLM(hallucinate=True), tid="t-override")
    _asked_again(graph.invoke(Command(resume={"approved": True, "reviewer": "A. Analyst"}), cfg), "grounding")
    _asked_again(
        graph.invoke(Command(resume={"approved": True, "reviewer": "A. Analyst", "override_grounding": True}), cfg),
        "grounding",
    )
    final = graph.invoke(
        Command(resume={"approved": True, "reviewer": "A. Analyst", "override_grounding": True,
                        "notes": "stock figure confirmed from management accounts"}),
        cfg,
    )
    assert "Grounding check: overridden by reviewer" in final["final_memo"]
    assert "stock figure confirmed" in final["final_memo"]
    assert final["review"].reviewer == "A. Analyst"


def test_reviewer_can_correct_after_a_refusal(tmp_path):
    graph, cfg, _ = _stopped(tmp_path, tid="t-correct")
    graph.invoke(Command(resume={"approved": True}), cfg)
    final = graph.invoke(Command(resume={"approved": True, "reviewer": "A. Analyst"}), cfg)
    assert "APPROVED by A. Analyst" in final["final_memo"]


def test_reviewer_sees_missing_citations(tmp_path):
    class Untagged(FakeLLM):
        def complete(self, system, user):
            return super().complete(system, user).replace(" [M2]", "", 1)

    _, _, state = _stopped(tmp_path, Untagged(), tid="t-untagged")
    payload = state["__interrupt__"][0].value
    assert payload["guard_grounded"] is False
    assert "M2" in payload["guard_missing_citations"]


# --- panel run 02, D12 --------------------------------------------------------------

@pytest.mark.parametrize("decision, match", [
    ({"approved": True, "reviewer": "   "}, "reviewer"),
    ({"approved": True, "reviewer": "A", "decided_at": "2020-01-01T00:00:00Z"}, "decided_at"),
    ({"approved": "yes", "reviewer": "A"}, "approved"),
])
def test_review_refuses_blank_names_backdating_and_loose_booleans(tmp_path, decision, match):
    graph, cfg, _ = _stopped(tmp_path, tid=f"t-d12-{match}")
    _asked_again(graph.invoke(Command(resume=decision), cfg), match)


def test_reviewer_name_is_stripped(tmp_path):
    graph, cfg, _ = _stopped(tmp_path, tid="t-strip")
    final = graph.invoke(Command(resume={"approved": True, "reviewer": "  A. Analyst  "}), cfg)
    assert final["review"].reviewer == "A. Analyst"
