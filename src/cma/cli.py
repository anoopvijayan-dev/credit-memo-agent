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
from contextlib import contextmanager, suppress
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
case_app = typer.Typer(no_args_is_help=True, help="Work with a case folder.")
app.add_typer(case_app, name="case")
console = Console()
err_console = Console(stderr=True)  # warnings, so stdout keeps a command's own output


@app.command()
def generate(
    archetype: str = typer.Option("cafe", help=f"One of {list(synth.ARCHETYPES)}"),
    profile: str = typer.Option("stretched", help=f"One of {list(synth.PROFILES)}"),
    seed: int = 7,
    months: int = 12,
    out: Path = Path("examples"),
    feature: list[str] = typer.Option(
        [], "--feature",
        help=f"Opt-in synthetic feature, repeatable: one of {list(synth.FEATURES)}, or 'all'. The truth then "
        "holds every bucket, line category and indicator, for the v2 rules and the case profile it gives.",
    ),
    case: bool = typer.Option(
        False, "--case",
        help="Also make a demo case in the case root (CMA_HOME) holding every account written, with the case "
        "profile the generator gives; the truth's 'case' then holds that case's truth, own-account pairs included.",
    ),
):
    """Write a synthetic statement CSV and its ground-truth JSON."""
    features = list(synth.FEATURES) if "all" in feature else feature
    try:
        rows, truth = synth.generate(archetype, profile, seed=seed, months=months, features=features)
    except ValueError as e:
        console.print(f"[red]Refused.[/red] {escape(str(e))}")
        raise typer.Exit(code=2) from e
    csv_path, truth_path = synth.write(rows, truth, out)
    console.print(f"wrote {csv_path} ({len(rows)} transactions) and {truth_path}")
    for account in truth.accounts:
        other = out / f"{csv_path.stem}_{account['key']}.csv"
        console.print(f"wrote {other} ({len(account['rows'])} transactions), the applicant's {account['key']} account")
    if case:
        def warn(message: str) -> None:  # before the case is made (R32), as `cma case new` warns
            err_console.print(f"[yellow]Warning:[/yellow] {escape(message)}")

        with _case_errors():  # every case-store and case-log refusal, as the other case commands
            case_id, folder = synth.to_case(rows, truth, out, warn=warn)
        console.print(f"made case {case_id} at {folder}, demo, with {1 + len(truth.accounts)} account(s)")


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
    from .importers import contract, monzo

    try:
        r = monzo.convert(export, out, opening_balance)
    except contract.ImportRefused as e:  # by line, never a cell's value (T28)
        console.print(f"[red]Import refused.[/red] {escape(str(e))}")  # cells are text, never markup (T04 review 31)
        raise typer.Exit(code=2) from e
    console.print(
        f"wrote {out}: {r.rows} transactions, {r.start} to {r.end}; "
        f"opening £{r.opening_balance:,.2f}, closing £{r.closing_balance:,.2f} ({r.balance_source})"
        + (f"; skipped {r.skipped_non_gbp} non-GBP rows" if r.skipped_non_gbp else "")
    )
    console.print("Check the closing balance against your Monzo app before trusting any metric.")


TORN_REMEDY = ("An entry that was not finished can be dropped once you confirm it: run `cma case repair` "
               "on this case, then do this again (R42).")


@contextmanager
def _case_errors():
    """A refusal from the case store or its log, said without a traceback. Exit 2 for a
    refusal, 3 for a broken log, 4 while another window holds the case or a save is not
    confirmed (check the case log)."""
    from . import case, store

    try:
        yield
    except store.CaseStoreError as e:
        console.print(f"[red]Refused.[/red] {escape(str(e))}")
        raise typer.Exit(code=2) from None
    except case.CaseLogBroken as e:
        console.print(f"[red]Case log broken:[/red] entry {e.first_bad} {escape(e.reason)}. Nothing was changed.")
        if e.torn:
            console.print(TORN_REMEDY)
        raise typer.Exit(code=3) from None
    except case.SaveNotConfirmed as e:
        console.print(f"[red]Not confirmed.[/red] {escape(str(e))}")
        raise typer.Exit(code=4) from None
    except (case.CaseLocked, case.LogConflict) as e:
        console.print(f"[red]Not recorded.[/red] {escape(str(e))}")
        raise typer.Exit(code=4) from None
    except FirmConfigError as e:
        console.print(f"[red]Refused.[/red] {escape(str(e))}")
        raise typer.Exit(code=2) from None
    except OSError as e:
        # The file system refused a step outside a save (the case root cannot be made, a
        # file cannot be read). Said as a refusal, never a traceback; a save's own errors
        # reach here as SaveNotRecorded or SaveNotConfirmed (R42).
        what = e.strerror or type(e).__name__
        where = f" ({e.filename})" if e.filename else ""
        console.print(f"[red]Refused.[/red] {escape(what + where)}; nothing was recorded in the case.")
        raise typer.Exit(code=2) from None


