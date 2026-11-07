# Security seat (build/shaper:security), panel run 02: fix/v01-panel-c1-c6

**Verdict: FAIL.** 0 blocker, 3 major, 6 minor. Pass: R2, R3, R7, R8, R9, R10. Fail: R4, R5, R6, P1, P2. Not proven: R1.

(Saved by the orchestrator from the seat's hand-back; the harness refused the seat's own write of this file.)

## Major
1. **src/cma/categorise.py:30, R6.** `^FPS CREDIT` labels every inbound Faster Payment as revenue, and `\bINV\d+` does the same for any credit with that text. metrics.py:46 drops owner drawings from operating cash flow but has no rule for owner money coming back in. Round-trip repro: £6k a month out as drawings and back by FPS, net cash zero; M2 +£72k, DSCR 1.43 → 6.65, policy R2 refer → pass. Gameable, and hits honest statements whenever a director puts money in.
2. **src/cma/guard.py:25, R4/P2.** NUMBER skips numerals next to '.' or letters and has no number-word handling. `c.48k`, `48250pounds`, `forty-eight thousand pounds` all return grounded=True; `£48,250` is caught.
3. **src/cma/cli.py:83, R5.** `--yes --reviewer X` records "APPROVED by X" before anyone read the draft; X is unauthenticated free text. With finding 2, an invented spelled-out figure gets auto-approved.

## Minor
4. **src/cma/models.py:132, R5.** Reviewer `'   '` accepted (min_length=1 without strip; cli.py:50 checks truthiness only).
5. **src/cma/mcp_server.py:48 (also 23/35/42).** No path allow-list or caller check; transactions_for returns every row of any statement-shaped CSV, raw descriptions to an LLM client (prompt-injection channel the memo graph avoids). Exceptions can quote file contents; on mcp 2.2.0 the client sees only "Error executing tool X", but pyproject allows `mcp>=2.0`.
6. **.github/workflows/ci.yml:9.** No `permissions:` block; actions pinned to tags not SHAs; dependencies unpinned.
7. **src/cma/synth.py:52 / :41, R8.** "PARCELFORCE WORLDWIDE" and "PLUMBWORLD" are real UK businesses; line 23 says counterparties are fictional.
8. **.github/workflows/ci.yml:21, R8.** No control stops a real statement being committed; regenerate each committed CSV from its truth.json in CI.
9. **src/cma/synth.py:214, P1.** Truth `lowest` starts from the opening balance; 8 of 1080 seeded statements mismatch on M5 (trades/good seed 1: 17327.55 vs 14000.0).

## Verified safe
- Statement descriptions never reach the drafting model (graph.py:105-118: fixed account name 'Applicant', period, metric names/methods, policy text).
- No secrets in repo; CI needs none; fake LLM default.
- All 10 committed CSVs byte-identical to synth.generate output.
- ingest fails closed on every malformed case in R9.
- Policy loaded with yaml.safe_load.

## Not proven
- R1: computation is deterministic, but finding 2 means no guarantee that no model-made figure reaches a memo.
- Real-model behaviour (no key). Provider retention terms for a lender's metrics need a human check.
- MCP error-text exposure on mcp versions other than 2.2.0.
