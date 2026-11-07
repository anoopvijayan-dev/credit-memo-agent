# Bridgie panel run 02 — bet v01-pilot-ready — branch fix/v01-panel-c1-c6

| seat | schema | verdict |
|---|---|---|
| clean-code-02 | ok | fail |
| data-intensive-02 | ok | fail |
| platform-02 | ok | fail |
| reviewer-02 | ok | fail |
| security-02 | ok | fail |
| staff-02 | ok | fail |

**Findings:** 1 blocker, 41 major, 39 minor

## Before and after (run 01 on main → run 02 on fix/v01-panel-c1-c6)

| | run 01 | run 02 |
|---|---|---|
| Seat verdicts | 6 fail | 6 fail |
| Blockers (raw) | 3 | 1 |
| Raw findings | 88 | 81 |
| R2 every figure traceable | 0/6 pass | **6/6 pass** |
| R9 bad input refused | 0/6 pass | **4/6 pass** |
| R1 deterministic figures | 5/6 pass | 5/6 pass, 1 n/p |
| R4 no invented numbers | 0/6 | 0/6 (new, narrower holes) |
| R5 named human approves | 0/6 | 0/6 (whitespace name, unread --yes) |
| P1 metrics match truth | 3/6 | 0/6 (seats ran seed sweeps this time) |
| Tests | 38 | 94 |

### What happened to C1–C6

| Issue | Status after PR #1 | Evidence |
|---|---|---|
| C1 suffixed numbers (£48k, 3.7x) | **Fixed**; residual: unit glued on (`48250GBP`, `GBP1.2m`, `c.48k`) and number words | no seat repeats £48k/3.7x; 5/6 report the residual |
| C2 £2,050 as a year; empty memo | **Fixed** | no seat repeats |
| C3 sign-blind categoriser (drawdown) | **Fixed** | no seat repeats; a different inflow issue (D4) is new |
| C4 truncated evidence | **Fixed** | R2 passes 6/6 |
| C5 no input validation | **Fixed**; residual: date order not checked after the balance chain; common UK bank CSV dialects refused | R9 passes 4/6 |
| C6 unnamed approval | **Mostly fixed**; residual: whitespace reviewer accepted, caller can set `decided_at`, `--yes` approves an unread draft | 6/6 report whitespace |

Orchestrator reproduced on 2026-11-07: D1, D2 (sign and `48250GBP` and number words), D3, D5 (whitespace), D4 (owner money in).

## Consolidated run-02 issues

| # | sev | issue | where | seats | was |
|---|---|---|---|---|---|
| D1 | **blocker** | No debt service → M10 = 99.0 constant; a loss-making business (M9 −£48,000) passes the only fail-level cash rule. Policy outcome decided in code. | metrics.py:98 | 5/6 (staff: blocker) | C9 |
| D2 | major | Guard ignores sign and unit: "a healthy £3,200 [M5] in credit" with M5 −3,200; "12% [M1]" with M1 = 12 months. | guard.py:46, 79 | 6/6 | new |
| D3 | major | Guard blind to units glued on (`48250GBP`, `GBP1.2m`, `c.48k`) and to number words ("forty-eight thousand pounds"). | guard.py:25 | 6/6 | C1 residual |
| D4 | major | Every inbound FPS/BACS credit is revenue: owner money back in, capital injections, VAT repayments inflate M2 and DSCR; gameable round-trip lifts DSCR 1.43 → 6.65. | categorise.py:30 | 4/6 | new |
| D5 | major | Bare 0–31 exemption covers every policy count (M1, M6, M7, M13): "2 returned direct debits" grounded when M7 = 5. | guard.py:84 | 3/6 | new |
| D6 | major | Model is given policy thresholds and asked to explain rules; the guard flags them, so real-model memos will reach analysts ungrounded and overrides will become habit. | graph.py:108, policy.py:43 | 5/6 | new |
| D7 | major | Written memo has [Mn] tags but no metrics table, evidence ids, model or policy version; in-memory state lost on exit — the artefact alone can't answer "where did this figure come from". | cli.py:100, graph.py:174 | 4/6 | new |
| D8 | major | M15 counterparty key absorbs invoice numbers: one payer at 90% reads 10%; card businesses always 0 and pass. | metrics.py:57 | 6/6 | C7 |
| D9 | major | M6 counts dates with a transaction, not days closed overdrawn (overdrawn 2–27 Jan → 1). Generator shares the definition. | metrics.py:37, synth.py:219 | 5/6 | C14 upgraded |
| D10 | major | M13 VAT window fits one HMRC stagger group; on-time stagger-1 payers referred; no VAT at all passes. | metrics.py:62 | 4/6 | C8 |
| D11 | major | Bounced and re-presented DD counted twice in M8/M12. | categorise.py:20 | 1/6 | new |
| D12 | major | Approval: whitespace reviewer accepted; caller can set `decided_at`; `--yes --reviewer X` approves a draft nobody read. | models.py:132, cli.py:50, 83 | 6/6 | C6 residual |
| D13 | major | Date order not validated after the balance chain (a 2062 typo loads, M1 = 12, R1 passes); newest-first, DD/MM, BOM and split paid-in/out exports refused. | ingest.py:77, 90 | 3/6 | C5 residual |
| D14 | major | Default policy file not in the wheel; non-editable install fails. | policy.py:14, pyproject.toml | 2/6 | new |
| D15 | major | MemorySaver: pending review lost on restart, rejections discarded, unbounded checkpoints. | graph.py:46 | 1/6 | C16 |
| D16 | minor | P1 false: M5 ignores the opening balance (13–38 counterexamples per few thousand seeds); tests use seed 7 only. | metrics.py:36, synth.py:214 | 6/6 | C12 |
| D17 | minor | M1 counts calendar months touched (340 days → 13); dormant month inflates M3. | metrics.py:24, 73 | 2/6 | C14 |
| D18 | minor | Evals compare only M2 with truth though CI/README claim every metric; policy YAML not validated (`on_fail: pass` fails open). | run_evals.py:58, policy.py:36 | 3/6 | new |
| D19 | minor | MCP tools read any path; CI has no `permissions:`; PARCELFORCE is a real company; `cma generate` default end date is today. | mcp_server.py, ci.yml, synth.py | 4/6 | C11 + new |

**Panel verdict run 02:** FAIL, but every C1–C6 promise moved: traceability now passes everywhere, input validation mostly. What remains is narrower and more about credit-analysis correctness (D1, D4, D8–D11) than plumbing. Kill criteria still not met.

## Requirements matrix (pass / fail / not_proven per seat)