@case_app.command("new")
def case_new(
    demo: bool = typer.Option(False, "--demo", help="Synthetic data or your own statements, not an applicant's (R48)."),
    label: str | None = typer.Option(None, "--label", help="Your own label for the case. Kept in the case folder."),
):
    """Make a case under the case root (CMA_HOME, or private/cma) and print its random id.
    Warns first, on stderr, when the root lies in a OneDrive, Dropbox, Google Drive or
    iCloud Drive folder, and records the warning in the case log."""
    from . import store

    def warn(message: str) -> None:  # before the case is made (R32)
        err_console.print(f"[yellow]Warning:[/yellow] {escape(message)}")

    with _case_errors():
        case_id = store.new_case(label=label, demo=demo, warn=warn)
    console.print(case_id)


@case_app.command("profile")
def case_profile(
    case_id: str = typer.Argument(..., metavar="CASE", help="The case id `cma case new` printed."),
    from_file: Path | None = typer.Option(None, "--from", help="A YAML profile to replace the whole profile with."),
    label: str | None = typer.Option(None, "--label"),
    demo: bool | None = typer.Option(None, "--demo/--not-demo"),
    business_name: str | None = typer.Option(None, "--business-name"),
    director: list[str] | None = typer.Option(None, "--director", help="Repeat for each; replaces the list."),
    shareholder: list[str] | None = typer.Option(None, "--shareholder", help="Repeat for each; replaces the list."),
    own_account: list[str] | None = typer.Option(
        None, "--own-account", help="The applicant's other accounts; repeat for each; replaces the list."),
    vat_stagger: int | None = typer.Option(None, "--vat-stagger", help="1, 2 or 3."),
    proposed_repayment: str | None = typer.Option(
        None, "--proposed-repayment", help="The proposed facility's monthly repayment, in pounds."),
):
    """Show a case's profile, or change it. Every change is checked and logged (R42)."""
    from . import store

    with _case_errors():
        folder = store.case_folder(case_id)
        replacement = None
        if from_file is not None:
            if not from_file.is_file():
                raise store.CaseStoreError(f"no file at {from_file}")
            replacement = store.parse_profile(from_file.read_bytes(), str(from_file))
        changes = {key: value for key, value in (
            ("label", label), ("demo", demo), ("business_name", business_name),
            ("directors", director), ("shareholders", shareholder), ("own_accounts", own_account),
            ("vat_stagger", vat_stagger), ("proposed_monthly_repayment", proposed_repayment))
            if value is not None and value != []}

        def update(current: dict) -> dict:
            # Applied to the profile as it stands once the case is held, so a change made
            # meanwhile by another window is kept (T13 review 2).
            return {**(replacement if replacement is not None else current), **changes}

        profile = store.update_profile(folder, update)
    console.print(escape(store._profile_bytes(profile).decode("utf-8")).rstrip())


