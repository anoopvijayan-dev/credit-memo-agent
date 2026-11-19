"""Command line: generate statements, run the memo graph, approve or reject."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from pathlib import Path

import typer
from langgraph.types import Command
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from . import guard as grd
from . import synth
from .graph import build, evidence_record, sqlite_checkpointer
from .ingest import StatementError
from .llm import get_llm
from .policy import PolicyError

app = typer.Typer(no_args_is_help=True, add_completion=False)
import_app = typer.Typer(no_args_is_help=True, help="Convert a bank export into a statement CSV.")
app.add_typer(import_app, name="import")
case_app = typer.Typer(no_args_is_help=True, help="Work with a case folder.")
app.add_typer(case_app, name="case")
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
    policy: Path | None = typer.Option(None, help="Policy YAML; defaults to the packaged uk_sme_default policy."),
    llm: str = typer.Option("fake", help="fake | anthropic | openai"),
    vat_stagger: int | None = typer.Option(
        None, min=1, max=3,
        help="HMRC VAT stagger group (1, 2 or 3, from the VAT certificate). Without it, VAT timeliness is referred.",
    ),
    reviewer: str | None = typer.Option(None, help="Your name, recorded on the memo."),
    yes: bool = typer.Option(
        False, "--yes",
        help="Approve without prompting (scripts). Needs --reviewer; refuses ungrounded memos; "
        "the memo is stamped 'automated, no interactive review'.",
    ),
    out: Path | None = typer.Option(
        None, help="Write the approved memo here, with NAME.evidence.json beside it. A rejected memo goes to "
        "NAME.rejected.md instead."
    ),
    checkpoint_db: Path | None = typer.Option(
        None, help="SQLite file that keeps a pending review across restarts (needs the 'durable' extra)."
    ),
    thread: str | None = typer.Option(None, help="Name this review, to pick it up later with `cma resume`."),
):
    """Run the memo graph on a statement and stop for human review."""
    if yes and not (reviewer or "").strip():
        console.print("[red]--yes needs --reviewer NAME: an approval must say who approved.[/red]")
        raise typer.Exit(code=2)
    _refuse_to_overwrite(out)
    thread_id = thread or str(uuid.uuid4())
    with _checkpointer(checkpoint_db) as saver:
        graph = build(get_llm(llm), checkpointer=saver)
        cfg = {"configurable": {"thread_id": thread_id}}
        if graph.get_state(cfg).values:
            console.print(f"[red]Review id {thread_id} is already used in {checkpoint_db}. Pick a new --thread.[/red]")
            raise typer.Exit(code=2)
        try:
            state = graph.invoke(
                {"statement_path": str(statement), "policy_path": str(policy) if policy else None,
                 "vat_stagger": vat_stagger},
                cfg,
            )
        except (StatementError, PolicyError) as e:
            console.print(f"[red]Refused.[/red] {e}")
            raise typer.Exit(code=2) from e
        except Exception as e:  # the model provider: network, auth, cut-off reply
            console.print(f"[red]The model call failed[/red] ({type(e).__name__}): {e}")
            raise typer.Exit(code=4) from e
        if checkpoint_db:
            console.print(f"review id: {thread_id} (kept in {checkpoint_db}; `cma resume {thread_id}` picks it up)")
        _review(graph, cfg, state, reviewer, yes, out)


@app.command()
def resume(
    thread: str = typer.Argument(..., help="The review id printed by `cma run --checkpoint-db`."),
    checkpoint_db: Path = typer.Option(..., help="The SQLite file the review was kept in."),
    reviewer: str | None = typer.Option(None, help="Your name, recorded on the memo."),
    out: Path | None = typer.Option(None, help="As for `cma run`."),
):
    """Pick up a memo that is waiting for review, after a restart."""
    if not checkpoint_db.exists():
        console.print(f"[red]{checkpoint_db} does not exist.[/red]")
        raise typer.Exit(code=2)
    _refuse_to_overwrite(out)
    with _checkpointer(checkpoint_db) as saver:
        graph = build(get_llm("fake"), checkpointer=saver)  # the draft is already made; no model call
        cfg = {"configurable": {"thread_id": thread}}
        snap = graph.get_state(cfg)
        if not snap.interrupts:
            console.print(f"[red]Nothing is waiting for review under {thread}.[/red]")
            raise typer.Exit(code=1)
        problem = _stored_state_problem(snap.values)
        if problem:
            console.print(f"[red]Not resuming {thread}:[/red] {problem}")
            raise typer.Exit(code=2)
        state = {**snap.values, "__interrupt__": snap.interrupts}
        _review(graph, cfg, state, reviewer, False, out)


def _stored_state_problem(values: dict) -> str | None:
    """A stored review is only resumed if it still says what it said: the statement file
    is unchanged and the stored memo still passes (or fails) the guard the same way."""
    path = Path(values["statement_path"])
    if not path.is_file():
        return f"the statement {path} is no longer there"
    if hashlib.sha256(path.read_bytes()).hexdigest() != values.get("statement_sha256"):
        return f"the statement {path} has changed since the memo was drafted"
    again = grd.check(values["memo"], values["metrics"], values["policy"], required_headings=grd.REQUIRED_HEADINGS)
    if again.grounded != values["guard"].grounded:
        return "the stored grounding result does not match the stored memo"
    return None


def _refuse_to_overwrite(out: Path | None) -> None:
    """Never replace an earlier memo or its evidence: the pair is the audit record."""
    if out is None:
        return
    taken = [p for p in _outputs(out) if p.exists()]
    if taken:
        console.print(f"[red]Refusing to overwrite {', '.join(map(str, taken))}. Choose another --out.[/red]")
        raise typer.Exit(code=2)


def _outputs(out: Path) -> list[Path]:
    rejected = out.with_name(out.stem + ".rejected.md")
    return [out, _evidence_path(out), rejected, _evidence_path(rejected)]


def _evidence_path(memo: Path) -> Path:
    return memo.with_name(memo.stem + ".evidence.json")


@contextmanager
def _checkpointer(path: Path | None):
    if path is None:
        yield None
        return
    try:
        with sqlite_checkpointer(path) as saver:
            yield saver
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2) from e


def _review(graph, cfg, state, reviewer, yes, out):
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
        console.print("[green]All figures trace to the metrics table or the policy.[/green]")
    else:
        console.print(f"[red]Unmatched numbers:[/red] {payload['guard_unmatched']}")
        if payload["guard_missing_citations"]:
            console.print(f"[red]Figures quoted without their tag:[/red] {payload['guard_missing_citations']}")

    if yes:
        if not grounded:
            console.print("[red]--yes will not approve a memo that failed the grounding check. Review it interactively.[/red]")
            raise typer.Exit(code=3)
        decision = {"approved": True, "reviewer": reviewer, "mode": "automated",
                    "notes": "Approved by script (--yes); the draft was not reviewed interactively."}
    else:
        decision = _ask(payload, grounded, reviewer)

    final = _decide(graph, cfg, decision)
    while "__interrupt__" in final:
        payload = final["__interrupt__"][0].value
        console.print(f"[red]Not accepted:[/red] {payload['error']}")
        if yes:
            raise typer.Exit(code=3)
        decision = _ask(payload, grounded, reviewer)
        final = _decide(graph, cfg, decision)

    approved = final["review"].approved
    if out:
        target = out if approved else out.with_name(out.stem + ".rejected.md")
        evidence = _evidence_path(target)
        target.write_text(final["final_memo"])
        evidence.write_text(json.dumps(evidence_record(final), indent=2, default=str))
        console.print(f"wrote {target} and {evidence}")
    if not approved:
        console.rule("Not approved")
        console.print(Markdown(final["final_memo"]))
        raise typer.Exit(code=1)
    if not out:
        console.rule("Final")
        console.print(Markdown(final["final_memo"]))


def _decide(graph, cfg, decision: dict) -> dict:
    """Resume with this decision, and make sure it is the one that was recorded.

    Two people can open the same pending review from a shared --checkpoint-db. If
    someone else decided first, resuming is a no-op that returns *their* decision; say
    so instead of reporting it as ours.
    """
    if not graph.get_state(cfg).interrupts:
        _decided_elsewhere(graph.get_state(cfg).values)
    final = graph.invoke(Command(resume=decision), cfg)
    review = final.get("review")
    if "__interrupt__" not in final and (
        review is None or review.reviewer != (decision.get("reviewer") or "").strip()
        or review.approved != decision.get("approved")
    ):
        _decided_elsewhere(final)
    return final


def _decided_elsewhere(values: dict) -> None:
    r = values.get("review")
    who = f"{r.reviewer} at {r.decided_at:%Y-%m-%d %H:%M UTC}" if r else "someone else"
    console.print(f"[red]This review was already decided by {who}. Your decision was not recorded.[/red]")
    raise typer.Exit(code=5)


def _ask(payload: dict, grounded: bool, reviewer: str | None) -> dict:
    name = (reviewer or "").strip()
    while not name:
        name = typer.prompt("Your name (recorded on the memo)").strip()
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


@case_app.command("check")
def case_check(folder: Path = typer.Argument(..., help="The case folder (cases/<id>).")):
    """Load a case's log and check its hash chain against its head file (R43).

    Exit 0 only when the whole log is intact, 3 when an entry was changed, removed,
    reordered or not finished, naming the first bad entry, and 4 while another window or
    command is saving to the case (D75): check again. Entries are named by number, never
    quoted."""
    from . import case

    if not folder.is_dir():
        console.print(f"[red]No case folder at {folder}.[/red]")
        raise typer.Exit(code=2)
    check = case.for_case(folder).verify()
    if not check.checked:
        console.print("Case log is being saved by another window or command; nothing could be "
                      "checked yet. Check again in a moment.")
        raise typer.Exit(code=4)
    if not check.ok:
        console.print(f"[red]Case log broken:[/red] entry {check.first_bad} {check.reason}.")
        console.print("Nothing may be recorded or signed off on this case until it is resolved.")
        raise typer.Exit(code=3)
    if check.in_progress:  # R43, D75: being saved is never intact, so not exit 0
        console.print(f"Case log is being saved by another window or command: its "
                      f"{check.head.position} recorded entries check out, the rest is not "
                      "checked yet. Check again in a moment.")
        raise typer.Exit(code=4)
    console.print(f"case log intact: {check.head.position} entries, head {check.head.hash[:12]}")


@app.command()
def truth(path: Path):
    """Print the ground truth for a generated statement."""
    console.print_json(json.dumps(json.loads(Path(path).read_text()), indent=2))


if __name__ == "__main__":
    app()
