"""statement-review T02 (R53) against the real previous release, not a model of it.

Each test extracts the source at previous_release.BASE and runs it in a child
interpreter: a checkpoint written by the old `cma run` resumes under this `cma resume`;
the old Transaction loads what this one writes; and M1-M16 are unchanged."""

import json
import sqlite3
from datetime import date

import previous_release as prev
import pytest
from typer.testing import CliRunner

from cma import synth
from cma.cli import app
from cma.models import EVIDENCE_SCHEMA_VERSION, Category, Transaction

OLD_FIELDS = {"id", "date", "description", "amount", "balance", "category", "reverses"}


def _stored_transaction_keys(blob: bytes) -> list[set[str]]:
    """The field names of every Transaction in a msgpack checkpoint blob, whether stored
    on its own or as a line of a stored Statement."""
    import ormsgpack
    from langgraph.checkpoint.serde.jsonplus import EXT_PYDANTIC_V2

    found: list[set[str]] = []
    opt = ormsgpack.OPT_NON_STR_KEYS

    def hook(code, data):
        v = ormsgpack.unpackb(data, ext_hook=hook, option=opt)
        if code == EXT_PYDANTIC_V2 and v[0] == "cma.models":
            if v[1] == "Transaction":
                found.append(set(v[2]))
            elif v[1] == "Statement":
                found.extend(set(t) for t in v[2]["transactions"])
        return None

    ormsgpack.unpackb(blob, ext_hook=hook, option=opt)
    return found


@pytest.fixture(scope="module")
def old_src(tmp_path_factory):
    return prev.extract(tmp_path_factory.mktemp("previous-release"))


@pytest.mark.contracts
def test_the_previous_release_is_really_the_old_code(old_src, tmp_path):
    # The child runs the old package: none of the T02 additions exist there.
    got = prev.python(old_src, (
        "from cma.models import Category, Transaction\n"
        "print(json.dumps({'cats': [c.value for c in Category],"
        " 'fields': sorted(Transaction.model_fields)}))"), tmp_path)
    assert "cash_deposit" not in got["cats"] and len(got["cats"]) == 13
    assert got["fields"] == ["amount", "balance", "category", "date", "description", "id", "reverses"]


@pytest.mark.contracts
def test_a_checkpoint_written_by_the_previous_release_resumes(old_src, tmp_path):
    import langgraph.checkpoint.sqlite  # noqa: F401  (needs the 'durable' extra; fails, not skips)

    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv, _ = synth.write(rows, truth, tmp_path)
    db = tmp_path / "legacy.sqlite"

    # The old `cma run` drafts the memo and stops at the review prompt; EOF leaves it pending.
    r = prev.run(old_src, ["-c", "from cma.cli import app; app()", "run", str(csv),
                           "--checkpoint-db", str(db), "--thread", "t02-legacy"], tmp_path)
    assert "review id: t02-legacy" in r.stdout, r.stdout + r.stderr
    assert r.returncode != 0  # aborted at the prompt, so nothing was decided

    # What the old release stored: Transactions without any of the new fields.
    con = sqlite3.connect(db)
    blobs = [b for (b,) in con.execute("SELECT checkpoint FROM checkpoints")]
    con.close()
    stored = [keys for b in blobs for keys in _stored_transaction_keys(b)]
    assert stored and all(k == OLD_FIELDS for k in stored)

    # This release picks it up and approves it.
    out = tmp_path / "t02-legacy.md"
    r = CliRunner().invoke(app, ["resume", "t02-legacy", "--checkpoint-db", str(db),
                                 "--reviewer", "C. Later", "--out", str(out)], input="y\n\n")
    assert r.exit_code == 0, r.output
    assert "APPROVED by C. Later" in out.read_text()
    record = json.loads((tmp_path / "t02-legacy.evidence.json").read_text())
    assert record["schema_version"] == EVIDENCE_SCHEMA_VERSION == 1
    assert record["review"]["reviewer"] == "C. Later" and record["review"]["approved"] is True


