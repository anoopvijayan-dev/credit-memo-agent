"""Command line: generate statements, run the memo graph, approve or reject."""

from __future__ import annotations

import hashlib
import os
import tempfile

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt
import json
import uuid
from contextlib import contextmanager
from pathlib import Path

import typer
from langgraph.types import Command
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.table import Table

from . import firm, synth
from . import guard as grd
from .categorise import RulesetError, active_ruleset
from .firm import FirmConfigError, OfflineLockError
from .graph import (
    StaleReviewError,
    build,
    evidence_record,
    guard_fields,
    refreshed,
    ruleset_used,
    sqlite_checkpointer,
)
from .indicators import ThresholdsError, load_thresholds
from .ingest import StatementError, load_csv
from .llm import get_llm
from .metrics import AmountError, ReconciliationError
from .policy import PolicyError

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
    policy: Path | None = typer.Option(None, help="Policy YAML; defaults to the packaged uk_sme_default policy."),
    thresholds: Path | None = typer.Option(
        None, help="Indicator thresholds YAML (R14); defaults to the packaged, illustrative indicators.yaml."
    ),
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
    try:
        # Refuse a bad CMA_RULESET before the graph checkpoints anything: a refusal after the first
        # checkpoint would leave the review id taken, so a corrected retry could not reuse it.
        active_ruleset()
        load_thresholds(thresholds)  # a malformed file is refused naming its line (R14)
        firm_config = firm.load()  # the run's one reading of firm.yaml
        model = get_llm(llm, firm_config=firm_config)  # under the lock a hosted model is refused here
    except (RulesetError, ThresholdsError, OfflineLockError, FirmConfigError) as e:
        console.print(f"[red]Refused.[/red] {escape(str(e))}")
        raise typer.Exit(code=2) from e
    thread_id = thread or str(uuid.uuid4())
    with _checkpointer(checkpoint_db) as saver:
        graph = build(model, checkpointer=saver, firm_config=firm_config)
        cfg = {"configurable": {"thread_id": thread_id}}
        # The id check and the first write are one step under the review lock, so two runs
        # never both start the same review id (T04 review 27).
        with _review_lock(checkpoint_db):
            if graph.get_state(cfg).values:
                console.print(f"[red]Review id {thread_id} is already used in {checkpoint_db}. Pick a new --thread.[/red]")
                raise typer.Exit(code=2)
            try:
                state = graph.invoke(
                    {"statement_path": str(statement), "policy_path": str(policy) if policy else None,
                     "thresholds_path": str(thresholds) if thresholds else None, "vat_stagger": vat_stagger},
                    cfg,
                )
            except (StatementError, PolicyError, RulesetError, ThresholdsError, AmountError,
                    FirmConfigError, OfflineLockError) as e:
                # OfflineLockError here: a hosted request refused before it was sent, so a
                # refusal, not a model failure.
                console.print(f"[red]Refused.[/red] {escape(str(e))}")
                raise typer.Exit(code=2) from e
            except ReconciliationError as e:  # R3: no figures for a case whose buckets do not add up
                # Line ids come from the statement, so they are escaped, never read as markup.
                console.print(f"[red]Refused, no figures shown.[/red] {escape(str(e))}")
                raise typer.Exit(code=6) from e
            except Exception as e:  # the model provider: network, auth, cut-off reply
                console.print(f"[red]The model call failed[/red] ({type(e).__name__}): {escape(str(e))}")
                raise typer.Exit(code=4) from e
        if checkpoint_db:
            console.print(f"review id: {thread_id} (kept in {checkpoint_db}; `cma resume {thread_id}` picks it up)")
        _review(graph, cfg, state, reviewer, yes, out, lock=checkpoint_db)


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
            console.print(f"[red]Not resuming {thread}:[/red] {escape(problem)}")
            raise typer.Exit(code=2)
        try:
            # Today's figures, if the review was drafted by an earlier release (T04 review 16).
            update = refreshed(snap.values)
        except (AmountError, ReconciliationError) as e:
            console.print(f"[red]Not resuming {thread}: refused, no figures shown.[/red] {escape(str(e))}")
            raise typer.Exit(code=6) from e
        except StaleReviewError as e:
            console.print(f"[red]Not resuming {thread}:[/red] {escape(str(e))}")
            raise typer.Exit(code=2) from e
        if update:
            # Today's figures are written into the stored review before it is shown, so the
            # decision is applied to exactly what the reviewer saw: nothing is read again after
            # they answer (T04 review 24). The review returns to the reviewer as it was. It is
            # written only if the review is still the pending one read above; a review someone
            # decided or refreshed meanwhile is never reopened (T04 review 25).
            with _review_lock(checkpoint_db):  # the check and the write are one step (T04 review 26)
                if not _still_pending(graph, cfg, snap):
                    console.print(f"[red]Not resuming {thread}:[/red] the review changed while it was being "
                                  "opened; run `cma resume` again")
                    raise typer.Exit(code=2)
                graph.update_state(cfg, {**update, "refreshed_for_review": True}, as_node="guard")
                state = graph.invoke(None, cfg)
        else:
            state = {**snap.values, "__interrupt__": snap.interrupts}
        _review(graph, cfg, state, reviewer, False, out, lock=checkpoint_db)


