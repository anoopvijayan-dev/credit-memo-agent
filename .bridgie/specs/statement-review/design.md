# Design — Statement review: every figure traced, the unclear lines first

## Context

**The bet as signed.** One underwriter at a UK lender (D1) reads months of business bank statements by hand before writing each credit memo. The firm bans generic AI, and the friend's fear is a made-up number on a file they sign. The owner chose to treat this as a showcase for one firm (D15, D24): build a working model, show it, then ask the firm's approver for a pilot (D14, D25). The demo runs on the owner's own statements, as a demonstration of the mechanism rather than of the time saving (D23).

**What the evidence changed.** The underwriter's written reply of 2026-11-16 is one anecdotal source and possibly templated (evidence-synthesis.md). It moves the value from adding up to judging:
- 25-45 of a 45-60 minute review goes on 'unusual transactions, internal transfers, director loans, cash deposits'. Adding up takes 10-20.
- They want to click any number and see its transactions, correct categories themselves, get a summary they can paste into their memo, and keep the lending decision.
- The pilot blockers they expect are data protection and accuracy. A tool that runs on the firm's own machine 'could be attractive', and IT/compliance must approve it.
- Formats: an estimated 9 of 15 files are PDF, 4 Open Banking and 2 mixed. This is an estimate, not a count.

**What the repo already does** (read at e618469). It imports a five-column CSV or a Monzo export with strict validation (src/cma/ingest.py, src/cma/importers/monzo.py). An ordered regex categoriser never guesses (src/cma/categorise.py). It computes 16 figures with evidence ids (src/cma/metrics.py:248-292) and applies an illustrative YAML policy (src/cma/policies/uk_sme_default.yaml). A LangGraph pipeline drafts the memo, checks it with the guard and stops for a named reviewer (src/cma/graph.py). There is an offline template model (src/cma/llm.py:26-69) and an MCP server (src/cma/mcp_server.py).

**Gap against the reply**

| The underwriter asked for | Today | Gap |
|---|---|---|
| Genuine trading income separated from transfers, director money and cash | Revenue is claimed narrowly; every other credit is one OTHER pile in M16 | No breakdown of money in; director loans, own-account transfers, refunds and cash look alike |
| Real acquirers and merchant cash advances recognised | Only synthetic acquirer names (README:125-126) | Real card revenue lands in M16 and refers the case |
| Click any number to its lines | Evidence ids in a JSON file; an MCP tool | Nowhere a person can click |
| Correct categories | Not possible | No override and no record of who changed what |
| Monthly credits, debits and average balance | Period totals; lowest balance only | No monthly table, no average balance |
| A summary for my memo | The agent's own memo, with a pass/refer/fail verdict | Not in the firm's field order; the verdict reads as a decision |
| Runs on our machine, with an audit trail | The offline model exists; outputs are git-ignored | Nothing stops a hosted model; no tamper-evident log |
| Open Banking feed, PDFs | CSV and Monzo only (README:111) | No adapter |

## Options considered

**Option A: local review workspace (chosen).** The cma package serves a web page on the underwriter's machine, reachable only from that machine (loopback). Every figure, bucket and monthly cell opens its lines, and the lines needing judgement come first. The underwriter re-categorises lines with a reason and the figures layer recomputes. A named sign-off writes a memo-ready pack and a self-contained HTML record. The model is off by default.

**Option B: static HTML report plus a CLI overrides file.** Each run writes one self-contained HTML file with click-through. Corrections are typed into an overrides file and the run repeated; sign-off stays at the terminal. This is the cheapest option and needs no server. *It lost because* the two things the underwriter asked for beyond click-through, correcting categories and a usable summary, would happen in a file-editing loop outside the page. The page also cannot record time per case, which is the bet's outcome. It is kept as the format of the signed record (the pack's HTML).

**Option C: spreadsheet as the interface.** Export lines and figures to a workbook, let the underwriter edit a category column, and re-import. It is familiar: they add things up in a spreadsheet today. *It lost because* a figure loses its link to its evidence as soon as a cell is edited or copied. The tool also cannot tell a category correction from an edited amount, and re-importing a hand-edited workbook is the error-prone step the bet is meant to remove. A monthly-summary CSV stays as an export.

