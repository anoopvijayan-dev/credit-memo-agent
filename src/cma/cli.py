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
from .llm import get_llm

app = typer.Typer(no_args_is_help=True, add_completion=False)
import_app = typer.Typer(no_args_is_help=True, help="Convert a bank export into a statement CSV.")
app.add_typer(import_app, name="import")
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
    yes: bool = typer.Option(False, "--yes", help="Approve without prompting (for scripts)."),
    out: Path | None = typer.Option(None, help="Write the final memo here."),
):
    """Run the memo graph on a statement and stop for human review."""
    graph = build(get_llm(llm))
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    state = graph.invoke({"statement_path": str(statement), "policy_path": str(policy) if policy else None}, cfg)

    interrupts = state.get("__interrupt__") or []
    if not interrupts:
        raise typer.Exit(code=1)
    payload = interrupts[0].value

    _print_metrics(state["metrics"])
    _print_policy(state["policy"])
    console.rule("Draft memo")
    console.print(Markdown(payload["memo"]))
    console.rule("Grounding check")
    if payload["guard_grounded"]:
        console.print("[green]All figures trace to the metrics table.[/green]")
    else:
        console.print(f"[red]Unmatched numbers:[/red] {payload['guard_unmatched']}")

    if yes:
        decision = {"approved": True, "reviewer": "cli --yes"}
    else:
        approved = typer.confirm(payload["question"], default=False)
        notes = typer.prompt("Notes (optional)", default="", show_default=False)
        decision = {"approved": approved, "notes": notes}

    final = graph.invoke(Command(resume=decision), cfg)
    if out:
        out.write_text(final["final_memo"])
        console.print(f"wrote {out}")
    else:
        console.rule("Final")
        console.print(Markdown(final["final_memo"]))


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


@import_app.command("monzo")
def import_monzo(
    export: Path = typer.Argument(..., help="The CSV exported from the Monzo app."),
    out: Path = typer.Option(..., "-o", "--out", help="Where to write the statement CSV. Keep it out of git."),
    opening_balance: float | None = typer.Option(
        None, help="Balance just before the first transaction. Needed when the export has no Balance column."
    ),
):
    """Convert a Monzo export locally. Nothing is uploaded; the file stays on this machine."""
    from .importers import monzo

    try:
        r = monzo.convert(export, out, opening_balance)
    except monzo.MonzoImportError as e:
        console.print(f"[red]Import refused.[/red] {e}")
        raise typer.Exit(code=2) from e
    console.print(
        f"wrote {out}: {r.rows} transactions, {r.start} to {r.end}; "
        f"opening £{r.opening_balance:,.2f}, closing £{r.closing_balance:,.2f} ({r.balance_source})"
        + (f"; skipped {r.skipped_non_gbp} non-GBP rows" if r.skipped_non_gbp else "")
    )
    console.print("Check the closing balance against your Monzo app before trusting any metric.")


@app.command()
def truth(path: Path):
    """Print the ground truth for a generated statement."""
    console.print_json(json.dumps(json.loads(Path(path).read_text()), indent=2))


if __name__ == "__main__":
    app()
