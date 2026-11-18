"""Category values, the money-in bucket map and the optional Transaction fields
(statement-review T02: R53, R55)."""

import json
import sqlite3
from datetime import date
from typing import Any

import pytest
from typer.testing import CliRunner

from cma import synth
from cma.cli import app
from cma.models import CREDIT_BUCKET, REVENUE_CATEGORIES, Bucket, Category, Transaction

NEW_FIELDS = ("account_key", "rule_id", "provider_hint")


def test_existing_category_values_are_unchanged():
    # An old checkpoint or evidence record names categories by value; none may move.
    assert {(c.name, c.value) for c in Category} >= set({
        "REVENUE_CARD": "revenue_card_settlement", "REVENUE_TRANSFER": "revenue_transfer",
        "PAYROLL": "payroll", "HMRC_VAT": "hmrc_vat", "HMRC_PAYE": "hmrc_paye", "RENT": "rent",
        "SUPPLIER": "supplier", "LOAN_REPAYMENT": "loan_repayment", "LOAN_DRAWDOWN": "loan_drawdown",
        "BANK_FEES": "bank_fees", "OWNER_DRAWINGS": "owner_drawings", "RETURNED_DD": "returned_dd",
        "OTHER": "other",
    }.items())


def test_new_category_values():
    assert {c.value for c in Category} >= {
        "cash_deposit", "own_account_transfer", "director_funding", "refund_in",
        "hmrc_repayment", "other_explained_in",
    }
    assert len(Category) == 13 + 6


@pytest.mark.parametrize("category, bucket", [
    (Category.REVENUE_CARD, Bucket.TRADING_RECEIPTS),
    (Category.REVENUE_TRANSFER, Bucket.TRADING_RECEIPTS),
    (Category.CASH_DEPOSIT, Bucket.TRADING_RECEIPTS),
    (Category.OWN_ACCOUNT_TRANSFER, Bucket.OWN_ACCOUNT_IN),
    (Category.DIRECTOR_FUNDING, Bucket.DIRECTOR_FUNDING),
    (Category.LOAN_DRAWDOWN, Bucket.LENDING_DRAWDOWNS),
    (Category.REFUND_IN, Bucket.REFUNDS_RETURNED),
    (Category.RETURNED_DD, Bucket.REFUNDS_RETURNED),
    (Category.HMRC_REPAYMENT, Bucket.HMRC_REPAYMENTS),
    (Category.OTHER_EXPLAINED_IN, Bucket.OTHER_EXPLAINED_IN),
    (Category.OTHER, Bucket.UNEXPLAINED),
])
def test_each_credit_category_has_one_bucket(category, bucket):
    assert CREDIT_BUCKET[category] is bucket


def test_bucket_map_covers_exactly_the_credit_categories():
    # A credit in any category not in the map is a reconciliation failure (R3).
    assert set(CREDIT_BUCKET.values()) == set(Bucket)
    assert len(Bucket) == 8
    debit_only = {Category.PAYROLL, Category.HMRC_VAT, Category.HMRC_PAYE, Category.RENT,
                  Category.SUPPLIER, Category.LOAN_REPAYMENT, Category.BANK_FEES,
                  Category.OWNER_DRAWINGS}
    assert set(CREDIT_BUCKET) == set(Category) - debit_only


def test_cash_deposits_are_trading_receipts():
    # R55, D31: cash paid in counts in M2 and every figure built on trading receipts.
    assert Category.CASH_DEPOSIT in REVENUE_CATEGORIES
    assert {c for c, b in CREDIT_BUCKET.items() if b is Bucket.TRADING_RECEIPTS} == REVENUE_CATEGORIES


def test_new_transaction_fields_default():
    t = Transaction(id="T1", date=date(2026, 3, 5), description="X", amount=1.0, balance=1.0)
    assert (t.account_key, t.rule_id, t.provider_hint) == ("main", None, None)
    t = Transaction(id="T1", date=date(2026, 3, 5), description="X", amount=1.0, balance=1.0,
                    account_key="savings", rule_id="v2:cash_counter", provider_hint="CASH")
    assert (t.account_key, t.rule_id, t.provider_hint) == ("savings", "v2:cash_counter", "CASH")


def test_a_transaction_dumped_before_the_change_still_loads():
    old = {"id": "T1", "date": "2026-03-05", "description": "X", "amount": 1.0, "balance": 1.0,
           "category": "revenue_card_settlement", "reverses": None}
    t = Transaction(**old)
    assert t.category is Category.REVENUE_CARD and t.account_key == "main"


# --- an old checkpoint resumes (R53) ---------------------------------------------------