def _stored_state_problem(values: dict) -> str | None:
    """A stored review is only resumed if it still says what it said: the statement file
    is unchanged and the stored memo still passes (or fails) the guard the same way."""
    path = Path(values["statement_path"])
    try:
        data = path.read_bytes()  # read once: the bytes hashed are the bytes checked (T04 review 17)
    except OSError:
        return f"the statement {path} is no longer there"
    if hashlib.sha256(data).hexdigest() != values.get("statement_sha256"):
        return f"the statement {path} has changed since the memo was drafted"
    try:
        # A review drafted by an earlier release is shown only if today's loader would still
        # take the statement; otherwise its stored figures are not shown (R2, T04 review 15).
        loaded = load_csv(path, data=data)
    except StatementError as e:
        return f"the statement would now be refused, so the stored figures are not shown: {e}"

    # The stored lines must be the lines of the statement the hash names, as today's loader
    # reads them, or figures recomputed from them would describe another statement (T04 review 17).
    def raw(ts):
        return [(t.id, t.date, t.description, t.amount, t.balance) for t in ts]

    if raw(loaded.transactions) != raw(values["statement"].transactions):
        return f"the stored lines are not the lines of {path} as it reads today"
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
def _review_lock(db: Path | None):
    """Held by every `cma` process while it checks a pending review and writes to it: a
    resume refreshing figures, and a decision being applied. The check and the write are
    then one step for every process sharing the checkpoint file, so a review decided by one
    is never reopened or decided again by another (T04 review 26)."""
    if db is None:
        yield
        return
    real = Path(db).resolve()  # one lock however the file is reached, through a symlink too (T04 review 30)
    with open(real.with_name(real.name + ".lock"), "a+b") as f:
        if fcntl is not None:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        else:  # Windows
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def _still_pending(graph, cfg, snap) -> bool:
    """Whether the review is still the pending one `snap` read: same checkpoint, waiting."""
    now = graph.get_state(cfg)
    if now.interrupts and now.config["configurable"].get("checkpoint_id") == \
            snap.config["configurable"].get("checkpoint_id"):
        return True
    if now.values.get("review"):
        _decided_elsewhere(now.values)
    return False


@contextmanager
def _checkpointer(path: Path | None):
    if path is None:
        yield None
        return
    try:
        with sqlite_checkpointer(path) as saver:
            yield saver
    except typer.Exit:
        raise  # a RuntimeError too: its own exit code stands (T04 review 25)
    except RuntimeError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(code=2) from e


