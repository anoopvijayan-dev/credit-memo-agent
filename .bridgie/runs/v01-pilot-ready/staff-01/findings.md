# staff-01 · build/shaper:staff · v01-pilot-ready

**Verdict: FAIL** — 0 blocker, 9 major, 7 minor. Pass: R1 R3 R7 R8 R10. Fail: R2 R4 R5 R6 R9 P1 P2. Tests (38) and ruff pass; nothing here is more than a week's work, so the kill criterion is not met, but v0.1 is not yet fit for a lender's statement.

## Major
1. `src/cma/guard.py:57` (P2) — the four-digit-year exemption fires before the £/% test, so `£2,050`–`£2,099` and `2,050%` are never checked or counted. Repro in evidence.json.
2. `src/cma/ingest.py:22` (R9) — no validation at all: inconsistent running balances and duplicate ids are accepted and metrics are computed from them; missing columns die as a bare `KeyError`.
3. `src/cma/graph.py:129` (R5) — reviewer identity is optional: bare `True` resumes, `ReviewDecision.reviewer` defaults to `"analyst"` (`models.py:129`), the interactive CLI never asks for a name (`cli.py:70`), `--yes` records `"cli --yes"` (`cli.py:66`).
4. `src/cma/graph.py:125` (R4) — `missing_citations` are not in the interrupt payload or the final stamp. A memo failing only on untagged figures shows the human `Unmatched numbers: []` and is filed as `overridden (21 numbers checked, 0 unmatched)`.
5. `src/cma/metrics.py:89,97,112` (R2) — evidence truncated to 50/200 ids (M6 50 of 291, M9 200 of 362 on the committed example); M10 cites only loan lines. The click-through-to-lines promise does not hold for the metrics that drive DSCR and days-overdrawn.
6. `src/cma/categorise.py:21` (R6) — rules are sign-blind. A loan drawdown credit becomes `loan_repayment` (M8 = −11,200, M10 = −3.85, policy FAIL); a VAT refund becomes `hmrc_vat` and reduces "VAT paid".
7. `src/cma/metrics.py:62-70` (M13) — hard-codes one VAT stagger and a due date HMRC does not use (real rule: quarter end + 1 month + 7 days). Stagger-1 businesses paying on 7 Feb/May/Aug/Nov are 3-for-3 "late" and referred by policy R5. `synth.py:189-196` encodes the same wrong date, so tests agree with each other, not with HMRC. This is the requirement most likely to be wrong.
8. `src/cma/metrics.py:57` (M15) — counterparty key is `split()[2:5]`; two-word payers keep their invoice number in the key, so each invoice is a separate counterparty. trades/poor seed 35: M15 = 12.4 vs true top-payer share 27.3. Policy R7 concentration is understated.
9. `src/cma/metrics.py:98` (M10) — "Debt service cover" excludes owner drawings from the numerator. cafe_stretched shows cover 1.43 while drawings (£30,000) exceed post-debt cash, the account bottoms at −£30,122 and is overdrawn 217 days. A credit analyst will not accept this as DSCR; call it "cash cover before drawings" and add cover after drawings. Zero debt yields the magic `99.00`.

## Minor
10. `src/cma/metrics.py:87` — M6 method says "closing balance", code counts any intraday running balance below zero. Synthetic ordering (credits first) hides the gap.
11. `src/cma/metrics.py:36` vs `src/cma/synth.py:214` (P1) — M5 and truth disagree on whether the opening balance counts; 3 of 225 seeds fail (trades/good seeds 1, 6, 9). The committed test passes only at seed 7.
12. `src/cma/guard.py:16` — a letter suffix makes the regex backtrack: `1.25x` is checked as `1` (passes), `£264.3k` as `£264` (flagged); the `/1000` variant at line 31 is unreachable.
13. `src/cma/graph.py:133` — a rejected review still produces `final_memo` and the CLI writes it to `--out`.
14. `src/cma/policy.py:43` — threshold digits in `detail` go into the prompt (`graph.py:90`) while the guard flags any non-metric number; a real model explaining a referred rule is guaranteed a redraft. The FakeLLM never quotes thresholds, which is why evals show 0 redrafts.
15. `src/cma/synth.py:52` — `PARCELFORCE WORLDWIDE` is a real company; README says all counterparties are invented.
16. `src/cma/ingest.py:33` — account name defaults to the filename stem and is sent to the model (`graph.py:93`).

## Staff-lens notes (not findings)
- Design is close to the smallest that meets the requirements; the boundaries (ingest / categorise / metrics / policy / guard / graph) are right. Two things sit in the wrong module: counterparty extraction (M15) belongs in categorise or ingest, not inside a metric; threshold text belongs in the allowed-number set, not the prompt.
- Six-month misunderstandings to remove now: the guard's prose-integer rule (documented, fine) vs the undocumented year-band hole; "closing balance" wording on M6; "DSCR" naming on M10; the VAT window; `redrafts == 2` meaning three drafts.
- Out of scope is written down (bet non-goals, README "Known limits"): CSV only, seasonal trend, illustrative thresholds. Missing from that list: single VAT stagger, no input validation, evidence truncation.

## Not proven / limits of this review
- No real-model run: grounding rate and redrafts with Anthropic/OpenAI are untested here (needs a key; the eval task in the bet covers it). Finding 14 predicts non-zero redrafts.
- R8 verified by reading the generator and grepping committed statements, not by any external PII scan.
- M9 double-counting: not found. M9 is a straight signed sum over non-loan, non-drawings lines; its weakness is inclusion, not duplication (OTHER credits such as a director's capital injection count as operating inflow).
