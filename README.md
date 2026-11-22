# credit-memo-agent

A UK SME cash-flow credit memo that a risk committee can audit.

Give it twelve months of business current-account statements and it produces a credit
memo in which **every figure is computed deterministically, cited to the statement
lines behind it, checked by a grounding guard, and held for a human to approve**. The
language model writes the prose. It never does the arithmetic.

```
load → categorise → metrics → policy → draft → guard → review (human) → finalise
                                         ↑                 │
                                         └──── redraft ────┘   if ungrounded, max 2
```

## Why

Lenders don't need a model that guesses at cash flow. They need one that says
"revenue averaged £22,114 a month [M3]" and lets an analyst click through to the 261
settlements that number came from. See
[ADR 001](docs/decisions/001-the-model-never-does-the-arithmetic.md).

## Quick start

```bash
pip install -e ".[dev]"

# Make a synthetic statement (there is no open UK SME dataset, so we generate one
# with a ground-truth file alongside it).
cma generate --archetype cafe --profile stretched --out examples

# Opt-in features (repeat --feature, or --feature all): acquirers, director, cash,
# lenders, two_accounts. The truth file then holds every money-in bucket, every line's
# category and every indicator, for the v2 rules and the case profile it records;
# two_accounts also writes the reserve account as cafe_stretched_reserve.csv.
# Without --feature the statement and truth are byte-identical to earlier releases.
cma generate --archetype cafe --profile stretched --out /tmp/cma-demo --feature all
# --case also makes one demo case in CMA_HOME holding both accounts and the case profile;
# the truth's "case" then holds that case's truth, the own-account pairs included.
cma generate --archetype cafe --profile stretched --out /tmp/cma-demo --feature all --case

# Run the memo graph. It stops, asks your name, and asks you to approve.
# --vat-stagger is the HMRC stagger group from the VAT certificate (the examples use 3).
cma run examples/cafe_stretched.csv --vat-stagger 3

# Scripts can approve with --yes, but only with a named reviewer and only if
# every figure passed the grounding check; the memo is stamped
# "automated, no interactive review". --out also writes memo.evidence.json
# (every transaction id behind every figure, with "schema_version": 1; a record
# without that key is v0, and v1 only adds keys); a rejected memo goes to
# memo.rejected.md.
# Existing files are never overwritten.
cma run examples/cafe_stretched.csv --vat-stagger 3 --yes --reviewer "A. Analyst" --out private/memo.md

# Keep a pending review across restarts (pip install -e ".[durable]"). The database
# holds the whole statement, so keep it in private/ too.
cma run examples/cafe_stretched.csv --checkpoint-db private/reviews.sqlite --thread case-7
cma resume case-7 --checkpoint-db private/reviews.sqlite

# A real statement: keep the export and everything made from it under private/
# (git-ignored, as are *.csv, memo files, evidence files and databases anywhere).
cma import monzo "private/Monzo Data Export.csv" --opening-balance 123.45 --out private/mine.csv

# A case: a folder under the case root (CMA_HOME, default private/cma; owner-only, and
# refused inside a git tree that does not ignore it). Each command is logged in the case.
# A root inside a OneDrive, Dropbox, Google Drive or iCloud Drive folder gets a warning
# on stderr before the case is made, recorded in the case log: keep cases on a local disk.
cma case new --demo --label "Cafe trial"          # prints a random case id
cma case profile <id> --director "Jane Doe" --own-account "ACME HOLDINGS" --proposed-repayment 1500
cma case import <id> private/statement.csv --format csv
cma case import <id> "private/Monzo Data Export.csv" --format monzo --account monzo --opening-balance 123.45
# An OBIE v3.1 transactions file (one response, an array of pages, or pages one after
# another): booked GBP lines only, pending ones left out and counted, the bank's
# TransactionId kept as the line id, a transaction a page repeats kept once. The balance
# comes from the per-line balances, or from a statement balance at the end of a date.
cma case import <id> private/transactions.json --format openbanking --account current
cma case import <id> private/transactions.json --format openbanking --statement-balance 1389.75 --at 2026-04-03
# Lines booked at the same moment (a date-only export puts a day at midnight) are put in the
# order their per-line balances chain in. An export with a balance on every line, on none or
# on each day's last line always imports. When only some lines at one moment carry a balance,
# finding the order is a search: it is found in nearly every case, and otherwise the file is
# refused saying the search limit was reached (never that the chain is broken).
# A TrueLayer Data API transactions export gets the same checks, its running_balance read
# as the per-line balance. TrueLayer's own categories are kept beside the statement only as
# untrusted hints (statements/<account>.hints.json); no figure, rule or indicator reads them.
cma case import <id> private/truelayer.json --format truelayer --account current
# A bank's own CSV download (paid-in and paid-out columns, dates like 03/03/2026 or
# 3 Mar 2026, newest first) through a named profile (barclays, lloyds, natwest, starling),
# or through your own column mapping (columns, date_format, order; see
# src/cma/bank_profiles/profiles.yaml). The statement written passes the same checks as a
# five-column CSV; without the bank's balance column, pass --opening-balance.
cma case import <id> private/lloyds.csv --format bank-csv:lloyds --account current
cma case import <id> private/bank.csv --format bank-csv --mapping private/bank-map.yaml --opening-balance 200.00

# Correct one line's category (only the category; one valid for the line's direction),
# with a reason and your typed name. The override goes to overrides.jsonl and the case
# log with your operating-system user and the UTC time. `cma case figures` recomputes
# every figure from the statements and the whole override log and marks with * each one
# that rests on an overridden line. An override whose line is gone, or whose statement
# changed since it was recorded, is stale and not applied until you confirm or drop it.
cma case override <id> L42 director_funding --reason "director's own transfer" --name "Pat"
cma case overrides <id>                           # applied, stale, superseded or dropped
cma case figures <id>
cma case resolve-override <id> 3 --confirm --name "Pat"   # or --drop

# Open the review workspace for a case: a page served on 127.0.0.1 only, from the next
# free port from 8765 (--port to choose another). Each start prints a URL with a new
# random token; a request without it, or whose Host is not 127.0.0.1 and that port, is
# refused. The page loads only files bundled in the package. Ctrl-C stops it.
# Every figure, money-in bucket and monthly cell on the page links to exactly the lines
# behind it (date, description, amount, balance, category and the rule or override that
# set it), with the lines' total for a sum (the figure negated for money paid out), or the
# inputs and method for an average, share, ratio or count.
# Each line in the judgement queue and the drill-downs has a form to change its category
# (only categories valid for its direction), with a reason and your typed name; stale
# overrides are listed to confirm or drop. A save from a page older than the case log is
# refused as a conflict: reload and save again. Each save is appended to the case log and
# the page comes back with the figures, indicators and policy check recomputed.
cma review <id>                                   # --no-browser to only print the URL

# Preview the pack before sign-off: the memo-ready sections in the order of the memo field
# map (by default the 13 fields in src/cma/memo_fields/memo_fields.yaml; a firm's own
# memo_fields.yaml at the case root replaces it), each figure followed by its id, fields
# with no figure marked for the underwriter and left empty, the illustrative policy check
# (label first) while show_policy_verdict is on, the demo stamp on a demo case, the
# grounding guard's verdict and the snapshot hash a sign-off is bound to. It writes no
# file, only a preview entry in the case log. Exit 1 when the text fails the guard.
cma case preview <id>

# Sign off the figures of the latest preview (or --snapshot <hash>): it asks for your typed
# name and confirms, records it with your operating-system user and the UTC time, and writes
# memo.md, record.html (a self-contained record that works offline: every figure links to
# its lines, with the sign-off), evidence.json and monthly.csv once to packs/<snapshot> in
# the case folder. Signing off the
# figures does not approve the loan. Refused, writing nothing, when the case changed since
# the preview (it names what), when the case log is broken (exit 3, naming the first bad
# entry), when the figures do not reconcile or the text fails the guard, or when that
# snapshot's pack is already written; a pack is never overwritten. There is no --yes. The
# workspace (`cma review`) offers the same preview and sign-off from the case page.
cma case sign <id>

# Verify an evidence record: recompute every figure, bucket and monthly cell from the
# inputs it recorded and list each mismatch by id (never a description). A pack is checked
# against its case's statements, profile and firm rules, which must be the ones recorded,
# and the override log up to the position the pack recorded, so later overrides and
# accounts imported later change nothing. A `cma run` record is checked against its
# statement file, which must hash as recorded. A record made with another ruleset is
# refused, naming the package version and ruleset that made it; a record without
# schema_version (v0) is checked for M1 to M16 only. It writes nothing. Exit 0 with no
# mismatch, 1 with a mismatch, 2 when the record cannot be verified.
cma verify private/cma/cases/<id>/packs/<snapshot>/evidence.json
cma verify memo.evidence.json --statement private/statement.csv

# Drop an entry the case log holds past its recorded entries that a crash, a full disk or a
# lost power left unfinished, once you confirm it with your typed name; the drop is itself
# recorded. Refused, changing nothing, when nothing is torn or the log is broken before it
# (exit 3). Then sign off or save again what the entry held.
cma case repair <id>

# Check a case's hash-chained log (log.jsonl) against its head file. Exit 3 names the
# first entry that was changed, removed, reordered or left half written.
cma case check private/cma/cases/<id>

# Delete every file of a case (asks first; --yes skips the question). The case root's
# purge_ledger.jsonl gains one line with only the case id, the UTC time and your
# operating-system user. A purge stopped part-way is finished by running it again.
cma case purge <id>

# Choose the categorisation table (src/cma/rules/<id>.yaml; default v2). v1 is the
# original table, frozen; v2 adds real UK acquirers, gateways, marketplaces, delivery
# platforms, lenders and merchant cash advance providers, own pots and savings, cash
# paid in (counted as trading receipts), cash-withdrawal wording, HMRC repayments,
# refunds and explained non-trading credits. An unknown id is refused. The evidence
# file records the table's id and hash, and under "lines" each line's id, category and
# the rule that set it (no descriptions). CMA_RULESET=v1 is the rollback. The committed
# lines whose category v2 changes are listed in docs/v2-newly-recognised.md; M1-M16 do
# not move on any committed case or fixture. To go back to the previous release itself,
# set CMA_RULESET=v1 first and finish any review drafted under v2 before installing it: a
# review drafted under v2 can hold a category (director_funding, for one) that the
# previous release cannot read back.
CMA_RULESET=v1 cma run examples/cafe_stretched.csv

# Use a real model instead of the offline one. The offline lock in firm.yaml (under
# CMA_HOME, default private/cma) is on by default and refuses hosted models and the MCP
# server; turn it off on purpose first. The prompt still carries only the tables.
# Edit the file the tool reads, keeping its other settings: open it in an editor and set
#   offline_lock: false
# (create it with just that line if it does not exist yet).
mkdir -p "${CMA_HOME:-private/cma}" && ${EDITOR:-nano} "${CMA_HOME:-private/cma}/firm.yaml"
# In a case of more than one account, a debit pairs with the single credit of the same
# amount in another account dated within own_account_pairing_days (default 3, 0 to 31) of
# it; a debit with more than one such credit is left unpaired and listed in the judgement
# queue with every candidate. The value is part of the preview's snapshot, so changing it
# after a preview makes sign-off refuse that preview. Delete the key before installing
# an older release: an older release refuses a firm.yaml with a key it does not know.
export ANTHROPIC_API_KEY=...   # or OPENAI_API_KEY
cma run examples/cafe_stretched.csv --llm anthropic
```

