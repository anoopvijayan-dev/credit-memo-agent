# clean-code seat — v01-pilot-ready

**Verdict: FAIL** — 6 major, 4 minor, 0 blocker. Tests: 38 passed (pytest -q, 0.71s, no API key). R2, R4, R5, R9, P2 fail; R1, R3, R6, R7, R8, R10, P1 pass.

## Major

1. `src/cma/guard.py:16` (P2) — `NUMBER` regex backtracks on a trailing letter instead of rejecting. `'1.5x'` is read as `'1'` (plain int, allowed), so an invented DSCR written as `1.5x` passes `grounded=True`. `'£22.4k'` is read as `'£22'`, so the k-suffix variant at line 31 is unreachable dead code.
2. `src/cma/guard.py:63` (R4) — missing-citation check is skipped for integer metric values <= 31 without £/%. `"overdrawn on 25 days."` with M6=25 is grounded with no citation. M1/M6/M7/M13 drive policy rules R1/R3/R4/R5 and can be quoted untagged.
3. `src/cma/metrics.py:57` (R1 / policy R7) — M15 keys payers by `description.split()[2:5]`, hard-coding synth's `FPS CREDIT <name> INV####` layout. Two-word payers get their invoice number in the key, so each invoice is a separate "customer". Repro: 3 invoices from T WHITTAKER + 1 from ELMS DENTAL PRACTICE -> M15 = 25% (truth 75%). Policy R7 (60%) would pass a concentrated book. M15 has no truth test.
4. `src/cma/metrics.py:89,97,112` (R2) — evidence lists sliced `[:50]`/`[:200]`. Every cafe statement: M9 cites 200 of 362 operating lines, M6 50 of 291. Figures are not traceable to all their lines. `test_every_metric_cites_evidence` checks existence, not completeness. M10 (line 100) cites only loan ids though it depends on all operating lines.
5. `src/cma/models.py:129`, `graph.py:129-130`, `cli.py:70` (R5) — reviewer identity is optional. `reviewer` defaults to `"analyst"`, `review()` accepts a bare `True`, interactive CLI never asks who. `Command(resume=True)` finalises with "Reviewed by analyst: APPROVED." No test requires an identity.
6. `src/cma/ingest.py:22-31` (R9) — no validation. Inconsistent running balances accepted (2 rows, balance jumps 10 -> 999 on +5); duplicate ids accepted (`by_id()` silently drops one, breaking evidence lookup); missing column surfaces as bare `KeyError: 'amount'`. No `tests/test_ingest.py`.

## Minor

7. `src/cma/guard.py:16` (P2) — lookbehind does not exclude a preceding digit, so `[M10]` yields a stray `'0'`, `[M15]` a `'5'`. `numbers_checked` and `grounding_rate` (the headline eval number) are inflated: a 15-figure FakeLLM memo reports 21 checked.
8. `src/cma/metrics.py:98` (R1) — no-debt case sets M10 to sentinel `99.0`, displayed `99.00`; model can quote "cover of 99.00 [M10]" and policy R2 passes on a magic number.
9. `src/cma/mcp_server.py:35,42` + `graph.py:74-81` — load -> categorise -> compute pipeline assembled by hand in three places; no single owner, MCP tools can drift from the graph.
10. `tests/` — coverage gaps vs requirements: no ingest test (R9); OTHER fallthrough never reached (R6; synth emits no unmatched credit); policy refer bands/on_fail/custom path untested (R7); no test that the draft prompt excludes transactions (R3); no truth test for M3, M4, M9, M10, M13, M14, M15 (which is how #3 slipped through); nothing for cli.py or mcp_server.py.

## Clean-code notes (not findings)

- `metrics.compute` is one 95-line function building 15 metrics; any metric change churns the same unit. Fine at v0.1, will not stay fine.
- `FakeLLM` regex-parses the `as_table` markdown it is handed (llm.py:30); a test double living in `src/` whose correctness is coupled to table layout.
- Names are consistent (`Mn` ids, `evidence`, `grounded`); no abstraction with a single caller worth removing.

## Not proven / scope

- R3 and R6 pass by reading only; no test pins either behaviour (see #10).
- No real-model eval run exists in the repo (`evals/results/` is gitignored; only `fake.json` present locally), so grounding rate against a real LLM is unmeasured — outside this seat's remit but relevant to the bet's evidence tasks.
- Did not run the CLI interactively or the MCP server; read both.
