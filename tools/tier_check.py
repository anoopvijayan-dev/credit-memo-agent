"""One verify tier, read from the full run's record (pytest --tier-results), not run again.

    python tools/tier_check.py contracts

Passes when every test the full run ran with that marker passed (or was skipped), and when it
ran none (as `pytest -m <marker>` exiting 5 did, by the owner's choice of 2026-11-16). Fails when
any failed or errored, and when there is no record of a complete run of this exact commit and
working tree: a stale, partial or missing record never passes a tier. Prints each test it read."""

import json
import subprocess
import sys
from pathlib import Path


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def main(marker: str) -> int:
    path = Path(_git("rev-parse", "--git-path", "cma-tier-results.json")).resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"tier {marker}: no record of the full run ({e.__class__.__name__}); run pytest --tier-results first")
        return 1
    head, tree = _git("rev-parse", "HEAD"), _git("status", "--porcelain", "--untracked-files=no")
    if data.get("head") != head or data.get("tree") != tree:
        print(f"tier {marker}: the full run's record is of another commit or working tree; not used")
        return 1
    if data.get("exitstatus") not in (0, 1) or not data.get("collected"):
        print(f"tier {marker}: the full run did not complete (exit status {data.get('exitstatus')}); not used")
        return 1
    mine = {n: t for n, t in data["tests"].items() if marker in t["markers"]}
    for nodeid, t in sorted(mine.items()):
        print(f"{t['outcome'].upper():8} {nodeid}")
    bad = [n for n, t in mine.items() if t["outcome"] in ("failed", "error")]
    print(f"tier {marker}: {len(mine)} tests from the full run of {head[:12]}: "
          f"{len(mine) - len(bad)} passed or skipped, {len(bad)} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