@pytest.mark.contracts
def test_the_previous_release_loads_a_transaction_this_one_writes(old_src, tmp_path):
    # Rollback by reinstalling the previous release: its own Transaction model reads a
    # line saved by this one, keeps every field it knows, and ignores the new three.
    t = Transaction(id="T1", date=date(2026, 3, 5), description="X", amount=1.0, balance=1.0,
                    category=Category.REVENUE_CARD, account_key="savings", rule_id="v2:x",
                    provider_hint="CASH")
    payload = t.model_dump_json()
    got = prev.python(old_src, (
        "from cma.models import Transaction\n"
        f"print(Transaction.model_validate_json({payload!r}).model_dump_json())"), tmp_path)
    assert got == json.loads(t.model_dump_json(exclude={"account_key", "rule_id", "provider_hint"}))


def _metric_rows(metrics) -> list[tuple]:
    return [(m["id"], m["unit"], m["value"]) for m in metrics]


CASES = [(a, p) for a in synth.ARCHETYPES for p in synth.PROFILES]


@pytest.mark.dual_run
@pytest.mark.parametrize("archetype, profile", CASES)
def test_m1_to_m16_are_unchanged_from_the_previous_release(old_src, tmp_path, archetype, profile):
    # R53, R10: T02 adds categories and fields only; no committed or synthetic case has a
    # cash line, so every figure keeps its id, unit and value.
    from cma import categorise, ingest, metrics

    rows, truth = synth.generate(archetype, profile, end=date(2026, 9, 30))
    csv, _ = synth.write(rows, truth, tmp_path)
    new = [m.model_dump() for m in metrics.compute(categorise.categorise(ingest.load_csv(csv)))]
    old = prev.python(old_src, (
        "from cma import categorise, ingest, metrics\n"
        f"ms = metrics.compute(categorise.categorise(ingest.load_csv({str(csv)!r})))\n"
        "print(json.dumps([m.model_dump() for m in ms], default=str))"), tmp_path)
    assert _metric_rows(new) == _metric_rows(old)
    assert [m["evidence"] for m in new] == [m["evidence"] for m in old]
    assert [m["id"] for m in new if m["method"] != next(o for o in old if o["id"] == m["id"])["method"]] == ["M2"]


@pytest.mark.dual_run
def test_the_committed_example_is_unchanged_from_the_previous_release(old_src, tmp_path):
    from cma import categorise, ingest, metrics

    csv = prev.REPO / "examples" / "cafe_stretched.csv"
    new = [m.model_dump() for m in metrics.compute(categorise.categorise(ingest.load_csv(csv)))]
    old = prev.python(old_src, (
        "from cma import categorise, ingest, metrics\n"
        f"ms = metrics.compute(categorise.categorise(ingest.load_csv({str(csv)!r})))\n"
        "print(json.dumps([m.model_dump() for m in ms], default=str))"), tmp_path)
    assert _metric_rows(new) == _metric_rows(old)
    assert [m["evidence"] for m in new] == [m["evidence"] for m in old]


@pytest.mark.contracts
def test_all_extras_are_installed():
    # The resume, MCP and hosted-model contracts are only proven with every extra present
    # (pip install -e ".[dev,mcp,durable,llm]"); a missing one fails here instead of
    # silently skipping a test elsewhere.
    import anthropic  # noqa: F401
    import langgraph.checkpoint.sqlite  # noqa: F401
    import mcp  # noqa: F401
    import openai  # noqa: F401


@pytest.mark.contracts
def test_a_second_run_with_the_same_out_is_refused_and_leaves_the_record(tmp_path):
    # Idempotency of the evidence writer: two runs give the same key set, and a replay
    # onto an existing --out is refused without touching the memo or its evidence.
    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv, _ = synth.write(rows, truth, tmp_path)
    runner = CliRunner()
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    for out in (a, b):
        r = runner.invoke(app, ["run", str(csv), "--yes", "--reviewer", "A", "--out", str(out)])
        assert r.exit_code == 0, r.output
    ev_a, ev_b = tmp_path / "a.evidence.json", tmp_path / "b.evidence.json"

    def keys(p):
        from test_models import _key_paths
        return _key_paths(json.loads(p.read_text()))

    assert keys(ev_a) == keys(ev_b)
    before = (a.read_bytes(), ev_a.read_bytes())
    r = runner.invoke(app, ["run", str(csv), "--yes", "--reviewer", "A", "--out", str(a)])
    assert r.exit_code == 2 and "Refusing to overwrite" in r.output
    assert (a.read_bytes(), ev_a.read_bytes()) == before