class _Ext:
    """A msgpack extension held open for editing. Not a dataclass: ormsgpack would pack
    one as a map instead of handing it to `default`."""

    def __init__(self, code: int, value: Any):
        self.code, self.value = code, value


def _strip_new_fields(blob: bytes) -> tuple[bytes, int]:
    """Rewrite a msgpack checkpoint blob as the version before this change wrote it: every
    stored Transaction without the new fields. Returns the blob and how many were stripped."""
    import ormsgpack
    from langgraph.checkpoint.serde.jsonplus import EXT_PYDANTIC_V2

    opt = ormsgpack.OPT_NON_STR_KEYS
    stripped = 0

    def hook(code, data):
        return _Ext(code, ormsgpack.unpackb(data, ext_hook=hook, option=opt))

    def strip(fields: dict) -> None:
        nonlocal stripped
        assert all(f in fields for f in NEW_FIELDS)
        for f in NEW_FIELDS:
            del fields[f]
        stripped += 1

    def walk(o):
        if isinstance(o, _Ext):
            v = o.value
            if o.code == EXT_PYDANTIC_V2 and v[0] == "cma.models":
                # A stored Statement holds its lines as plain dicts (model_dump recurses).
                if v[1] == "Transaction":
                    strip(v[2])
                elif v[1] == "Statement":
                    for t in v[2]["transactions"]:
                        strip(t)
            o.value = walk(v)
        elif isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        elif isinstance(o, list):
            return [walk(v) for v in o]
        return o

    def default(o):
        if isinstance(o, _Ext):
            return ormsgpack.Ext(o.code, ormsgpack.packb(o.value, default=default, option=opt))
        raise TypeError(type(o))

    tree = walk(ormsgpack.unpackb(blob, ext_hook=hook, option=opt))
    return ormsgpack.packb(tree, default=default, option=opt), stripped


def _strip_saved(o, counter: list[int]):
    """Walk an InMemorySaver's stores and rewrite every msgpack blob in the old shape."""
    if isinstance(o, tuple) and len(o) == 2 and o[0] == "msgpack" and isinstance(o[1], bytes):
        new, n = _strip_new_fields(o[1])
        counter[0] += n
        return ("msgpack", new)
    if isinstance(o, tuple):
        return tuple(_strip_saved(v, counter) for v in o)
    if isinstance(o, dict):
        return {k: _strip_saved(v, counter) for k, v in o.items()}
    return o


@pytest.mark.contracts
def test_a_pending_review_saved_before_the_new_fields_resumes(tmp_path):
    # Needs no optional extra: the same serializer and allow-list the SQLite saver uses,
    # over the in-memory saver. The stored state is rewritten as the old version wrote it,
    # handed to a fresh saver and graph (a restart), and the review is resumed to the end.
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    from cma.graph import _serde, build
    from cma.llm import get_llm

    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv, _ = synth.write(rows, truth, tmp_path)
    cfg = {"configurable": {"thread_id": "case-old"}}
    before = InMemorySaver(serde=_serde())
    state = build(get_llm("fake"), checkpointer=before).invoke(
        {"statement_path": str(csv), "policy_path": None, "vat_stagger": None}, cfg)
    assert state["__interrupt__"]

    counter = [0]
    after = InMemorySaver(serde=_serde())
    for name in ("storage", "writes", "blobs"):
        old = _strip_saved(dict(getattr(before, name)), counter)
        store = getattr(after, name)
        for k, v in old.items():
            store[k] = {ns: dict(c) for ns, c in v.items()} if name == "storage" else v
    assert counter[0] > 0  # the stored state really held Transactions, now in the old shape

    graph = build(get_llm("fake"), checkpointer=after)
    snap = graph.get_state(cfg)
    assert snap.interrupts
    lines = snap.values["statement"].transactions
    assert lines and all((t.account_key, t.rule_id, t.provider_hint) == ("main", None, None)
                         for t in lines)
    final = graph.invoke(Command(resume={"approved": True, "reviewer": "C. Later", "notes": "",
                                         "override_grounding": False}), cfg)
    assert "APPROVED by C. Later" in final["final_memo"]


