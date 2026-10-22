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

    final = graph.invoke(Command(resume={"approved": True, "notes": "ok"}), cfg)
    assert "APPROVED" in final["final_memo"]
    assert "Grounding check: passed" in final["final_memo"]


def test_rejection_is_recorded(tmp_path):
    graph = build(FakeLLM())
    cfg = {"configurable": {"thread_id": "t-reject"}}
    graph.invoke({"statement_path": str(_statement(tmp_path))}, cfg)
    final = graph.invoke(Command(resume={"approved": False, "notes": "want 24 months"}), cfg)
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
    final = graph.invoke(Command(resume={"approved": False}), cfg)
    assert "Grounding check: overridden" in final["final_memo"]
