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

# Choose the categorisation table (src/cma/rules/<id>.yaml; default v1). v1 is the
# original table, frozen; an unknown id is refused. The evidence file records the
# table's id and hash.
CMA_RULESET=v1 cma run examples/cafe_stretched.csv

# Use a real model instead of the offline one.
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
| Categoriser | `categorise.py` + `cma/rules/v1.yaml` | Ordered regex rules, direction-aware, kept as validated data and chosen by `CMA_RULESET`. A transfer in is revenue only with an invoice reference. Returned DDs are linked to the debit they undo. Unmatched lines are `OTHER`, never guessed. |
| Metrics | `metrics.py` | Sixteen figures (revenue, trend, lowest balance, days overdrawn, returned DDs, debt service cover after drawings, VAT timeliness by stagger group, non-trading money in…). Each carries the transaction ids it came from; a figure that can't be computed is n/a with the reason, never a placeholder. |
| Policy | `cma/policies/uk_sme_default.yaml` | Nine illustrative rules with pass / refer / fail, and what a missing figure means. Validated on load. Edit the YAML, not the code. |
| Draft | `graph.py` + `llm.py` | The model gets the metrics table and the policy table, nothing else, and must tag every figure ([Mn]) and threshold ([Rn]). |
| Guard | `guard.py` | Reads every number in the memo, in digits or words, back against the table: value at the precision written, unit, sign, and the tag right after it. |
| Review | `graph.py` | LangGraph `interrupt()`; resumes only with a named reviewer's decision. The final memo carries an appendix of every figure, rule, the model and file hashes. |
| MCP server | `mcp_server.py` | Exposes metrics, policy and evidence lookup as tools for any MCP client; reads only .csv/.yaml under `CMA_DATA_DIR`. |
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

## Data

Everything here is synthetic. No real statements, no customer data, no employer
data. The counterparties are invented.

## Licence

MIT.
