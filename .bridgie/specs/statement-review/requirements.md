# Requirements — Statement review: every figure traced, the unclear lines first

Bet: statement-review, signed 2026-11-16. Scope is a showcase for one firm (D15, D24): a working model the underwriter can try, after which the firm's approver decides on a pilot (D14, D25). Forms and risk tags follow the template.

Revision 3, after D52 (changes requested; the owner raised it as D49 after the fourth T03 review). Only R4 changes: it gains the ambiguity policy, and the two terms it uses are defined below. Every other requirement, property and id is as revision 2 left it.

Revision 2, after D34 (changes requested). Ids from revision 1 are kept and never reused. Changed: R1 and R14 (cash deposits are trading receipts and the large ones are flagged, D31); R15 (judgement queue); R22, R23, R26 and R28 (the pack is written once, at sign-off, and carries the policy check); R25 (pass/refer/fail shown with the illustrative label, D28); R47 (synthetic cash); R50 (no durations, D32); P1 and P17. New: R55, R56, P19, P20. Withdrawn: R49 (timing the underwriter, D32). Confirmed as designed: local-first (D29), OBIE v3.1 and TrueLayer first (D30), Tier 1 only by 2026-11-29 (D33).

Terms. *The system*: the cma package running on the underwriter's machine. *Figures layer*: src/cma/metrics.py and a new indicators module, the only code that does arithmetic on money (ADR 001). *Case*: one application, holding one or more of the applicant's accounts, a profile, an override log and a case log. *The reply*: the underwriter's written answers of 2026-11-16 (one anecdotal source; see evidence-synthesis.md). *Judgement queue*: the lines the workspace lists first because a person has to decide what they are. *Policy check*: the pass, refer or fail outcome and the rule results from the packaged policy (src/cma/policies/uk_sme_default.yaml). Its rule ids (R1 to R9, cited as [R1] to [R9] in a pack) are not the requirement ids in this file. *Illustrative label*: the sentence shown with every policy outcome, saying that the thresholds ship with the tool, are not the firm's lending policy, and do not make the lending decision. *Preview*: the pack's content and its snapshot hash, shown before sign-off; nothing is written until sign-off. *Line-type cue* (revision 3): wording that says what kind of line it is: a returned payment, a move to or from the applicant's own pot or savings, HMRC, a refund or reversal, or the payment rail (Faster Payment, BACS, CHAPS, a transfer, a standing order), which belongs to no bucket. *Name list* (revision 3): a maintained list of who sent a credit or what it is for: acquirers, gateways, marketplaces, delivery platforms, lenders and merchant cash advance providers, director and capital-introduced wording, cash paid-in wording, invoice references and explained non-trading words.

## R1 · ubiquitous · risk: money
THE SYSTEM SHALL place every credit in a case in exactly one money-in bucket (trading receipts, own-account transfers in, director and shareholder funding, lending drawdowns, refunds and returned payments, HMRC repayments, other explained money in, unexplained), and SHALL report total money in and each bucket as a GBP figure with the ids of the lines behind it.
Source: the reply, 'if a tool could reliably tell me that £80k entered the business account but only £55k represented genuine trading receipts, explain the remaining £25k'; D31 (cash deposits sit inside trading receipts, R55).

## R2 · ubiquitous · risk: money
THE SYSTEM SHALL compute every money figure, bucket, monthly cell, indicator comparison and comparison difference in integer pence from GBP amounts, in the figures layer only, and never in the review workspace, the pack renderer or a model.

## R3 · unwanted · risk: money
IF the money-in buckets do not sum to total money in to the penny, or any credit is in no bucket or in more than one, THEN THE SYSTEM SHALL NOT show or export figures for the case, and SHALL report the difference in pence and the lines involved.

