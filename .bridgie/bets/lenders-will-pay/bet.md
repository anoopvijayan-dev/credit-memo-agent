---
id: lenders-will-pay
title: UK SME lenders and brokers will pay for a credit memo whose every figure is cited and human-approved
lane: product
risk: none
owner: Anoop Vijayan
status: evidence
confidence: opinion
appetite: { time: 2w, tokens_usd: 20 }
kill_criteria:
  - fewer than 3 of 20 contacted brokers/lenders agree to a 30-minute look by 2026-12-19
  - no one names a price or a budget owner after seeing the demo
non_goals:
  - selling to banks
  - "replacing the lender's credit decision"
  - building a SaaS UI before a paid pilot
created: 2026-11-07
---

# UK SME lenders and brokers will pay for a credit memo whose every figure is cited and human-approved

## Who

A credit analyst or head of credit at a UK non-bank SME lender, or a commercial-finance broker (NACFB member) who packages deals for lenders, spreading 12 months of business current-account statements before writing a credit memo.

## Problem in their words

ASSUMPTION, not yet quoted from anyone: "Spreading a statement takes an afternoon, the memo still gets a number wrong, and when compliance asks where a figure came from nobody can say." Earlier BridgeTrade outreach (2026) to UK commercial-finance brokers and NACFB award winners suggested roughly 1,400 broker firms still do bank-statement analysis by hand; large MCA providers (Liberis, 365 Business Finance, Nucleus) were judged likely to build rather than buy.

## Why now

LLM tools are being trialled in lending and producing plausible but invented figures; an auditable memo (deterministic metrics, citations, human sign-off) is a differentiator. Open banking data is cheaper to access than two years ago. Plaid launched LendScore 2 on 6 Oct 2026, signalling vendor attention on cash-flow underwriting.

## What they do today

Excel spreading plus a Word memo; or an extraction/categorisation vendor (Ocrolus, Codat Lending, Plaid) that stops at data and leaves the memo to the analyst. Brokers often forward raw statements to lenders.

## Expected outcome

By 2026-12-19: 20 named brokers/lenders contacted; at least 5 take a 30-minute look; at least 2 run an anonymised statement through it; at least 1 names a price band and a budget owner for an assessment (fixed fee) or pilot. Read from a simple outreach log.

## Open questions

Raised by the grill.
- D1: Problem in their words: no buyer has said it yet. Run the 'last memo you wrote' interview with 5 analysts or brokers (time spent, errors caught, what compliance or the credit committee asked) before showing any demo, so the answers aren't led. Which pain do they actually name first, unprompted?
- D2: Which buyer? The bet says brokers often forward raw statements to lenders, so many brokers may not spread at all, and lenders may re-spread whatever a broker sends because a broker pack is a sales document. Separately: what is the ~1,400 'still by hand' firms figure? Is it a count of firms observed spreading by hand, or roughly the NACFB membership size? Settle this by pulling the BridgeTrade notes and asking 3 brokers what they send and whether the lender redoes it.
- D3: What they do today, and why it's tolerable: a 10–50 person non-bank lender may already get categorised transactions from an open-banking or accounts vendor (e.g. Codat, Experian, Validis, Ocrolus) or from its loan-origination system, leaving only the memo, which is the analyst's judgement and the part they least want to hand over. Find out with a stack question in each interview.
- D4: Who actually demands traceability? 'Compliance asks where a figure came from' assumes a regulator, funder or auditor who checks. Much limited-company SME lending is outside FCA regulation, so the pressure may come from warehouse funders, British Business Bank guarantee-scheme audits, or credit committees. That is a different buyer and a different pitch. Ask each head of credit for their last audit or funder finding on credit files.
- D5: Why now, and does it survive a reversal? The differentiator answers LLM-invented figures, but that only matters to lenders who are using LLMs on memos. If they aren't, the problem isn't theirs yet. If incumbents ship cited memos (Plaid has just launched LendScore 2), the edge goes. Answer with a source search plus one interview question: 'Have you tried an AI tool on memos, and what went wrong?'
- D6: Is the outcome metric readable and calibrated? 'Agree to a 30-minute look' measures curiosity. A price band named after a free demo costs the person nothing. The 3-of-20 threshold has no baseline: pull the reply rate from the BridgeTrade 2026 outreach log. The appetite is 2 weeks but the window runs 6 weeks to 19 Dec, and kill criterion 2 has no date. What should count as the signal?
- D7: Can the owner run this test now? The build plan records an employment IP clause and an employer in merchant-funding risk. Selling to UK SME lenders and MCA providers while employed may hit conflict-of-interest, non-solicit or IP terms. Running real statements, even anonymised ones from sole traders, needs a data-protection basis. Read the actual contract clauses and draft the data-protection terms; memory doesn't count here.
- D9: Crediflow (Manchester) already sells a UK credit-memo generator that fills lenders' own templates with source traceability (launched Oct 2026, per Business Cloud and StartupMag). Your 'cited and human-approved' edge may not be unique. Which angle should the next seat test against it?
- D10: The bet's 'Who' lists two buyers: non-bank lender credit analysts and NACFB brokers. They have different pains, budgets and competitors, and the ~1,400 figure counts NACFB members, not broker firms found spreading by hand. Which one should the research and interviews target first?
- D11: Which segment of lending should pricing and competitor research cover? Limited-company lending sits outside FCA regulation (FCA response to the FSB super-complaint), so the pull for traceability may come from funders or guarantee schemes, not the regulator.

## Evidence tasks

- Interview 5 analysts/brokers about the last memo they wrote: time spent, errors caught, what compliance asked.
- Find out who owns the budget for analyst tooling at a 10–50 person lender.

## Decision log

- 2026-11-07: bet created alongside engineering bet v01-pilot-ready.