**Option D: hosted service (SaaS) now.** One hosted web app per firm; D14 frames the business as SaaS. *It lost for this bet because* applicant data would leave the firm, which is the blocker the reply names. A hosted pilot also needs sign-in, tenancy and a data processing agreement before the approver will look at it. Option A's workspace is the same web page, so hosting it later is a change of deployment, not a rewrite. Raised as a question for the owner.

**Option E: let a model categorise the unclear lines.** Send unexplained lines to a model and accept the buckets it suggests. *It lost because* it puts payer-written text and names in a prompt, which raises GDPR and prompt-injection risk from whoever wrote the payment reference. It also breaks ADR 001 ('the model receives a table, not a statement'), and a category chosen by a model is exactly the kind of invented judgement the underwriter fears. Deterministic rules, rules the firm learns and per-line overrides do this job instead.

**Sub-decision: how to show what is unexplained.** (i) Redefine M16 to mean unexplained only. (ii) Keep M16's value (money in that is not trading receipts), rename it to say so, and add a figure for each bucket plus the unexplained share (M26). *Chose (ii)*: a memo already signed that cites M16 must keep its meaning, and ids are stable.

**Sub-decision: how sign-off works.** (i) Drive the existing LangGraph review interrupt from the workspace. (ii) Make the case folder the source of truth, and make sign-off a case-log event bound to a snapshot hash. *Chose (ii)*. The figures are a pure function of the case's inputs, so a checkpoint would hold nothing extra, and re-running the graph after every override would also re-run the draft node. ReviewDecision's name and notes validation is reused, not copied. `cma run` keeps its interrupt unchanged.

**Sub-decision: where the categorisation rules live.** (i) Keep them as code and add more. (ii) Move the rule tables into packaged, validated data files like the policy YAML; the matching passes (reversals, round trips, pairing, profile names) stay in code. *Chose (ii)*: the lists of acquirers, lenders and gambling operators are what changes most often, the v1 table can be kept as data for rollback, and a firm's own rules use the same format.

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
          +--> review workspace (loopback; renders only)
          |
          +--> pack builder (field map, section text, guard) --> sign-off (case log event)
                    --> memo.md, record.html, evidence.json, monthly.csv

