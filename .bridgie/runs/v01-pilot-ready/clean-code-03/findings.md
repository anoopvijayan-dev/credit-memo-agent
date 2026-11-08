# Panel run 03 · seat build/shaper:clean-code · fix/v01-panel-c1-c6 @ 542e55b

**Verdict: FAIL.** 1 blocker (D4 fixed only for round trips: owner money still lifts debt cover), 3 major, 9 minor. 174/174 tests pass. P1 holds on 270 generated statements.

(Saved by the orchestrator from the seat's hand-back; the harness refused the seat's own write of this file.)

## Blocker
- **src/cma/metrics.py:174** — M9 counts OTHER credits (capital injection, savings/pot transfer, HMRC refund) as operating cash. One £9,000 "FPS CREDIT CAPITAL INJECTION" moves M10 1.00 → 1.62, R2 fail → pass, overall fail → refer; M16 7.0% so R9 passes. Only test (test_panel_run02_metrics.py:73) is a round trip that nets to zero.

## Major
- **src/cma/metrics.py:240** — M3 = M2 / M1, but M2 includes revenue from boundary months M1 drops (+8.4% in probe). test_panel_run02_metrics.py:176 locks it in.
- **src/cma/cli.py:164** — evidence path is NAME.evidence.json for approved and rejected; later rejection overwrites an approved memo's evidence.
- **src/cma/importers/monzo.py:141** — `describe()` never gets `extra`; Monzo Description/reference dropped; "INV 1042" payment → other; M2 0, M16 100%, M15 n/a.

## Minor
- **src/cma/guard.py:55** — "4 may" read as a date (case-insensitive).
- **src/cma/guard.py:60** — "one" exempt: "one late VAT payment" passes with M13 = 2.
- **src/cma/policies/uk_sme_default.yaml:61** — R6 description says "15%", threshold is -15; quoting the description with [R6] is flagged.
- **src/cma/metrics.py:108** — INV_TOKEN/RAIL_PREFIX duplicate categorise.py:29 and `_payee`.
- **src/cma/graph.py:129** — `_threshold` duplicates `Metric.display`; guard DISPLAY_DECIMALS a third copy.
- **evals/run_evals.py:85** — no truth for M3, M9, M10, M16; no test of policy.validate/PolicyError; none asserting the prompt excludes transactions (R3).
- **src/cma/importers/monzo.py:158** — with --opening-balance the Balance column is ignored.
- **.github/workflows/evals.yml:42** — runs any `code_ref` with the main-pinned token.
- **src/cma/models.py:81** — `Metric.available` has no caller.

## Requirements
Pass: R1, R3 (read, untested), R5, R6, R7, R9, R10, P1. Fail: R2 (cli.py:164), R4 and P2 (guard escapes).

## Not proven
- R8 full scan. Monzo returned DDs: "DD REFUND" is categorised other, never returned_dd, so M7 may always be 0 on Monzo (unverified against a real export). `cma resume` with nothing pending and `--vat-stagger` bounds code-read only.
