"""T25: the pack's evidence JSON v1 and monthly.csv, written at sign-off (C6; R26, R29, R53,
R56, R34, P8).

evidence.json keeps every key `cma run`'s evidence record has today, in the same shapes and
with its policy key unchanged, for each account of the case, and adds the case id, the
accounts' import format and file hashes, the ruleset, firm-rules, thresholds and profile
hashes, the overrides, the money-in buckets, the policy check shown or hidden (with the label
and the policy hash), the snapshot, the sign-off, egress offline and the package version.
monthly.csv holds the monthly summary, and none of its text cells begins with a formula
character (P8)."""

import csv
import hashlib
import io
import json
import re
from datetime import date

import pytest
from typer.testing import CliRunner

from cma import case as caselog
from cma import overrides, pack, policy, store, synth
from cma.cli import app
from cma.importers.contract import FORMULA_START
from cma.models import EVIDENCE_SCHEMA_VERSION

pytestmark = pytest.mark.contracts

# The keys `cma run`'s evidence record has before this change (graph.evidence_record), and
# those of its policy key. R53: new evidence fields are additive; none of these goes.
TODAY_KEYS = {"schema_version", "statement", "model", "egress", "vat_stagger", "ruleset", "lines", "metrics",
              "monthly_summary", "thresholds", "indicators", "judgement_queue", "policy", "guard", "redrafts",
              "review", "memo",
              "package_version"}  # added by T38 so verify can name the version that made a record (R45)
POLICY_KEYS = {"overall", "policy_name", "policy_sha256", "rules"}
# The keys of today's record that describe its one statement; a pack carries them per account.
STATEMENT_KEYS = {"statement", "vat_stagger", "lines", "metrics", "monthly_summary", "thresholds", "indicators",
                  "judgement_queue", "policy", "guard"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    monkeypatch.setenv("CMA_RULESET", "v2")
    return root


def _synth_statement(tmp_path):
    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    path, _ = synth.write(rows, truth, tmp_path / "synth")
    return path


def _case(path):
    case_id = store.new_case(label="t25")
    folder = store.case_folder(case_id)
    store.import_file(folder, path, "csv")
    return case_id, folder


def _sign(case_id, folder):
    p = pack.build_preview(folder, case_id)
    s = pack.sign_off(folder, case_id, p.snapshot, "Ann Smith")
    return p, s, json.loads((s.pack / "evidence.json").read_text(encoding="utf-8"))


def _keys(items):
    return [sorted(x) for x in items]


def test_cma_run_evidence_keeps_todays_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("CMA_RULESET", "v2")
    (tmp_path / "synth").mkdir()
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", str(_synth_statement(tmp_path)), "--yes", "--reviewer", "A",
                                 "--out", str(out)])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    assert set(record) == TODAY_KEYS
    assert set(record["policy"]) == POLICY_KEYS
    assert record["schema_version"] == EVIDENCE_SCHEMA_VERSION


