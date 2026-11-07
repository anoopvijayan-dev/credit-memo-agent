---
id: v01-pilot-ready
title: "credit-memo-agent v0.1 is fit to run a lender's anonymised statement in a pilot"
lane: engineering
risk: money
owner: Anoop Vijayan
status: draft
confidence: opinion
appetite: { time: 2w, tokens_usd: 20 }
kill_criteria: ["a blocker finding in the money path that cannot be fixed in a week", "no lender or broker agrees to run a statement through it by week 6"]
non_goals: ["full underwriting decision", "real customer data", "CAMT/MT940 adapters in v0.1"]
created: 2026-11-07
---

# credit-memo-agent v0.1 is fit to run a lender's anonymised statement in a pilot

## Who

Credit analysts and heads of credit at UK non-bank SME lenders and commercial-finance brokers who spread twelve months of business current-account statements by hand before writing a memo.

## Problem in their words

"Spreading a statement takes an afternoon, the memo still gets a number wrong, and when compliance asks where a figure came from nobody can say." (Paraphrase of the BridgeTrade broker conversations, 2026.)

## Why now

Lenders are trialling LLM tools and getting burned by invented figures. A memo whose every number is deterministic, cited and human-approved is the differentiator, and the public repo is the proof.

## What they do today

Excel spreading plus a Word memo; or an extraction vendor (Ocrolus/Codat-style) that stops at the data and leaves the memo to the analyst.

## Expected outcome

A broker or lender runs one anonymised statement through v0.1 and the memo (a) contains no figure that is not in the metrics table, (b) cites every figure to statement lines, (c) stops for their analyst to approve. Read as: zero ungrounded numbers across their statement; analyst says the policy outcomes match their own view or explains why not.

## Open questions

Raised by the review panel; see `.bridgie/runs/v01-pilot-ready/`.

## Evidence tasks

- Run the eval suite against a real model and publish grounding rate and redrafts.
- One external statement (anonymised) through the pipeline with a lender's analyst reviewing.

## Decision log

- 2026-11-07: bet created; six-seat verify panel run against v0.1 (see runs).