**Components**
- **C1 Rule tables and categoriser** (src/cma/categorise.py; rule tables as data under src/cma/rules/). Built-in tables v1 (today's) and v2 (the new lists). Precedence: per-line override, then protected built-in patterns (returned DD, director, capital, shareholder, lender, HMRC), then profile names (directors, own accounts), then firm rules, then the rest of the built-in table, then the default (a debit is supplier, a credit is unexplained). Each line records which rule set its category. A pattern that is ambiguous between buckets resolves to one that needs judgement, never to trading receipts.
- **C2 Figures layer** (src/cma/metrics.py, new src/cma/indicators.py). Adds M17-M28, the monthly summary and the indicators. All sums are in integer pence. Each figure comes with its evidence and, for sums, the evidence total. A case whose buckets do not reconcile is refused.
- **C3 Case store** (new src/cma/case.py). Case root, case folders, profile, override log, hash-chained case log, single-writer lock, snapshot hash, purge and purge ledger, and the git-ignore and sync-folder checks.
- **C4 Importers** (src/cma/importers/). A shared step that walks and verifies the balance, extracted from monzo.py; new OBIE v3.1, TrueLayer and bank-CSV importers. Every importer writes the five-column file that ingest.load_csv validates.
- **C5 Review workspace** (new src/cma/review/). A loopback server and one bundled page with no build step. It renders what C2 computed and never computes money. The override form offers only categories valid for the line's direction.
- **C6 Pack builder and sign-off** (new src/cma/pack.py). Memo field map, deterministic section text, grounding guard, HTML record, evidence JSON v1, monthly CSV, atomic write, sign-off binding, and verify (C8).
- **C7 Firm configuration** (new src/cma/firm.py). offline_lock (default on), show_policy_verdict (default off), timing (default off), and the paths to firm rules, thresholds and field map.
- **C9 Pseudonymiser** (new src/cma/pseudonymise.py).
- **C10 Synthetic data and evals** (src/cma/synth.py, evals/run_evals.py).
- **C11 Report** (CLI over C3). A local per-case summary with no personal data.
- **C12 Existing paths** (graph.py, cli.py, mcp_server.py, llm.py). Behaviour unchanged; new fields are additive; the offline lock is checked.

### Requirement traceability

| Requirement | Component |
|---|---|
| R1, R2, R3 | C2 (C5 and C6 refuse to render when reconciliation fails) |
| R4, R5, R6, R7, R21, R54 | C1 |
| R8, R9, R41 | C1 (pairing), C2 (combined figures), C3 (accounts) |
| R10 | C1, C2, C10 |
| R11, R12, R13, R14 | C2 |
| R15, R16, R25, R33, R48, R52 | C5 |
| R17, R18, R19 | C3 (log), C1 (apply), C5 (form) |
| R20 | C3, C5, C1 |
| R22, R23, R26, R27, R28 | C6 |
| R24 | C5, C6 |
| R29, R30 | C7, C12 |
| R31, R32, R35, R42, R43 | C3 |
| R34 | C5, C6 (escaping, CSV), C12 (prompt) |
| R36, R37, R38, R39, R40 | C4, C3 |
| R44, R45 | C6 (verify) |
| R46 | C9 |
| R47 | C10 |
| R49, R50 | C3, C5, C11 |
| R51 | C2 (difference), C5 (entry and display) |
| R53 | C12 |

Every requirement maps to a component. Two things in the bet are not system requirements and have no component: the outcome metric itself, hours per application (R49 and R50 only make it measurable), and the approver's answer (D25, E1, E5).

### Out of scope

- PDF extraction. An estimated majority of files are PDF, but there is no sample layout, and extraction is where invented numbers come from. When a layout arrives, it plugs into the importer contract and the balance-chain check.
- Plaid and Yapily importers, until a sample of the firm's feed exists (E7).
- Any credit decision, recommendation or score. The illustrative policy stays in `cma run` only.
- Companies House, the credit bureau, or any other network lookup.
- Hosting, sign-in, multi-tenancy, and a data processing agreement.
- Merchant cash advance repayments taken out of card settlements before payout; they never appear as a line.
- A seasonality-aware trend (24 months, year on year).
- DOCX output. Sections are copied as plain or rich text until the firm's template arrives (E8).
- A money-out breakdown beyond the existing figures and the monthly totals.
- Any claim about the market. This is for one firm and one underwriter.

### Cut line for 2026-11-29

Tier 1, demonstrating the mechanism (D23): R1-R7, R10-R19, R22-R31, R33-R35, R42, R43, R47, R48, R52-R54 (37 requirements). It covers the v2 tables with real acquirers and lenders, the buckets, monthly summary and indicators, single-account cases with a profile and an override log, the workspace, the pack and sign-off, the offline lock, and a synthetic case shaped like a business.
Tier 2: R8, R9, R20, R21, R32, R36-R41, R44-R46, R49-R51 (17 requirements). It covers the importers, multiple accounts, firm rules, verify, the pseudonymiser, timing, the report and comparison.

## Consequences

**Gains**
- The underwriter gets the view they asked for: £80k in, of which £55k is trading and the other £25k is explained by bucket. Every figure is clickable, and their own corrections are on record.
- The approver's question, does data leave the firm, has an answer the code enforces: offline lock on, the MCP server refuses to start, and the evidence states offline mode.
- Firm rules build up in a file the firm owns. That is the only switching cost any board lens found.
- The bet's outcome becomes readable from local records: time per case and unexplained share, with no personal data, once the owner and the friend turn timing on.

**Costs and risks**
- The scope is large for two weeks and $20, and Tier 1 alone is most of it.
- A server, even one reachable only from the same machine, is new attack surface (see Security notes).
- Twelve more figures make the `cma run` prompt longer and the appendix wider.
- Conservative defaults understate a cash business. Cash deposits stay out of trading receipts until a person moves them (owner question).
- The categoriser still reads payer-written text, and a payer can name themselves after an acquirer. Indicators reduce this but do not remove it.
- The requirement most likely to be wrong is that the reply's 13 generic fields are the firm's memo. It is cheap to check (E8: a redacted template) and cheap to fix, because the field map is data.

### Perspective check

- **Security.** *Who can call it:* the operating-system user who owns the case root. The workspace also needs a per-launch token and a loopback Host header (R33). *Trust boundary:* imported files and payer text are checked by the loader and escaped on output (R34). *Secrets:* only an optional model key in the environment, never logged. *Personal data:* kept under one case root readable only by its owner, never committed to git (R31), never in prompts (R30); evidence holds only hashes of the profile. *Risk path:* lending data; the human acknowledgement is the named sign-off bound to a snapshot (R22). *Worst case:* see Security notes.
- **Platform.** *Blast radius:* one Python package; new optional parts; the evidence JSON gains fields. *Old and new side by side:* `cma run` is untouched, new commands are additive, and CMA_RULESET=v1 reproduces today's categories. *Resources:* files on disk only; no tables, queues, cron jobs or IAM. *Rollback:* an environment flag or a reinstall, with no data repair. *Observability:* see that section.
- **Staff.** *Smallest change:* Tier 1 is the smallest set that shows the mechanism in the reply's terms; Tier 2 can be cut off. *Ownership:* arithmetic stays in metrics.py and indicators.py, categorisation in categorise.py plus the rule data; the workspace and pack only render. *Six-month misunderstandings removed:* M16 is renamed so it cannot be confused with M26 (unexplained); the action reads 'sign off figures', not 'approve the loan'; OTHER means unexplained; pseudonymised data is still personal data. *Most likely wrong:* the memo fields (E8) and the cash-deposit default. *Out of scope:* listed above.
- **Clean code.** *What changes most:* the rule lists, kept apart as data. *Names:* follow the code: money in (inflows in metrics.py), override, firm rule, case, pack, sign-off. *Tests:* properties P1-P18, synthetic truth for every bucket, and golden files for the importers. *Duplication removed:* all importers share one balance walk; sign-off reuses ReviewDecision's validation. *Duplication introduced:* the v1 rule table, kept as data for one release. *No single-caller abstraction:* no storage interface for a future hosted version; the importer contract has four callers.
- **Data-intensive.** *Idempotency:* imports are keyed by file hash, an identical override changes nothing, and a second sign-off is refused. *Ordering and concurrency:* one writer per case, enforced by a lock plus the expected log position; a second tab gets a conflict and reloads. *Partial failure:* append-only logs with a check for a torn last entry; packs are written to a temporary folder and then renamed. *Consistency:* the workspace reads the latest state; the pack and sign-off bind to a snapshot. *Migration:* additive, nothing to backfill. *Money:* integer pence, GBP only, and amounts with more than two decimals are refused.

## Data changes

**Category enum (src/cma/models.py).** Six values are added and none of the existing values change. Credit categories map to buckets as follows:

| Credit category | Bucket |
|---|---|
| revenue_card_settlement, revenue_transfer | trading receipts (M2) |
| cash_deposit (new) | cash deposits (M18) |
| own_account_transfer (new; also used for debits) | own-account transfers in (M19) |
| director_funding (new) | director and shareholder funding (M20) |
| loan_drawdown | lending drawdowns (M21) |
| refund_in (new), returned_dd | refunds and returned payments in (M22) |
| hmrc_repayment (new) | HMRC repayments (M23) |
| other_explained_in (new: grants, insurance claims, interest) | other explained money in (M24) |
| other | unexplained money in (M25) |

In the code and the UI, `other` now means unexplained. Merchant cash advances use loan_drawdown and loan_repayment. Gambling is an indicator, not a category. A credit in any other category is a reconciliation failure (R3).

**Transaction (src/cma/models.py).** Three optional fields are added: the account key (default 'main'), the id of the rule, firm rule or override that set the category, and the provider's category as an untrusted hint. Amount and balance keep their current types where the model meets the rest of the code; the figures layer converts them to pence.

**New figures.** No id is ever reused. M1-M16 keep their ids, units and values; M16 is renamed 'Non-trading money in (share)'.

| id | name | unit |
|---|---|---|
| M17 | Total money in | GBP |
| M18 | Cash deposits | GBP |
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

Reconciliation: M17 = M2 + M18 + M19 + M20 + M21 + M22 + M23 + M24 + M25, in pence. M16 and M26 share one denominator: money in less lending drawdowns, returned direct debits and own-account transfers. Credits that fall to `other` today keep the same treatment in M9 whichever new bucket they land in, so M9 does not move on existing cases. M27 averages the closing balance of every calendar day in the period, carried over days with no lines, as M6 does. M28 adds the proposed monthly repayment for each month of the period to existing debt service.

**Monthly summary.** One row per calendar month: the month, a part-month flag, money in, money out, trading receipts, average and lowest end-of-day balance, and days overdrawn. Each cell carries its evidence ids and their total.

**Indicators.** Each has an id, a kind, the threshold that fired, evidence ids and a one-line reason. Thresholds live in a packaged, validated YAML file that the firm can replace.

**Rule tables.** Packaged YAML under src/cma/rules/: v1 is today's table, frozen; v2 adds acquirers, gateways, marketplaces and delivery platforms, lenders and merchant cash advance providers, wording for own pots and savings, cash wording, HMRC repayments, refunds, and explained non-trading credits. Tables are validated on load like the policy, and each has an id and a hash.

**Case root.** Defaults to private/cma under the working directory, or CMA_HOME. Layout:

    firm.yaml            offline_lock, show_policy_verdict, timing, paths
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
      packs/<snapshot>/  memo.md, record.html, evidence.json, monthly.csv

Case ids are random and never the applicant's name. The profile never leaves the case folder; evidence records carry only its hash.

**Evidence JSON v1.** Everything in today's record, plus: schema_version, case id, accounts (key, source format, file hash, period, line count), ruleset id and hash, firm rules hash, profile hash, overrides (sequence number, line, old and new category, reason, name, OS user, time), money-in buckets, monthly summary, indicators, snapshot hash, sign-off (name, OS user, time, notes), egress (offline, or the model's name) and package version.

