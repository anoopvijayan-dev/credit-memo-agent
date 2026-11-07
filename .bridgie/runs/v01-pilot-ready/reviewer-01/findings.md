# verify/reviewer — v01-pilot-ready

**Verdict: FAIL** — 1 blocker, 7 major, 7 minor. Criteria: R1, R3, R6, R7, R8, R10 pass; R2, R4, R5, R9, P1, P2 fail. Tests: 38 passed, ruff clean, sweep of 675 generated statements run.

## Blocker
- `src/cma/guard.py:16` (R4/P2) — NUMBER's trailing `(?![A-Za-z\d])` means any figure followed by a letter is skipped or cut to its leading integer. `cover of 3.7x`, `£48k`, `£1.2m` pass as grounded (`numbers_checked=1, unmatched=[]`). The `x` suffix is how DSCR is normally written, so an invented ratio reaches the analyst marked grounded.

## Major
- `src/cma/guard.py:57` (P2) — `value in YEARS` skips 2000–2099 even with £ or %; `£2,050` and `2,050%` are never checked (`numbers_checked=0`).
- `src/cma/metrics.py:97` (R2) — evidence truncated: M9 `[:200]`, M6 `[:50]` (l.89), M15 `[:200]` (l.112). On the committed `examples/cafe_stretched.csv` M9 cites 200 of 362 lines and M6 cites 50 of 291. M1 cites nothing (l.75).
- `src/cma/ingest.py:22` (R9) — no validation: duplicate ids, balances inconsistent with amounts, `nan`/`inf` amounts all accepted (M2=nan, M5=-9999999). Duplicate ids also make `by_id()` lossy, breaking traceability. Missing column -> bare `KeyError`.
- `src/cma/categorise.py:21` — rules are sign-blind. A loan drawdown credit becomes LOAN_REPAYMENT -> M8=-46,200, M10=-0.65, policy R2 FAIL for a borrower with true cover 7.9x. `\bINV\d+` (l.24) turns a supplier debit into revenue (M2 30,000 -> 18,000); `HMRC VAT` (l.17) turns a VAT refund into negative VAT paid.
- `src/cma/metrics.py:57` — M15 counterparty key is description words `[2:5]`; two-word payers absorb the INV reference and split per payment. trades/stretched seed 20: M15=11.8 vs true top share 25.3. Will be worse on real bank narratives.
- `src/cma/graph.py:129` (R5) — `review()` accepts a bare bool/any dict; reviewer defaults to the string `analyst` (`models.py:129`). CLI interactive path (`cli.py:68-70`) never asks who approved. `Command(resume=True)` yields `Reviewed by analyst: APPROVED`. Contradicts ADR 001 §5 and README.
- `src/cma/cli.py:66` (R5) — `--yes` finalises APPROVED with no human. The shipped `examples/cafe_stretched.memo.md` was produced this way (`Reviewed by cli --yes`), so the README's proof of the human gate is a memo that bypassed it.

## Minor
- `src/cma/graph.py:141` (R5) — rejected reviews still emit `final_memo`; `cli.py:74` writes it to `--out`.
- `src/cma/synth.py:214` (P1) — truth `lowest_balance` starts at the opening balance; metrics takes min over lines. trades/good seed 1: M5=17,327.55 vs truth 14,000. P1 fails for 5/675 cases; tests pin seed 7.
- `src/cma/metrics.py:98` (R1) — M10 sentinel `99.0` is displayed as `99.00` and quoted in memos as a real figure.
- `src/cma/metrics.py:37` — M6 counts any intraday negative balance; method text (l.88) says closing balance. Day closing +2,600 after a -400 dip counts as overdrawn.
- `src/cma/guard.py:31` — `/1000` variant keyed on the bare number: `£264.3 [M2]` accepted as £264,312.50; the intended `£264.3k` is rejected.
- `src/cma/graph.py:125` (R4) — interrupt payload and final stamp (l.139) omit `missing_citations`; CLI prints `Unmatched numbers: []` for a citation-only failure.
- `src/cma/mcp_server.py:23` — tools read any client-supplied path (CSV, YAML at l.43) with no base-dir restriction; `transactions_for` returns raw lines to the model-side client.

## Not proven / out of scope
- No real-model run exists (`evals/results/fake.json` only); grounding rate and redraft counts against Anthropic/OpenAI are unmeasured. Bet evidence task 1 is open.
- Policy thresholds and VAT-quarter assumption (`metrics.py:62`, Jan/Apr/Jul/Oct stagger only) are illustrative; not assessed as defects.
- Repros for every finding are in `evidence.json`; probe scripts were run from the scratchpad with `PYTHONPATH=src`, nothing under the repo was modified.
