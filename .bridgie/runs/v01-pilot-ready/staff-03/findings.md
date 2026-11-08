# staff-03 (build/shaper:staff), v01-pilot-ready, branch fix/v01-panel-c1-c6 @ 542e55b

**Verdict: FAIL.** 1 blocker in the money path (a capital injection turns a DSCR fail into a pass), 2 majors (guard bypass, Monzo revenue lost). Each fixable in about a day; none is a kill.

(Saved by the orchestrator from the seat's hand-back; the harness refused the seat's own write of this file.)

Baseline: `pytest -q` 174 passed; `evals/run_evals.py --llm fake` 9/9 grounded, truth ok.

## Blocker
1. **src/cma/metrics.py:174** — M9 and M10 count every non-financing credit as operating cash, including inflows categorise.py sends to OTHER (capital injection, owner money in, savings, grants, unexplained). Probe: 12 months at M10 1.00x (R2 fail) → 3.08x (R2 pass) after one "CAPITAL INJECTION J SMITH" £25,000; M16 17.2% < R9's 20%. R8 liftable the same way.

## Major
2. **src/cma/guard.py:55** — month-name date pattern blanks "Month + any 1–2 digits" (32–99 too) and leaves the unit unread: "In March 62% of transfer revenue came from one payer." and "By September 85 days had closed overdrawn." pass.
3. **src/cma/importers/monzo.py:141** — `describe()` called without the Monzo reference; INV refs dropped; every Monzo transfer becomes OTHER, M2 card-only, M15 n/a and R7 passes via when_missing.

## Minor
4. **src/cma/guard.py:58** — list-marker exemption takes any integer at line start: "...owes HMRC\n45000. That is a concern." passes.
5. **src/cma/cli.py:164** — same evidence.json name for approved and rejected; rejected re-run overwrites the approved memo's evidence.
6. **src/cma/metrics.py:240** — M3 divides revenue including dropped boundary months by covered months only (11,666.67 vs 10,000).
7. **evals/run_evals.py:85** — no truth for M9, M10, M16 (inputs to fail rules); synth never emits non-trading inflows.
8. **src/cma/metrics.py:152** — M4 compares opposite seasons; limit not written down.
9. **src/cma/importers/monzo.py:158** — with `--opening-balance`, Balance column never checked; `_money` accepts "1e5", "1,234".

## Staff-lens answers
- M9 not double-counting; but R8 tests M9 before drawings, so "positive cash flow" passes while the owner takes out more than the business earns.
- M10 is a defensible historical cash-cover proxy once finding 1 is fixed; excludes the proposed facility; say "historical, existing debt only".
- Design broadly the smallest; Monzo importer not yet trustworthy.

## Not proven
- R8 full audit of names; P1 only nine seeds; real-model eval, `cma resume`, MCP allow-list not run (read only).