def _review(graph, cfg, state, reviewer, yes, out, lock: Path | None = None):
    interrupts = state.get("__interrupt__") or []
    if not interrupts:
        raise typer.Exit(code=1)
    # What is shown is what will be decided: the state's figures and grounding result.
    payload = {**interrupts[0].value, **guard_fields(state["guard"])}
    # Settle which rule table the evidence will name while the review is still pending. A
    # review drafted by an older version has none stored, and naming it reads the packaged
    # v1 table; if that fails after the decision is recorded, the memo has no evidence and
    # the review can no longer be resumed. Refuse here instead, with nothing decided.
    try:
        ruleset = ruleset_used(state)
    except RulesetError as e:
        console.print(f"[red]Refused, nothing recorded.[/red] {escape(str(e))}")
        raise typer.Exit(code=2) from e

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

    final = _decide(graph, cfg, decision, lock)
    while "__interrupt__" in final:
        payload = final["__interrupt__"][0].value
        console.print(f"[red]Not accepted:[/red] {payload['error']}")
        if yes:
            raise typer.Exit(code=3)
        decision = _ask(payload, grounded, reviewer)
        final = _decide(graph, cfg, decision, lock)

    approved = final["review"].approved
    if out:
        target = out if approved else out.with_name(out.stem + ".rejected.md")
        evidence = _evidence_path(target)
        # Both are made in full before either is written, each is written beside its target
        # and moved into place, evidence first: a failure while making them writes nothing,
        # and no memo is left without its evidence (T04 review 27).
        record = json.dumps(evidence_record({**final, "ruleset": ruleset}), indent=2, default=str)
        for path, text in ((evidence, record), (target, final["final_memo"])):
            # A staging file with a name of its own, never an existing sibling (T04 review 28).
            fd, part = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".part")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
                os.replace(part, path)
            except BaseException:
                Path(part).unlink(missing_ok=True)
                raise
        console.print(f"wrote {target} and {evidence}")
    if not approved:
        console.rule("Not approved")
        console.print(Markdown(final["final_memo"]))
        raise typer.Exit(code=1)
    if not out:
        console.rule("Final")
        console.print(Markdown(final["final_memo"]))


def _decide(graph, cfg, decision: dict, lock: Path | None = None) -> dict:
    """Resume with this decision, and make sure it is the one that was recorded.

    Two people can open the same pending review from a shared --checkpoint-db. If
    someone else decided first, resuming is a no-op that returns *their* decision; say
    so instead of reporting it as ours.
    """
    with _review_lock(lock):  # checked and applied as one step (T04 review 26)
        return _decide_locked(graph, cfg, decision)


def _decide_locked(graph, cfg, decision: dict) -> dict:
    if not graph.get_state(cfg).interrupts:
        _decided_elsewhere(graph.get_state(cfg).values)
    try:
        final = graph.invoke(Command(resume=decision), cfg)
    except (StaleReviewError, AmountError, ReconciliationError) as e:
        # Applying the decision works the figures out again; if that now fails (the policy
        # file changed while the review was open, say), the decision is not recorded and the
        # review stays pending, as revision 28 says (T04 review 23).
        console.print(f"[red]Not recorded:[/red] {escape(str(e))}")
        raise typer.Exit(code=2) from e
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
        t.add_row(m.id, m.name, m.display_exact(), f"{len(m.evidence)} txns")  # money to the penny
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
    opening_balance: str | None = typer.Option(
        None, help="Balance just before the first transaction. Needed when the export has no Balance column."
    ),  # text, read exactly by the importer, never rounded through a float first (T04 review 8)
):
    """Convert a Monzo export locally. Nothing is uploaded; the file stays on this machine."""
    from .importers import monzo

    try:
        r = monzo.convert(export, out, opening_balance)
    except monzo.MonzoImportError as e:
        console.print(f"[red]Import refused.[/red] {escape(str(e))}")  # cells are text, never markup (T04 review 31)
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