| req | clean-code | data-intensive | platform | reviewer | security | staff |
|---|---|---|---|---|---|---|
| P1 | fail | fail | fail | fail | fail | fail |
| P2 | fail | fail | fail | fail | fail | fail |
| R1 | pass | pass | pass | pass | n/p | pass |
| R2 | pass | pass | pass | pass | pass | pass |
| R3 | pass | pass | pass | pass | pass | pass |
| R4 | fail | fail | fail | fail | fail | fail |
| R5 | fail | fail | fail | fail | fail | fail |
| R6 | pass | fail | pass | fail | fail | fail |
| R7 | pass | pass | pass | pass | pass | fail |
| R8 | n/p | n/p | n/p | pass | pass | pass |
| R9 | pass | fail | pass | fail | pass | pass |
| R10 | pass | pass | pass | pass | pass | pass |

## Blocker (1)

- **[staff]** `src/cma/metrics.py:98` R7 — When debt service is zero, M10 is a hard-coded 99.0 regardless of cash flow (dscr=inf at line 50). A loss-making business with no existing loans gets 'debt service cover 99.00' in the memo and passes the policy's only fail-level cash rule (R2 >= 1.25). New-to-credit applicants are common for working-capital facilities.
  - repro: 12 months: CARD ACQUIRER SETTLEMENT +10000 on the 2nd, FRESHFIELD FOODS LTD DD -14000 on the 5th, opening 100000. metrics.compute -> M9=-48000.0, M10=99.0; policy.evaluate -> R2 'pass'. (scratchpad panel02/staff/probe1.py case A)

## Major (41)

- **[staff]** `src/cma/categorise.py:20`  — Bounced and re-presented direct debits are double counted. The original debit keeps its category (loan repayment / HMRC VAT), the 'UNPAID DD' reversal credit becomes returned_dd and lands in operating cash flow (metrics.py:46), and the re-presented debit is counted again. M8 debt service, M12 VAT paid and M13 are overstated, and M9 gains the reversal.
  - repro: Monthly +10000 card, -1000 'DD BRIDGEPOINT FINANCE LOAN'; March: '+1000 UNPAID DD BRIDGEPOINT FINANCE LOAN' then re-presented -1000; May: HMRC VAT -3000, '+3000 UNPAID DD HMRC VAT', HMRC VAT -3000. Result M8=13000 (true 12000), M12=6000 (true 3000), M13=2, M10=9.08 (true 9.75). (probe3.py)
- **[data-intensive]** `src/cma/categorise.py:30` R6 — '^FPS CREDIT|^BACS CREDIT' tags every Faster Payments/BACS credit as customer revenue, whoever paid it. Real current accounts receive HMRC repayments, transfers from the owner's own accounts and insurance payouts this way, and these inflate M2/M3/M9 and DSCR. That is a guess based on the payment rail, not a classification, and R6 says credits should be 'other' unless a rule actually identifies them.
  - repro: categorise_one(Transaction(description='FPS CREDIT HMRC VAT REPAYMENT', amount=5000, ...)) -> revenue_transfer; same for 'FPS CREDIT MR J SMITH OWN ACCOUNT TFR' and 'FPS CREDIT INSURANCE CLAIM PAYOUT'
- **[reviewer]** `src/cma/categorise.py:30` R6 — The '^FPS CREDIT|^BACS CREDIT' alternatives classify every inbound Faster Payment or BACS credit as customer revenue based on the payment rail alone: owner capital injections, inter-account transfers, HMRC refunds, grants. That is a guess, not a rule match. It inflates M2, M3, M9 and M10 (DSCR).
  - repro: Credits 'FPS CREDIT J SMITH CAPITAL INJECTION' 25000 and 'FPS CREDIT HMRC VAT REPAYMENT' 4000 -> both revenue_transfer; M2 = 29000.
- **[security]** `src/cma/categorise.py:30` R6 — Every inbound Faster Payment (`^FPS CREDIT`) and any credit whose text contains `INV<digits>` is categorised as trading revenue, whoever sent it. An owner moving money back in, a director's loan, a refund or a transfer from savings all count as revenue and as operating inflow. metrics.py:46 excludes owner drawings from operating cash flow but has no matching treatment for owner money coming back in. So an applicant can round-trip cash to inflate revenue and DSCR: this is a known fraud pattern in the money path.
  - repro: Take examples/cafe_stretched.csv. Once a month insert 'TFR OWNER DRAWINGS -6000.00' then 'FPS CREDIT A OWNER SAVINGS +6000.00' on the same day, and re-walk the balances (script: scratchpad/panel02/security/probe_roundtrip.py). Before: M2 265368.87, M9 19721.52, M10 1.43, R2=refer. After: M2 337368.87, M9 91721.52, M10 6.65, R2=pass. Net cash moved is zero.
- **[staff]** `src/cma/categorise.py:30` R6 — '^FPS CREDIT|^BACS CREDIT' classifies every inbound bank transfer as customer revenue. Transfers from the business's own savings account, HMRC VAT repayments and owner capital injections become revenue: an unidentified credit is guessed as revenue instead of falling to 'other'. This inflates M2, M3, M9 and M10.
  - repro: 12 x card +10000 plus 'FPS CREDIT HARBOURSIDE SAVINGS ACCT' +50000, 'FPS CREDIT HMRC VAT REPAYMENT' +8000, 'FPS CREDIT MR J SMITH CAPITAL INJECTION' +20000 -> all revenue_transfer, M2=198000 vs trading revenue 120000. (probe1.py case E)
- **[security]** `src/cma/cli.py:83` R5 — `--yes` builds an approval {'approved': True, 'reviewer': <flag>} as soon as the memo is grounded, before any person has read the draft. The final memo then says 'APPROVED by <name>' for a decision no human made. The reviewer is a free-text, unauthenticated string, so anyone who can run the CLI (a cron job, CI, an agent) can finalise a memo in any analyst's name. R5 requires a recorded human decision.
  - repro: cma run examples/cafe_stretched.csv --yes --reviewer 'Head of Credit' --out memo.md -> memo.md ends with 'APPROVED by Head of Credit at ...' and no prompt ever appeared. With the guard bypass above, the same command also approves a memo containing 'forty-eight thousand pounds'.
- **[platform]** `src/cma/cli.py:100` R2 — The deliverable memo written with --out contains [Mn] tags but no metrics table, methods or evidence ids, and the console table prints only evidence counts (cli.py:125). A compliance reader holding the memo cannot trace a figure to statement lines without re-running the pipeline or the MCP server, which is the bet's stated problem.
  - repro: cma run examples/cafe_stretched.csv --yes --reviewer A --out m.md; grep -c T0 m.md -> 0 (no transaction ids anywhere in the artefact).
