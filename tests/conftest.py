"""Record each test's outcome and markers when asked (--tier-results), so the verify tiers that
are subsets of the full run (contracts, invariants, dual_run) read their results from it instead
of running those tests a second time (tools/tier_check.py)."""

import json
import subprocess
from contextlib import suppress
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption("--tier-results", action="store_true", default=False,
                     help="write every test's outcome and markers for tools/tier_check.py")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def results_path() -> Path:
    """In the worktree's own git folder: never an untracked file, never shared between worktrees."""
    return Path(_git("rev-parse", "--git-path", "cma-tier-results.json")).resolve()


class _Recorder:
    def __init__(self):
        self.tests: dict[str, dict] = {}

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        report = (yield).get_result()
        entry = self.tests.setdefault(item.nodeid, {"markers": sorted({m.name for m in item.iter_markers()}),
                                                    "outcome": "passed"})
        if report.failed:
            entry["outcome"] = "failed" if report.when == "call" else "error"
        elif report.skipped and entry["outcome"] == "passed" and report.when in ("setup", "call"):
            entry["outcome"] = "skipped"

    def pytest_sessionfinish(self, session, exitstatus):
        data = {"head": _git("rev-parse", "HEAD"), "tree": _git("status", "--porcelain", "--untracked-files=no"),
                "exitstatus": int(exitstatus), "collected": session.testscollected, "tests": self.tests}
        path = results_path()
        tmp = path.with_suffix(".part")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)


def pytest_configure(config):
    if config.getoption("--tier-results"):
        with suppress(subprocess.CalledProcessError):
            results_path().unlink(missing_ok=True)  # a run that dies early leaves no earlier record behind
        config.pluginmanager.register(_Recorder(), "cma-tier-results")
