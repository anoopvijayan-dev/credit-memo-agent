# ADR 001: The model never does the arithmetic

**Status:** accepted · **Date:** 2026-10-22

## Context

A credit memo is a document someone signs. If a figure in it is wrong, the lender
makes a bad decision, and the person who signed it carries the consequence. Language
models are good at prose and unreliable at sums: they round, they transpose, and they
occasionally produce a plausible number that appears nowhere in the source.

Most open examples of "AI underwriting" hand the model the raw statement and ask for
a memo. That is the wrong division of labour.

## Decision

1. **Money is only added up in `metrics.py`.** It is plain Python, it is tested
   against a generator that knows the right answers, and every metric records the
   transaction ids it was computed from.
2. **The model receives a table, not a statement.** The draft prompt contains the
   metrics table and the policy result. It never sees the transactions.
3. **Every figure in the memo carries a citation tag** (`£22,114 [M3]`). A reviewer
   can click from the tag to the metric to the statement lines behind it.
4. **A grounding guard reads the memo back.** Any number that isn't a metric value is
   flagged; any metric value without its tag in the same sentence is flagged. The
   model gets two attempts to fix its draft. After that the memo still stops for a
   human, but it arrives marked as not grounded.
5. **Nothing is final until a person approves it.** The graph interrupts at the
   review node and only resumes with a recorded decision and reviewer name.

## Consequences

- The model's job shrinks to writing clear English about numbers it was given. That
  is the job it is good at.
- Evaluation becomes possible without a labelled memo corpus: we can measure
  grounding rate, redraft count and policy agreement on synthetic cases with known
  answers (see `evals/run_evals.py`).
- Some things a human analyst would notice from raw lines (a one-off large credit, a
  suspicious counterparty name) are invisible to the memo until there is a metric for
  them. That is deliberate; add a metric, with evidence, rather than letting the model
  free-read the statement.
- The guard allows small integers in prose ("three months") without a tag. This is a
  known soft spot and is documented in `guard.py`.