**Synthetic truth (src/cma/synth.py).** New opt-in generator features, with truth fields for each bucket, for own-account pairs and for each line's category. Existing archetypes and profiles produce byte-identical statements. In their truth files, the director capital line's category becomes director_funding.

## Interfaces and contracts

**CLI (all additions)**
- `cma case new [--demo] [--label TEXT]` prints a random case id.
- `cma case import CASE FILE --format csv|monzo|openbanking|truelayer|bank-csv[:PROFILE] [--account KEY] [--statement-balance AMOUNT --at DATE]`
- `cma case profile CASE` edits the profile.
- `cma review CASE` starts the workspace, prints the URL with its token, and opens a browser if there is one.
- `cma case pack CASE` and `cma case sign CASE` are also available in the workspace. Sign-off is interactive only; there is no --yes.
- `cma case purge CASE`
- `cma verify EVIDENCE [--case CASE]`
- `cma pseudonymise FILE --format F --out PATH [--profile CASE]`
- `cma report` prints a summary of every case with no personal data.
- `cma generate` gains opt-in features: director funding, own-account transfers, cash, real acquirers, lenders, two accounts.
- Environment variables: CMA_HOME (case root) and CMA_RULESET (v1 or v2).

**Importer contract.** Input: one source file and an optional statement balance. Output: the five-column statement for one account, the account metadata, and an optional hints file. The output must pass ingest.load_csv unchanged, and all importers share one step that walks and verifies the balance. Refusals give line or record numbers, never cell values, as the MCP server does today.

