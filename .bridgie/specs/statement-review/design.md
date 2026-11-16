# Design — Statement review: every figure traced, the unclear lines first

Revision 2, 2026-11-16, after D34 (changes requested). What changed from revision 1: cash deposits count as trading receipts and the large ones are flagged (D31); the pass/refer/fail outcome is shown with an illustrative label (D28); the tool does not time the underwriter (D32); and the pack is written once, at sign-off. Everything else stands as revision 1 designed it, and the owner confirmed local-first (D29), OBIE v3.1 and TrueLayer first (D30) and Tier 1 only by 2026-11-29 (D33).

## Context

**The bet as signed.** One underwriter at a UK lender (D1) reads months of business bank statements by hand before writing each credit memo. The firm bans generic AI, and the friend's fear is a made-up number on a file they sign. The owner chose to treat this as a showcase for one firm (D15, D24): build a working model, show it, then ask the firm's approver for a pilot (D14, D25). The demo runs on the owner's own statements, as a demonstration of the mechanism rather than of the time saving (D23).

**What the evidence changed.** The underwriter's written reply of 2026-11-16 is one anecdotal source and possibly templated (evidence-synthesis.md). It moves the value from adding up to judging:
- 25-45 of a 45-60 minute review goes on 'unusual transactions, internal transfers, director loans, cash deposits'. Adding up takes 10-20.
- They want to click any number and see its transactions, correct categories themselves, get a summary they can paste into their memo, and keep the lending decision.
- The pilot blockers they expect are data protection and accuracy. A tool that runs on the firm's own machine 'could be attractive', and IT/compliance must approve it.
- Formats: an estimated 9 of 15 files are PDF, 4 Open Banking and 2 mixed. This is an estimate, not a count.

**What the owner decided on revision 1** (D28-D34, 2026-11-16)
- D31: cash deposits count as trading receipts, and deposits above a threshold are flagged. Revision 1 kept every cash deposit out of trading receipts until a person moved it.
- D28: pass/refer/fail is shown, labelled as illustrative thresholds, not the firm's policy. Revision 1 hid it unless the firm loaded its own policy.
- D32: no timing in the tool; files are timed with a stopwatch. R49 is withdrawn and R50 reports no durations.
- D29, D30, D33: the design defaults stand: local-first, OBIE v3.1 and TrueLayer before Plaid or Yapily, Tier 1 only by 2026-11-29.

**What the repo already does** (re-read at 43f3e57; the lines revision 1 cited are where it said). It imports a five-column CSV or a Monzo export with strict validation (src/cma/ingest.py, src/cma/importers/monzo.py). An ordered regex categoriser never guesses (src/cma/categorise.py). No rule mentions cash, so a cash deposit today falls to OTHER and shows in M16 (src/cma/categorise.py:37-50, 64-66). It computes 16 figures with evidence ids (src/cma/metrics.py:248-292); revenue is the set REVENUE_CATEGORIES (src/cma/models.py:33), which M2, M3, M4 and M9 read (src/cma/metrics.py:121, 172). It applies an illustrative YAML policy to pass, refer or fail (src/cma/policy.py:83-123, src/cma/policies/uk_sme_default.yaml), and the guard already accepts a threshold tagged [Rn] when it is introduced as a limit (src/cma/guard.py:189-201). A LangGraph pipeline drafts the memo, checks it with the guard and stops for a named reviewer (src/cma/graph.py). There is an offline template model (src/cma/llm.py:26-69) and an MCP server (src/cma/mcp_server.py).

**Gap against the reply**

| The underwriter asked for | Today | Gap |
|---|---|---|
| Genuine trading income separated from transfers, director money and cash | Revenue is claimed narrowly; every other credit, cash included, is one OTHER pile in M16 | No breakdown of money in; cash takings, director loans, own-account transfers and refunds look alike |
| Real acquirers and merchant cash advances recognised | Only synthetic acquirer names (README:125-126) | Real card revenue lands in M16 and refers the case |
| Large cash deposits highlighted | Nothing recognises cash | Cash is neither counted as takings nor flagged |
| Click any number to its lines | Evidence ids in a JSON file; an MCP tool | Nowhere a person can click |
| Correct categories | Not possible | No override and no record of who changed what |
| Monthly credits, debits and average balance | Period totals; lowest balance only | No monthly table, no average balance |
| A summary for my memo | The agent's own memo, with a pass/refer/fail verdict | Not in the firm's field order; nothing next to the verdict says the thresholds are illustrative |
| Runs on our machine, with an audit trail | The offline model exists; outputs are git-ignored | Nothing stops a hosted model; no tamper-evident log |
| Open Banking feed, PDFs | CSV and Monzo only (README:111) | No adapter |

## Options considered

**Option A: local review workspace (chosen).** The cma package serves a web page on the underwriter's machine, reachable only from that machine (loopback). Every figure, bucket and monthly cell opens its lines, and the lines needing judgement come first. The underwriter re-categorises lines with a reason and the figures layer recomputes, along with the indicators and the illustrative policy check. A named sign-off writes a memo-ready pack and a self-contained HTML record. The model is off by default.

