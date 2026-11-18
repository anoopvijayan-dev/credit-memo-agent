# Gotchas

Dated one-line corrections learned in this project.
- 2026-11-18 Mark tests so the runner runs them by name: a test that proves a correctness property P<n> gets `@pytest.mark.invariants` and has `p<n>` in its name; interface and data-format tests get `@pytest.mark.contracts`; tests comparing the v1 rule table with today's results get `@pytest.mark.dual_run`. The reviewer only accepts a test result the runner produced, and unmarked tests reach it only as an aggregate count.
- 2026-11-18 In claim.json, claim only what the runner's configured tiers prove (ruff, compileall, the full pytest suite, the offline evals, the marker tiers). Leave out claims about wheel installs, historical replays or "no skips" unless a configured tier shows them; an unproven claim becomes a reviewer finding.
- 2026-11-18 Keep helper scripts out of the run folder (.bridgie/runs/...). Put test helpers under tests/ in the worktree; anything a seat writes under .bridgie/ fails the run.
- 2026-11-18 A requirement is done only when production code calls it: a building block with no caller yet (e.g. a case-log append that no command records into) is failed by the reviewer for that requirement.
- 2026-11-18 Install the project with all extras before verifying (pip install -e ".[dev,mcp,durable,llm]"); without the durable extra the SQLite checkpoint tests are skipped and resume claims cannot be proven.
