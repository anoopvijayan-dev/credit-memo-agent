# Seat `build/shaper:data-intensive` — bet `v01-pilot-ready`

**Verdict: FAIL** — 1 blocker, 9 major, 5 minor. 38/38 tests pass, but the tests do not reach the defects below.

## Findings (by severity)

**Blocker**
- `src/cma/guard.py:16` (P2/R4) — NUMBER's trailing `(?![A-Za-z\d])` makes unit-suffixed numbers invisible or truncated: `2.75x` checks as plain int `2` and passes, `£48k` never matches, `1.42x [M10]` is "grounded" without comparing 1.42. An invented DSCR or `£48k` reaches the final memo stamped "Grounding check: passed".

**Major**
- `src/cma/guard.py:57` (P2) — `value in YEARS` runs before the £ check: `£2,050` and anything 2000–2099 is never checked (`£2,400` is).
- `src/cma/metrics.py:98` (R1) — M10 = literal `99.0` when debt service is 0. A hard-coded number sits in the metrics table, is quoted with `[M10]`, and makes policy R2 pass. Still 99.0 when M9 is −38,000.
- `src/cma/categorise.py:21` (R6) — rules ignore sign. `IWOCA LOAN ADVANCE +25000` → loan_repayment; M8 = −6,200, M10 = −1.94, R2 fails for the wrong reason. `RENT RECEIVED` → rent, `DIVIDEND … PLC` credit → owner_drawings. A facility drawdown in-period is the normal pilot case.
- `src/cma/metrics.py:57` (R1) — M15 counterparty key is `split()[2:5]`: `T WHITTAKER INV1234` is a new payer per invoice (single payer ×12 → 8.3%, not 100%); `J & L CARTER` and `J & L SMITH` collapse to `J & L` (→ 100%, not 50%). R7 outcome is arbitrary.
- `src/cma/metrics.py:89,97,112` (R2) — evidence capped at `[:50]`/`[:200]`. On cafe_stretched M9 cites 200 of 362 lines, M6 cites 50 of 291. Figures cannot be recomputed from their citations.
- `src/cma/ingest.py:19` (R9) — duplicate ids load silently: sums double-count (M2 +780.02), `by_id()` drops a row, `transactions_for` returns one line for two cited ids.
- `src/cma/ingest.py:28` (R9) — running balance never reconciled against amounts: one corrupted cell gives M5 = −999,999.0 with no error. synth already guarantees the invariant the loader should check.
- `src/cma/synth.py:214` (P1) — P1 is false as stated: truth seeds `lowest` with the opening balance (never a statement line); metrics takes min over lines. 5 of 540 statements mismatch on M5 (trades/good seeds 1, 6, 9, 53, 57: e.g. 17327.55 vs 14000.0). `tests/test_metrics.py:16` runs only seed 7.
- `src/cma/cli.py:70` (R5) — interactive review omits `reviewer`; decision is recorded as the default `"analyst"` (`models.py:129`). No reviewer identity in the pilot path.

**Minor**
- `src/cma/metrics.py:24` (R1) — dormant month dropped from `months`: M1 = 11 on a 12-month period, M3 inflated (24,696.80 vs 24,187.43), and the prompt's Period line contradicts M1.
- `src/cma/metrics.py:34` (R1) — trend reported as `0.0%` when first-3-months revenue is 0, and tautologically 0.0 for statements under 6 months; R6 passes.
- `src/cma/metrics.py:37` (R1) — M6 counts any intraday negative line; method text (line 88) says closing balance. Same-day order is by id string.
- `src/cma/ingest.py:24` (R9) — schema errors are bare `KeyError: 'balance'` / `KeyError: 'id'` / `Invalid isoformat string` with no row context.
- `src/cma/ingest.py:33` (R3) — file stem goes verbatim into the model prompt as `Account:`; a pilot filename can leak the borrower name to the provider.

## Proven
- Float money sums are exact for this workload: 2,700 comparisons of M2/M8/M9/M11/M12 against `Decimal` sums of the CSV strings over 60 seeds × 9 cases, worst diff 0.0 after `round(.,2)`; adversarial 5,000 × 0.10 + 1e9 also exact. Bounded because n·ε·Σ|x| ≪ 0.005 for any plausible statement; minor-unit integers remain the better design but are not a v0.1 defect.
- R3: captured draft prompt holds no transaction ids or descriptions. R7: thresholds live only in YAML. R8: all 38 distinct narratives across committed CSVs are synthetic; no account/sort-code patterns. R10: CI and 38 tests run with no key.
- Same input twice: re-invoking a waiting thread re-raises the interrupt; `resume={}` re-interrupts rather than finalising; two threads on one checkpointer stay independent.

## Not proven
- Behaviour with a real model (grounding rate, redraft count): no API key; FakeLLM never emits `x`/`k` suffixes, so the blocker is untested by the suite.
- DST: dates only, no timestamps; not applicable to this schema.
- Policy YAML schema validation (unknown metric id, missing `on_fail` → KeyError): read but not probed, risk none.