## R4 · ubiquitous · risk: money
THE SYSTEM SHALL recognise, from maintained lists shipped as data: credits from UK card acquirers, payment gateways, marketplaces and delivery platforms as trading receipts; lines naming UK business lenders and merchant cash advance providers as lending drawdowns (money in) or debt service (money out); moves between the applicant's own pots or savings as own-account transfers; HMRC credits as HMRC repayments; refund and reversal credits as refunds; and cash paid in at a counter or machine as cash deposits.
THE SYSTEM SHALL treat a credit's wording as ambiguous when its line-type cues point to more than one money-in bucket, when a payment rail appears with cash paid-in wording, or, with no line-type cue, when the name lists it matches point to more than one money-in bucket; SHALL place an ambiguous credit in unexplained money in, record every rule it matched, and list it in the judgement queue (R15); and SHALL NOT count it as trading receipts unless the underwriter moves it there with an override (R17). Only a per-line override (R17), a case-profile name (R5, R6) or an own-account pairing (R8) settles an ambiguous credit; a firm rule does not. Line-type cues that agree outrank name lists that point elsewhere (PAYPAL REFUND is a refund); a returned-payment code the bank prints at the start of the line (UNPAID DD) settles the line type on its own; and a match inside a longer listed phrase counts only as the longer phrase (STRIPE CAPITAL is a lending drawdown, not also a card settlement; HMRC REFUND is an HMRC repayment).
Source: README known limits (unknown acquirers land in M16); the reply (Stripe, SumUp, merchant cash advances); D46 (line-type wording before name lists); D48 (bare words are not evidence); D52 (ambiguous or conflicting wording goes to the judgement queue and is never counted as trading receipts).

## R5 · conditional · risk: money
IF the case profile names a director or shareholder, THEN THE SYSTEM SHALL put each credit whose counterparty matches that name in director and shareholder funding, even when the credit carries an invoice reference.

## R6 · conditional · risk: money
IF the case profile names the applicant's other accounts, THEN THE SYSTEM SHALL classify lines in either direction whose counterparty matches one of them as own-account transfers, and SHALL leave them out of trading receipts and out of operating cash flow (M9).

## R7 · unwanted · risk: money
IF no built-in rule, firm rule or override explains a credit, THEN THE SYSTEM SHALL NOT count it as trading receipts; it stays in unexplained money in.

## R8 · conditional · risk: money
IF a case holds more than one of the applicant's accounts, THEN THE SYSTEM SHALL pair each debit in one account with the single credit of the same amount in another account of the case within the configured number of days, and SHALL classify both lines as own-account transfers.
Source: the reply, 'transfers between business accounts can inflate turnover if counted twice'.

## R9 · unwanted · risk: money
IF a debit has more than one candidate credit for an own-account pairing, THEN THE SYSTEM SHALL NOT pair it, and SHALL list the debit and every candidate in the judgement queue as a possible own-account transfer.

## R10 · ubiquitous · risk: money
THE SYSTEM SHALL keep the ids, units and values of M1 to M16 unchanged on every committed synthetic case and test fixture, except for lines that a new rule now recognises, which the change SHALL list; M16 SHALL be renamed so that its name says it measures money in that is not trading receipts.

## R11 · ubiquitous · risk: money
THE SYSTEM SHALL produce a monthly summary with one row per calendar month in the period, part months marked as part, giving money in, money out, trading receipts, average end-of-day balance, lowest end-of-day balance and days overdrawn, each cell with the ids of the lines behind it.
Source: the reply, 'calculating monthly credits, debits, average balances and existing repayments'.

## R12 · ubiquitous · risk: money
THE SYSTEM SHALL report the average end-of-day balance over the period as a figure, citing the lines that set each day's closing balance.

## R13 · conditional · risk: money
IF the case profile gives the proposed facility's monthly repayment, THEN THE SYSTEM SHALL report debt service cover including that repayment; otherwise THE SYSTEM SHALL show that figure as n/a with the reason.

