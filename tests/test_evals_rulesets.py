"""The offline evals pass under both rule tables (CMA_RULESET=v1 and v2), and the summary
names the table it ran with, so a result can be read as a v1 or a v2 run."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.dual_run
@pytest.mark.parametrize("ruleset", ["v1", "v2"])
def test_offline_evals_pass_and_name_their_ruleset(tmp_path, ruleset):
    env = {**os.environ, "CMA_RULESET": ruleset}
    r = subprocess.run([sys.executable, str(ROOT / "evals" / "run_evals.py"), "--llm", "fake", "--out", str(tmp_path)],
                       env=env, capture_output=True, text=True, timeout=300)
    print(r.stdout)  # the runner log keeps the per-case table and the summary
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"9/9 memos fully grounded; model = fake; ruleset = {ruleset}" in r.stdout
