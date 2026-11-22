"""P7 and R54 checked against the code before T01, and against the installed wheel.

tests/test_rules.py compares v1 with the committed golden. These tests check the golden
and the packaging themselves:
- the golden is rebuilt from the pre-change commit (PRE_CHANGE, kept with its capture
  tools in tests/golden/p7_tools/) and must equal the committed file on every key;
- a review left pending in a --checkpoint-db by that commit resumes under this code;
- the built wheel carries v1.yaml byte for byte, and loads it from outside the checkout;
- a missing rules package is refused like a missing table, not blamed on the model.

They need git and the pre-change commit in the repository (CI fetches full history), and
pip able to build a wheel.
"""

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import venv
from datetime import date
from pathlib import Path

import pytest

from cma import categorise, synth
from cma.categorise import load_ruleset

ROOT = Path(__file__).parents[1]
TOOLS = ROOT / "tests" / "golden" / "p7_tools"
GOLDEN = ROOT / "tests" / "golden" / "p7_v1_baseline.json.gz"
PRE_CHANGE = "fd7b64c"  # the last commit with the rule table as code


def _env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "CMA_RULESET")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


@pytest.fixture(scope="session")
def pre_change(tmp_path_factory) -> Path:
    """A clean export of the pre-change commit."""
    if shutil.which("git") is None or subprocess.call(
            ["git", "cat-file", "-e", f"{PRE_CHANGE}^{{commit}}"], cwd=ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
        pytest.skip(f"needs git and commit {PRE_CHANGE} (a full clone)")
    out = tmp_path_factory.mktemp("pre_change")
    archive = subprocess.run(["git", "archive", PRE_CHANGE], cwd=ROOT, check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(out)], input=archive, check=True)
    assert not (out / "src" / "cma" / "rules").exists(), "not the commit before the table moved"
    return out


@pytest.mark.dual_run
@pytest.mark.invariants
def test_p7_golden_is_rebuilt_exactly_from_the_pre_change_commit(pre_change, tmp_path):
    """Run the pre-change suite with the capture plugin, rebuild the baseline from the
    capture with the pre-change code, and compare with the committed golden."""
    capture, rebuilt = tmp_path / "capture.json", tmp_path / "p7.json"
    suite = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "capture_plugin", "-p", "no:cacheprovider"],
        cwd=pre_change, capture_output=True, text=True,
        env=_env(CAPTURE_OUT=str(capture), PYTHONPATH=f"{pre_change / 'src'}{os.pathsep}{TOOLS}"))
    assert suite.returncode == 0, suite.stdout[-2000:]
    assert " skipped" not in suite.stdout.splitlines()[-1], "a capturing test was skipped: " + suite.stdout[-800:]
    make = subprocess.run(
        [sys.executable, str(TOOLS / "make_p7_baseline.py"), str(capture), str(rebuilt)],
        cwd=pre_change, capture_output=True, text=True,
        env=_env(MADE_AT=PRE_CHANGE, PYTHONPATH=str(pre_change / "src")))
    assert make.returncode == 0, make.stderr[-2000:]
    assert f"cma from {pre_change / 'src' / 'cma'}" in make.stdout, make.stdout

    got = json.loads(rebuilt.read_text())
    want = json.loads(gzip.decompress(GOLDEN.read_bytes()))
    assert want["made_at_commit"] == PRE_CHANGE
    assert set(got) == set(want)
    for key in sorted(want):  # _about included
        assert got[key] == want[key], f"golden key {key!r} differs from the pre-change rebuild"
    assert got == want
    assert len(got["synthetic"]) == 13 and len(got["examples"]) == 1 and len(got["fixtures"]) == 48


@pytest.mark.contracts
@pytest.mark.dual_run
def test_a_review_left_pending_by_the_pre_change_version_resumes_here(pre_change, tmp_path, monkeypatch):
    """`cma run --checkpoint-db` under the pre-change commit leaves a review pending in a
    real SQLite file. Resumed here with a damaged v1.yaml it is refused with nothing
    recorded; with the table repaired the same review resumes and its evidence names v1,
    inferred."""
    pytest.importorskip("langgraph.checkpoint.sqlite")
    from typer.testing import CliRunner

    from cma.cli import app
    from cma.graph import build, sqlite_checkpointer
    from cma.llm import FakeLLM

    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv_path, _ = synth.write(rows, truth, tmp_path)
    db = tmp_path / "reviews.db"
    old = subprocess.run(
        [sys.executable, "-m", "cma.cli", "run", str(csv_path), "--checkpoint-db", str(db), "--thread", "old-1"],
        cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        env=_env(PYTHONPATH=str(pre_change / "src")))
    assert "review id: old-1" in old.stdout, old.stdout + old.stderr
    with sqlite3.connect(db) as c:
        assert c.execute("select count(*) from checkpoints where thread_id = 'old-1'").fetchone()[0] > 0

    def pending():
        with sqlite_checkpointer(db) as saver:
            snap = build(FakeLLM(), checkpointer=saver).get_state({"configurable": {"thread_id": "old-1"}})
        return snap

    snap = pending()
    assert snap.interrupts and "ruleset" not in snap.values and snap.values.get("review") is None

    text = (ROOT / "src" / "cma" / "rules" / "v1.yaml").read_text()
    monkeypatch.setattr(categorise, "_table_bytes",
                        lambda rid: text.replace("category: returned_dd", "category: [returned_dd]", 1).encode())
    load_ruleset.cache_clear()
    out = tmp_path / "memo.md"
    args = ["resume", "old-1", "--checkpoint-db", str(db), "--reviewer", "C", "--out", str(out)]
    try:
        r = CliRunner().invoke(app, args, input="y\n\n")
        assert r.exit_code == 2, r.output
        assert "Refused, nothing recorded." in r.output
        assert not out.exists() and not (tmp_path / "memo.evidence.json").exists()
        snap = pending()
        assert snap.interrupts and snap.values.get("review") is None, "the review must still be pending"

        monkeypatch.undo()
        load_ruleset.cache_clear()
        r = CliRunner().invoke(app, args, input="y\n\n")
    finally:
        load_ruleset.cache_clear()
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    assert record["ruleset"] == {"id": "v1", "sha256": load_ruleset("v1").sha256, "inferred": "pre-ruleset checkpoint"}
    # The pre-change version recorded no rule ids; each line's is inferred from v1.
    assert record["lines"] and all((line["rule_id"] or "").startswith("v1:") for line in record["lines"])
    assert "APPROVED by C" in out.read_text()