@case_app.command("import")
def case_import(
    case_id: str = typer.Argument(..., metavar="CASE", help="The case id `cma case new` printed."),
    file: Path = typer.Argument(..., help="The bank file. Copied into the case; nothing is uploaded."),
    fmt: str = typer.Option(..., "--format", help="csv (the five-column statement), monzo (a Monzo export), "
                            "openbanking (an OBIE v3.1 transactions file), truelayer (a TrueLayer Data API "
                            "transactions export), or bank-csv:PROFILE (a bank's CSV download through a named "
                            "profile: barclays, lloyds, natwest or starling), or bank-csv with --mapping."),
    account: str = typer.Option("main", "--account", help="A short key for the account, such as main or savings."),
    opening_balance: str | None = typer.Option(
        None, help="Monzo or bank CSV only: the balance just before the first transaction, when the export has no "
                   "balance column."),
    mapping: Path | None = typer.Option(
        None, "--mapping", help="bank-csv only: a YAML column mapping (columns, date_format, order) for an export "
                                "no named profile covers."),
    statement_balance: str | None = typer.Option(
        None, help="Open Banking or TrueLayer only: a balance from the bank's statement, at the end of the date "
                   "--at, when the file has no per-line balances."),
    at: str | None = typer.Option(None, "--at", help="Open Banking or TrueLayer only: the date (YYYY-MM-DD) --statement-balance "
                                                     "is the closing balance of."),
):
    """Import a statement into a case: the original kept by hash, the statement per account (R31, R42)."""
    from . import store

    with _case_errors():
        folder = store.case_folder(case_id)
        r = store.import_file(folder, file, fmt, account=account, opening_balance=opening_balance,
                              statement_balance=statement_balance, at=at, mapping=mapping)
    if r.already:
        console.print(f"already imported (sha256 {r.input_sha256[:12]}); the case is unchanged")
        return
    console.print(f"imported account {r.account}: {r.lines} transactions, {r.start} to {r.end} "
                  f"(sha256 {r.input_sha256[:12]})")
    if r.pending:  # R36: pending lines are left out, and counted
        console.print(f"left out {r.pending} pending transaction{'s' if r.pending != 1 else ''}")
    if r.repeated:
        console.print(f"{r.repeated} transaction{'s' if r.repeated != 1 else ''} repeated across pages kept once")


@case_app.command("override")
def case_override(
    case_id: str = typer.Argument(..., metavar="CASE", help="The case id `cma case new` printed."),
    line: str = typer.Argument(..., help="The line's id in its statement."),
    category: str = typer.Argument(..., help="The category to give it; only one valid for the line's direction."),
    reason: str = typer.Option(..., "--reason", prompt="Reason", help="Why the line is this category."),
    name: str = typer.Option(..., "--name", prompt="Your name", help="Your name, as typed."),
    account: str = typer.Option("main", "--account", help="The account key the line is in."),
    expected: int | None = typer.Option(None, "--expected", help="The case log position you last saw."),
):
    """Change one line's category, with a reason and your name (R17). Only the category
    changes (R18); the override goes to the case's override log and its case log, and the
    figures are recomputed from the statements and the whole log."""
    from . import overrides, store

    with _case_errors():
        folder = store.case_folder(case_id)
        r = overrides.add_override(folder, line, category, reason=reason, name=name, account=account,
                                   expected=expected)
    if not r.recorded:
        console.print(f"line {escape(line)} is already {r.old}; nothing was recorded")
        return
    console.print(f"override {r.seq}: line {escape(line)} {r.old} -> {r.new}")


@case_app.command("overrides")
def case_overrides(case_id: str = typer.Argument(..., metavar="CASE", help="The case id `cma case new` printed.")):
    """List the case's overrides: applied, stale (until confirmed or dropped; R19),
    superseded by a later one, or dropped."""
    from . import overrides, store

    with _case_errors():
        rows = overrides.listing(store.case_folder(case_id))
    if not rows:
        console.print("no overrides")
    for o, status in rows:  # one line each, so a narrow terminal never splits a status
        console.print(f"{o.seq}  {o.account}  line {o.line}  {o.old} -> {o.new}  "
                      f"by {o.name} ({o.os_user}) at {o.at}  [{status}]", soft_wrap=True,
                      markup=False)