@pytest.mark.contracts
def test_a_checkpoint_written_before_the_new_fields_resumes(tmp_path):
    # The same, end to end through `cma run` and `cma resume`. This is the contract the
    # task names, so it runs with all extras installed and fails, not skips, without the
    # 'durable' extra: a skipped resume test proves nothing.
    import langgraph.checkpoint.sqlite  # noqa: F401  (pip install -e ".[durable]")
    runner = CliRunner()
    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv, _ = synth.write(rows, truth, tmp_path)
    db = tmp_path / "reviews.sqlite"
    r = runner.invoke(app, ["run", str(csv), "--checkpoint-db", str(db), "--thread", "case-old"], input="\x04")
    assert "review id: case-old" in r.output

    total = 0
    con = sqlite3.connect(db)
    for table, col in (("checkpoints", "checkpoint"), ("writes", "value")):
        for rowid, type_, blob in con.execute(f"SELECT rowid, type, {col} FROM {table}").fetchall():
            if type_ == "msgpack" and blob:
                new, n = _strip_new_fields(blob)
                total += n
                con.execute(f"UPDATE {table} SET {col} = ? WHERE rowid = ?", (new, rowid))
    con.commit()
    con.close()
    assert total > 0  # the stored state really held Transactions, now in the old shape

    out = tmp_path / "memo.md"
    r = runner.invoke(app, ["resume", "case-old", "--checkpoint-db", str(db), "--reviewer", "C. Later",
                            "--out", str(out)], input="y\n\n")
    assert r.exit_code == 0, r.output
    assert "APPROVED by C. Later" in out.read_text()


def test_m2_counts_and_names_cash_deposits():
    # R55: a cash deposit is in M2, cited by id, and M2's method says cash paid in counts.
    from cma import metrics
    from cma.models import Statement

    lines = [
        Transaction(id="T0", date=date(2026, 1, 2), description="Opening", amount=10.0,
                    balance=10.0, category=Category.REVENUE_CARD),
        Transaction(id="T1", date=date(2026, 3, 30), description="Cash paid in", amount=100.0,
                    balance=110.0, category=Category.CASH_DEPOSIT),
    ]
    m2 = next(m for m in metrics.compute(Statement(account_name="A", transactions=lines))
              if m.id == "M2")
    assert m2.value == 110.0 and "T1" in m2.evidence
    assert "cash paid in" in m2.method.lower()


# --- the evidence record `cma run` writes (R53) -----------------------------------------

# Every key path in the record `cma run` wrote before this change (examples/
# cafe_stretched.memo.evidence.json at cba9dba). Readers keyed on these must keep working.
TODAYS_EVIDENCE_KEYS = {
    "/guard", "/guard/grounded", "/guard/missing_citations", "/guard/numbers_checked",
    "/guard/unmatched", "/memo", "/metrics", "/metrics[]/evidence", "/metrics[]/id",
    "/metrics[]/method", "/metrics[]/name", "/metrics[]/unit", "/metrics[]/value", "/model",
    "/policy", "/policy/overall", "/policy/policy_name", "/policy/policy_sha256",
    "/policy/rules", "/policy/rules[]/description", "/policy/rules[]/detail",
    "/policy/rules[]/metric_id", "/policy/rules[]/op", "/policy/rules[]/outcome",
    "/policy/rules[]/refer_above", "/policy/rules[]/refer_below", "/policy/rules[]/rule_id",
    "/policy/rules[]/threshold", "/policy/rules[]/unit", "/redrafts", "/review",
    "/review/approved", "/review/decided_at", "/review/mode", "/review/notes",
    "/review/override_grounding", "/review/reviewer", "/statement", "/statement/end",
    "/statement/path", "/statement/sha256", "/statement/start", "/statement/transactions",
    "/vat_stagger",
}


def _key_paths(o, prefix: str = "") -> set[str]:
    out: set[str] = set()
    if isinstance(o, dict):
        for k, v in o.items():
            out |= {f"{prefix}/{k}"} | _key_paths(v, f"{prefix}/{k}")
    elif isinstance(o, list):
        for v in o:
            out |= _key_paths(v, f"{prefix}[]")
    return out


@pytest.mark.contracts
def test_todays_evidence_keys_are_a_subset_of_the_new_record(tmp_path):
    from cma.models import EVIDENCE_SCHEMA_VERSION

    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv, _ = synth.write(rows, truth, tmp_path)
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", str(csv), "--yes", "--reviewer", "A", "--out", str(out)])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    assert _key_paths(record) >= TODAYS_EVIDENCE_KEYS  # additive only
    assert record["schema_version"] == EVIDENCE_SCHEMA_VERSION == 1


@pytest.mark.contracts
def test_the_committed_example_evidence_carries_todays_keys_and_a_schema_version():
    from pathlib import Path

    from cma.models import EVIDENCE_SCHEMA_VERSION

    path = Path(__file__).parent.parent / "examples" / "cafe_stretched.memo.evidence.json"
    record = json.loads(path.read_text())
    assert _key_paths(record) >= TODAYS_EVIDENCE_KEYS
    assert record["schema_version"] == EVIDENCE_SCHEMA_VERSION