- **[staff]** `src/cma/cli.py:100`  — The approved memo written to --out is memo text plus a stamp (graph.py:174). It has [Mn] tags but no metrics table, methods or evidence ids, and the M-ids are defined only in the in-memory MemorySaver state that is gone when the process exits. 'When compliance asks where a figure came from' cannot be answered from the artefact, which is the bet's outcome (b).
  - repro: cma run examples/cafe_stretched.csv --yes --reviewer 'A. Analyst' --out memo.md; memo.md contains '[M3]' etc. and no table or transaction ids (compare examples/cafe_stretched.memo.md:1-19).
- **[platform]** `src/cma/graph.py:46` R5 — Review state lives only in an in-process MemorySaver, and nothing persists the human decision: a pending review is lost if the process dies, a rejection is printed and then discarded (cli.py:95), and checkpoints accumulate without bound in a long-running process. To run as a service it needs a durable checkpointer (Postgres/SQLite with the same serde allow-list) and an append-only decision log; today the only record is the --out file of an approved memo.
  - repro: After one run, len(list(graph.checkpointer.list(cfg))) == 10, all in memory; cma run ... answering 'n' exits 1 with no file written and no decision stored anywhere.
- **[staff]** `src/cma/graph.py:54` R4 — The prompt requires the Recommendation to name the referred/failed rules, and each rule line passed to the model includes its threshold (policy.py:43, 'rule requires >= 1.25'). Thresholds are not metrics, so a real model that explains a referral naturally ('below the 1.5x refer band') is flagged, redrafted twice, and reaches the analyst marked ungrounded. Reviewers will learn to override. FakeLLM never quotes thresholds, so the 9/9 grounded evals cannot show this.
  - repro: guard.check('Cover of 1.42x [M10] is below the 1.5x refer band and above the 1.25x minimum.', metrics incl. M10=1.42) -> grounded=False, unmatched ['1.5x','1.25x']. (probe1.py case F, last line)
- **[platform]** `src/cma/graph.py:108` R4 — The draft prompt hands the model the policy rule descriptions and details ('Debt service cover of at least 1.25x', 'rule requires >= 1.25', '60', '-15'), and system rule 4 tells it to explain referred/failed rules, but thresholds are not metrics, so any memo that restates a threshold is ungrounded. A real model will hit this routinely: redraft loops and analysts trained to override the guard. Only FakeLLM, which never quotes thresholds, has been evaluated.
  - repro: guard.check('Debt service cover of 1.42 [M10] is below the policy refer band of 1.5x and the 1.25x minimum.', M) -> unmatched ['1.5x','1.25x']; 'Top customer share is 38.0% [M15], under the 60% limit.' -> unmatched ['60%'].
- **[clean-code]** `src/cma/graph.py:174`  — The approved memo that is written out (final_memo = memo + stamp; cli.py:100 writes only this) contains [Mn] tags but neither the metrics table nor the evidence ids, nor the model or policy name. The MemorySaver state is gone when the process exits, so a reader of memo.md cannot trace any figure to statement lines. That is the compliance problem the bet sets out to solve (outcome b).
  - repro: Read graph.py:159-174 and cli.py:99-101; committed output examples/cafe_stretched.memo.md (lines 1-19) has '[M2]' and no table, method or transaction ids, and nothing else is persisted.
- **[clean-code]** `src/cma/guard.py:25` P2 — The NUMBER lookbehind (?<![A-Za-z\d.]) plus a currency group limited to £$€ means a figure glued to a currency code (the usual financial-press style 'GBP1.2m', 'USD48250') is never scanned. An invented facility size or stock value then passes as grounded, which breaks R4 and the bet's outcome (a).
  - repro: guard.check('Revenue £264,313 [M2]. The facility requested is GBP1.2m.', metrics) -> grounded=True, unmatched=[]. The same holds for 'Stock worth GBP48250.' 'EUR48,250' is only partly caught (flags '250'). Numbers written in words ('forty-eight thousand pounds') also pass.
- **[platform]** `src/cma/guard.py:25` P2 — The NUMBER regex requires no letter immediately before (line 25) or after (line 29) a number, so any figure written against a currency code or word is never scanned. An invented amount written '48250GBP', 'GBP48250' or '48250pounds' passes as grounded, and --yes will approve it.
  - repro: PYTHONPATH=src python3 -c "from cma import guard; from cma.models import Metric; M=[Metric(id='M10',name='d',value=1.42,unit='ratio',method='x')]; print(guard.check('DSCR 1.42 [M10]. Stock is worth 48250GBP.',M).grounded)"  -> True
- **[security]** `src/cma/guard.py:25` P2 — The NUMBER regex skips any numeral preceded by '.' or a letter, or followed by a letter, and it has no handling for spelled-out numbers. An invented figure written as 'c.48k', '48250pounds' or 'forty-eight thousand pounds' passes the grounding check, so the memo is presented as grounded and `--yes` will approve it.
  - repro: PYTHONPATH=src python3 -c "from pathlib import Path;from cma import ingest,categorise,metrics,guard;ms=metrics.compute(categorise.categorise(ingest.load_csv(Path('examples/cafe_stretched.csv'))));[print(x,guard.check('Total revenue was £265,369 [M2]. Stock is worth '+x+'.',ms).grounded) for x in ['c.48k','48250pounds','forty-eight thousand pounds','£48,250']]" -> True, True, True, False
- **[data-intensive]** `src/cma/guard.py:46` R4 — The guard matches a written number against both +value and -value of every metric, so a memo that drops or flips the sign of a figure is grounded: an overdraft of -£5,000 can be written as a £5,000 credit balance, and a 12.5% revenue decline as 12.5% growth. These are exactly the figures (M5, M4) that drive the credit view.
  - repro: PYTHONPATH=src python3 -c "from cma import guard; from cma.models import Metric as M; ms=[M(id='M4',name='t',value=-12.5,unit='pct',method='x'),M(id='M5',name='l',value=-5000.0,unit='GBP',method='x')]; print(guard.check('Lowest balance was a healthy £5,000 in credit [M5].',ms).grounded, guard.check('Revenue grew 12.5% [M4] over the year.',ms).grounded)"  -> True True
- **[platform]** `src/cma/guard.py:46` R4 — _matches accepts the written number against both +value and -value, so a memo that states an overdraft as a credit balance, or negative operating cash flow as positive, is grounded. Sign is the money fact for M5 and M9.
  - repro: M5=-2300, M9=-15000: guard.check('The lowest balance was £2,300 [M5], so the account stayed in credit.') -> grounded True; guard.check('Net operating cash flow was £15,000 [M9].') -> grounded True (scratchpad panel02/platform/guard_probe.py).
