# platform-02 · build/shaper:platform · v01-pilot-ready (branch fix/v01-panel-c1-c6)

**Verdict: FAIL.** 0 blocker, 7 major, 7 minor. R4, R5, P1 and P2 fail; R8 not proven; the rest pass (pytest: 94 passed).

(Saved by the orchestrator from the seat's hand-back; the harness refused the seat's own write of this file.)

## Major
1. `src/cma/guard.py:25` (and :29), P2. A number touching a letter is never scanned: `48250GBP`, `GBP48250`, `48250pounds` are grounded.
2. `src/cma/guard.py:46`, R4. The guard ignores sign: with M5 = -2,300, "lowest balance was £2,300 [M5], so the account stayed in credit" is grounded; same for negative M9 stated as positive.
3. `src/cma/metrics.py:36`, P1. M5 ignores the opening balance; trades/good seeds 1, 6, 9 give 17,327.55 / 16,704.74 / 16,356.30 vs truth 14,000.00 (3 of 675). Tests use seed 7 only.
4. `src/cma/graph.py:108`, R4. Prompt gives the model policy thresholds (1.25x, 1.5, 60, -15) that are not metrics; a memo restating them fails the guard — expect redraft loops and override habits with a real model.
5. `src/cma/policy.py:14` + `pyproject.toml:30`. Default policy file not in the wheel; non-editable install fails with FileNotFoundError. Blocks the container/Helm path.
6. `src/cma/graph.py:46`, R5. MemorySaver: pending review lost on restart; rejections discarded (`cli.py:95`); checkpoints unbounded. A service needs a durable checkpointer with the same serde allow-list plus an append-only decision log.
7. `src/cma/cli.py:100`, R2 in the deliverable. The `--out` memo has `[Mn]` tags but no metrics table, methods or evidence ids; compliance cannot trace a figure from the artefact.

## Minor
8. `src/cma/models.py:132`, R5. Whitespace reviewer accepted; caller can supply `decided_at`.
9. `src/cma/cli.py:42`. `--llm` defaults to fake so `CMA_LLM` is ignored; stamp records no provider, model, policy or version.
10. `src/cma/llm.py:75`. No stop_reason/finish_reason check; guard doesn't check headings (truncated memo passes); provider errors uncaught; no logging in `src/`.
11. `src/cma/guard.py:45`. Tolerance unit-blind and scales with written precision: "£1m [M2]" passes for £612,345; "12x [M1]" passes.
12. `src/cma/mcp_server.py:23`. Tools take any path; error text echoes the file's first line.
13. `evals/run_evals.py:58`/:72. Eval checks truth for M2 only, though CI and README claim every metric; finding 3 would not fail CI.
14. `src/cma/policy.py:36`. Policy YAML not validated: `on_fail: pass` fails open; bad metric/op raises bare KeyError mid-graph.

## Platform gaps for v0.2
- State: durable checkpointer with the `CHECKPOINT_TYPES` allow-list.
- Concurrency: thread_id per request, idempotent resume.
- Config/secrets: no validated settings object; missing `[llm]` extra fails at first use.
- Observability: no log/metric for guard result, redrafts, provider latency.
- Rollback: policy version not recorded on the memo.

## Not proven
- R8 provenance of every committed file (grep for sort codes/IBANs/emails clean).
- Real-model grounding rate and redrafts (no key).
- Allow-list behaviour with a SQLite/Postgres checkpointer.
