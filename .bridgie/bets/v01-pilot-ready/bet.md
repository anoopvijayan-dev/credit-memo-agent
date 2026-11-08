---
id: v01-pilot-ready
title: "credit-memo-agent v0.1 is fit to run a lender's anonymised statement in a pilot"
lane: engineering
risk: money
owner: Anoop Vijayan
status: evidence
confidence: opinion
appetite: { time: 2w, tokens_usd: 20 }
kill_criteria:
  - a blocker finding in the money path that cannot be fixed in a week
  - no lender or broker agrees to run a statement through it by week 6
non_goals: [full underwriting decision, real customer data, CAMT/MT940 adapters in v0.1]
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
- D1: Where did the problem quote come from? This bet calls it a paraphrase of BridgeTrade broker conversations; lenders-will-pay/bet.md:23 says it is an assumption not yet quoted from anyone. To settle it, produce the dated notes or run one new interview. Recalling it from memory does not count as evidence.
- D2: What file will a pilot lender actually send? v0.1 reads only the synthetic five-column CSV (id, date, description, amount, balance), and CAMT/MT940 adapters are a non-goal. To find out, ask three contacts to describe or send a redacted sample of what their analysts receive today.
- D3: Who anonymises the statement, under what data-protection basis, and does the anonymised data still work? A sole trader's statement is personal data even with the name removed. Redacting payer names breaks M15 and the FPS CREDIT categoriser rules. Test this by running one redacted statement the way the lender would redact it and see which metrics survive.
- D4: Outcome and baseline: the success measure is 'zero ungrounded numbers', but a number can be grounded and still wrong. Is there an existing analyst spread of the same statement to compare metrics against, and what tolerance counts as a match? Getting this needs a data request to the pilot lender.
- D5: Who, by name, would run the statement? Is there anyone who has said yes in principle, and when does the 'week 6' kill clock start? The playbook has outreach in week 3, and C1–C6 still need fixing first. Check this against the outreach log, not memory.
- D6: Why now: is there evidence that UK lenders are actually trialling LLM memo tools and getting burned by invented figures? And would the differentiator survive if Ocrolus, Codat or Plaid ship cited memos (Plaid launched LendScore 2 on 6 Oct 2026)? Answering needs a source search or three interview questions.
- D7: Scope of this bet: kill criterion 2 ('no lender or broker agrees to run a statement by week 6') tests demand and overlaps lenders-will-pay. As written, an engineering bet can be killed by a sales outcome, and its own engineering fitness has no pass date.
- D9: Geography: this report assumes UK non-bank SME lenders and commercial-finance brokers, per the bet. Plaid and Ocrolus lean US. Confirm the scope.
- D10: Segment: the Who is a role at two kinds of firm. Crediflow, Heron and Arya.ai sell to lenders. Heron has a separate broker suite. Which one do you pilot first?
- D11: Page fetches were blocked in this run, so all facts come from search-result summaries. Should I rerun with fetch enabled to read vendor pricing and review pages in full and check dates?

## Evidence tasks

- Run the eval suite against a real model and publish grounding rate and redrafts.
- One external statement (anonymised) through the pipeline with a lender's analyst reviewing.

## Decision log

- 2026-11-07: bet created; six-seat verify panel run against v0.1 (see runs).