- **[reviewer]** `src/cma/guard.py:46` R4 — _matches accepts +/- the metric value, and line 79 matches against every metric regardless of unit. The memo can reverse the sign of a balance or cash flow, or quote a metric in the wrong unit, and pass.
  - repro: M5=-3200, M9=-12000, M1=12 (months). 'The lowest balance was a healthy £3,200 [M5] in credit.' -> grounded True; 'Net operating cash flow was a positive £12,000 [M9].' -> True; 'Revenue grew 12% [M1] year on year.' -> True.
- **[data-intensive]** `src/cma/guard.py:84` R4 — Bare integers 0-31 that match no metric are exempt. Days overdrawn (M6), returned direct debits (M7), late VAT payments (M13) and months (M1) all live in that range and drive policy rules R1, R3, R4, R5, so an invented count is accepted with no citation. P2 allows this exemption; R4 does not.
  - repro: metrics M6=3, M7=2, M13=0, M2=264000: guard.check('Revenue was £264,000 [M2]. There were 9 returned direct debits and 4 late VAT payments.', ms) -> grounded=True, checked=3, unmatched=[]; '... overdrawn on 20 days.' -> grounded=True (scratchpad panel02/data-intensive/probe_guard.py)
- **[reviewer]** `src/cma/guard.py:84` R4 — A bare integer from 0 to 31 that equals no metric is exempt, and that range covers every policy-driving count (M1, M6, M7, M13). The memo can misstate returned direct debits or days overdrawn and still be graded grounded. R4 has no such exemption; the P2 carve-out contradicts R4 for exactly the numbers the policy decides on.
  - repro: Metrics M7=5 (returned DDs), M6=27, M1=12. guard.check('There were 2 returned direct debits and the account was overdrawn on 3 days [M1].', M).grounded -> True (policy rule R3 threshold is 2).
- **[staff]** `src/cma/guard.py:84` R4 — The guard only sees digits. Numbers in words pass, and so do word scales after a bare integer of 0-31 (the prose-integer exemption) or after a tagged metric value. Matching ignores unit and sign (line 46), so a metric can be quoted as a different quantity or the wrong sign. The guard reports all of these as grounded.
  - repro: metrics M1=12 months, M2=264312.5, M5=-2300. guard.check grounded=True for: 'Stock is worth forty-eight thousand pounds.'; 'The owner says HMRC is owed 30 thousand.'; 'Cash reserves are £12 [M1] million.'; 'The lowest balance was a healthy £2,300 [M5] in credit.' (probe1.py case F)
- **[staff]** `src/cma/ingest.py:68`  — Only ISO dates in an oldest-first, BOM-free, signed-amount CSV are accepted. UK bank exports are typically DD/MM/YYYY, newest-first, often with separate paid-in/paid-out columns and an Excel BOM. All of these are refused. The bet's non-goals mention only CAMT/MT940, so the pilot will rely on an unwritten, unaudited manual reshaping step before the 'every figure is traced' chain starts.
  - repro: Reverse rows of examples/cafe_stretched.csv -> StatementError with 385 problems ('running balance ... does not follow'). Same file saved as utf-8-sig -> 'missing column(s): id (found: \ufeffid, ...)'.
- **[data-intensive]** `src/cma/ingest.py:90` R9 — The running-balance chain is checked in file order (line 77), and then rows are re-sorted by date with no check that dates are non-decreasing. A date typo that leaves the balance chain intact is accepted and silently moves money between months, changing M1, M3, M4 and policy outcomes.
  - repro: synth cafe/good end=2026-09-30; change one Sept-2026 settlement's date to 2027: load_csv accepts it, M1 12->13, M3 24187.43->22326.86, M4 +4.3% -> -32.3% (R6 refer), period end 2027-09-01. Also accepted: rows dated 2026-03-05, 2026-01-02, 2026-02-10 in that order.
- **[reviewer]** `src/cma/ingest.py:90` R9 — The balance walk (line 77) is validated in file order, then rows are re-sorted by date. A row with a valid but wrong date (a typo) passes, lands in another month, and changes the metrics. The validated sequence is not the one computed from.
  - repro: synth.generate('cafe','good',end=2026-09-30,months=11); set the last row's date to 2062-09-30; load_csv accepts it; M1 = 12 and policy rule R1 (twelve months) -> pass instead of refer.
- **[clean-code]** `src/cma/metrics.py:36` P1 — M5 'Lowest balance' takes the minimum over post-transaction balances only and ignores the opening balance (first.balance - first.amount). When the account opens at its low point, M5 is overstated. This falsifies P1: synth.py:214 seeds truth.lowest_balance with the opening balance. The repo test passes only because seed 7 avoids the case.
  - repro: PYTHONPATH=src: rows,t = synth.generate('trades','good',seed=1,end=date(2026,9,30)) -> t.lowest_balance = 14000.0; categorise+compute gives M5 = 17327.55 (first row is +4227.55 to 18227.55). Sweep over 3240 generated statements: 16 M5 mismatches (scratch probe_p1.py).
- **[data-intensive]** `src/cma/metrics.py:36` P1 — M5 is the minimum of the per-row balances only. The opening balance (first.balance - first.amount) is never considered, but the generator's truth includes it (synth.py:214). P1 fails on 38 of 3,240 generated statements, and the error always makes the lowest balance look better than it was. The fixed-seed tests miss it.
  - repro: synth.generate('trades','good',seed=1,months=12,end=date(2026,9,30)): first row is a +4227.55 credit to balance 18227.55; M5=17327.55, truth.lowest_balance=14000.0 (probe_p1.py: Counter of failures is all M5, in good/stretched ecommerce and trades)
- **[platform]** `src/cma/metrics.py:36` P1 — M5 'Lowest balance' is the minimum post-transaction balance and ignores the opening balance (derivable as first.balance - first.amount). When the account starts at its low point the metric overstates the lowest balance by thousands, and P1 fails.
  - repro: synth.generate('trades','good',seed=1,months=12,end=date(2026,9,30)) -> M5=17327.55, truth.lowest_balance=14000.00; also seeds 6 (16704.74) and 9 (16356.30). 3/675 cases in panel02/platform/p1.py. tests/test_metrics.py only exercises seed 7.
- **[clean-code]** `src/cma/metrics.py:37` R1 — M6 'Days overdrawn' counts only days that have a transaction with balance < 0, not calendar days on which the account closed overdrawn as its method text (line 88) claims. Days with no transactions while overdrawn are dropped, so M6 is understated and policy R4 (< 15 days) can pass a case that should refer. synth.py:219-220 copies the same definition, so test_metrics_match_truth agrees with the generator by construction rather than with the definition.
  - repro: Statement: monthly £5,000 card credits, a £20,000 debit on 2026-03-01, next credit on 2026-03-30. Account is overdrawn 1-29 March; metrics.compute gives M6 = 1 and policy R4 = pass. On synth trades/poor (seed 7, end 2026-09-30) M6 = 151 vs 342 calendar days carried forward. See scratch probe_repro.py / probe_metrics.py.
