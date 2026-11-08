# staff-02 · build/shaper:staff · v01-pilot-ready (branch fix/v01-panel-c1-c6)

**Verdict: FAIL**: 1 blocker, 10 major, 7 minor. Pass: R1, R2, R3, R8, R9, R10. Fail: R4, R5, R6, R7, P1, P2. The pipeline (deterministic metrics, guard, interrupt) is sound; several metric definitions are not ones a UK credit analyst would accept, and the guard has holes for figures written in words.

(Saved by the orchestrator from the seat's hand-back; the harness refused the seat's own write of this file.)

## Blocker
- **src/cma/metrics.py:98** (R7): with no debt service, M10 is the code constant 99.0. A loss-making business with no loans passes R2, the only fail-level cash rule. Repro: 12 months +10k revenue, -14k cost → M9 -48,000, M10 99.0, R2 pass. The outcome is decided in code, not YAML.

## Major
- **metrics.py:46**: M10 numerator adds back owner drawings (cafe/good 3.12 vs 0.95 after drawings; cafe/stretched 1.43 vs -0.74); proposed facility not in the denominator and not written down as out of scope. Not a defensible DSCR proxy.
- **metrics.py:57**: M15 key = description words [2:5], includes `INVnnnn` for two-word payers; true 90% reads 10% and R7 passes. Card-settled revenue gives 0.0 and R7 auto-passes. llm.py:43, policy YAML and the example memo still say "of inflows".
- **metrics.py:37**: M6 counts dates with any negative balance, not days closed overdrawn; overdrawn 2–27 Jan gives M6=1. synth.py:220 shares the definition, so P1 is circular for M6.
- **metrics.py:65**: M13 on-time window is days 1–11 of Jan/Apr/Jul/Oct, not HMRC stagger deadlines; on-time payments on the 7th of Feb/May/Aug/Nov give M13=4. No VAT paid at all gives M13=0 and passes R5.
- **categorise.py:20 + metrics.py:46**: bounced and re-presented DD counted twice (loan M8 13,000 vs 12,000; VAT M12 6,000 vs 3,000, M13=2).
- **categorise.py:30** (R6): `^FPS CREDIT|^BACS CREDIT` puts every inbound transfer into revenue (own-account transfers, VAT repayments, capital injections); M2 198k vs 120k trading revenue.
- **guard.py:84 / :46** (R4, P2): "forty-eight thousand pounds", "30 thousand", "£12 [M1] million" pass; "a healthy £2,300 [M5] in credit" passes when M5 is -2,300 (unit and sign ignored).
- **graph.py:54 + policy.py:43** (R4): the prompt includes thresholds; any threshold the model quotes is flagged, so real-model memos will reach the analyst ungrounded by design. FakeLLM never quotes thresholds.
- **cli.py:100 / graph.py:174**: the written memo has [Mn] tags but no metrics table or evidence ids; in-memory state is lost on exit, so the artefact alone can't answer "where did this figure come from".
- **ingest.py:68**: newest-first, DD/MM/YYYY, BOM and split paid-in/paid-out CSVs (common UK exports) are refused; the pilot depends on an unwritten manual reshaping step.

## Minor
- **synth.py:214** (P1): truth lowest balance starts at opening balance; trades/good seeds 1, 6, 9 counterexamples. Tests use seed 7 only.
- **synth.py:126**: default end date is `date.today()`, so `cma generate` output changes monthly.
- **models.py:132** (R5): reviewer `'   '` accepted.
- **evals/run_evals.py:72**: exit code depends only on M2; grounding ignored; README and CI overclaim.
- **metrics.py:34**: M4 measures seasonality; first3=0 gives 0.0 and a pass.
- **metrics.py:24**: month gaps not detected (13-month span missing March gives M1=12).
- **synth.py:52** (R8): PARCELFORCE WORLDWIDE is a real company.

## Staff lens
- Graph is right-sized; the weak point is metric definitions, with no record of which credit convention each follows.
- Most likely wrong requirement: P1. It proves code agrees with a generator written by the same hand. Cheapest check: one anonymised statement spread in Excel by an analyst, compared line by line.
- Will be misunderstood: policy rule ids R1–R7 collide with requirement ids; "Debt service cover" read as DSCR; M15 "of inflows"; prose-integer exemption accepts "N thousand"; README says unmatched lines are OTHER but debits go to SUPPLIER.
- Out of scope, not written: proposed facility's debt service, real-bank CSV dialects, non-trading credits.

## Not proven
- Real-model grounding rate and redrafts; CI on GitHub not executed by the seat; MCP `transactions_for` usability for a reviewer.
