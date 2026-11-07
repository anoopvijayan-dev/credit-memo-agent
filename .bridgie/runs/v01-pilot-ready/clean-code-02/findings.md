# clean-code-02: v01-pilot-ready (branch fix/v01-panel-c1-c6)

**Verdict: FAIL.** 0 blocker, 5 major, 8 minor. Fail: R4, R5, P1, P2. Pass: R1, R2 (in memory), R3, R6, R7, R9, R10. Not proven: R8. pytest 94 passed; offline evals 9/9 grounded.

(Saved by the orchestrator from the seat's hand-back; the harness refused the seat's own write of this file.)

## Major
1. `src/cma/metrics.py:37` M6 counts only days with a transaction below zero; overdrawn 1–29 March with no transactions gives M6 = 1 and R4 pass. synth.py:219-220 copies the definition, so the truth test is tautological.
2. `src/cma/metrics.py:57` M15 key `split()[2:5]` absorbs the invoice number for two-word payers; one payer with 70% gives M15 = 30.0, R7 pass. No M15 test.
3. `src/cma/metrics.py:36` M5 ignores the opening balance, falsifying P1 (trades/good seed=1: truth 14000.0 vs 17327.55; 16 of 3,240 sweep mismatches, all M5).
4. `src/cma/guard.py:25` (P2/R4) figures attached to a currency code are never scanned (`GBP1.2m`, `GBP48250`); number words pass.
5. `src/cma/graph.py:174` + `cli.py:100` written memo has [Mn] tags but no metrics table, evidence ids, model or policy; MemorySaver lost on exit — figures in memo.md can't be traced (bet outcome b).

## Minor
6. `guard.py:46`, `:79` sign and unit ignored ("+7.3% [M4]" when -7.3; "£3,200 [M5] in credit" when -3200; "£25 [M6]" when M6 = 25 days).
7. `graph.py:108` thresholds in prompt get flagged by the guard ("1.25x"); fails safe but lowers real-model grounding.
8. `metrics.py:98` M10 99.0 with no debt even when M9 = -12,000; R2 passes.
9. `models.py:132` reviewer `"   "` accepted (graph and CLI).
10. `metrics.py:59` card/gateway businesses get M15 = 0, so R7 passes vacuously; rule text says "inflows".
11. `policy.py:14` default policy not shipped in the wheel (read, not executed).
12. `mcp_server.py:35`, `:42` duplicates the load → categorise → compute pipeline; no tests.
13. Untested: R3 prompt exclusion (verified by hand with a spy LLM); policy refer bands, on_fail and missing metric; M3, M4, M13–M15 vs independent truth; P1/P2 only example tests.

## Clean-code notes
- Unit boundaries sound; no single-caller abstractions worth removing.
- The duplication that matters is synth re-implementing M5 and M6, which hides findings 1 and 3.