- **[reviewer]** `src/cma/metrics.py:37` R1 — Days overdrawn counts only dates on which a transaction happened, not calendar days the closing balance was negative, as the M6 method claims. An account overdrawn for 17 days with no activity scores 1, which can turn policy rule R4 (fewer than 15 days) from refer into pass. P1 cannot catch this because synth.py:219-220 uses the same definition.
  - repro: CSV, opening balance 1000: 2026-01-03 FASTER PAYMENT SUPPLIER -1500 (balance -500); 2026-01-20 CARD SETTLEMENT +2000. compute() -> M6 = 1 (true value 17).
- **[staff]** `src/cma/metrics.py:37` P1 — M6 'Days overdrawn' counts dates on which some transaction left a negative balance, not days the account closed overdrawn; days with no transactions while overdrawn are not counted. The generator computes truth the same way (synth.py:220), so P1/tests cannot detect it. R4 (<15 days) can pass a month-long overdraft.
  - repro: Opening 0; 2025-01-01 +100; 2025-01-02 -5000; 2025-01-28 +6000; one +100 credit per later month. Overdrawn 2-27 Jan (26 days); M6=1, R4 pass. (probe1.py case C)
- **[staff]** `src/cma/metrics.py:46`  — M10 is not a defensible DSCR proxy: the numerator adds back owner drawings (excluded from operating), so cash the owner took out counts as cover. A credit analyst deducts drawings for an owner-managed SME. It also covers existing debt only; the proposed facility's repayment is not in the denominator, and that is not written down as out of scope.
  - repro: end=2026-09-30, seed 7: cafe/good M10=3.12 but (M9-M11)/M8=0.95; cafe/stretched 1.43 vs -0.74; ecommerce/stretched 1.36 vs -0.22. cafe/good is policy 'pass' while its cash after drawings does not cover its existing loan. (probe2.py)
- **[clean-code]** `src/cma/metrics.py:57` R7 — M15 counterparty key is description words [2:5]. For two-word payer names that slice includes the invoice number ('OAKFIELD PROPERTIES INV1071'), so every invoice becomes its own counterparty. Concentration comes out understated and policy R7 (top payer < 60%) passes. No test checks M15 against an independent value; truth has no concentration field.
  - repro: 12 months x (7 x 'FPS CREDIT OAKFIELD PROPERTIES INV<n>' £1,000 + 3 x 'FPS CREDIT MRS P ADEYEMI INV<n>' £1,000): true top share 70%, M15 = 30.0, R7 = pass. On synth trades statements the 8 customers become 34-37 keys.
- **[data-intensive]** `src/cma/metrics.py:57`  — Counterparty key is words 2-4 of the description, so for two-word payer names the invoice number becomes part of the key ('T WHITTAKER INV1001') and each invoice counts as a separate customer. Concentration (M15) is understated and policy R7 passes when it should refer. This also happens in the repo's own synthetic trades statements.
  - repro: 12 months of 'FPS CREDIT T WHITTAKER INV10mm' £9,000 + 'FPS CREDIT ELMS DENTAL PRACTICE INV20mm' £1,000: true top share 90%, M15 = 10.0, R7 'pass'. synth trades/stretched seed=3: true 17.7% vs M15 14.0; trades/poor seed=1: 15.9% vs 14.9 (probe_metrics.py, probe_p1.py)
- **[reviewer]** `src/cma/metrics.py:57` R1 — The counterparty key is description words 2-4. For two-word payer names that includes the per-payment invoice number, so one customer becomes many and top-customer share (M15) is understated. Policy rule R7 then passes a fully concentrated book. This happens in the repo's own synthetic data (OAKFIELD PROPERTIES, T WHITTAKER, RIVERSIDE LETTINGS; and 'S & K PATEL' is truncated to 'S & K').
  - repro: Ten credits 'FPS CREDIT OAKFIELD PROPERTIES INV1000..1009', 1000 each -> M15 = 10.0 (true value 100).
- **[staff]** `src/cma/metrics.py:57`  — M15 counterparty key is description words [2:5]. For two-word payer names it includes the invoice reference, so each invoice becomes its own 'counterparty' and concentration is understated. For card/gateway revenue it is 0.0 (line 59) and R7 auto-passes, while the memo template (llm.py:43) and policy text (policy/uk_sme_default.yaml:53) call it a share 'of inflows'; the committed example memo says the top payer is '0.0% [M15] of inflows' for a business whose inflows are 100% one acquirer.
  - repro: 12 months of 'FPS CREDIT T WHITTAKER INV10nn' +9000 and 'FPS CREDIT ELMS DENTAL PRACTICE INV20nn' +1000: true top share 90%, M15=10.0, R7 pass. (probe1.py case B); examples/cafe_stretched.memo.md:5
- **[reviewer]** `src/cma/metrics.py:62` R1 — VAT lateness assumes every business pays on the 1st-11th of Jan/Apr/Jul/Oct. Only one of HMRC's three stagger groups is due then; the others are due on the 7th of Feb/May/Aug/Nov or Mar/Jun/Sep/Dec. On a real lender statement, on-time VAT is counted late and refers rule R5. A business that pays no VAT at all gets M13=0 and passes. The synth generator (synth.py:189-196) shares the assumption, so evals cannot see it.
  - repro: Four 'HMRC VAT' debits on 2026-02-07, 05-07, 08-07, 11-07 (on time for stagger 1) -> M13 = 4. A statement with no VAT payments -> M13 = 0.
- **[staff]** `src/cma/metrics.py:65`  — M13 late VAT treats a payment as on time only if made in days 1-11 of Jan/Apr/Jul/Oct. HMRC deadlines are one month and seven days after the period end across three stagger groups, so on-time payments on a real statement are counted late (R5 refer). A business that pays no VAT at all scores 0 late and passes R5 'No late VAT payments'.
  - repro: 12 months revenue plus HMRC VAT -3000 on 2025-02-07, 05-07, 08-07, 11-07 (on time for stagger 1) -> M13=4, R5 refer. Same statement with no VAT lines -> M12=0, M13=0, R5 pass. (probe1.py case D)
- **[data-intensive]** `src/cma/metrics.py:73`  — M1 counts distinct calendar months touched, not the period covered. A 340-day statement from 28 Jan to 3 Jan counts as 13 months and passes the 12-month policy rule R1. A dormant month disappears from M1 and from the M3 denominator (line 79), so average monthly revenue is overstated.
  - repro: rows 2025-01-28, one per month Feb-Dec, 2026-01-03 -> M1=13 (R1 pass). 11 months of £10,000 with June absent -> M1=11, M3=10000.0 (12-month average 9166.67).