**Review workspace API.** Internal to the package; the page and the server ship together, so it is not a shared contract. It can read the case summary; read the lines behind a figure, bucket, monthly cell or indicator; post an override with the expected log position; preview and promote a firm rule; build a pack; sign off a snapshot; and post a comparison figure. Every request carries the session token. Every write carries the expected log position and gets a conflict if that position is stale.

**Pack.** memo.md (sections in field-map order, figures with their ids, blanks left for the underwriter), record.html (self-contained, makes no external requests), evidence.json (v1) and monthly.csv. Written once, to a new folder named by the snapshot hash.

**Memo field map.** A YAML file giving, for each field, its name, the figures or tables it shows, and a sentence template whose numbers are placeholders for figure ids. Fields with nothing mapped are marked for the underwriter. The default reproduces the 13 fields in the reply: business details, loan request, trading history, monthly turnover and credits, average and minimum balances, existing commitments, returned direct debits, VAT and HMRC, director loans and transfers between the business's accounts, credit history, affordability, key risks and recommendation. Credit history, key risks and recommendation stay blank.

**MCP tools.** compute_metrics returns M1-M28 (an addition), and a new read-only tool returns the money-in buckets. The server refuses to start under the offline lock.

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
| An override names a line that a corrected re-import removed | Shown as stale and not applied until confirmed or dropped | R19 |
| Two browser tabs or two terminals write to one case | The lock plus the expected log position: the stale write gets a conflict and reloads | R17 |
| Power is lost while a log entry is being written | The torn last entry is detected; sign-off is refused until the underwriter confirms dropping it | R43 |
| The tool crashes while writing a pack | The pack goes to a temporary folder and is renamed; leftovers are removed on the next start; no partial pack is visible | R26 |
| The pack's text fails the guard (a template bug, or a firm-edited template with a literal number) | No pack is written; the failing number is named | R28 |
| The buckets do not reconcile (a bug) | No figures are shown or exported; the difference in pence and the lines involved are reported | R3 |
| The profile or an override changes after the pack was built | Sign-off is refused, naming what changed | R23 |
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
- The committed examples (examples/cafe_stretched.memo.md, .memo.evidence.json and .truth.json) are regenerated in the same change. The CSV is byte-identical; the appendix gains M17-M28 and M16's new name; in the truth file, the director capital line becomes director_funding. test_categoriser_matches_truth runs under v2; v1 is covered by P7, which compares values.
- Policy YAML: R9's description (uk_sme_default.yaml:86, 'left unexplained') is reworded to match M16's new name, and the policy hash changes, recorded as before. R9 keeps M16. Whether it should move to M26 is a decision for whoever owns the firm's policy.
- Pending reviews in a --checkpoint-db written by the old version still resume: the new Transaction fields have defaults, and no Category value is removed. No new type travels through the graph state, so CHECKPOINT_TYPES does not change.
- Reversible: an older package ignores case folders it does not know and reads the v1 evidence keys it knows. CMA_RULESET=v1 brings back the old categorisation without a reinstall.

