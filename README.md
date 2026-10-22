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

# Run the memo graph. It stops and asks you to approve.
cma run examples/cafe_stretched.csv

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
| Ingest | `ingest.py` | CSV in: `id, date, description, amount, balance`. |
| Categoriser | `categorise.py` | Ordered regex rules. Deterministic. Unmatched lines are `OTHER`, never guessed. |
| Metrics | `metrics.py` | Fifteen figures (revenue, trend, lowest balance, days overdrawn, returned DDs, debt service cover, VAT conduct…). Each carries the transaction ids it came from. |
| Policy | `policy/uk_sme_default.yaml` | Seven illustrative rules with pass / refer / fail. Edit the YAML, not the code. |
| Draft | `graph.py` + `llm.py` | The model gets the metrics table and the policy result, nothing else, and must tag every figure. |
| Guard | `guard.py` | Reads every number in the memo back against the table. |
| Review | `graph.py` | LangGraph `interrupt()`; resumes only with a recorded decision. |
| MCP server | `mcp_server.py` | Exposes metrics, policy and evidence lookup as tools for any MCP client. |
| Evals | `evals/run_evals.py` | Runs all nine cases, reports grounding rate, redrafts and agreement with ground truth. Fails CI if a metric disagrees with truth. |

## Evaluation

With the offline model, all nine synthetic cases are fully grounded and every metric
matches the generator's truth:

```
case                   policy  grounded  rate   redrafts truth
cafe/good              pass    True      1.0    0        True
cafe/stretched         refer   True      1.0    0        True
cafe/poor              fail    True      1.0    0        True
trades/good            refer   True      1.0    0        True
…
9/9 memos fully grounded
```

Run `python evals/run_evals.py --llm anthropic` to measure a real model. The
interesting numbers are grounding rate and redrafts, which is where models differ.

## Known limits (v0.1)

- CSV only. CAMT.053 / MT940 / Open Banking JSON adapters are next.
- Revenue trend compares the first and last three months, so a seasonal business
  (the `trades` archetype peaks in winter) can be referred for decline. The fix is 24
  months of data and a year-on-year comparison, not a cleverer prompt.
- The guard lets small integers through untagged ("three months"). Documented in
  `guard.py`.
- Policy thresholds are illustrative. They are not any lender's real policy.

## Data

Everything here is synthetic. No real statements, no customer data, no employer
data. The counterparties are invented.

## Licence

MIT.