## R14 · ubiquitous · risk: data
THE SYSTEM SHALL raise risk indicators from thresholds in an editable file (a single credit that is large against typical trading receipts; each cash deposit at or above the single-deposit limit; a calendar month in which cash deposits exceed a set share of that month's trading receipts; a cash deposit made within a set number of days after a cash withdrawal of an amount within a set tolerance; payments to gambling operators; returned direct debits; runs of consecutive overdrawn days; irregular credits from a named acquirer), each listing its lines and the threshold that fired, and SHALL label any threshold still at its shipped value as illustrative.
Source: the reply, 'Highlight director loans, unusual credits and large cash deposits'; 'Flag returned direct debits, persistent overdraft usage'; D31, 'with deposits above a threshold flagged'.

## R15 · event · risk: ux
WHEN the underwriter opens a case, THE SYSTEM SHALL show the money-in breakdown, the monthly summary, the figures, the indicators and, while R25 applies, the policy check, with the judgement queue (unexplained lines, possible own-account transfers, and every line an indicator flagged, which includes cash deposits at or above the single-deposit limit) listed first, largest amount first.

## R16 · event · risk: ux
WHEN the underwriter selects a figure, a bucket or a monthly cell, THE SYSTEM SHALL list exactly the lines behind it, each with date, description, amount, balance, category and the rule or override that set the category; for a sum it SHALL show the lines' total next to the figure, and for an average, share, ratio or count it SHALL show the inputs and the method.
Source: the reply, 'I'd want to click any number in the report and see exactly which transactions make up that figure'.

## R17 · event · risk: money
WHEN the underwriter changes a line's category and gives a reason, THE SYSTEM SHALL append the change (line, old and new category, reason, typed name, operating-system user, UTC time) to the case's override log, recompute every figure from the statements and the whole override log, and mark each figure that now rests on an overridden line.
Source: the reply, 'Allow me to manually correct categories'.

## R18 · unwanted · risk: money
IF a requested change would alter a line's amount, date, balance or description, add or remove a line, or give a line a category not valid for its direction, THEN THE SYSTEM SHALL NOT accept it; overrides change categories only.

## R19 · conditional · risk: data
IF an override names a line that is not in the case, or was recorded against a statement file whose hash no longer matches, THEN THE SYSTEM SHALL NOT apply it, and SHALL show it as stale until the underwriter confirms or drops it.

## R20 · event · risk: data
WHEN the underwriter promotes an override to a firm rule, THE SYSTEM SHALL show the lines the rule would match in the open case before saving, then save the counterparty pattern, direction, category, name, time and reason to the firm rules file, and apply the rule to every case categorised afterwards.

## R21 · unwanted · risk: money
IF a firm rule would reclassify a line matched by a built-in director, capital, shareholder, lender, HMRC or returned-payment pattern, THEN THE SYSTEM SHALL NOT apply the firm rule to that line; only a per-line override with a reason can change it.

## R22 · event · risk: data
WHEN the underwriter signs off a case's figures, THE SYSTEM SHALL require a typed name, record it with the operating-system user and UTC time, and bind it to the snapshot of the preview the underwriter signed: statement hashes, ruleset, firm rules, profile, override log position, thresholds, memo field map, policy hash, the show_policy_verdict setting and package version.

## R23 · unwanted · risk: data
IF anything in the snapshot has changed since the preview was built, THEN THE SYSTEM SHALL NOT accept the sign-off, and SHALL say what changed.

## R24 · ubiquitous · risk: ux
THE SYSTEM SHALL word the underwriter's action as signing off the figures, never as approving the loan, and SHALL leave the recommendation and every judgement field blank for the underwriter.
Source: the reply, 'I wouldn't want AI making the final lending decision. I'd want it assisting the underwriter.'

## R25 · state · risk: ux
WHILE show_policy_verdict is on in the firm configuration (the default), THE SYSTEM SHALL show in the review workspace and the pack the overall pass, refer or fail outcome and each rule's result from the packaged policy, computed from the current figures, and SHALL put the illustrative label in the same workspace panel and the same pack section as the outcome.
Source: D28, 'Show it, labelled as illustrative thresholds, not the firm's policy'; README:127, 'Policy thresholds are illustrative. They are not any lender's real policy.'

## R26 · event · risk: data
WHEN a case is signed off, THE SYSTEM SHALL write the review pack once, from the signed preview, containing: memo-ready sections ordered by an editable memo field map (by default the 13 fields listed in the reply), each figure followed by its id; while R25 applies, a policy-check section after the memo fields that no memo field maps to; a self-contained HTML record that works offline, with click-through from every figure to its lines and the sign-off; the evidence JSON; and the monthly summary as CSV. THE SYSTEM SHALL NOT overwrite an existing pack.
Source: the reply, 'Produce a summary I can use directly in my credit memo'.

## R27 · conditional · risk: data
IF a memo field has no figure mapped to it, THEN THE SYSTEM SHALL mark it as for the underwriter and leave it empty.

## R28 · unwanted · risk: data
IF the pack's text fails the grounding guard (a number that is not a figure or a policy threshold at the precision shown, a figure without its id, or a threshold without its rule id and a limit word), THEN THE SYSTEM SHALL NOT write the pack.

## R29 · state · risk: security
WHILE offline lock is on in the firm configuration (the default), THE SYSTEM SHALL refuse to create a hosted model client, SHALL refuse to start the MCP server, SHALL open no network connection other than the loopback workspace, and SHALL record offline mode in every evidence record.
Source: the reply, 'A tool that operates entirely on our own machine or within our own environment, without transmitting customer data externally, could be attractive.'

## R30 · unwanted · risk: security
IF a hosted model is used, THEN THE SYSTEM SHALL NOT put transaction descriptions, counterparty names, case profile fields or any line-level data in a prompt; a prompt carries only the metric and rule tables, the period and the placeholder account name.

## R31 · ubiquitous · risk: security
THE SYSTEM SHALL keep each case's statements, profile, logs and outputs under one case root readable only by the operating-system user who created it, and SHALL refuse to create a case root inside a git working tree that does not ignore it.

## R32 · conditional · risk: security
IF the case root lies inside a folder that a known cloud-sync client uploads (OneDrive, Dropbox, Google Drive, iCloud Drive), THEN THE SYSTEM SHALL warn before creating a case, and SHALL record the warning in the case log.

## R33 · event · risk: security
WHEN the review workspace starts, THE SYSTEM SHALL listen on the loopback interface only, issue a new random session token, refuse any request that lacks the token or carries a Host header other than the loopback address and port, and serve only assets bundled in the package.

## R34 · ubiquitous · risk: security
THE SYSTEM SHALL treat all payer-written and provider-written text (descriptions, references, merchant names, provider categories) as untrusted: escape it in HTML, neutralise leading formula characters in text cells of CSV exports, never send it to a model, and never act on it.

## R35 · event · risk: data
WHEN the underwriter purges a case, THE SYSTEM SHALL delete all the case's files, and SHALL record in a firm-level ledger only the case id, the purge time and who purged it.

## R36 · event · risk: data
WHEN a UK Open Banking (OBIE Account and Transaction API v3.1) transactions file is imported, THE SYSTEM SHALL keep only booked GBP transactions, keep the provider's transaction ids, rebuild the running balance from per-line balances or from a supplied statement balance, and refuse the file if the balance chain breaks anywhere.
Source: D5; D30; E3 and E7 (provider unknown); the reply names TrueLayer, Yapily and Plaid as common.

## R37 · event · risk: data
WHEN a TrueLayer Data API transactions export is imported, THE SYSTEM SHALL apply the same checks as R36, and SHALL keep the provider's own categories only as untrusted hints that no figure uses.
Source: D30.

## R38 · event · risk: data
WHEN a bank CSV with separate paid-in and paid-out columns, a non-ISO date format or newest-first order is imported, THE SYSTEM SHALL convert it through a named bank profile or a custom column mapping, and SHALL apply the same checks as the five-column loader.

## R39 · conditional · risk: data
IF a file already imported into the case is imported again, THEN THE SYSTEM SHALL leave the case unchanged.

## R40 · unwanted · risk: data
IF a new account overlaps an existing account of the case in period and shares most of its (date, amount) pairs, THEN THE SYSTEM SHALL NOT add it, and SHALL say it is probably the same account imported twice.

## R41 · ubiquitous · risk: money
THE SYSTEM SHALL keep line ids unique within a case by prefixing them with the account key when the case holds more than one account, compute flow figures across all the case's accounts after removing paired own-account transfers, and compute balance figures on the combined end-of-day balance over the dates every account covers, showing n/a with the reason when those dates do not overlap.

## R42 · ubiquitous · risk: data
THE SYSTEM SHALL append every import, profile change, override, firm-rule promotion, preview, pack write and sign-off to the case log, each entry carrying the hash of the entry before it.
Source: the reply, 'The firm would need confidence that data is secure, access is controlled and there's an audit trail.'

## R43 · unwanted · risk: data
IF the case log's hash chain is broken or its last entry is incomplete, THEN THE SYSTEM SHALL NOT accept a sign-off, and SHALL name the first bad entry.

## R44 · event · risk: data
WHEN asked to verify an evidence record, THE SYSTEM SHALL recompute every figure, bucket and monthly cell from the recorded inputs and report each mismatch, or confirm that there is none.

## R45 · conditional · risk: data
IF the evidence record was made with a ruleset other than the one installed, THEN THE SYSTEM SHALL refuse to verify it, and SHALL name the package version and ruleset that made it.

## R46 · event · risk: data
WHEN asked to pseudonymise a statement, THE SYSTEM SHALL write a copy in which personal and business names and account numbers are replaced by consistent tokens while recognised institutions (acquirers, lenders, HMRC) and all amounts, dates and balances are kept, SHALL tokenise the case profile the same way, and SHALL label the output as pseudonymised personal data, not anonymous data.
Source: the reply, 'I'd start with synthetic or properly anonymised sample statements and compare the results with our existing process'.

## R47 · ubiquitous · risk: none
THE SYSTEM SHALL generate synthetic business cases that include real acquirer names, director funding, own-account transfers across two accounts, cash deposits (at least one at or above the single-deposit limit, and one paid in after a similar cash withdrawal) and lender lines, with ground truth for every bucket, line and indicator, while every existing archetype and profile still produces a byte-identical statement.

## R48 · state · risk: ux
WHILE a case is marked as a demo (synthetic data, or the owner's own statements per D23), THE SYSTEM SHALL show a banner in the workspace and stamp the pack, saying it demonstrates the mechanism and is not an applicant file.

## R50 · event · risk: ux
WHEN asked for a report, THE SYSTEM SHALL summarise for each case the lines in the judgement queue, the lines each indicator flagged, overrides by old and new category, unexplained share of money in, whether every figure was traced, and whether the case was signed off, with no applicant names, descriptions or amounts and no duration derived from the case log's timestamps.
Source: D32 (files are timed with a stopwatch, outside the tool).

## R51 · event · risk: ux
WHEN the underwriter enters their own figure for a memo field or a month, THE SYSTEM SHALL show the difference from its own figure and list, largest first, the lines in that period whose bucket a person might read differently: cash deposits, unexplained lines, possible own-account transfers and overridden lines.
Source: the reply, 'compare the results with our existing process'.

## R52 · event · risk: ux
WHEN the underwriter saves an override on a case of up to 10,000 lines, THE SYSTEM SHALL show the recomputed figures, indicators and policy check within 2 seconds on a typical office laptop.

## R53 · ubiquitous · risk: data
THE SYSTEM SHALL keep cma run, cma resume, cma import monzo, cma generate, the MCP tools and the evidence JSON working with their current options and fields; new evidence fields SHALL be additive, and every new evidence record SHALL carry a schema version.

## R54 · conditional · risk: data
IF CMA_RULESET is set to v1, THEN THE SYSTEM SHALL categorise with the built-in rule table as it was before this change, and SHALL record v1 as the ruleset in every evidence record.

## R55 · ubiquitous · risk: money
THE SYSTEM SHALL count every credit categorised as a cash deposit as trading receipts, in M2 and in every figure and monthly cell built on trading receipts, and SHALL report the cash deposits within trading receipts as a separate GBP figure with the ids of the deposits behind it.
Source: D31, 'Counted as trading receipts, with deposits above a threshold flagged'; the reply, 'Highlight ... large cash deposits'.

## R56 · state · risk: ux
WHILE show_policy_verdict is off in the firm configuration, THE SYSTEM SHALL NOT show an overall outcome or any rule result in the review workspace or the pack, and SHALL record in the evidence that the policy check was hidden.
Source: D28 (the setting is the rollback path for showing the outcome).

## P1 · property
For any case, trading receipts and the other seven money-in buckets SHALL sum to total money in to the penny, and every credit's id SHALL appear in exactly one bucket's evidence; the cash-deposits figure is not a bucket and is not added.
Validates: R1, R2, R3, R7

## P2 · property
For any sum figure, bucket or monthly cell, the signed amounts of its evidence lines SHALL total the figure to the penny.
Validates: R1, R11, R16, R55

## P3 · property
For any statements, profile and override log, computing the figures twice SHALL give identical results, and the figures after applying overrides one at a time SHALL equal a fresh computation with all of them applied.
Validates: R17, R44

## P4 · property
For any override log, the set of (id, date, amount, balance, description) across the case's lines SHALL be the same before and after the overrides are applied.
Validates: R18

## P5 · property
For any case whose descriptions and profile contain sentinel strings, no prompt built for a model SHALL contain any sentinel.
Validates: R30, R34

## P6 · property
For any credit that no built-in rule, firm rule or override matches, its id SHALL be in the evidence of unexplained money in and not in the evidence of M2.
Validates: R7

## P7 · property
For any committed synthetic case and test fixture, M1 to M16 under the new ruleset SHALL equal their values before this change, except on lines the change lists as newly recognised; under CMA_RULESET=v1 they SHALL equal them with no exceptions.
Validates: R10, R53, R54

## P8 · property
For any description text, the workspace HTML and the pack HTML SHALL contain it only in escaped form, and no text cell in an exported CSV SHALL begin with =, +, -, @, a tab or a carriage return.
Validates: R34

## P9 · property
For any import file, importing it twice SHALL leave the same case state and the same case log head as importing it once.
Validates: R39

## P10 · property
For any statement and profile, the pseudonymised statement and profile SHALL give identical figures, buckets, monthly cells and indicators.
Validates: R46

## P11 · property
For any multi-account case, each own-account pair SHALL be one debit and one credit of equal amount in different accounts, no line SHALL be in two pairs, and total money in less own-account transfers in SHALL equal the generator's credits from outside the applicant's accounts.
Validates: R6, R8, R9, R41

## P12 · property
For any case, monthly trading receipts SHALL sum to M2 and monthly money in SHALL sum to total money in, to the penny.
Validates: R11, R55

## P13 · property
For any signed pack, verifying its evidence record against its case SHALL reproduce every figure, bucket and monthly cell exactly.
Validates: R22, R44

## P14 · property
For any case, the pack's text SHALL pass the grounding guard with no unmatched numbers and no missing citations.
Validates: R26, R28

## P15 · property
For any case log, changing, removing or reordering any entry before the head SHALL be detected on the next load.
Validates: R42, R43

## P16 · property
For any firm rules file, no line matched by a protected built-in pattern SHALL change category through a firm rule.
Validates: R21

## P17 · property
For any synthetic case generated with the new features, every bucket, every line's category and every indicator flag SHALL equal the generator's ground truth.
Validates: R1, R4, R5, R8, R14, R47, R55

## P18 · property
For any run while offline lock is on, asking for a hosted model or starting the MCP server SHALL fail before any network call is attempted.
Validates: R29

## P19 · property
For any case, the evidence of the cash-deposits figure SHALL be exactly the credits categorised as cash deposits, each of them SHALL also be in M2's evidence, the figure SHALL not exceed M2, and every cash deposit at or above the single-deposit limit SHALL be in the judgement queue with the indicator that flagged it.
Validates: R14, R15, R55

## P20 · property
For any case, with show_policy_verdict on, every workspace panel and pack section that shows a pass, refer or fail outcome SHALL contain the illustrative label and the recommendation field SHALL be empty; with it off, neither the workspace nor the pack SHALL contain an outcome or a rule result.
Validates: R24, R25, R56