## Rollout and flag

**Flags**
- CMA_RULESET: becomes v2 by default once evals and P7 pass; v1 restores today's categorisation exactly.
- offline_lock in firm.yaml: on by default. Turning it off is a deliberate act that is recorded, and every evidence record states where data went.
- show_policy_verdict in firm.yaml: off by default (R25).
- timing in firm.yaml: off by default until the owner decides.
- The workspace, packs, importers, pseudonymiser, verify and report are new commands; none of them runs unless invoked.

**Steps**
1. Land C1 (tables as data, v1 frozen) with P7 passing before any category changes.
2. Land the v2 tables, buckets, monthly summary and indicators behind CMA_RULESET. Make v2 the default when evals and truth for the new synthetic features pass.
3. Land the case store, workspace, pack and offline lock. Demo on the owner's statements and on a synthetic case shaped like a business, both marked as demos (D23).
4. Send the approver the exact data statement (D25): it runs on the firm's machine, the offline lock is on, and nothing leaves.
5. Do Tier 2 after the approver answers. Pilot only on pseudonymised or approved real files.

**Rollback.** Set CMA_RULESET=v1, or reinstall the previous wheel. Packs and evidence are immutable and record their ruleset and package version, so nothing needs repair. The old version leaves case folders written by the new one alone.

