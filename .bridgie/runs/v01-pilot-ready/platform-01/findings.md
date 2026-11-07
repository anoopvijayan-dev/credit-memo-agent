# platform-01 · build/shaper:platform · v01-pilot-ready

**Verdict: FAIL** — 38 tests and 9/9 offline evals pass, R1/R3/R7/R8/R10/P1 hold, but R2, R4, R5, R6, R9 and P2 do not; the grounding guard (the product's one differentiator) has trivial bypasses.

## Findings (1 blocker, 8 major, 10 minor)

**Blocker**
- `src/cma/guard.py:16` (R4) — NUMBER regex drops any number followed by a letter: `48k`, `£48k`, `£1.2m`, `3bn` are never checked; `1.25x` backtracks to `1` and is allowed as prose. `guard.check('Stock is worth £48k and cover is 1.25x.', ms)` → grounded.

**Major**
- `src/cma/guard.py:57` (P2) — values 2000–2099 are skipped as years before the £/% test: `£2,050` is never checked.
- `src/cma/guard.py:69` (R4) — an empty or number-free memo is grounded with rate 1.0 (`models.py:121-124`). `AnthropicLLM.complete` returns `''` on a text-less response (`llm.py:75`) and that reaches the reviewer as "All figures trace to the metrics table"; the eval's headline grounded metric is gameable.
- `src/cma/categorise.py:21` (R6) — rules are sign-blind. `IWOCA LOAN DRAWDOWN` +£20k → loan_repayment: M8 13,800 → −6,200, M10 3.12 → −6.95, policy pass → fail. `HMRC VAT REPAYMENT` credit → hmrc_vat; `DIVIDEND RECEIVED` → owner_drawings; debit `PAYMENT TO STOCKHAVEN INV4471` → revenue_transfer (M2 reduced). Real statements contain all of these.
- `src/cma/metrics.py:89` (R2) — evidence truncated: M6 `[:50]`, M9/M15 `[:200]` (lines 89, 97, 112). In 6/9 synthetic cases M9 cites 200 of 203–368 lines and M6 50 of 54–376; the cited lines cannot reproduce the figure.
- `src/cma/ingest.py:22` (R9) — no validation: inconsistent balances, duplicate ids, empty ids and `amount=nan` are accepted and metrics computed (M2 = nan); a missing column is a bare `KeyError`.
- `src/cma/models.py:129` (R5) — reviewer identity optional, defaults to `'analyst'`; the interactive CLI never asks for one (`cli.py:68-70`); bare `True` accepted (`graph.py:129`). Every documented approval is stamped "Reviewed by analyst".
- `src/cma/cli.py:65` (R5) — `--yes` finalises with reviewer `'cli --yes'` and no human; `evals/run_evals.py:48` likewise. The committed example memo is approved this way.
- `src/cma/metrics.py:62` (money) — M13 assumes calendar-quarter VAT stagger and payment within 10 days of the quarter-start month. UK deadline is period end + 1 month + 7 days, so a compliant payment on 7 May for a March quarter is "late" → spurious policy R5 refer on real statements. Synthetic data pays on the 7th of the quarter-start month, hiding this.

**Minor**
- `src/cma/metrics.py:37` — M6 counts intraday dips; method text (line 88) says closing balance. P1 passes because `synth.py:219` shares the bug.
- `src/cma/metrics.py:98` — M10 = constant 99.0 when no debt; memo would print "99.00 [M10]".
- `src/cma/metrics.py:107` — M13 evidence cites all VAT lines, not the late ones; M10 (line 100) cites only loan lines.
- `src/cma/graph.py:44` — MemorySaver per `build()`; a second process cannot resume a review (`KeyError: 'statement_path'`). OK for the v0.1 CLI, blocks the v0.2 service.
- `src/cma/cli.py:47` — no logging anywhere in `src/`; thread_id discarded; guard result, redrafts, model, reviewer recorded only as memo text. Nothing to alert on.
- `src/cma/llm.py:69` — no timeout/retry/error mapping; OpenAI path has no max_tokens (lines 88-92); empty content returned as `''`.
- `src/cma/graph.py:141` — rejected memo still yields `final_memo` and `cli.py:72-74` writes it to `--out`; stamp at line 138 says "overridden" on a rejection (asserted by `tests/test_graph.py:55`).
- `src/cma/ingest.py:33` — account_name = file stem, sent to the model (`graph.py:93`); a borrower-named file leaks identity.
- `evals/run_evals.py:58` — only M2 checked vs truth; `ci.yml:22-23` and README claim all metrics.
- `src/cma/mcp_server.py:21` — tools read arbitrary host paths; `transactions_for` silently drops unknown ids (line 52); nothing in CI imports the module.

## Platform lens, v0.1 → v0.2
Blast radius today is one process and one CSV; nothing is durable. To run as a service: swap MemorySaver for a configurable Sqlite/Postgres checkpointer and refuse resumes on unknown threads; add one structured event per node (thread_id, llm.name, numbers_checked, unmatched, redrafts, reviewer) as the Langfuse hook; give provider calls timeout/retries and a "draft failed" state instead of a crash; make reviewer identity mandatory and remove `--yes` from the production path; validate CSV at ingest so bad data fails before any figure exists; put the statements root behind config for the MCP server. Rollback is a redeploy with no data repair as long as nothing is persisted, which is also why there is no audit trail yet.

## Not proven
- Grounding rate against a real model: no API key in this environment; only FakeLLM/EmptyLLM/prose stubs were run.
- MCP server end-to-end: import succeeds (`mcp 2.2.0`) but no test or client exercised the tools.
- Categoriser recall on real-world descriptors (WORLDPAY, STRIPE, SUMUP, ZETTLE all → `other`, so M2 ≈ 0 on many real statements): compliant with R6 as written, but the pilot's "policy outcome matches the analyst's view" cannot hold until the rule set is widened against a real anonymised statement.