`examples/cafe_stretched.memo.md` is the output of the offline model on the committed
example.

## What's in the box

| Layer | File | What it does |
|---|---|---|
| Synthetic data | `synth.py` | Three business archetypes (café, trades, e-commerce) × three risk profiles (good, stretched, poor). Seeded. Writes the ground truth next to each statement. |
| Ingest | `ingest.py` | CSV in: `id, date, description, amount, balance`. Refuses duplicate ids, non-ISO or future dates, out-of-order dates, loose amounts and a broken balance chain; reads newest-first files. |
| Import | `importers/monzo.py` | Monzo export → statement CSV, locally. Monzo Flex is read as a loan. |
| Categoriser | `categorise.py` + `cma/rules/v1.yaml`, `v2.yaml` | Ordered regex rules and name lists, direction-aware, kept as validated data and chosen by `CMA_RULESET`. Precedence: per-line override, protected rules (the line-type cues: returned DD, own-pot moves, HMRC, refunds; then lenders, director, capital introduced, shareholder, generic loan wording), an own-account pairing, an ambiguous verdict, profile names, firm rules, the rest of the table, the default; each line records its rule id. Under v2 a credit whose matching rules point to two or more money-in buckets (cues first; names only when no cue matches), or that has a payment rail with cash paid-in wording, is ambiguous: it stays `OTHER` (unexplained, never trading receipts) and records `v2:conflict:` with every rule it matched in either stage, the rail included, until a person overrides it or an own-account pairing settles it; a director's or own account's name from the case profile only adds itself as one more candidate. A credit that names both a director or shareholder and an own account from the profile is ambiguous too, under v1 as under v2, unless a protected verdict decides it; on v1 the returned-payment, HMRC, loan, drawings and director, owner, shareholder, capital-introduced, refund, reversal and own-pot rules give that protected verdict. Inside one bucket the table's `category_order` picks the category (cash deposit, customer transfer, card settlement; returned payment, refund), never rule order. Names match as whole words in any script. A transfer in is revenue only with an invoice reference. Returned DDs are linked to the debit they undo. Unmatched lines are `OTHER`, never guessed. |
| Metrics | `metrics.py` | Twenty-eight figures (revenue, trend, lowest balance, days overdrawn, returned DDs, debt service cover after drawings, VAT timeliness by stagger group, non-trading money in (M16), total money in (M17), cash inside trading receipts (M18), the seven other money-in buckets (M19-M25), the unexplained share (M26), average end-of-day balance (M27) and debt service cover including a proposed facility (M28, n/a with the reason in `cma run`, which has no case profile)…), added up in integer pence. Each carries the transaction ids it came from; a figure that can't be computed is n/a with the reason, never a placeholder. If the money-in buckets do not add up to total money in to the penny, no figures are shown: `cma run` exits 6 naming the difference in pence and the lines. |
| Indicators | `indicators.py` + `cma/thresholds/indicators.yaml` | Eight risk indicators (a large single credit, a cash deposit at or above the single-deposit limit, a cash-heavy month, cash paid back in after a similar withdrawal, gambling payments, returned direct debits, runs of overdrawn days, irregular credits from a named acquirer), compared in pence and written into the evidence record, each with its lines and the thresholds that fired. The thresholds are illustrative; `cma run --thresholds FILE` takes a firm's own, and a malformed file is refused naming the line. |
| Policy | `cma/policies/uk_sme_default.yaml` | Nine illustrative rules with pass / refer / fail, and what a missing figure means. Validated on load. Edit the YAML, not the code. |
| Draft | `graph.py` + `llm.py` | The model gets the metrics table and the policy table, nothing else, and must tag every figure ([Mn]) and threshold ([Rn]). |
| Guard | `guard.py` | Reads every number in the memo, in digits or words, back against the table: value at the precision written, unit, sign, and the tag right after it. |
| Review | `graph.py` | LangGraph `interrupt()`; resumes only with a named reviewer's decision. The final memo carries an appendix of every figure, rule, the model and file hashes. |
| MCP server | `mcp_server.py` | Exposes metrics, the money-in buckets (`money_in_buckets`, read only, line ids only), policy and evidence lookup as tools for any MCP client; reads only .csv/.yaml under `CMA_DATA_DIR`; refuses to start while the offline lock is on. |
| Evals | `evals/run_evals.py` | Runs all nine cases, reports grounding rate, redrafts and agreement with ground truth on nine metrics. Fails CI if any metric disagrees with truth or the offline memo is ungrounded. |