@case_app.command("resolve-override")
def case_resolve_override(
    case_id: str = typer.Argument(..., metavar="CASE", help="The case id `cma case new` printed."),
    seq: int = typer.Argument(..., help="The stale override's number."),
    confirm: bool = typer.Option(False, "--confirm", help="Apply it to the statement as it is now."),
    drop: bool = typer.Option(False, "--drop", help="Never apply it."),
    name: str = typer.Option(..., "--name", prompt="Your name", help="Your name, as typed."),
):
    """Confirm or drop a stale override (R19)."""
    from . import overrides, store

    if confirm == drop:
        console.print("[red]Refused.[/red] give one of --confirm or --drop")
        raise typer.Exit(code=2)
    with _case_errors():
        overrides.resolve_stale(store.case_folder(case_id), seq, "confirm" if confirm else "drop", name=name)
    console.print(f"override {seq} {'confirmed' if confirm else 'dropped'}")


@case_app.command("figures")
def case_figures(case_id: str = typer.Argument(..., metavar="CASE", help="The case id `cma case new` printed.")):
    """Print the case's figures, recomputed from its statements and the whole override log;
    a figure marked * rests on an overridden line (R17). Stale overrides are listed, not
    applied (R19)."""
    from . import overrides, store

    with _case_errors():
        views = overrides.case_figures(store.case_folder(case_id))
    for account, f in views.items():
        table = Table(title=f"Figures: account {account}")
        for col in ("ID", "Metric", "Value", "Lines", ""):
            table.add_column(col)
        for m in f.metrics:
            table.add_row(m.id, m.name, m.display_exact(), str(len(m.evidence)), "*" if m.id in f.marked else "")
        console.print(table)
        if f.marked:
            console.print(f"* rests on an overridden line ({len(f.overridden)} lines overridden)")
        for s in f.stale:
            console.print(f"[yellow]stale override {s.seq}[/yellow]: line {escape(s.line)} -> {s.new} "
                          f"({s.why}); not applied until confirmed or dropped")


@case_app.command("preview")
def case_preview(case_id: str = typer.Argument(..., metavar="CASE", help="The case id `cma case new` printed.")):
    """Print the pack's memo sections, in the memo field map's order, the policy check while
    show_policy_verdict is on, the grounding guard's verdict and the snapshot hash a sign-off
    will be bound to (R22, R26, R28). Writes no file: only a preview entry in the case log.
    Exit 1 when the text fails the grounding guard, 2 when the case cannot be previewed."""
    from . import pack, store

    with _case_errors():
        folder = store.case_folder(case_id)
        try:
            p = pack.build_preview(folder, case_id)
        except ReconciliationError as e:  # R3: no figures, so no sections
            console.print(f"[red]Refused.[/red] {escape(str(e))}")
            raise typer.Exit(code=2) from None
        except (pack.FieldMapError, pack.PreviewError, StatementError, AmountError, RulesetError,
                ThresholdsError, PolicyError) as e:
            console.print(f"[red]Refused.[/red] {escape(str(e))}; nothing was recorded.")
            raise typer.Exit(code=2) from None
    # The text quotes the case's own figures only; printed as it is, never read as markup.
    console.print(p.text, markup=False, highlight=False, soft_wrap=True)
    if not p.grounded:
        console.print("[red]The text fails the grounding guard[/red]; no pack can be signed off from "
                      "this preview (R28):")
        for account, number, why in p.findings():
            console.print(f"  account {escape(account)}: {escape(number or '-')}: {escape(why)}", markup=True)
    else:
        console.print("Grounding guard: every number is a figure or a policy threshold, with its id.")
    console.print(f"snapshot {p.snapshot}")
    console.print(f"recorded as case log entry {p.position}; nothing else was written")
    if not p.grounded:
        raise typer.Exit(code=1)