## Observability

Nothing leaves the machine, so every signal is local, and the underwriter chooses whether to share it.

- **The signal that proves it works:** for each case, in `cma report` and in each pack's evidence: traced figures 100% (every figure has evidence or an n/a reason), reconciled true, guard grounded true.
- **The signal that tests the bet's biggest risk:** M26 (unexplained share of money in) on each real or pseudonymised case, and the size of the judgement queue. If most cases sit above 20%, the tool is handing the hard part back (board risk 1).
- **The signal for the outcome:** active minutes per case and overrides by kind, when timing is on.
- **Log lines:** one structured line per event in the case log, with the event, case id, snapshot and counts, and no names or descriptions. For example: event=pack_built traced=100 reconciled=true unexplained_pct=12.4.
- **What fires when it does not work:** there is no remote alerting, by design. The tool refuses instead, with a non-zero exit and a red banner, when reconciliation fails, the guard fails, the log chain breaks, or a hosted model is requested under the offline lock. `cma report` exits non-zero if any case has traced figures below 100% or reconciled false.
- **CI:** evals extended with truth for every bucket; P1-P18 run as tests; the wheel check also loads the rule tables, thresholds and field map from an installed wheel.

## Security notes

- **Who can call it:** the operating-system user who owns the case root, whose files are readable by that user only. The workspace listens only on loopback, requires a random per-launch token on every request, rejects any Host header that is not the loopback address (against DNS rebinding) and sends no permissive CORS headers. Writes also need the expected log position.
- **Trust boundary:** bank data the applicant controls enters through the importers, and the loader checks its structure and balance chain. Descriptions are written by payers: they are escaped in HTML under a strict content security policy with no inline script, formula characters are neutralised in CSV, and they never go into prompts.
- **Secrets:** an optional model API key in the environment, only when the offline lock is off. It is never logged or written to evidence.
- **Personal data:** statements, the profile (director names, account identifiers), override reasons and packs stay in the case folder. Evidence holds ids, figures and hashes, not descriptions or the profile. Purge deletes the folder and leaves a ledger line with no personal data. Encryption at rest is the firm's disk encryption; the tool adds none of its own. Retention follows the firm's policy, and purge is how the tool carries it out.
- **Risk path:** lending data (risk: data). The human acknowledgement is the named sign-off bound to the snapshot. The name is typed, not authenticated (README known limit), so the OS user is recorded alongside it. Real sign-in belongs to a hosted version.
- **Worst cases with this deployed:** First, an applicant writes script into a payment reference to act in the underwriter's browser, for example to change categories or sign off. Escaping, the content security policy and the token stop it. Second, an applicant makes money look like trading income by naming the payer after an acquirer or adding an invoice reference. The round-trip check, the director-name rule and the indicators surface it, and the line is one click from its figure. Third, someone turns the offline lock off and uses a hosted model. The prompt still carries only tables (P5), and the evidence records where data went.
- **Pseudonymised is not anonymous:** amounts, dates and balances can identify a business again, so the output is labelled as personal data.

## Consumer trace

These are the shared contracts this change touches, with every consumer found. The searches were run with ripgrep from the repository root at e618469, excluding .bridgie/. The commands below are equivalent forms with no backslash escapes.

