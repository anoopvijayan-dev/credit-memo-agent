"""Command line: generate statements, run the memo graph, approve or reject."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import typer
from langgraph.types import Command
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from . import synth
from .graph import build
from .ingest import StatementError
from .llm import get_llm

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.command()
def generate(
    archetype: str = typer.Option("cafe", help=f"One of {list(synth.ARCHETYPES)}"),
    profile: str = typer.Option("stretched", help=f"One of {list(synth.PROFILES)}"),
    seed: int = 7,
    months: int = 12,
    out: Path = Path("examples"),
):
    """Write a synthetic statement CSV and its ground-truth JSON."""
    rows, truth = synth.generate(archetype, profile, seed=seed, months=months)
    csv_path, truth_path = synth.write(rows, truth, out)
    console.print(f"wrote {csv_path} ({len(rows)} transactions) and {truth_path}")


@app.command()
def run(
    statement: Path,
    policy: Path | None = typer.Option(None, help="Policy YAML; defaults to policy/uk_sme_default.yaml"),
    llm: str = typer.Option("fake", help="fake | anthropic | openai"),
    reviewer: str | None = typer.Option(None, help="Your name, recorded on the memo."),
    yes: bool = typer.Option(
        False, "--yes", help="Approve without prompting (scripts). Needs --reviewer; refuses ungrounded memos."
    ),
    out: Path | None = typer.Option(None, help="Write the approved memo here (nothing is written if rejected)."),
):
    """Run the memo graph on a statement and stop for human review."""
    if yes and not reviewer:
        console.print("[red]--yes needs --reviewer NAME: an approval must say who approved.[/red]")
        raise typer.Exit(code=2)
    graph = build(get_llm(llm))
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    try:
        state = graph.invoke({"statement_path": str(statement), "policy_path": str(policy) if policy else None}, cfg)
    except StatementError as e:
        console.print(f"[red]Statement refused.[/red] {e}")
        raise typer.Exit(code=2) from e

    interrupts = state.get("__interrupt__") or []
    if not interrupts:
        raise typer.Exit(code=1)
    payload = interrupts[0].value

    _print_metrics(state["metrics"])
    _print_policy(state["policy"])
    console.rule("Draft memo")
    console.print(Markdown(payload["memo"]))
    console.rule("Grounding check")
    grounded = payload["guard_grounded"]
    if grounded:
        console.print("[green]All figures trace to the metrics table.[/green]")
    else:
        console.print(f"[red]Unmatched numbers:[/red] {payload['guard_unmatched']}")
        if payload["guard_missing_citations"]:
            console.print(f"[red]Figures quoted without their tag:[/red] {payload['guard_missing_citations']}")

    if yes:
        if not grounded:
            console.print("[red]--yes will not approve a memo that failed the grounding check. Review it interactively.[/red]")
            raise typer.Exit(code=3)
        decision = {"approved": True, "reviewer": reviewer}
    else:
        decision = _ask(payload, grounded, reviewer)

    final = graph.invoke(Command(resume=decision), cfg)
    while "__interrupt__" in final:
        payload = final["__interrupt__"][0].value
        console.print(f"[red]Not accepted:[/red] {payload['error']}")
        if yes:
            raise typer.Exit(code=3)
        decision = _ask(payload, grounded, reviewer)
        final = graph.invoke(Command(resume=decision), cfg)
    if not final["review"].approved:
        console.rule("Not approved")
        console.print(Markdown(final["final_memo"]))
        raise typer.Exit(code=1)
    if out:
        out.write_text(final["final_memo"])
        console.print(f"wrote {out}")
    else:
        console.rule("Final")
        console.print(Markdown(final["final_memo"]))


def _ask(payload: dict, grounded: bool, reviewer: str | None) -> dict:
    name = reviewer or typer.prompt("Your name (recorded on the memo)")
    approved = typer.confirm(payload["question"], default=False)
    override = False
    if approved and not grounded:
        override = typer.confirm("The grounding check failed. Override it?", default=False)
        approved = override
    if override:
        notes = typer.prompt("Why are you overriding the grounding check?")
    else:
        notes = typer.prompt("Notes (optional)", default="", show_default=False)
    return {"approved": approved, "reviewer": name, "notes": notes, "override_grounding": override}


def _print_metrics(metrics):
    t = Table(title="Metrics", show_lines=False)
    t.add_column("id"), t.add_column("metric"), t.add_column("value", justify="right"), t.add_column("evidence")
    for m in metrics:
        t.add_row(m.id, m.name, m.display(), f"{len(m.evidence)} txns")
    console.print(t)


def _print_policy(p):
    t = Table(title=f"Policy: {p.policy_name} — overall {p.overall.value.upper()}")
    t.add_column("rule"), t.add_column("outcome"), t.add_column("detail")
    colour = {"pass": "green", "refer": "yellow", "fail": "red"}
    for r in p.rules:
        t.add_row(f"{r.rule_id} {r.description}", f"[{colour[r.outcome.value]}]{r.outcome.value}[/]", r.detail)
    console.print(t)


@app.command()
def truth(path: Path):
    """Print the ground truth for a generated statement."""
    console.print_json(json.dumps(json.loads(Path(path).read_text()), indent=2))


if __name__ == "__main__":
    app()
