"""Run the release before statement-review T02 next to this one.

The previous release is the source tree at BASE, extracted from this repository's own
history with `git archive`, and run in a child interpreter whose PYTHONPATH puts that
tree first. Nothing is simulated: the child imports the old cma package itself.
"""

import json
import os
import subprocess
import sys
import tarfile
from io import BytesIO
from pathlib import Path

# The revision T02 branched from (bridgie-task/statement-review/T02).
BASE = "cba9dbad600f3258de587780dd6e3a46ff59203a"
REPO = Path(__file__).resolve().parent.parent


def extract(dest: Path) -> Path:
    """Write BASE's src/ under dest and return the directory to put on PYTHONPATH."""
    blob = subprocess.run(["git", "-C", str(REPO), "archive", BASE, "src"],
                          check=True, capture_output=True).stdout
    with tarfile.open(fileobj=BytesIO(blob)) as tar:
        tar.extractall(dest, filter="data")
    return dest / "src"


def run(src: Path, args: list[str], cwd: Path, stdin: str = "") -> subprocess.CompletedProcess:
    """Run `python args...` with the previous release first on the path."""
    env = {**os.environ, "PYTHONPATH": str(src), "COLUMNS": "200"}
    return subprocess.run([sys.executable, *args], cwd=cwd, env=env, input=stdin,
                          capture_output=True, text=True, timeout=300)


def python(src: Path, code: str, cwd: Path) -> object:
    """Run code under the previous release; it prints one JSON value, which is returned.
    The child first proves it imported the old package, not this one."""
    guard = (
        "import cma, json, sys\n"
        f"assert cma.__file__.startswith({str(src)!r}), cma.__file__\n"
    )
    r = run(src, ["-c", guard + code], cwd)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)