@case_app.command("sign")
def case_sign(
    case_id: str = typer.Argument(..., metavar="CASE", help="The case id `cma case new` printed."),
    snapshot: str | None = typer.Option(
        None, "--snapshot", help="The snapshot `cma case preview` printed; the latest preview's if not given."),
):
    """Sign off the figures of a preview, with your typed name, and write the pack once to
    packs/<snapshot> (R22, R24, R26). Interactive only: it asks for your name and confirms.
    Signing off the figures does not approve the loan. Refused, writing nothing, when the case
    changed since the preview (naming what), when the case log is broken (naming the first bad
    entry), when the figures do not reconcile or the text fails the grounding guard, or when
    the snapshot's pack is already written. An entry left unfinished is dropped with `cma case
    repair` first. Exit 2 refused, 3 broken log, 4 not recorded."""
    from . import case as caselog
    from . import pack, store

    with _case_errors():
        folder = store.case_folder(case_id)
        if snapshot is None:
            latest = pack.latest_preview(caselog.for_case(folder).entries())
            if latest is None:
                console.print("[red]Refused.[/red] this case has no preview yet: run `cma case preview` "
                              "first, read it, then sign off its figures.")
                raise typer.Exit(code=2)
            snapshot = latest[1]["snapshot"]
        console.print(f"Preview snapshot {escape(snapshot)}")
        console.print(pack.SIGN_WORDING)
        name = typer.prompt("Your name, to sign off the figures")
        if not typer.confirm(f"Sign off the figures of snapshot {snapshot[:12]} as {name}?", default=False):
            console.print("Not signed off; nothing was written.")
            raise typer.Exit(code=1)
        try:
            s = pack.sign_off(folder, case_id, snapshot, name)
        except pack.SignOffRefused as e:
            console.print(f"[red]Refused.[/red] {escape(str(e))}; nothing was written.")
            raise typer.Exit(code=2) from None
        except ReconciliationError as e:  # R3
            console.print(f"[red]Refused.[/red] {escape(str(e))}; nothing was written.")
            raise typer.Exit(code=2) from None
        except (pack.FieldMapError, pack.PreviewError, StatementError, AmountError, RulesetError,
                ThresholdsError, PolicyError, FirmConfigError) as e:
            console.print(f"[red]Refused.[/red] {escape(str(e))}; nothing was written.")
            raise typer.Exit(code=2) from None
    if s.completed:
        console.print(f"The pack for snapshot {s.snapshot} was already written; its sign-off by {escape(s.name)} "
                      f"(operating-system user {escape(s.os_user)}) at {s.at} was not recorded, and is now "
                      f"recorded as case log entry {s.position}. The pack was not rewritten.", soft_wrap=True)
        return
    console.print(f"Figures signed off by {escape(s.name)} (operating-system user {escape(s.os_user)}) "
                  f"at {s.at}, bound to snapshot {s.snapshot}.", soft_wrap=True)
    console.print(f"Pack written once to {escape(str(s.pack))}: {', '.join(s.files)}; recorded as case log "
                  f"entry {s.position}.", soft_wrap=True)


@case_app.command("repair")
def case_repair(case_id: str = typer.Argument(..., metavar="CASE", help="The case id `cma case new` printed.")):
    """Drop an entry the case log holds past its recorded entries, not finished because the
    tool, the disk or the power stopped while it was being saved, once you confirm it with
    your typed name (R42's repair, R43). The drop is itself recorded, with your name, so the
    log says who confirmed it. Interactive only. Refused, changing nothing, when no entry is
    torn or when the log is broken before it (that is not a torn save). Exit 2 refused,
    3 broken log, 4 not recorded or another window is saving."""
    from . import case as caselog
    from . import store

    with _case_errors():
        folder = store.case_folder(case_id)
        log = caselog.for_case(folder)
        check = log.verify()
        if not check.checked or check.in_progress:
            console.print("[red]Not repaired.[/red] another window or command is saving to this case; "
                          "nothing was changed. Try again in a moment.")
            raise typer.Exit(code=4)
        if check.ok:
            console.print("[red]Refused.[/red] the case log is intact: nothing is torn, and nothing was changed.")
            raise typer.Exit(code=2)
        if not check.torn:
            raise caselog.CaseLogBroken(log.path, check.first_bad, check.reason, False)
        console.print(f"Case log entry {check.first_bad} {escape(check.reason)}.")
        console.print(f"Entries 1 to {check.head.position} are recorded and intact. Dropping entry "
                      f"{check.first_bad} removes only what was not finished; its save was not recorded.")
        name = typer.prompt("Your name, to confirm dropping it")
        if not any(ch.isalnum() for ch in name):  # as a sign-off and an override ask (R17, R22)
            console.print("[red]Refused.[/red] type your name, with at least one letter or digit; "
                          "nothing was changed.")
            raise typer.Exit(code=2)
        if not typer.confirm(f"Drop the unfinished entry {check.first_bad} as {name}?", default=False):
            console.print("Not repaired; nothing was changed.")
            raise typer.Exit(code=1)
        try:
            entry = log.drop_torn_entry(expected=check.head.position, by=name)
        except ValueError as e:
            console.print(f"[red]Refused.[/red] {escape(str(e))}; nothing was changed.")
            raise typer.Exit(code=2) from None
    console.print(f"Dropped the unfinished entry {check.first_bad}; recorded as case log entry {entry.seq}, "
                  f"confirmed by {escape(name.strip())}. Save or sign off again what it held.", soft_wrap=True)