**Option B: static HTML report plus a CLI overrides file.** Each run writes one self-contained HTML file with click-through. Corrections are typed into an overrides file and the run repeated; sign-off stays at the terminal. This is the cheapest option and needs no server. *It lost because* the two things the underwriter asked for beyond click-through, correcting categories and a usable summary, would happen in a file-editing loop outside the page, and the underwriter would see the effect of a correction on the figures and the policy check only after a re-run. It is kept as the format of the signed record (the pack's HTML).

**Option C: spreadsheet as the interface.** Export lines and figures to a workbook, let the underwriter edit a category column, and re-import. It is familiar: they add things up in a spreadsheet today. *It lost because* a figure loses its link to its evidence as soon as a cell is edited or copied. The tool also cannot tell a category correction from an edited amount, and re-importing a hand-edited workbook is the error-prone step the bet is meant to remove. A monthly-summary CSV stays as an export.

**Option D: hosted service (SaaS) now.** One hosted web app per firm; D14 frames the business as SaaS. *It lost for this bet because* applicant data would leave the firm, which is the blocker the reply names. A hosted pilot also needs sign-in, tenancy and a data processing agreement before the approver will look at it. Option A's workspace is the same web page, so hosting it later is a change of deployment, not a rewrite. The owner confirmed local-first (D29).

**Option E: let a model categorise the unclear lines.** Send unexplained lines to a model and accept the buckets it suggests. *It lost because* it puts payer-written text and names in a prompt, which raises GDPR and prompt-injection risk from whoever wrote the payment reference. It also breaks ADR 001 ('the model receives a table, not a statement'), and a category chosen by a model is exactly the kind of invented judgement the underwriter fears. Deterministic rules, rules the firm learns and per-line overrides do this job instead.

**Sub-decision: how to show what is unexplained.** (i) Redefine M16 to mean unexplained only. (ii) Keep M16's value (money in that is not trading receipts), rename it to say so, and add a figure for each bucket plus the unexplained share (M26). *Chose (ii)*: a memo already signed that cites M16 must keep its meaning, and ids are stable.

**Sub-decision: how cash deposits count (D31).** (i) Out of trading receipts until the underwriter moves them (revision 1). (ii) In trading receipts, with the cash inside them reported as its own figure (M18) and indicators on large deposits, cash-heavy months and cash withdrawn then paid back in. (iii) A firm setting that chooses (i) or (ii). *Chose (ii)*, the owner's answer. (i) lost because it understates a cash business such as a café and makes the underwriter add back every ordinary deposit by hand. (iii) lost because M2 would then mean different things in different packs, so a signed memo citing M2 could not be read without knowing the setting; a firm that wants a deposit out moves it with an override and a reason. What (ii) gives up: personal cash paid in by an owner lifts M2, M9 and M10 unless an indicator catches it or a person moves it (see Consequences).

**Sub-decision: how the policy outcome is shown (D28).** (i) Hidden unless the firm loads its own policy (revision 1). (ii) Shown from the packaged policy in the workspace and the pack, with the illustrative label inside the same panel and the same pack section, behind a firm setting that is on by default. (iii) Removed from the review flow; `cma run` keeps it. *Chose (ii)*, the owner's answer. Putting the label in the same section, not in a footnote or only in the HTML record, means copying the section copies the label. (i) lost because the firm has no policy file to load (E8 is open), so the outcome would stay hidden for the whole showcase. (iii) lost because the showcase would no longer show the whole chain, from a traced figure to a rule result that a person must act on.

**Sub-decision: how sign-off works.** (i) Drive the existing LangGraph review interrupt from the workspace. (ii) Make the case folder the source of truth, and make sign-off a case-log event bound to a snapshot hash. *Chose (ii)*. The figures are a pure function of the case's inputs, so a checkpoint would hold nothing extra, and re-running the graph after every override would also re-run the draft node. ReviewDecision's name and notes validation is reused, not copied. `cma run` keeps its interrupt unchanged. Revision 2 settles the order: the underwriter signs a preview, and the pack is written once, at sign-off, from that preview. Revision 1 said both that sign-off binds to a pack already built and that a pack is never overwritten, which left nowhere to record the sign-off.

**Sub-decision: where the categorisation rules live.** (i) Keep them as code and add more. (ii) Move the rule tables into packaged, validated data files like the policy YAML; the matching passes (reversals, round trips, pairing, profile names) stay in code. *Chose (ii)*: the lists of acquirers, lenders, cash wording and gambling operators are what changes most often, the v1 table can be kept as data for rollback, and a firm's own rules use the same format.

## Decision

Build Option A as components inside the existing package. ADR 001 still holds: money is added up only in the figures layer, and the model never sees a statement.

    importers (csv, monzo, openbanking, truelayer, bank-csv)
          |
          v
    case store: inputs, profile, override log, case log
          |
          v
    loader and validation -> categoriser (rule tables, profile, firm rules, overrides)
          |
          v
    figures layer: M1-M28, money-in buckets, monthly summary, indicators (in pence)
          |
          +--> policy check (packaged illustrative policy; policy.evaluate, unchanged)
          |
          +--> review workspace (loopback; renders only)
          |
          +--> preview (field map, section text, guard) --> sign-off (case log event)
                    --> pack written once: memo.md, record.html, evidence.json, monthly.csv

**Components**
- **C1 Rule tables and categoriser** (src/cma/categorise.py; rule tables as data under src/cma/rules/). Built-in tables v1 (today's) and v2 (the new lists). v2 names cash paid in (counter, deposit machine, Post Office and PayPoint wording) as cash_deposit, and names cash-withdrawal wording that only the cash round-trip indicator reads. Precedence: per-line override, then protected built-in patterns (returned DD, director, capital, shareholder, lender, HMRC), then profile names (directors, own accounts), then firm rules, then the rest of the built-in table, then the default (a debit is supplier, a credit is unexplained). Each line records which rule set its category. A pattern that is ambiguous between buckets resolves to one that needs judgement, never to trading receipts; cash wording is not ambiguous, and counting cash as trading is the owner's decision (D31).
- **C2 Figures layer** (src/cma/metrics.py, new src/cma/indicators.py). Adds M17-M28, the monthly summary and the indicators, including the three cash indicators. cash_deposit is a trading-receipts category, so M2 and every figure built on it include cash, and M18 reports the cash inside M2. All sums and threshold comparisons are in integer pence. Each figure comes with its evidence and, for sums, the evidence total. A case whose buckets do not reconcile is refused.
- **C3 Case store** (new src/cma/case.py). Case root, case folders, profile, override log, hash-chained case log, single-writer lock, snapshot hash, purge and purge ledger, and the git-ignore and sync-folder checks.
- **C4 Importers** (src/cma/importers/). A shared step that walks and verifies the balance, extracted from monzo.py; new OBIE v3.1, TrueLayer and bank-CSV importers. Every importer writes the five-column file that ingest.load_csv validates.
- **C5 Review workspace** (new src/cma/review/). A loopback server and one bundled page with no build step. It renders what C2 and policy.evaluate computed and never computes money. The override form offers only categories valid for the line's direction. While show_policy_verdict is on, a policy-check panel shows the outcome and each rule's result on the current figures, with the illustrative label in the panel.
- **C6 Preview, pack and sign-off** (new src/cma/pack.py). Memo field map, deterministic section text, the labelled policy-check section, grounding guard, HTML record, evidence JSON v1, monthly CSV, sign-off binding to the preview's snapshot, and an atomic write of the pack once, at sign-off.
- **C7 Firm configuration** (new src/cma/firm.py). offline_lock (default on), show_policy_verdict (default on, D28), and the paths to firm rules, thresholds and field map. There is no timing setting (D32).
- **C8 Verify** (new src/cma/verify.py). Recomputes an evidence record from its case and refuses records from another ruleset.
- **C9 Pseudonymiser** (new src/cma/pseudonymise.py).
- **C10 Synthetic data and evals** (src/cma/synth.py, evals/run_evals.py). New opt-in features, cash included, with truth for every bucket, line and indicator.
- **C11 Report** (CLI over C3). A local per-case summary with no personal data and no durations.
- **C12 Existing paths** (graph.py, cli.py, mcp_server.py, llm.py, policy.py). Behaviour unchanged; new fields are additive; the offline lock is checked. policy.evaluate and PolicyResult are reused as they are.

### Requirement traceability

| Requirement | Component |
|---|---|
| R1, R2, R3 | C2 (C5 and C6 refuse to render when reconciliation fails) |
| R4, R5, R6, R7, R21, R54 | C1 |
| R55 | C1 (category), C2 (M2, M18 and the figures built on them) |
| R8, R9, R41 | C1 (pairing), C2 (combined figures), C3 (accounts) |
| R10 | C1, C2, C10 |
| R11, R12, R13, R14 | C2 |
| R15, R16, R33, R48, R52 | C5 |
| R25, R56 | C7 (setting), C5 (panel), C6 (pack section and evidence) |
| R17, R18, R19 | C3 (log), C1 (apply), C5 (form) |
| R20 | C3, C5, C1 |
| R22, R23, R26, R27, R28 | C6 |
| R24 | C5, C6 |
| R29, R30 | C7, C12 |
| R31, R32, R35, R42, R43 | C3 |
| R34 | C5, C6 (escaping, CSV), C12 (prompt) |
| R36, R37, R38, R39, R40 | C4, C3 |
| R44, R45 | C8 |
| R46 | C9 |
| R47 | C10 |
| R50 | C11, C3 |
| R51 | C2 (difference), C5 (entry and display) |
| R53 | C12 |

Every active requirement maps to a component; R49 is withdrawn (D32). Two things in the bet are not system requirements and have no component: the outcome metric itself, hours per application, which the owner and the friend read by stopwatch (D32), and the approver's answer (D25, E1, E5).

### Out of scope

- PDF extraction. An estimated majority of files are PDF, but there is no sample layout, and extraction is where invented numbers come from. When a layout arrives, it plugs into the importer contract and the balance-chain check.
- Plaid and Yapily importers, until a sample of the firm's feed exists (E7, D30).
- Any credit decision or recommendation. The illustrative policy check is shown, labelled (D28), and never fills the recommendation or any other judgement field.
- Loading the firm's own lending policy into the workspace or the pack, until the firm supplies one; `cma run --policy` keeps that option.
- Timing the underwriter in the tool, or deriving durations from the case log (D32).
- A firm setting that takes cash deposits out of trading receipts (see the cash sub-decision).
- Companies House, the credit bureau, or any other network lookup.
- Hosting, sign-in, multi-tenancy, and a data processing agreement.
- Merchant cash advance repayments taken out of card settlements before payout; they never appear as a line.
- A seasonality-aware trend (24 months, year on year).
- DOCX output. Sections are copied as plain or rich text until the firm's template arrives (E8).
- A money-out breakdown beyond the existing figures and the monthly totals.
- Any claim about the market. This is for one firm and one underwriter.

### Cut line for 2026-11-29 (decided, D33)

Tier 1, demonstrating the mechanism (D23): R1-R7, R10-R19, R22-R31, R33-R35, R42, R43, R47, R48, R52-R56 (39 requirements), plus the stopwatch trial sheet (documentation only). It covers the v2 tables with real acquirers, lenders and cash wording, the buckets with cash inside trading receipts, the monthly summary and indicators, single-account cases with a profile and an override log, the workspace with the illustrative policy check, the preview, pack and sign-off, the offline lock, and a synthetic case shaped like a business.
Tier 2: R8, R9, R20, R21, R32, R36-R41, R44-R46, R50, R51 (16 requirements). It covers the importers, multiple accounts, firm rules, verify, the pseudonymiser, the report and the comparison.

## Consequences

**Gains**
- The underwriter gets the view they asked for: £80k in, of which £55k is trading and the other £25k is explained by bucket. Every figure is clickable, and their own corrections are on record.
- A cash business is not understated: takings paid in as cash count as trading, the cash inside them is its own figure, and the deposits a person should look at come first (D31).
- The showcase runs the whole chain, from traced figures to an illustrative pass, refer or fail that names the rules a person must look at, and the label says whose decision it is (D28).
- The approver's question, does data leave the firm, has an answer the code enforces: offline lock on, the MCP server refuses to start, and the evidence states offline mode.
- Firm rules build up in a file the firm owns. That is the only switching cost any board lens found.
- The bet's outcome is read by stopwatch on comparable files (D32). For each case, the report adds the size of the judgement queue, the indicator counts and the overrides by kind. Those explain the minutes without the tool measuring the person.

**Costs and risks**
- The scope is large for two weeks and $20, and Tier 1 alone is most of it.
- A server, even one reachable only from the same machine, is new attack surface (see Security notes).
- Twelve more figures make the `cma run` prompt longer and the appendix wider.
- Cash paid in by the owner now counts as trading receipts. It lifts M2, M9 and M10, and can turn an illustrative fail into a pass (D31). The single-deposit, monthly-share and round-trip indicators catch the common patterns. They miss personal cash split into deposits below the limit, in months where cash stays under the share limit. The underwriter sees that only in M18 and the cash lines.
- An outcome on screen can be read as the tool deciding. The friend said they don't want that, and the approver may object to it. The label sits beside every outcome, the recommendation stays blank, and show_policy_verdict turns the outcome off without a release.
- On the owner's own statements the illustrative policy will probably refer or fail, because a personal account has little trading income. The demo banner (R48) says the case demonstrates the mechanism; the presenter should say why before showing it.
- The categoriser still reads payer-written text, and a payer can name themselves after an acquirer. Indicators reduce this but do not remove it.
- The requirement most likely to be wrong is that the reply's 13 generic fields are the firm's memo. It is cheap to check (E8: a redacted template) and cheap to fix, because the field map is data. Next most likely: the cash default (D31) and its limits, which rest on one anecdotal source. The override report shows within the first few cases whether the friend keeps moving cash out of trading receipts.

### Perspective check

- **Security.** *Who can call it:* the operating-system user who owns the case root. The workspace also needs a per-launch token and a loopback Host header (R33). *Trust boundary:* imported files and payer text are checked by the loader and escaped on output (R34). *Secrets:* only an optional model key in the environment, never logged. *Personal data:* kept under one case root readable only by its owner, never committed to git (R31), never in prompts (R30); evidence holds only hashes of the profile. *Risk path:* lending data; the human acknowledgement is the named sign-off bound to a snapshot that includes the policy hash and whether the check was shown (R22). The illustrative outcome is not an acknowledgement. *Worst case:* see Security notes, now including cash paid in to look like trading.
- **Platform.** *Blast radius:* one Python package; new optional parts; the evidence JSON gains fields; PolicyResult and the checkpoint allow-list do not change. *Old and new side by side:* `cma run` is untouched, new commands are additive, and CMA_RULESET=v1 reproduces today's categories, cash included. *Resources:* files on disk only; no tables, queues, cron jobs or IAM. *Rollback:* an environment flag (CMA_RULESET), a config flip (show_policy_verdict) or a reinstall, with no data repair. *Observability:* see that section.
- **Staff.** *Smallest change:* Tier 1 is the smallest set that shows the mechanism in the reply's terms; Tier 2 can be cut off. The cash change is one category mapped into an existing set, and the policy check reuses policy.evaluate and the guard as they are. *Ownership:* arithmetic stays in metrics.py and indicators.py, the outcome in policy.py, categorisation in categorise.py plus the rule data; the workspace and pack only render. *Six-month misunderstandings removed:* M16 is renamed so it cannot be confused with M26 (unexplained); M18 is the cash inside M2, not a bucket beside it; [R1] to [R9] in a pack are policy rules, not requirement ids; the action reads 'sign off figures', not 'approve the loan'; OTHER means unexplained; pseudonymised data is still personal data. *Most likely wrong:* the memo fields (E8), the cash default (D31) and the cash limits. *Out of scope:* listed above.
- **Clean code.** *What changes most:* the rule lists and thresholds, kept apart as data. *Names:* follow the code: money in (inflows in metrics.py), trading receipts (M2 keeps its name, Total revenue, so signed memos still read), override, firm rule, case, preview, pack, sign-off, policy check. *Tests:* properties P1-P20, synthetic truth for every bucket and indicator, and golden files for the importers. *Duplication removed:* all importers share one balance walk; sign-off reuses ReviewDecision's validation; the pack's policy section reuses policy.evaluate and the guard's [Rn] check. *Duplication introduced:* the v1 rule table, kept as data for one release. *No single-caller abstraction:* no storage interface for a future hosted version; the label is one constant, not a templating layer; the importer contract has four callers.
- **Data-intensive.** *Idempotency:* imports are keyed by file hash, an identical override changes nothing, and a second sign-off is refused. *Ordering and concurrency:* one writer per case, enforced by a lock plus the expected log position; a second tab gets a conflict and reloads. *Partial failure:* append-only logs with a check for a torn last entry; the pack is written to a temporary folder and then renamed. *Consistency:* the workspace reads the latest state; the preview and sign-off bind to a snapshot that includes thresholds, the policy hash and the show_policy_verdict setting, so a sign-off cannot attach to an outcome the underwriter did not see. *Migration:* additive, nothing to backfill. *Money:* integer pence, GBP only, thresholds compared in pence, and amounts with more than two decimals are refused.

## Data changes

**Category enum (src/cma/models.py).** Six values are added and none of the existing values change. Credit categories map to buckets as follows:

| Credit category | Bucket |
|---|---|
| revenue_card_settlement, revenue_transfer | trading receipts (M2) |
| cash_deposit (new) | trading receipts (M2); also counted in cash deposits within trading receipts (M18) |
| own_account_transfer (new; also used for debits) | own-account transfers in (M19) |
| director_funding (new) | director and shareholder funding (M20) |
| loan_drawdown | lending drawdowns (M21) |
| refund_in (new), returned_dd | refunds and returned payments in (M22) |
| hmrc_repayment (new) | HMRC repayments (M23) |
| other_explained_in (new: grants, insurance claims, interest) | other explained money in (M24) |
| other | unexplained money in (M25) |

cash_deposit joins REVENUE_CATEGORIES, so M2, M3, M4, M9, M10, M28 and the monthly trading receipts include cash. M15 does not, because it reads named customer transfers only. Under v1 no line is ever categorised cash_deposit, so v1 figures do not move. In the code and the UI, `other` now means unexplained. Merchant cash advances use loan_drawdown and loan_repayment. Gambling and cash withdrawals are read by indicators, not categories. A credit in any other category is a reconciliation failure (R3).

**Transaction (src/cma/models.py).** Three optional fields are added: the account key (default 'main'), the id of the rule, firm rule or override that set the category, and the provider's category as an untrusted hint. Amount and balance keep their current types where the model meets the rest of the code; the figures layer converts them to pence.

**New figures.** No id is ever reused. M1-M16 keep their ids and units. M16 is renamed 'Non-trading money in (share)'; M2 keeps the name Total revenue, and its method sentence now names cash paid in. Values move only on lines a v2 rule now recognises (R10).

| id | name | unit |
|---|---|---|
| M17 | Total money in | GBP |
| M18 | Cash deposits within trading receipts | GBP |
| M19 | Own-account transfers in | GBP |
| M20 | Director and shareholder funding | GBP |
| M21 | Lending drawdowns | GBP |
| M22 | Refunds and returned payments in | GBP |
| M23 | HMRC repayments | GBP |
| M24 | Other explained money in | GBP |
| M25 | Unexplained money in | GBP |
| M26 | Unexplained share of money in | pct |
| M27 | Average end-of-day balance | GBP |
| M28 | Debt service cover including the proposed facility | ratio |

Reconciliation: M17 = M2 + M19 + M20 + M21 + M22 + M23 + M24 + M25, in pence. M18 is part of M2, is not added again, and its evidence is a subset of M2's. M16 and M26 share one denominator: money in less lending drawdowns, returned direct debits and own-account transfers. Cash deposits are trading receipts, so they are in neither numerator. Credits that fall to OTHER today keep the same treatment in M9 whichever new bucket they land in, with one exception: cash deposits now count as trading cash (D31). No committed fixture or synthetic case has a cash line (Consumer trace, item 13), so no committed value moves because of cash. M27 averages the closing balance of every calendar day in the period, carried over days with no lines, as M6 does. M28 adds the proposed monthly repayment for each month of the period to existing debt service.

**Monthly summary.** One row per calendar month: the month, a part-month flag, money in, money out, trading receipts (cash included), average and lowest end-of-day balance, and days overdrawn. Each cell carries its evidence ids and their total.

**Indicators.** Each has an id, a kind, the threshold that fired, evidence ids and a one-line reason. Thresholds live in a packaged, validated YAML file that the firm can replace. Amounts in it are pounds with at most two decimals and are compared in pence. While a threshold still has its shipped value, the workspace and the pack call it illustrative, as they do the policy. Kinds: a large single credit against typical trading receipts; a cash deposit at or above the single-deposit limit; a month with cash above a set share of its trading receipts; cash paid in within a window of days after a cash withdrawal, the two amounts within a tolerance; a payment to a gambling operator; a returned direct debit; a run of consecutive overdrawn days; an irregular credit from a named acquirer. Shipped cash values, subject to the owner question: a single deposit of £1,000 or more, cash above 50% of a month's trading receipts, and a round trip within 7 days and 10% of the amount.

**Policy check.** PolicyResult and policy.evaluate do not change. The workspace and the pack evaluate only the packaged policy. The label is one constant with no digits in it, so it never trips the guard: 'Illustrative thresholds shipped with this tool. They are not your firm's lending policy, and this is not a lending decision: the underwriter decides.' The pack's evidence records whether the check was shown, the label, and the policy's name and hash. These do not go into PolicyResult, so the checkpoint allow-list and `cma run` are untouched. As in revision 1, the description of policy rule R9 is reworded for M16's new name, and the policy hash changes.

**Rule tables.** Packaged YAML under src/cma/rules/. v1 is today's table, frozen. v2 adds acquirers, gateways, marketplaces and delivery platforms; lenders and merchant cash advance providers; wording for own pots and savings; cash paid in (counter, deposit machine, Post Office and PayPoint); cash-withdrawal wording for the round-trip indicator only; HMRC repayments; refunds; and explained non-trading credits. Tables are validated on load like the policy, and each has an id and a hash.

**Case root.** Defaults to private/cma under the working directory, or CMA_HOME. Layout:

    firm.yaml            offline_lock, show_policy_verdict, paths
    firm_rules.yaml      promoted rules: pattern, direction, category, by, at, reason
    indicators.yaml      optional firm thresholds
    memo_fields.yaml     optional firm field map
    purge_ledger.jsonl   case id, time, who
    cases/<random id>/
      case.yaml          profile: label, demo flag, business name, directors and
                         shareholders, own accounts, VAT stagger, proposed monthly repayment
      inputs/            original files, read-only, named by hash
      statements/        five-column files the loader reads, one per account, plus hint files
      overrides.jsonl    append-only
      log.jsonl          append-only, hash-chained
      packs/<snapshot>/  written once at sign-off: memo.md, record.html, evidence.json, monthly.csv

Case ids are random and never the applicant's name. The profile never leaves the case folder; evidence records carry only its hash.

**Evidence JSON v1.** Everything in today's record, with its policy key unchanged. Added: schema_version, case id, accounts (key, source format, file hash, period, line count), ruleset id and hash, firm rules hash, thresholds hash, profile hash, overrides (sequence number, line, old and new category, reason, name, OS user, time), money-in buckets, monthly summary, indicators with the thresholds that fired, policy_check (shown or hidden, the label, the policy name and hash), snapshot hash, sign-off (name, OS user, time, notes), egress (offline, or the model's name) and package version.

**Synthetic truth (src/cma/synth.py).** New opt-in generator features, with truth fields for each bucket, for own-account pairs, for each line's category and for each indicator flag. Cash deposits are among them: at least one at or above the single-deposit limit and one paid in after a similar withdrawal, with truth bucket trading receipts. Existing archetypes and profiles produce byte-identical statements. In their truth files, the director capital line's category becomes director_funding.

## Interfaces and contracts

**CLI (all additions)**
- `cma case new [--demo] [--label TEXT]` prints a random case id.
- `cma case import CASE FILE --format csv|monzo|openbanking|truelayer|bank-csv[:PROFILE] [--account KEY] [--statement-balance AMOUNT --at DATE]`
- `cma case profile CASE` edits the profile.
- `cma review CASE` starts the workspace, prints the URL with its token, and opens a browser if there is one.
- `cma case preview CASE` prints the memo sections, the policy check when it is shown, and the snapshot hash. It writes nothing.
- `cma case sign CASE` asks for a typed name, checks the snapshot against the preview and writes the pack once. It is interactive only; there is no --yes. Preview and sign-off are also available in the workspace.
- `cma case purge CASE`
- `cma verify EVIDENCE [--case CASE]`
- `cma pseudonymise FILE --format F --out PATH [--profile CASE]`
- `cma report` prints a summary of every case with no personal data and no durations.
- `cma generate` gains opt-in features: director funding, own-account transfers, cash (with large deposits and a round trip), real acquirers, lenders, two accounts.
- Environment variables: CMA_HOME (case root) and CMA_RULESET (v1 or v2).

**Importer contract.** Input: one source file and an optional statement balance. Output: the five-column statement for one account, the account metadata, and an optional hints file. The output must pass ingest.load_csv unchanged, and all importers share one step that walks and verifies the balance. Refusals give line or record numbers, never cell values, as the MCP server does today.

**Review workspace API.** Internal to the package; the page and the server ship together, so it is not a shared contract. It can read the case summary; read the lines behind a figure, bucket, monthly cell or indicator; read the policy check when it is shown; post an override with the expected log position; preview and promote a firm rule; build a preview; sign off a preview's snapshot; and post a comparison figure. Every request carries the session token. Every write carries the expected log position and gets a conflict if that position is stale.

**Pack.** memo.md has the sections in field-map order, figures with their ids, and blanks left for the underwriter. While the check is shown, it then has a 'Policy check (illustrative)' section that no memo field maps to. Its first line is the label, followed by the overall outcome and each rule, with the metric value tagged [Mn] and the threshold tagged [Rn] after a limit word. record.html is self-contained and makes no external requests; it shows the label wherever an outcome appears, and shows the sign-off. The pack also holds evidence.json (v1) and monthly.csv. It is written once, at sign-off, to a new folder named by the snapshot hash.

**Memo field map.** A YAML file giving, for each field, its name, the figures or tables it shows, and a sentence template whose numbers are placeholders for figure ids. Fields with nothing mapped are marked for the underwriter. The default reproduces the 13 fields in the reply: business details, loan request, trading history, monthly turnover and credits, average and minimum balances, existing commitments, returned direct debits, VAT and HMRC, director loans and transfers between the business's accounts, credit history, affordability, key risks and recommendation. The turnover field states the cash within trading receipts (M18) next to M2. Credit history, key risks and recommendation stay blank. The policy check is not a memo field; the recommendation stays blank whatever the outcome.

**MCP tools.** compute_metrics returns M1-M28 (an addition), and a new read-only tool returns the money-in buckets. check_policy is unchanged. The server refuses to start under the offline lock.

**Graph and prompt.** Unchanged, except that the metrics table has more rows and M16 has its new name. FakeLLM parses rows by id and is unaffected; its sentence about M16 is reworded.

## Failure modes

| What happens | What the system does | Req |
|---|---|---|
| The same file is imported twice | Same hash: the case is unchanged; the log notes it was already imported | R39 |
| The same account arrives as a CSV and as Open Banking JSON, with different ids | Periods overlap and most (date, amount) pairs match: the second is refused as probably the same account | R40 |
| Open Banking JSON has no per-line balances and no statement balance was given | Refused, saying which balance to supply | R36 |
| A paged Open Banking export repeats a transaction id | Identical content: kept once. Different content: refused | R36, R39 |
| The feed includes pending transactions | Left out, and counted in the import message | R36 |
| An account or line is not in GBP | The account is refused; lines are never converted | R36, R2 |
| Lines with equal timestamps cannot be ordered so that the balance chain holds | Refused, naming the first break | R36 |
| Two equal-amount credits could pair with one own-account debit | Not paired; all listed for judgement | R9 |
| A firm rule pattern matches far more lines than expected | The preview shows how many lines in the open case it matches before saving | R20 |
| A built-in pattern is ambiguous between buckets | Goes to a bucket that needs judgement, never to trading receipts | R7 |
| A payer names themselves after an acquirer | An indicator fires on acquirer credits that are irregular or arrive by Faster Payment under a person's name; the line stays visible | R14 |
| A café banks its takings in cash every day | Each deposit counts as trading receipts and M18 shows the cash total. Deposits at or above the limit and months above the cash share are flagged and listed first | R55, R14, R15 |
| An owner pays personal cash in to lift turnover | It counts as trading receipts (D31). A deposit at or above the limit, a cash-heavy month, or cash paid in soon after a similar withdrawal is flagged; the underwriter moves it to director funding with a reason | R14, R17, R55 |
| A cash line also names a director, a lender or HMRC | The protected pattern wins; the line is never cash or trading receipts | R4, R21 |
| The cash limits in the thresholds file are missing or malformed | Refused at load, naming the line; no case opens with different flags | R14 |
| An override changes the illustrative outcome | The panel recomputes; a sign-off on a preview that showed the old outcome is refused as stale | R25, R23 |
| An underwriter copies the policy-check section into a credit file | The label is the section's first line, so it goes with it; the recommendation field stays blank | R25, R24 |
| show_policy_verdict is off | No outcome or rule result in the workspace or the pack; the evidence says the check was hidden | R56 |
| The packaged policy file is edited in place on the underwriter's machine | The outcome is still labelled illustrative; the pack records the edited file's hash, which differs from the release's | R22, R25 |
| An override names a line that a corrected re-import removed | Shown as stale and not applied until confirmed or dropped | R19 |
| Two browser tabs or two terminals write to one case | The lock plus the expected log position: the stale write gets a conflict and reloads | R17 |
| Power is lost while a log entry is being written | The torn last entry is detected; sign-off is refused until the underwriter confirms dropping it | R43 |
| The tool crashes while writing the pack at sign-off | The pack goes to a temporary folder and is renamed; leftovers are removed on the next start; no partial pack is visible, and the sign-off entry is written only after the rename | R26 |
| The pack's text fails the guard (a template bug, or a firm-edited template with a literal number) | No pack is written; the failing number is named | R28 |
| The buckets do not reconcile (a bug) | No figures are shown or exported; the difference in pence and the lines involved are reported | R3 |
| The profile, an override, a threshold or the policy changes after the preview was built | Sign-off is refused, naming what changed | R23 |
| Someone edits the case log by hand | The chain check fails; sign-off is refused and the first bad entry named | R43 |
| A hosted model is requested while the offline lock is on | Refused before any client is created | R29 |
| The hosted model is down or its reply is cut off (offline lock off) | `cma run` behaves as today (exit 4); the pack never depends on a model | R53 |
| The workspace port is in use, or there is no browser | The next free loopback port is used; the URL with its token is printed | R33 |
| The case root is inside OneDrive or another synced folder | A warning before the case is created, recorded in the case log | R32 |
| The case root is inside the repository but not git-ignored | Refused | R31 |
| The accounts in a multi-account case cover periods that do not overlap | Combined balance figures are n/a with the reason; flow figures are still combined | R41 |
| An evidence record was made with a different ruleset | Verify refuses and names the version that made it | R45 |
| The firm rules or thresholds file is malformed | Refused at load, naming the line; no case opens with silently different figures | R20, R14 |
| A very large case (10,000 lines or more) | Lines are paged in the workspace; recomputing has a 2-second target at 10,000 lines | R52 |

## Migration

- There is no database. Case folders are new, so there is nothing to backfill.
- Evidence JSON v1 is a superset of today's record. Records without schema_version are read as v0; verify recomputes M1-M16 for them and says that is all it can check.
- The committed examples (examples/cafe_stretched.memo.md, .memo.evidence.json and .truth.json) are regenerated in the same change. The CSV is byte-identical; the appendix gains M17-M28, M16's new name and M2's reworded method; in the truth file, the director capital line becomes director_funding. test_categoriser_matches_truth runs under v2; v1 is covered by P7, which compares values.
- Cash: no committed fixture, synthetic case or test has a cash deposit line, so moving cash into trading receipts changes no committed value. On real statements, including the owner's Monzo export, cash lines that fell to OTHER now count as trading and show in M18.
- Policy YAML: R9's description (uk_sme_default.yaml:86, 'left unexplained') is reworded to match M16's new name, and the policy hash changes, recorded as before. R9 keeps M16. Whether it should move to M26 is a decision for whoever owns the firm's policy. The workspace and pack read the same packaged file, so there is no second policy to migrate.
- Pending reviews in a --checkpoint-db written by the old version still resume: the new Transaction fields have defaults, no Category value is removed, and PolicyResult does not change. No new type travels through the graph state, so CHECKPOINT_TYPES does not change.
- Reversible: an older package ignores case folders it does not know and reads the v1 evidence keys it knows. CMA_RULESET=v1 brings back the old categorisation, with cash falling to OTHER, without a reinstall.

## Rollout and flag

**Flags**
- CMA_RULESET: becomes v2 by default once evals and P7 pass; v1 restores today's categorisation exactly, including cash falling to OTHER.
- offline_lock in firm.yaml: on by default. Turning it off is a deliberate act that is recorded, and every evidence record states where data went.
- show_policy_verdict in firm.yaml: on by default (D28). Off hides the outcome and every rule result in the workspace and the pack at once, without a release.
- The workspace, previews, packs, importers, pseudonymiser, verify and report are new commands; none of them runs unless invoked.

**Steps**
1. Land C1 (tables as data, v1 frozen) with P7 passing before any category changes.
2. Land the v2 tables (cash wording included), buckets, monthly summary and indicators behind CMA_RULESET. Make v2 the default when evals and truth for the new synthetic features pass, the cash truth included.
3. Land the case store, the workspace with the illustrative policy check, preview, pack and sign-off, and the offline lock. Demo on the owner's statements and on a synthetic case shaped like a business, both marked as demos (D23).
4. Give the friend the stopwatch trial sheet. They time comparable files the usual way and in the workspace, by stopwatch, outside the tool (D32).
5. Send the approver the exact data statement (D25): it runs on the firm's machine, the offline lock is on, and nothing leaves.
6. Do Tier 2 after the approver answers. Pilot only on pseudonymised or approved real files.

**Rollback.** For the policy check, set show_policy_verdict off. For categorisation, cash included, set CMA_RULESET=v1 or reinstall the previous wheel. Packs and evidence are immutable and record their ruleset, thresholds, policy hash, the show_policy_verdict setting and package version, so nothing needs repair. The old version leaves case folders written by the new one alone.

## Observability

Nothing leaves the machine, so every signal is local, and the underwriter chooses whether to share it.

- **The signal that proves it works:** for each case, in `cma report` and in each pack's evidence: traced figures 100% (every figure has evidence or an n/a reason), reconciled true, guard grounded true.
- **The signal that tests the bet's biggest risk:** M26 (unexplained share of money in) on each real or pseudonymised case, and the size of the judgement queue. If most cases sit above 20%, the tool is handing the hard part back (board risk 1).
- **The signal that tests D31:** overrides from cash_deposit to another category, and cash deposits flagged, per case, in `cma report`. If the friend moves most flagged cash out of trading receipts, the default or the limits are wrong.
- **The signal for the outcome:** stopwatch minutes per file, recorded by the friend on the trial sheet outside the tool (D32). The case log keeps event times for the audit trail only, and nothing reports a duration from them.
- **Log lines:** one structured line per event in the case log, with the event, case id, snapshot and counts, and no names or descriptions. For example: event=pack_written traced=100 reconciled=true unexplained_pct=12.4 cash_flagged=3 policy_check=shown.
- **What fires when it does not work:** there is no remote alerting, by design. Instead the tool refuses, with a non-zero exit and a red banner, when reconciliation fails, the guard fails, the log chain breaks, or a hosted model is requested under the offline lock. `cma report` exits non-zero if any case has traced figures below 100% or reconciled false.
- **CI:** evals extended with truth for every bucket and indicator, the cash ones included; P1-P20 run as tests; the wheel check also loads the rule tables, thresholds, field map and packaged policy from an installed wheel.

## Security notes

- **Who can call it:** the operating-system user who owns the case root, whose files are readable by that user only. The workspace listens only on loopback, requires a random per-launch token on every request, rejects any Host header that is not the loopback address (against DNS rebinding) and sends no permissive CORS headers. Writes also need the expected log position.
- **Trust boundary:** bank data the applicant controls enters through the importers, and the loader checks its structure and balance chain. Descriptions are written by payers: they are escaped in HTML under a strict content security policy with no inline script, formula characters are neutralised in CSV, and they never go into prompts.
- **Secrets:** an optional model API key in the environment, only when the offline lock is off. It is never logged or written to evidence.
- **Personal data:** statements, the profile (director names, account identifiers), override reasons and packs stay in the case folder. Evidence holds ids, figures and hashes, not descriptions or the profile. Purge deletes the folder and leaves a ledger line with no personal data. Encryption at rest is the firm's disk encryption; the tool adds none of its own. Retention follows the firm's policy, and purge is how the tool carries it out.
- **Risk path:** lending data (risk: data). The human acknowledgement is the named sign-off bound to the snapshot. The name is typed, not authenticated (README known limit), so the OS user is recorded alongside it. Real sign-in belongs to a hosted version. The illustrative outcome is computed, labelled and never signed as a decision.
- **Worst cases with this deployed:** First, an applicant writes script into a payment reference to act in the underwriter's browser, for example to change categories or sign off. Escaping, the content security policy and the token stop it. Second, an applicant makes money look like trading income: by naming the payer after an acquirer, adding an invoice reference, or, since D31, paying cash in. The round-trip check, the director-name rule and the indicators surface it, including the three cash indicators, and the line is one click from its figure. Cash split below every limit is the gap that remains, and it is visible only in M18. Third, someone turns the offline lock off and uses a hosted model. The prompt still carries only tables (P5), and the evidence records where data went.
- **Pseudonymised is not anonymous:** amounts, dates and balances can identify a business again, so the output is labelled as personal data.

## Consumer trace

These are the shared contracts this change touches, with every consumer found. Items 1-10 were searched with ripgrep from the repository root at e618469 for revision 1, and their cited lines were re-read at 43f3e57 for this revision. Items 11-14 are new and were searched at 43f3e57. All searches exclude .bridgie/. The commands below are equivalent forms with no backslash escapes.

1. **Category enum (values added; cash_deposit maps to trading receipts).** `rg -nw 'Category|REVENUE_CATEGORIES' -g '!.bridgie/**'`; `rg -n '[.](category|reverses)' src`; `rg -n 'category_by_id' -g '!.bridgie/**'`. Consumers: src/cma/models.py, src/cma/categorise.py, src/cma/metrics.py, src/cma/synth.py, src/cma/graph.py (the checkpoint allow-list names the type), src/cma/mcp_server.py (transactions_for returns the category value), tests/test_categorise.py, tests/test_metrics.py:31 (truth category per line), tests/test_panel_run02_metrics.py, tests/test_panel_run03.py, tests/test_import_monzo.py, examples/cafe_stretched.truth.json. Effect: additive; the director capital line's truth category changes; no fixture has a cash line.
2. **Transaction model (optional fields added).** `rg -n 'Transaction[(]' -g '!.bridgie/**'`. Constructed in src/cma/ingest.py:138, tests/test_categorise.py, tests/test_panel_run02_metrics.py and tests/test_panel_run03.py. Read in categorise.py, metrics.py, mcp_server.py (transactions_for) and graph.py (checkpoint allow-list). Effect: defaults keep every caller working.
3. **Metric list from metrics.compute (M17-M28 added, M16 renamed, M2's method reworded).** `rg -n 'evidence_record|load_csv|met[.]compute|metrics[.]compute|as_table|categorise[.]categorise|cat[.]categorise|Metric[(]' -g '!.bridgie/**'`. Consumers: src/cma/graph.py (draft table, appendix, evidence_record), src/cma/cli.py (_print_metrics), src/cma/mcp_server.py (compute_metrics, check_policy), src/cma/policy.py (checks metric ids), src/cma/guard.py (tags against metrics), src/cma/llm.py (FakeLLM parses table rows), evals/run_evals.py (truth by id), tests/test_metrics.py, tests/test_panel_run02_metrics.py, tests/test_panel_run03.py, tests/test_categorise.py, tests/test_import_monzo.py, tests/test_guard.py (builds Metric objects), and the committed examples. Effect: additive; the regex `rg -n 'findall' src/cma/llm.py` (lines 36 and 38) keys on table rows and still matches.
4. **M16's name and its share denominator.** `rg -n -i 'unclassified' -g '!.bridgie/**'`; `rg -n 'unexplained' src/cma/policies`. Consumers: src/cma/metrics.py:289, src/cma/llm.py:56, src/cma/categorise.py:12 (docstring), src/cma/synth.py (truth field), src/cma/policies/uk_sme_default.yaml:86 (R9 description), README.md:69,118,124, evals/run_evals.py:100, tests/test_metrics.py:53, tests/test_import_monzo.py:63-65, tests/test_panel_run02_metrics.py:73, tests/test_panel_run03.py:130, examples/cafe_stretched.memo.md:5,44,56, examples/cafe_stretched.memo.evidence.json, examples/cafe_stretched.truth.json. Effect: rename and wording only. Values change only where own-account lines now leave the denominator or cash lines leave the numerator; those fixtures are listed in the change, and today none has a cash line.
5. **Evidence JSON (schema_version and new keys, policy_check among them).** `rg -n 'evidence[.]json|evidence_record' -g '!.bridgie/**'`. Writer: src/cma/graph.py:166, through src/cma/cli.py:212. Readers: tests/test_cli.py:46,85,105 and the committed example evidence. New reader: cma verify. No other reader exists in the repo; outside it, any reader keyed on today's fields keeps working.
6. **Policy file wording and hash.** `rg -n 'policy_sha256|_sha256' -g '!.bridgie/**'`. Consumers: src/cma/policy.py:43,122, src/cma/graph.py:154 (appendix), tests/test_cli.py:48 (checks the hash is present, not its value), the committed examples. Effect: the hash changes and is recorded; the pack's snapshot now includes it.
7. **ingest.load_csv (signature unchanged; importers now feed it).** Found by the command in item 3: src/cma/graph.py:188, src/cma/mcp_server.py:54, tests/test_ingest.py, tests/test_import_monzo.py, tests/test_metrics.py. Effect: none.
8. **MCP tool outputs (additions; refuse under the offline lock).** `rg -n 'compute_metrics|check_policy|transactions_for|statement_summary' -g '!.bridgie/**'`. Consumers: src/cma/mcp_server.py, tests/test_mcp_paths.py, and MCP clients outside the repo.
9. **Checkpoint allow-list.** `rg -n 'CHECKPOINT_TYPES' -g '!.bridgie/**'`. Consumers: src/cma/graph.py:44-54 only. Unchanged, because the workspace does not go through the graph and PolicyResult does not change.
10. **.gitignore.** The default case root, private/cma, is already ignored by the private/ line. R31 refuses any other root inside the repository that is not ignored.
11. **REVENUE_CATEGORIES (gains cash_deposit).** `rg -nw 'REVENUE_CATEGORIES' -g '!.bridgie/**'`. Consumers: src/cma/models.py:33 (definition), src/cma/metrics.py:20, 121 (M2, M3, M4) and 172 (M9, and through it M10 and M28), tests/test_panel_run02_metrics.py:11, 70. Effect: v2 cash lines enter every revenue figure. The test at :70 checks that five transfer descriptions with no cash wording are not revenue, and it still passes.
12. **PolicyResult and the overall outcome (new readers: workspace, pack).** `rg -n 'PolicyResult|RuleResult|rules_table' -g '!.bridgie/**'`; `rg -n 'overall|policy_outcome' -g '!.bridgie/**'`. Consumers: src/cma/models.py:111, 128-132; src/cma/policy.py:83-123; src/cma/graph.py:38, 47, 101, 134-143, 155, 162, 216-217, 248; src/cma/guard.py:38, 147, 189-201 (the [Rn] threshold check the pack reuses); src/cma/llm.py:39-40; src/cma/cli.py:274; src/cma/mcp_server.py:82-86 (check_policy); evals/run_evals.py:59; tests/test_guard.py:169-173; tests/test_metrics.py:69; tests/test_panel_run02_metrics.py:48; examples/cafe_stretched.memo.md:24; examples/cafe_stretched.memo.evidence.json:2004. Effect: none of them changes. The new readers take PolicyResult as it is, and the label and the shown flag live in the pack's evidence.
13. **Cash wording in fixtures and synthetic data (none today).** `rg -n -i 'cash (deposit|paid)|paid in|counter credit|withdrawal' -g '!.bridgie/**'`. It matches only the column names in src/cma/ingest.py:39 and tests/test_ingest.py:108, so no committed case changes when cash moves into trading receipts.
14. **Timing (nothing to remove).** `rg -n -i 'timing|stopwatch|active.minutes|elapsed' -g '!.bridgie/**'` finds nothing; withdrawing R49 removes only planned work.