def test_pack_evidence_carries_todays_keys_per_account_in_the_same_shapes(home, tmp_path, monkeypatch):
    (tmp_path / "synth").mkdir()
    path = _synth_statement(tmp_path)
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", str(path), "--yes", "--reviewer", "A", "--out", str(out)])
    assert r.exit_code == 0, r.output
    run = json.loads((tmp_path / "memo.evidence.json").read_text())

    case_id, folder = _case(path)
    _, _, ev = _sign(case_id, folder)
    assert ev["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert {"schema_version", "model", "egress", "ruleset", "memo"} <= set(ev)
    (acct,) = ev["accounts"]
    assert set(acct) >= STATEMENT_KEYS
    # The same shapes as today's record, and the policy key unchanged.
    assert set(acct["statement"]) == set(run["statement"])
    assert acct["statement"]["sha256"] == run["statement"]["sha256"]
    assert acct["statement"]["transactions"] == run["statement"]["transactions"]
    assert set(acct["policy"]) == POLICY_KEYS and acct["policy"] == run["policy"]
    assert acct["metrics"] == json.loads(json.dumps(run["metrics"]))
    assert _keys(acct["lines"]) == _keys(run["lines"]) and acct["lines"] == run["lines"]
    assert acct["monthly_summary"] == run["monthly_summary"]
    assert set(acct["thresholds"]) == set(run["thresholds"]) and acct["thresholds"] == run["thresholds"]
    assert acct["indicators"] == run["indicators"]
    assert acct["judgement_queue"] == run["judgement_queue"]
    assert set(acct["guard"]) == set(run["guard"])
    assert set(ev["egress"]) >= {"destination", "offline_lock", "recorded"}
    assert ev["ruleset"] == run["ruleset"]


def test_pack_evidence_v1_names_every_input_the_sign_off_and_offline_egress(home, tmp_path):
    from cma import __version__

    (tmp_path / "synth").mkdir()
    case_id, folder = _case(_synth_statement(tmp_path))
    line = overrides.case_figures(folder)["main"].statement.transactions[0]
    target = next(c for c in overrides.valid_categories(line) if c != line.category)
    overrides.add_override(folder, line.id, target.value, reason="checked the invoice", name="Ann")
    p, s, ev = _sign(case_id, folder)
    assert ev["case"] == case_id and ev["snapshot"] == p.snapshot
    (acct,) = ev["accounts"]
    imported = next(e.data for e in caselog.for_case(folder).entries() if e.event == "import")
    assert acct["account"] == "main" and acct["format"] == "csv"
    assert acct["input_sha256"] == imported["input_sha256"]
    assert acct["statement_sha256"] == imported["statement_sha256"]
    assert acct["period"] == {"start": imported["start"], "end": imported["end"]}
    assert acct["transactions"] == imported["lines"] == len(acct["lines"])
    assert ev["ruleset"] == p.inputs["ruleset"] and set(ev["ruleset"]) >= {"id", "sha256"}
    for key in ("firm_rules_sha256", "thresholds_sha256", "profile_sha256"):
        assert key in ev and ev[key] == p.inputs[key]
    assert ev["thresholds_sha256"] == acct["thresholds"]["sha256"]
    (o,) = ev["overrides"]
    assert o == {"seq": 1, "account": "main", "line": line.id, "old_category": line.category.value,
                 "new_category": target.value, "reason": "checked the invoice", "name": "Ann",
                 "os_user": store._os_user(), "at": o["at"], "statement_sha256": acct["statement_sha256"],
                 "status": "applied"}
    assert acct["overridden"] == {line.id: overrides.ref(1)}
    # the money-in buckets, from the figures layer, with their lines (R1)
    by_id = {m["id"]: m for m in acct["metrics"]}
    mi = acct["money_in"]
    assert mi["total"]["id"] == "M17" and mi["total"]["value"] == by_id["M17"]["value"]
    assert [b["id"] for b in mi["buckets"]] == ["M2", "M19", "M20", "M21", "M22", "M23", "M24", "M25"]
    assert all(b["evidence"] == by_id[b["id"]]["evidence"] for b in mi["buckets"])
    assert mi["cash_within_trading"]["id"] == "M18"
    assert acct["monthly_summary"] and acct["indicators"] is not None
    # the policy check, shown, with its label and hash (R25)
    pc = ev["policy_check"]
    assert pc["shown"] is True and pc["label"] == policy.ILLUSTRATIVE_LABEL
    assert pc["policy_sha256"] == p.inputs["policy_sha256"] and acct["policy"]["overall"]
    so = ev["sign_off"]
    assert so["name"] == "Ann Smith" and so["snapshot"] == p.snapshot and so["at"] == s.at
    assert ev["egress"]["destination"] == "offline" and ev["egress"]["offline_lock"] is True
    assert ev["package_version"] == __version__
    assert ev["memo"] == p.text and ev["model"] is None
    # ids, never a description (R30, R34)
    text = (s.pack / "evidence.json").read_text(encoding="utf-8")
    assert all(t.description not in text for t in overrides.case_figures(folder)["main"].statement.transactions
               if len(t.description) > 12)


def test_pack_evidence_records_the_policy_check_hidden(home, tmp_path):
    (tmp_path / "synth").mkdir()
    case_id, folder = _case(_synth_statement(tmp_path))
    (home / "firm.yaml").write_text("show_policy_verdict: false\n", encoding="utf-8")
    p, _, ev = _sign(case_id, folder)
    pc = ev["policy_check"]
    assert pc["shown"] is False and pc["hidden"] is True and "label" not in pc
    assert pc["policy_sha256"] == p.inputs["policy_sha256"]
    assert all(a["policy"] is None for a in ev["accounts"])  # R56: no outcome, no rule result


# --- monthly.csv ---------------------------------------------------------------------------

HOSTILE = ["=HYPERLINK(1)", "+SUM(A1)", "-2+3", "@cmd", "=1"]
SPACED = ["TXN 001", "TXN 002", "L3", "TXN  4", "L 5"]  # ids may hold spaces


def _hostile_statement(ids):
    rows = ["id,date,description,amount,balance"]
    bal = 0
    for n, (lid, day, amt) in enumerate(zip(ids, ("2026-01-05", "2026-01-20", "2026-02-03", "2026-02-10",
                                                   "2026-03-01"), (12345, -5000, 8000, -20000, 1), strict=True)):
        bal += amt
        desc = ["=CMD()", "+FPS CREDIT", "-CARD SETTLEMENT STRIPE", "@CASH PAID IN", "\tTAB"][n]
        rows.append(f"{lid},{day},\"{desc}\",{amt / 100:.2f},{bal / 100:.2f}")
    return "\n".join(rows) + "\n"


def _csv_rows(s):
    data = (s.pack / "monthly.csv").read_bytes().decode("utf-8")
    return list(csv.DictReader(io.StringIO(data, newline="")))


@pytest.mark.invariants
@pytest.mark.parametrize("ids", [HOSTILE, SPACED, ["L1", "L2", "L3", "L4", "L5"]])
def test_p8_monthly_csv_text_cells_never_begin_with_a_formula_character(home, tmp_path, ids):
    src = tmp_path / "s.csv"
    src.write_text(_hostile_statement(ids), encoding="utf-8")
    case_id, folder = _case(src)
    _p, s, _ev = _sign(case_id, folder)
    rows = _csv_rows(s)
    assert list(rows[0]) == list(pack.MONTHLY_COLUMNS)
    values = {f"{c}" for c in pack.MONTHLY_CELLS}
    for row in rows:
        for col, cell in row.items():
            if col in values:  # a number, never text: money to the penny, days whole
                assert cell == "" or re.fullmatch(r"-?\d+(\.\d\d)?", cell), (col, cell)
                continue
            assert not cell.startswith(FORMULA_START), (col, cell)
    # every line id is there, neutralised where it needed to be
    seen = {x for row in rows for c in pack.MONTHLY_CELLS for x in json.loads(row[f"{c}_lines"])}
    assert set(ids) <= seen  # each id exactly, spaces and formula characters kept
    assert all(row[f"{c}_lines"].startswith("[") for row in rows for c in pack.MONTHLY_CELLS)


def test_monthly_csv_is_the_monthly_summary_to_the_penny(home, tmp_path):
    src = tmp_path / "s.csv"
    src.write_text(_hostile_statement(["L1", "L2", "L3", "L4", "L5"]), encoding="utf-8")
    case_id, folder = _case(src)
    _p, s, ev = _sign(case_id, folder)
    rows = _csv_rows(s)
    (acct,) = ev["accounts"]
    months = acct["monthly_summary"]
    assert [r["month"] for r in rows] == [m["month"] for m in months]
    assert all(r["account"] == "main" for r in rows)
    jan = rows[0]
    assert jan["money_in"] == "123.45" and jan["money_out"] == "50.00"
    for r, m in zip(rows, months, strict=True):
        assert r["part_month"] == ("yes" if m["part_month"] else "no")
        for c in pack.MONTHLY_CELLS:
            cell = m[c]
            assert json.loads(r[f"{c}_lines"]) == cell["evidence"]
            assert r[f"{c}_note"] == (cell["note"] or "")
            if cell["value"] is None:
                assert r[c] == ""
            elif cell["unit"] == "GBP":
                assert r[c] == f"{cell['value']:.2f}"
            else:
                assert int(r[c]) == cell["value"]
    # recorded as part of the pack, by hash (R42)
    signed = caselog.for_case(folder).entries()[-1].data
    assert signed["files"]["monthly.csv"] == hashlib.sha256((s.pack / "monthly.csv").read_bytes()).hexdigest()