@case_app.command("purge")
def case_purge(
    case_id: str = typer.Argument(..., metavar="CASE", help="The case id `cma case new` printed."),
    yes: bool = typer.Option(False, "--yes", help="Do not ask before deleting."),
):
    """Delete every file of a case, and record only its id, the time and your operating-
    system user in the firm's purge ledger (purge_ledger.jsonl at the case root; R35).
    This cannot be undone."""
    from . import store

    with _case_errors():
        store.purge_target(case_id)  # an unknown id is refused before anything is asked
    if not yes and not typer.confirm(f"Delete every file of case {case_id}? This cannot be undone",
                                     default=False):
        console.print("Not purged; nothing was deleted.")
        raise typer.Exit(code=1)
    with _case_errors():
        record = store.purge_case(case_id)
    console.print(f"purged case {record.case} at {record.purged_at} by {escape(record.by)}; "
                  f"recorded in {store.PURGE_LEDGER}")


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
        if check.in_progress:
            console.print("Case log is being saved by another window or command; nothing could "
                          "be checked yet. Check again in a moment.")
        else:
            console.print(f"Case log {check.reason}. Check again in a moment.")
        raise typer.Exit(code=4)
    if not check.ok:
        console.print(f"[red]Case log broken:[/red] entry {check.first_bad} {check.reason}.")
        console.print("Nothing may be recorded or signed off on this case until it is resolved.")
        if check.torn:
            console.print(TORN_REMEDY)
        raise typer.Exit(code=3)
    if check.in_progress:  # R43, D75: being saved is never intact, so not exit 0
        console.print(f"Case log is being saved by another window or command: its "
                      f"{check.head.position} recorded entries check out, the rest is not "
                      "checked yet. Check again in a moment.")
        raise typer.Exit(code=4)
    console.print(f"case log intact: {check.head.position} entries, head {check.head.hash[:12]}")


@app.command("review")
def review_workspace(
    case_id: str = typer.Argument(..., metavar="CASE", help="The case id `cma case new` printed."),
    port: int = typer.Option(8765, "--port", min=1, max=65535,
                             help="The loopback port to try first; the next free one is taken."),
    browser: bool = typer.Option(True, "--browser/--no-browser", help="Open the URL in a browser."),
):
    """Start the review workspace for a case: a page on this machine only (127.0.0.1), with
    a new random token in its URL, which is printed. Stop it with Ctrl-C (R33)."""
    from . import store
    from .review import server as review_server

    with _case_errors():
        folder = store.case_folder(case_id)
        try:
            srv = review_server.start(folder, case_id, port=port)
        except review_server.NoFreePort as e:
            raise store.CaseStoreError(str(e.strerror)) from None
    # The URL is the only way in: anyone who has it can read the case while this runs.
    console.print(f"Review workspace for case {case_id}, on this machine only:")
    console.print(srv.url, soft_wrap=True, markup=False, highlight=False)
    console.print("Keep this URL private. Press Ctrl-C to stop.")
    if browser:
        import webbrowser

        with suppress(Exception):  # no browser is fine; the URL is printed
            webbrowser.open(srv.url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()
    console.print("Review workspace stopped.")


@app.command()
def truth(path: Path):
    """Print the ground truth for a generated statement."""
    console.print_json(json.dumps(json.loads(Path(path).read_text()), indent=2))


if __name__ == "__main__":
    app()