@pytest.mark.contracts
@pytest.mark.dual_run
def test_rollback_a_review_left_pending_here_resumes_under_the_pre_change_version(pre_change, tmp_path):
    """Rollback path: a review left pending in a --checkpoint-db by this code (its state
    now carries `ruleset`) is resumed by the pre-change commit, as after reinstalling the
    previous wheel. It must finish and write its memo and evidence."""
    pytest.importorskip("langgraph.checkpoint.sqlite")
    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv_path, _ = synth.write(rows, truth, tmp_path)
    db, out = tmp_path / "reviews.db", tmp_path / "memo.md"
    # The rollback is CMA_RULESET=v1 and then the previous wheel: a review drafted under v2
    # (the default since T11) can hold a category the previous release has no value for
    # (director_funding here), which it cannot read back.
    new = subprocess.run(
        [sys.executable, "-m", "cma.cli", "run", str(csv_path), "--checkpoint-db", str(db), "--thread", "new-1"],
        cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        env=_env(PYTHONPATH=str(ROOT / "src"), CMA_RULESET="v1"))
    assert "review id: new-1" in new.stdout, new.stdout + new.stderr
    old = subprocess.run(
        [sys.executable, "-m", "cma.cli", "resume", "new-1", "--checkpoint-db", str(db), "--reviewer", "C",
         "--out", str(out)],
        cwd=tmp_path, input="y\n\n", capture_output=True, text=True,
        env=_env(PYTHONPATH=str(pre_change / "src")))
    assert old.returncode == 0, old.stdout[-2000:] + old.stderr[-2000:]
    assert "APPROVED by C" in out.read_text()
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    assert "ruleset" not in record and record["guard"]["grounded"] is True


@pytest.mark.contracts
def test_the_built_wheel_carries_v1_byte_for_byte(tmp_path):
    """Build the wheel, install it in a fresh virtual environment and load v1 from
    outside the checkout; its hash must be the source file's."""
    dist = tmp_path / "dist"
    built = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", str(dist), str(ROOT)],
                           capture_output=True, text=True, env=_env())
    assert built.returncode == 0, built.stderr[-2000:]
    (wheel,) = dist.glob("*.whl")
    env_dir = tmp_path / "venv"
    # System site-packages supply the dependencies; the wheel's cma, installed in the
    # venv's own site-packages, comes first on the path.
    venv.create(env_dir, system_site_packages=True, with_pip=True)
    py = env_dir / "bin" / "python"
    subprocess.run([str(py), "-m", "pip", "install", "-q", "--no-deps", "--no-index", str(wheel)],
                   check=True, capture_output=True, env=_env())
    away = tmp_path / "elsewhere"
    away.mkdir()
    probe = ("import json, cma; from cma import categorise as c; r = c.load_ruleset('v1'); "
             "print(json.dumps({'file': cma.__file__, 'id': r.id, 'sha256': r.sha256}))")
    ran = subprocess.run([str(py), "-I", "-c", probe], cwd=away, capture_output=True, text=True, env=_env())
    assert ran.returncode == 0, ran.stderr[-2000:]
    got = json.loads(ran.stdout)
    assert Path(got["file"]).is_relative_to(env_dir), got["file"]
    assert got["id"] == "v1"
    assert got["sha256"] == hashlib.sha256((ROOT / "src" / "cma" / "rules" / "v1.yaml").read_bytes()).hexdigest()


@pytest.mark.contracts
def test_cma_run_refuses_when_the_rules_package_is_missing(tmp_path):
    """An install with no cma/rules at all raises ModuleNotFoundError inside importlib;
    that is a missing table (exit 2), not a model failure (exit 4)."""
    pkg = tmp_path / "pkg"
    shutil.copytree(ROOT / "src" / "cma", pkg / "cma", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(pkg / "cma" / "rules")
    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv_path, _ = synth.write(rows, truth, tmp_path)
    r = subprocess.run(
        [sys.executable, "-m", "cma.cli", "run", str(csv_path), "--yes", "--reviewer", "A"],
        cwd=tmp_path, capture_output=True, text=True, env=_env(PYTHONPATH=str(pkg), CMA_RULESET="v1"))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "Refused." in r.stdout and "cannot be read" in r.stdout and "model call failed" not in r.stdout