- **[data-intensive]** `src/cma/metrics.py:98` R1 — With zero loan repayments, DSCR becomes the constant 99.0 whatever the operating cash flow, so policy R2 passes and the memo quotes 'debt service cover of 99.00' for a business burning cash. 99.0 is not computed from the statement (dscr = inf at line 50).
  - repro: 12 months of +£1,000 card settlement and -£3,000 supplier DD, no loan lines: M8=0.0, M9=-24000.0, M10=99.0; policy R2 -> pass, 'Debt service cover is 99.00; rule requires >= 1.25.'
- **[reviewer]** `src/cma/models.py:132` R5 — reviewer has min_length=1 without stripping, so a whitespace-only name is a valid identity. cli.py:50 checks truthiness only. The C6 fix was proven only against '' and a missing key (tests/test_graph.py:169).
  - repro: cma run <stmt.csv> --reviewer '   ' --yes --out memo.md -> exit 0, stamp 'APPROVED by     at 2026-11-07 23:11 UTC.'
- **[platform]** `src/cma/policy.py:14`  — DEFAULT_POLICY resolves to <site-packages parent>/policy/uk_sme_default.yaml, but the wheel (pyproject.toml:30 packages=['src/cma']) ships no policy file. Any non-editable install (pip install ., pip install git+..., a container image for the v0.2 Helm chart) fails at the policy node with FileNotFoundError unless --policy is passed.
  - repro: pip wheel . --no-deps -> wheel contains only cma/*.py; extract it and run python3 -I -c 'from cma import policy; policy.load_policy()' -> FileNotFoundError .../policy/uk_sme_default.yaml

## Minor (39)

- **[security]** `.github/workflows/ci.yml:9` R10 — The workflow sets no `permissions:` block, so the GITHUB_TOKEN gets the repository default (write on many repos for push to main). Actions are pinned to mutable tags (@v4/@v5), not SHAs. Dependencies install from unpinned floors (pip install -e '.[dev,mcp]').
  - repro: Read .github/workflows/ci.yml lines 8-21: there is no permissions key; 'uses: actions/checkout@v4' and 'actions/setup-python@v5'.
- **[security]** `.github/workflows/ci.yml:21` R8 — Nothing stops a real statement being committed. .gitignore does not exclude statement CSVs outside out/ and evals/results/; `cma generate` writes into the tracked examples/ by default (cli.py:30); and CI does not check that every committed CSV regenerates from synth. The pilot step that runs a lender's anonymised statement has no technical guard.
  - repro: cp <lender statement>.csv examples/ && pytest -q && ruff check -> CI stays green. A check that regenerates each examples/*.csv from its truth.json (as in scratchpad/panel02/security/probe_synth.py) would catch it.
- **[platform]** `evals/run_evals.py:58` P1 — The eval's truth check and exit code (line 72) cover only M2, yet ci.yml:22 and README:60 claim CI fails 'if any computed metric disagrees with ground truth'. The M5 defect above would not fail the eval.
  - repro: Read evals/run_evals.py:58 (only m['M2'] compared) and :72.
- **[reviewer]** `evals/run_evals.py:72` R10 — ci.yml:22-23 says the eval job fails if any computed metric disagrees with ground truth, but run_evals only compares M2 (line 58). The claim is wider than its proof.
  - repro: Read run_evals.py:58 and :72; no other metric is compared.
- **[staff]** `evals/run_evals.py:72`  — The eval exit code depends only on M2 matching truth (line 58), and it exits 0 even when 0/9 memos are grounded. README.md:60 and ci.yml claim it fails CI 'if any metric disagrees with truth'.
  - repro: Read run_evals.py:58 and :72; no other metric or grounding result affects the return code.
- **[platform]** `src/cma/cli.py:42`  — The CLI defaults --llm to 'fake' and so ignores CMA_LLM, and the finalise stamp (graph.py:167) does not record the provider, model, policy name/hash or code version. A pilot user who forgets --llm gets the FakeLLM template memo (with its canned 'can proceed to approval' sentence), approved and stamped with nothing showing it was not model-drafted.
  - repro: CMA_LLM=anthropic cma run examples/cafe_stretched.csv --yes --reviewer A -> FakeLLM memo, no API call; stamp has no model field.
- **[clean-code]** `src/cma/graph.py:105`  — Untested behaviours: no test asserts R3 (the draft prompt excludes transactions); policy.py has no unit test of refer_below/refer_above/on_fail or of a missing metric id; M3, M4, M13, M14 and M15 are never compared with independent truth; P1 and P2 are checked only by fixed examples (seed 7, end 2026-09-30) and never as properties.
  - repro: grep -rn 'refer_below\|refer_above\|on_fail' tests/ -> no matches; grep -rn 'M15\|M13\|M4\b' tests/test_metrics.py -> no truth assertion; test_metrics CASES use only the default seed.
- **[clean-code]** `src/cma/graph.py:108` R4 — The draft prompt passes each rule's detail, which includes the threshold ('rule requires >= 1.25', built at policy.py:43), and SYSTEM_PROMPT rule 4 asks the model to discuss the rules. Thresholds are not metrics, so a faithful real-model memo that quotes them is flagged. That depresses the grounding rate the bet plans to publish and trains reviewers to override. It fails safe.
  - repro: guard.check('Revenue £264,313 [M2]. Cover of 1.42x [M10] is above the 1.25x minimum.', metrics) -> unmatched=['1.25x'].
- **[reviewer]** `src/cma/graph.py:108` R4 — The draft prompt includes policy thresholds (policy.py:43 'rule requires >= 1.25') and tells the model to explain referred or failed rules. The guard flags those thresholds as ungrounded, so a faithful real-model memo will habitually need a reviewer override, which wears down the control.
  - repro: guard.check('Debt service cover of 1.42 [M10] against a policy minimum of 1.25x.', metrics with M10=1.42) -> unmatched ['1.25x'].
- **[data-intensive]** `src/cma/guard.py:29` P2 — The NUMBER lookarounds reject digits that touch letters, so '48250GBP' or 'approx48k' is never scanned. Spelled-out amounts ('forty-eight thousand pounds') are also unchecked.
  - repro: guard.check('Revenue was £264,000 [M2]. Stock is worth 48250GBP per the owner.', ms) -> grounded=True, checked=1
- **[reviewer]** `src/cma/guard.py:29` P2 — The negative lookahead rejects a number glued to letters, and the regex then backtracks to no match at all, so '48250GBP' or '£48,250pa' are never checked. Spelled-out amounts are not scanned either.
  - repro: guard.check('Stock is worth 48250GBP according to the owner [M2].', M).grounded -> True; same for 'forty-eight thousand pounds'.
- **[platform]** `src/cma/guard.py:45` R4 — Tolerance scales with the written precision and ignores units, so coarse or unit-swapped figures are grounded: '£1m [M2]' for M2=£612,345, and '12x [M1]' (months stated as a ratio).
  - repro: guard.check('Revenue was £1m [M2] over the year.', M) -> True; guard.check('Debt service cover was 12x [M1]', M) -> True.
- **[reviewer]** `src/cma/guard.py:45` R4 — The rounding tolerance scales with the suffix, so '£0m' matches any metric with an absolute value below £500,000.
  - repro: M2=120000: 'Revenue was £0m [M2].' -> grounded True.
- **[clean-code]** `src/cma/guard.py:46` R4 — The guard matches a written number against both +value and -value and ignores the metric unit (line 79). A sign-contradicting or unit-swapped figure is therefore 'grounded'.
  - repro: With M4=-7.3 pct, M5=-3200 GBP, M6=25 days: 'Revenue grew +7.3% [M4].', 'The lowest balance was a healthy £3,200 [M5] in credit.' and 'Overdraft limit is £25 [M6].' all return grounded=True.
- **[reviewer]** `src/cma/guard.py:81` R4 — Citations are checked per sentence, not per number, so swapping two figures' labels passes. This defeats the bet outcome '(b) cites every figure'.
  - repro: M2=120000, M8=30000: 'Total revenue was £30,000 [M8] and debt service was £120,000 [M2].' -> grounded True.
- **[data-intensive]** `src/cma/ingest.py:37` R9 — float() accepts '1_000' and '1e3' as amounts, and DictReader (line 46) silently drops extra trailing fields, so a malformed row can load.
  - repro: rows 'T1,2026-01-01,A,1_000,1000' / 'T1,2026-01-01,A,1e3,1000' / 'T1,2026-01-01,A,10.00,10.00,EXTRA' -> all ACCEPTED (probe_ingest.py)
- **[platform]** `src/cma/llm.py:75`  — Provider responses are not checked for stop_reason/finish_reason, and the guard does not check required headings, so a memo cut off at max_tokens=1500 (line 71) or before the Recommendation section passes as grounded. Provider exceptions are not caught (cli.py:57 catches only StatementError), so they surface as raw tracebacks, and there is no logging anywhere in src/ to show latency, retries or token use (Langfuse, the v0.2 tracing tool, has nothing to attach to).
  - repro: guard.check('Revenue was £612,345 [M2] and DSCR 1.42 [M10].', M) -> grounded True with no Recommendation; grep -rn logging src/ -> no matches.
- **[platform]** `src/cma/mcp_server.py:23`  — MCP tools accept any filesystem path, and StatementError echoes the file's first line, so a client can probe arbitrary host files. This is harmless over local stdio but a disclosure risk once the server is exposed for v0.2. There is no allow-listed statement root.
  - repro: PYTHONPATH=src python3 -c "from cma import mcp_server; mcp_server.statement_summary('/etc/passwd')" -> error text includes 'found: root:x:0:0:root:/root:/bin/bash'
- **[clean-code]** `src/cma/mcp_server.py:35`  — Duplication: the load -> categorise -> compute pipeline is re-implemented in mcp_server.py:35 and :42-44 as well as in graph.py:92-99, with no shared function. Any new validation step added to the graph silently skips the MCP path. mcp_server.py has no test at all.
  - repro: grep -n 'categorise.categorise(ingest.load_csv' src/cma/mcp_server.py -> lines 35 and 42; grep -rn mcp_server tests/ -> no matches.
- **[security]** `src/cma/mcp_server.py:48`  — No MCP tool restricts its path argument to an allowed root, and there is no caller check beyond stdio access. compute_metrics(path) returns M1's evidence, which is every transaction id, and transactions_for(path, ids) then returns every row with its raw description. Any statement-shaped CSV the server process can read is fully readable by the client, and attacker-influenced description text goes straight to an LLM client (Claude Desktop, n8n) without being marked untrusted: a prompt-injection channel the memo graph avoids. Error text that contains file contents (a header line such as a .env 'ANTHROPIC_API_KEY=...', or a YAML parser error quoting the line) is kept from the client only because mcp 2.2.0 hides non-ToolError exceptions. pyproject.toml:54 allows mcp>=2.0, so other versions are not verified.
  - repro: python3 -I scratchpad/panel02/security/probe_mcp.py: statement_summary('<dir>/.env') raises StatementError '(found: ANTHROPIC_API_KEY=sk-ant-FAKE-SECRET-123)'; compute_metrics('/etc/passwd') raises '(found: root:x:0:0:root:/root:/bin/bash)'. Over MCPServer.call_tool on 2.2.0 the client sees only 'Error executing tool statement_summary' (probe_mcp_wire.py). transactions_for('examples/cafe_stretched.csv', ['T00001','T00002']) returns full rows.
- **[staff]** `src/cma/metrics.py:24`  — Months are the distinct months that have transactions. A missing month is not detected, so a 13-month span with a gap still reports M1=12 and passes R1, and M3 divides by the months present.
  - repro: Statement 2025-01..2026-01 with no 2025-03 rows -> M1=12, policy R1 pass; ingest raises nothing.
- **[staff]** `src/cma/metrics.py:34`  — M4 compares the last three months with the first three, so it measures seasonality: trades/good is referred at -24.5% and ecommerce/good at -27.9%, while cafe/poor shows +18.5% growth. A first-three-month total of 0 gives 0.0 and passes R6. The limit is documented in the README, but policy R6 (policy/uk_sme_default.yaml:49) still acts on it, so the analyst will disagree with the outcome by design.
  - repro: probe2.py: trades/good M4=-24.5 -> policy refer; cafe/poor M4=18.5.
- **[reviewer]** `src/cma/metrics.py:36` P1 — Lowest balance ignores the opening balance, which can be derived from the first row (balance - amount). Ground truth (synth.py:214) includes it, so P1 fails in 13 of 4320 generated statements. The tests only use seed 7, which never hits this.
  - repro: synth.generate('trades','good',seed=1,end=date(2026,9,30)) -> M5 = 17327.55, truth.lowest_balance = 14000.0.
- **[data-intensive]** `src/cma/metrics.py:37`  — M6 counts a day as overdrawn if any intraday row balance is negative, but its method text (line 88), which is shown to the model and the analyst, says 'closing balance'.
  - repro: opening 100, same day -500 then +1000 (close 600): M6=1, method 'Distinct days on which the closing balance was below zero.'
- **[clean-code]** `src/cma/metrics.py:59` R7 — For card- or gateway-settled businesses M15 is 0 by definition, so policy R7 'Top counterparty below sixty percent of inflows' (policy/uk_sme_default.yaml:53) passes vacuously instead of being marked not assessable. The rule description also says 'inflows', but the metric covers transfer revenue only.
  - repro: synth cafe/* and ecommerce/*: M15 = 0.0 and R7 = pass (scratch probe_metrics.py). The FakeLLM memo then states 'largest single payer accounted for 0.0% [M15] of inflows' (examples/cafe_stretched.memo.md:5).
- **[data-intensive]** `src/cma/metrics.py:66`  — VAT counts as on time only when it is paid in the first 11 days of Jan/Apr/Jul/Oct. HMRC's due date is 1 month + 7 days after the period end, and there are three stagger groups, so payments that are on time for two of the three groups are flagged late (M13) and every such case refers under R5.
  - repro: VAT paid 2025-05-07 (due date for the quarter ending 31 Mar) -> M13 = 1
- **[clean-code]** `src/cma/metrics.py:98`  — When debt service is 0, M10 is reported as 99.0, a made-up figure that the memo will quote as 'debt service cover of 99.00 [M10]'. It does this even when net operating cash flow is negative, and policy R2 passes.
  - repro: 12 months of +£3,000 card settlement and -£4,000 supplier DD, no loan: M9 = -12000.0, M10 = 99.0, policy R2 = pass (probe run).
- **[clean-code]** `src/cma/models.py:132` R5 — reviewer uses min_length=1 with no strip, so whitespace counts as an identity. cli.py:50 only checks truthiness.
  - repro: PYTHONPATH=src python3 -c "from cma.graph import _review_problem; print(_review_problem({'approved':True,'reviewer':'   '}, True))" -> None (accepted); the final memo reads 'APPROVED by     at ...'.
- **[data-intensive]** `src/cma/models.py:132` R5 — reviewer has only min_length=1, so a whitespace name is accepted; decided_at (line 134) can be set by the caller, so an approval can be backdated. The audit stamp then has no identity and a false time.
  - repro: resume {'approved': True, 'reviewer': ' ', 'decided_at': '2019-01-01T00:00:00Z'} -> final memo 'APPROVED by   at 2019-01-01 00:00 UTC.'; also `cma run --yes --reviewer ' '` passes the cli.py:50 check
- **[platform]** `src/cma/models.py:132` R5 — reviewer min_length=1 accepts whitespace, and decided_at (line 134) is accepted from the resume payload, so an approval can be stamped 'APPROVED by     at 2020-01-01'. Reviewer identity is self-asserted text with no authentication, which a service will need.
  - repro: graph.invoke(Command(resume={'approved':True,'reviewer':'   ','decided_at':'2020-01-01T00:00:00Z'}),cfg) -> final_memo ends 'APPROVED by     at 2020-01-01 00:00 UTC.'
- **[security]** `src/cma/models.py:132` R5 — reviewer has min_length=1 but whitespace is not stripped, and cli.py:50 tests only truthiness, so an approval with reviewer '   ' is accepted and stamped 'APPROVED by    '.
  - repro: PYTHONPATH=src python3 -c "from cma.models import ReviewDecision;print(repr(ReviewDecision(approved=True,reviewer='   ').reviewer))" -> '   '; also: cma run examples/cafe_stretched.csv --yes --reviewer '   '
- **[staff]** `src/cma/models.py:132` R5 — min_length=1 counts whitespace, so a reviewer of '   ' is accepted as an identity by the review node and by `--yes --reviewer ' '` (cli.py:50 checks truthiness only).
  - repro: ReviewDecision(approved=True, reviewer='   ').reviewer -> '   ' (no error).
- **[clean-code]** `src/cma/policy.py:14` R7 — DEFAULT_POLICY resolves to <repo>/policy via parents[2], but the wheel ships only src/cma (pyproject [tool.hatch.build.targets.wheel]). A non-editable install fails with FileNotFoundError on the first run without --policy. CI uses pip install -e, so it never sees this.
  - repro: Not executed (read-only seat): from site-packages/cma/policy.py, parents[2] is .../lib, so load_policy() opens .../lib/policy/uk_sme_default.yaml, which the wheel (packages = ["src/cma"]) does not contain. Repro: pip install . into a fresh venv, then cma run <csv>.
- **[platform]** `src/cma/policy.py:36` R7 — Policy YAML is not validated: on_fail accepts 'pass', so a failed rule silently passes, and an unknown metric or op raises a bare KeyError mid-graph. A policy edit (the intended change path) can fail open without any error.
  - repro: Edit a rule to on_fail: pass with an unmet threshold -> RuleOutcome.PASS; overall can become pass.
- **[security]** `src/cma/synth.py:52` R8 — The synthetic counterparties include real UK businesses, 'PARCELFORCE WORLDWIDE' (line 52) and 'PLUMBWORLD' (line 41), while line 23 says they are fictional. No personal data is involved, but a public repo shows named real firms in invented bank statements.
  - repro: sed -n '23p;41p;52p' src/cma/synth.py
- **[staff]** `src/cma/synth.py:52` R8 — 'PARCELFORCE WORLDWIDE' is a real UK carrier, which contradicts 'Counterparties are fictional' (synth.py:23) and README.md:103. It is not customer data, so R8 still passes.
  - repro: grep -n PARCELFORCE src/cma/synth.py
- **[staff]** `src/cma/synth.py:126`  — The default end date is date.today(), so `cma generate` with the same arguments produces a different statement, trend and policy outcome each month. This contradicts the docstring at synth.py:8.
  - repro: synth.generate('cafe','stretched',seed=7,end=date(2026,9,30)) != synth.generate(...,end=date(2026,10,31)) -> rows differ (probe1.py case I).
- **[security]** `src/cma/synth.py:214` P1 — The generator's lowest_balance truth is seeded with the opening balance, which is not a statement line. M5 is the minimum over transaction balances. When the account never dips below its opening balance, truth and M5 disagree, so P1 fails for some seeds. The test only exercises seed 7.
  - repro: python3 -I scratchpad/panel02/security/probe_p1.py -> '1080 statements, 8 mismatches', e.g. trades/good seed=1 months=12: M5 17327.55 vs truth 14000.0
- **[staff]** `src/cma/synth.py:214` P1 — truth.lowest_balance starts at the opening balance, which is not a statement row, while M5 is the minimum over rows. P1 ('for any synthetic statement') fails; the tests only use seed 7.
  - repro: synth.generate('trades','good',seed=1,end=date(2026,9,30)) -> M5=17327.55, truth.lowest_balance=14000.0 (also seeds 6 and 9). (probe1.py case H)
