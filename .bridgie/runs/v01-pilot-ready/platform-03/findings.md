# platform-03 (build/shaper:platform) — v01-pilot-ready @ fix/v01-panel-c1-c6

**Verdict: FAIL** — 7 major, 3 minor; five majors in the durable-review, audit-file and CI paths this branch added. pytest 174 passed; offline evals 9/9 grounded, truth ok.

(Saved by the orchestrator from the seat's hand-back; the harness refused the seat's own write of this file.)

## Major
1. `.github/workflows/evals.yml:42` — job runs from main with `id-token: write` but checks out and executes arbitrary `code_ref` code; any branch gets a main-scoped OIDC token. `code_ref`/`model` interpolated into a script at :63.
2. `.github/workflows/evals.yml:58` — `run_evals.py | tee` under default `bash -e` (no pipefail): a truth mismatch or crash still gives a green job.
3. `src/cma/graph.py:222` (load at :193) — reused `--thread` with `--checkpoint-db` inherits `redrafts=2` and old `guard_feedback`; model told a draft it never wrote was rejected; no redrafts.
4. `src/cma/cli.py:164` — rejected run overwrites NAME.evidence.json beside an earlier approved NAME.md.
5. `src/cma/cli.py:151` — two reviewers resuming the same review: second decision silently dropped; B's CLI reports and writes A's approval.
6. `src/cma/graph.py:86` + `metrics.py:202`, `:150` — prompt rule 5 asks for the n/a reason from the method column, but those texts contain numbers ("1, 2 or 3", "six") the guard rejects; an obedient model is ungrounded on the default path.
7. `src/cma/importers/monzo.py:158` — with a Balance column and `--opening-balance`, no cross-check; wrong opening balance shifts every balance (M5/M6 wrong).

## Minor
8. `src/cma/llm.py:78` — `stop_reason` ignored, no timeout; truncated memo without Recommendation passes the guard. Provider errors not caught.
9. `src/cma/cli.py:80` — cp1252 CSV, missing file, malformed policy YAML end in raw tracebacks.
10. `.gitignore:14` — checkpoint SQLite holds raw descriptions; `*.sqlite` and import/memo outputs not ignored; no retention/delete.

## Platform gaps for v0.2 (not scored)
- No logging/metrics/tracing in `src/cma`.
- Single-file SQLite, no thread ownership/locking, no migration story for checkpointed models, no TTL.
- Config is env vars only.
- Crash mid-draft cannot be resumed.

## Not proven
- R4/P2 under-flagging search not run from this seat; P1 only nine seeds; R6 rules not read line by line; R8 committed CSVs not fully scanned; SDK support for ANTHROPIC_IDENTITY_TOKEN_FILE not verifiable offline.