1. **Category enum (values added).** `rg -nw 'Category|REVENUE_CATEGORIES' -g '!.bridgie/**'`; `rg -n '[.](category|reverses)' src`; `rg -n 'category_by_id' -g '!.bridgie/**'`. Consumers: src/cma/models.py, src/cma/categorise.py, src/cma/metrics.py, src/cma/synth.py, src/cma/graph.py (the checkpoint allow-list names the type), src/cma/mcp_server.py (transactions_for returns the category value), tests/test_categorise.py, tests/test_metrics.py:31 (truth category per line), tests/test_panel_run02_metrics.py, tests/test_panel_run03.py, tests/test_import_monzo.py, examples/cafe_stretched.truth.json. Effect: additive; the director capital line's truth category changes.
2. **Transaction model (optional fields added).** `rg -n 'Transaction[(]' -g '!.bridgie/**'`. Constructed in src/cma/ingest.py:138, tests/test_categorise.py, tests/test_panel_run02_metrics.py and tests/test_panel_run03.py. Read in categorise.py, metrics.py, mcp_server.py (transactions_for) and graph.py (checkpoint allow-list). Effect: defaults keep every caller working.
3. **Metric list from metrics.compute (M17-M28 added, M16 renamed).** `rg -n 'evidence_record|load_csv|met[.]compute|metrics[.]compute|as_table|categorise[.]categorise|cat[.]categorise|Metric[(]' -g '!.bridgie/**'`. Consumers: src/cma/graph.py (draft table, appendix, evidence_record), src/cma/cli.py (_print_metrics), src/cma/mcp_server.py (compute_metrics, check_policy), src/cma/policy.py (checks metric ids), src/cma/guard.py (tags against metrics), src/cma/llm.py (FakeLLM parses table rows), evals/run_evals.py (truth by id), tests/test_metrics.py, tests/test_panel_run02_metrics.py, tests/test_panel_run03.py, tests/test_categorise.py, tests/test_import_monzo.py, tests/test_guard.py (builds Metric objects), and the committed examples. Effect: additive; the regex `rg -n 'findall' src/cma/llm.py` (lines 36 and 38) keys on table rows and still matches.
4. **M16's name and its share denominator.** `rg -n -i 'unclassified' -g '!.bridgie/**'`; `rg -n 'unexplained' src/cma/policies`. Consumers: src/cma/metrics.py:289, src/cma/llm.py:56, src/cma/categorise.py:12 (docstring), src/cma/synth.py (truth field), src/cma/policies/uk_sme_default.yaml:86 (R9 description), README.md:69,118,124, evals/run_evals.py:100, tests/test_metrics.py:53, tests/test_import_monzo.py:63-65, tests/test_panel_run02_metrics.py:73, tests/test_panel_run03.py:130, examples/cafe_stretched.memo.md:5,44,56, examples/cafe_stretched.memo.evidence.json, examples/cafe_stretched.truth.json. Effect: rename and wording only. Values change only where own-account lines now leave the denominator; those fixtures are listed in the change.
5. **Evidence JSON (schema_version and new keys).** `rg -n 'evidence[.]json|evidence_record' -g '!.bridgie/**'`. Writer: src/cma/graph.py:166, through src/cma/cli.py:212. Readers: tests/test_cli.py:46,85,105 and the committed example evidence. New reader: cma verify. No other reader exists in the repo; outside it, any reader keyed on today's fields keeps working.
6. **Policy file wording and hash.** `rg -n 'policy_sha256|_sha256' -g '!.bridgie/**'`. Consumers: src/cma/policy.py:43,122, src/cma/graph.py:154 (appendix), tests/test_cli.py:48 (checks the hash is present, not its value), the committed examples. Effect: the hash changes and is recorded.
7. **ingest.load_csv (signature unchanged; importers now feed it).** Found by the command in item 3: src/cma/graph.py:188, src/cma/mcp_server.py:54, tests/test_ingest.py, tests/test_import_monzo.py, tests/test_metrics.py. Effect: none.
8. **MCP tool outputs (additions; refuse under the offline lock).** `rg -n 'compute_metrics|check_policy|transactions_for|statement_summary' -g '!.bridgie/**'`. Consumers: src/cma/mcp_server.py, tests/test_mcp_paths.py, and MCP clients outside the repo.
9. **Checkpoint allow-list.** `rg -n 'CHECKPOINT_TYPES' -g '!.bridgie/**'`. Consumers: src/cma/graph.py:44-54 only. Unchanged, because the workspace does not go through the graph.
10. **.gitignore.** The default case root, private/cma, is already ignored by the private/ line. R31 refuses any other root inside the repository that is not ignored.
