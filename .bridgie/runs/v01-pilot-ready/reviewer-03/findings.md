# reviewer-03 — v01-pilot-ready, branch fix/v01-panel-c1-c6 @ 542e55b

**Verdict: FAIL.** 1 blocker, 5 major. Fixes for D4, D5/D6, D11 and D16/D17 do not hold on inputs a pilot statement can contain. 174 tests pass.

(Saved by the orchestrator from the seat's hand-back; the harness refused the seat's own write of this file.)

## Blocker
- **src/cma/metrics.py:174** — M9/M10 include every OTHER inflow; one "FPS CREDIT J SMITH CAPITAL INTRODUCED" +30000 lifts M10 1.00 → 2.25 (R2 fail → pass), M16 11.1% (R9 pass). D4 test only covers a net-zero round trip.

## Major
- **src/cma/guard.py:55** — "<1-2 digits> may/march" blanked as a date: "overdrawn on 40 may be a concern", "These 9 may recur", "Arrears of £75 may follow" grounded.
- **src/cma/guard.py:59** — fraction/multiple words never flagged (half/halved, third, double/doubled, twice, triple) and "one" exempt.
- **src/cma/metrics.py:213** — M13 accepts a VAT DD returned unpaid as the quarter's payment; bounced 7 Jul, re-presented 20 Aug → M13 0, R5 pass.
- **src/cma/metrics.py:63** — boundary months: flat business 2025-10-01..2026-09-16 gives M4 -15.2% (R6 refer); 2025-10-16..2026-10-15 gives M1 13, M3 7.7% low; 2025-10-20..2026-09-30 M3 high.
- **src/cma/importers/monzo.py:141** — payment reference never passed to `describe`; transfer revenue lost on Monzo.

## Minor
- **src/cma/guard.py:182** — an [Rn] tag accepts the threshold value in any role ("cover is 1.25x [R2]" with M10 0.8).
- **src/cma/importers/monzo.py:158** — --opening-balance never checked against the Balance column.
- **src/cma/metrics.py:273** — M16 evidence omits the denominator inflows (R2).
- **src/cma/synth.py:221** — generate() crashes at months<=3 and poor/months=4.
- **.github/workflows/evals.yml:42** — arbitrary `code_ref` runs with the federated credential.

## Criteria
Pass: R1, R3, R5, R6, R7, R8, R9, R10, P1 (675 statements, 0 mismatches). Fail: R2 (M16 evidence), R4, P2.

## Not proven
- Real-model grounding rate; durable checkpoint races; real acquirer names (card revenue from real acquirers would land in OTHER/M16 and refer every pilot case); full R8 audit.
