# v01-pilot-ready · run 02 · build/shaper:data-intensive

**Verdict: FAIL.** 0 blocker, 8 major, 5 minor. Fail: R4, R5, R6, R9, P1, P2. Pass: R1, R2, R3, R7, R10. Not proven: R8.

(Saved by the orchestrator from the seat's hand-back; the harness refused the seat's own write of this file.)

## Major
1. **src/cma/guard.py:46 (R4).** Guard accepts ±value: "a healthy £5,000 in credit [M5]" passes when M5 = -5000; "grew 12.5% [M4]" passes when M4 = -12.5.
2. **src/cma/guard.py:84 (R4).** Bare 0–31 exemption covers every count metric that drives policy (M1, M6, M7, M13): "9 returned direct debits and 4 late VAT payments" passes untagged when M7=2, M13=0.
3. **src/cma/metrics.py:57.** Counterparty key `split()[2:5]` absorbs the invoice number for two-word payers; true 90% reads 10.0 and R7 passes; synth trades shows 17.7% true vs 14.0.
4. **src/cma/metrics.py:98 (R1).** Zero debt service gives DSCR 99.0 even with operating cash flow -£24,000; R2 passes; memo can quote "cover of 99.00".
5. **src/cma/ingest.py:90 (R9).** Balance chain validated in file order, rows then re-sorted by date, date order never checked: one mistyped year (2026→2027) accepted; M1 12→13, M4 +4.3%→-32.3%, R6 refers.
6. **src/cma/categorise.py:30 (R6).** `^FPS CREDIT|^BACS CREDIT` makes every faster-payment/BACS credit revenue (VAT repayment, own-account transfer, insurance payout) — a guess from the payment rail.
7. **src/cma/metrics.py:36 (P1).** M5 ignores the opening balance; truth includes it (synth.py:214). 38 of 3,240 generated statements fail, all on M5 (trades/good seed=1: 17327.55 vs 14000.00) — error makes the account look healthier.
8. **src/cma/metrics.py:73.** M1 counts calendar months touched: a 340-day statement counts as 13 and passes R1; a dormant month drops out of M1 and M3's denominator (10,000 vs 9,166.67).

## Minor
9. **metrics.py:66.** VAT on-time window is days 1–11 of Jan/Apr/Jul/Oct; HMRC due date is period end + 1 month + 7 days across three stagger groups; 2025-05-07 counts as late.
10. **metrics.py:37.** M6 counts intraday negatives; method text says "closing balance".
11. **ingest.py:37 (R9).** `float()` accepts `1_000` and `1e3`; DictReader drops extra trailing fields silently.
12. **guard.py:29 (P2).** Digits touching letters not scanned ("48250GBP"); spelled-out amounts not checked.
13. **models.py:132 (R5).** Reviewer `" "` accepted; caller can set `decided_at`; `--yes --reviewer " "` passes cli.py:50.

## Checked and fine
- Floats for money: bounded (20,000 random 2dp amounts to ~£1m equal exact pence sums after round(.,2)); far beyond SME volumes before error reaches 0.005.
- Duplicate ids, bad dates, non-numeric/non-finite amounts, broken balances, missing columns, empty file: refused with line numbers. Reverse-chronological export refused (fail-closed; message points at balances, not order).
- Same input twice: compute is pure, categorise idempotent; CLI uses a uuid4 thread so a second run cannot resume an old approval.
- DST: date-only data; not applicable.
- Division by zero: guarded; DSCR substitutes the sentinel (finding 4).

## Not proven
- R8: outside this lens.
- R4 against a real model: how often an LLM writes sign flips or small invented counts is unmeasured.