## Evaluation

With the offline model, all nine synthetic cases are fully grounded and every metric
matches the generator's truth:

```
case                   policy  grounded  rate   redrafts truth
cafe/good              pass    True      1.0    0        ok
cafe/stretched         refer   True      1.0    0        ok
cafe/poor              fail    True      1.0    0        ok
trades/good            refer   True      1.0    0        ok
…
9/9 memos fully grounded
```

Run `python evals/run_evals.py --llm anthropic` to measure a real model. The
interesting numbers are grounding rate and redrafts, which is where models differ.

## Independent review

v0.1 was reviewed by a six-seat agent panel (reviewer, security, clean-code,
platform, staff, data-intensive) run with [Bridgie](https://github.com/anoopvijayan-dev/bridge),
each seat in a fresh context against written requirements. Verdict: **fail**, with
17 consolidated issues; the report is in
[`.bridgie/runs/v01-pilot-ready/panel.md`](.bridgie/runs/v01-pilot-ready/panel.md).
The blocker and the five majors that broke the core promises (C1–C6) are fixed, each
with regression tests taken from the seats' repros. A second panel run on the fixes
([`panel-02.md`](.bridgie/runs/v01-pilot-ready/panel-02.md)) found 19 more (D1–D19,
one blocker), and a third ([`panel-03.md`](.bridgie/runs/v01-pilot-ready/panel-03.md))
found 27 (E1–E27, one blocker) in those fixes and the new code; all are fixed, with
tests named after each finding.

## Known limits (v0.1)

- CSV only. CAMT.053 / MT940 / Open Banking JSON adapters are next.
- Revenue trend compares the first and last three months, so a seasonal business
  (the `trades` archetype peaks in winter) can be referred for decline. The fix is 24
  months of data and a year-on-year comparison, not a cleverer prompt.
- The guard checks figures, not judgements: "no late VAT payments" when M13 is 2 has
  no number in it. The reviewer reads the metrics table in the appendix for that.
- Revenue by bank transfer needs an invoice reference to count. Statements without
  references show it as non-trading money in (M16), which refers the case.
- Pending reviews live in memory unless you pass `--checkpoint-db`.
- The reviewer's name is asserted, not authenticated: anyone at the terminal can type
  any name. A pilot inside a lender would put the CLI behind their own sign-in.
- Debt service cover (M10) is historical and covers existing debt only; it does not
  include the facility being applied for. Money in that isn't explained as trading
  is left out of it, and shows as non-trading money in (M16).
- The categoriser knows the synthetic acquirer names. Card revenue from a real
  acquirer it doesn't recognise lands in M16 and refers the case until a rule is added.
- Policy thresholds are illustrative. They are not any lender's real policy.
- The cloud-sync warning knows each client's own folder names, the OneDrive
  variables Windows sets and Dropbox's info file. A folder synced under any other
  name, or a network share, is not detected.

## Data

Everything here is synthetic. No real statements, no customer data, no employer
data. The counterparties are invented.

## Licence

MIT.
