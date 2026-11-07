# reviewer-02 · v01-pilot-ready · branch fix/v01-panel-c1-c6

**Verdict: FAIL.** 0 blocker, 8 major, 6 minor. Pass: R1, R2, R3, R7, R8, R10. Fail: R4, R5, R6, R9, P1, P2. Tests: 94 passed.

(Saved by the orchestrator from the seat's hand-back; the harness refused the seat's own write of this file.)

## Major
1. `src/cma/guard.py:84` (R4) bare integers 0–31 matching no metric are exempt, covering every policy count (M1, M6, M7, M13): with M7=5, "There were 2 returned direct debits … overdrawn on 3 days [M1]." is grounded.
2. `src/cma/guard.py:46`, `:79` (R4) magnitude only, any metric: "a healthy £3,200 [M5] in credit" with M5=-3200; "Revenue grew 12% [M1]" with M1=12 months.
3. `src/cma/metrics.py:37` (R1, rule R4) days overdrawn counts dates with a transaction; overdrawn 3–20 Jan with no activity gives M6=1 (true 17); synth shares the definition.
4. `src/cma/metrics.py:62-66` (rule R5) VAT on-time window fits one HMRC stagger group; stagger-1 on-time payments give M13=4; no VAT at all gives 0 and passes.
5. `src/cma/categorise.py:30` (R6) every inbound FPS/BACS credit is revenue; capital injection and VAT repayment inflate M2 and DSCR.
6. `src/cma/metrics.py:57` (rule R7) counterparty key includes the invoice number; one customer, ten invoices → M15 10% (true 100%); repo's own synth triggers it.
7. `src/cma/models.py:132` + `cli.py:50` (R5) whitespace reviewer accepted; `--reviewer '   ' --yes` exits 0.
8. `src/cma/ingest.py:90` + `:77` (R9) balances validated in file order then re-sorted by date; a mistyped date (2062-09-30) loads, M1=12, rule R1 passes instead of referring.

## Minor
9. `metrics.py:36` (P1) M5 ignores opening balance; 13 of 4,320 sweep statements fail.
10. `guard.py:29` (P2) "48250GBP", "£48,250pa" not scanned; spelled-out amounts not scanned.
11. `guard.py:81` citations checked per sentence, so swapped labels pass.
12. `guard.py:45` tolerance scales with suffix: "£0m [M2]" grounds any value below £500k.
13. `graph.py:108` + `policy.py:43` thresholds given to the model get flagged; overrides will be routine with a real model.
14. `evals/run_evals.py:58,72` CI claims any metric disagreement fails; only M2 is compared.

## Attack list
- Double submit: second resume after finalisation is a no-op.
- Authz: CLI and MCP local only.
- Timezone: dates only.

## Not proven
- Real-model frequency of findings 1, 2, 10, 13.
- R8 passed on grep/inspection; PARCELFORCE WORLDWIDE is a real carrier name.
