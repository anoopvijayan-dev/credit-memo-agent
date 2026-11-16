---
id: statement-review
title: "Small-business underwriters lose most of each application to reading bank statements, and would hand that part to an agent if it never invents a number"
lane: engineering
risk: data
owner: Anoop Vijayan
status: draft
confidence: opinion
appetite: { time: 2w, tokens_usd: 20 }
kill_criteria: []
non_goals: []
created: 2026-11-15
---

# Small-business underwriters lose most of each application to reading bank statements, and would hand that part to an agent if it never invents a number

## Who

An underwriter at a lender that makes small-business loans. Right now that is one friend of the owner. They work through a queue of applications, and for each one they must read several months of the applicant's business bank statements before they can write the credit memo. Their name goes on the file they sign. (Assumption: lender type, firm size, country and number of underwriters are unknown. The repo's Monzo import suggests the UK, but nobody has confirmed it.)

## Problem in their words

No direct quotes yet. Everything below is the owner paraphrasing one conversation, and none of it is backed by a ticket, an interview note or data.

- "I'm buried in applications." They cancelled a weekend road trip to catch up. (paraphrase, second-hand)
- Most of the time on each application goes on reading months of bank statements line by line before the memo can be written. (paraphrase; the share of time has not been measured)
- "If the AI makes up a number, it's my name on the file." This is given as the reason the firm won't allow generic AI. (assumption: our wording of the firm's stated reason)
- The tools built for this job are being phased out or cost too much. (paraphrase; which tools and what they cost is unknown)

## Why now

The owner reports two triggers. Neither has been checked:

1. The tools the firm could use for statement analysis are being withdrawn or priced out of reach, so the cheaper option is disappearing. (assumption: tools and dates not named)
2. Application volume has grown to the point where the underwriter gives up weekends. (assumption: we don't know since when, or whether it is seasonal)

Better language models are not a trigger from the customer's side. If neither claim survives a check, the honest answer is "nothing changed for them; the backlog is chronic".

## What they do today

They read every statement by hand, line by line. Probably that means PDFs downloaded from the applicant's bank, with figures (income, outgoings, average balance, bounced payments, existing loan repayments) totted up in a spreadsheet or by eye, then typed into the memo. (assumption: the exact format and tooling are unknown)

- **Generic AI:** firm policy forbids it, because nothing traces a figure back to a transaction.
- **Dedicated statement-analysis or Open Banking categorisation products:** they have used or looked at these, but say they are being phased out or cost too much. (assumption: products not named)
- **Alternative:** staying slow. The cost of the backlog falls on the underwriter's own time, not on the firm's budget.

## Expected outcome

**Metric:** hours this underwriter spends reading statements per application.

- **Baseline:** unknown. All we have is "most of each application". We need a timed baseline on 3 to 5 real files, read the usual way.
- **Target:** at least half the time cut on the same number of comparable files where the agent does the statement reading.
- **Trust condition:** every figure in the draft must link to the transactions behind it, with zero figures that can't be traced. The underwriter must say they would sign a file that uses the agent's figures, after checking them.
- **When it can be read:** by 2026-11-29, the end of the 2-week appetite. That date only holds if we can get anonymised or real statements in time; otherwise we can't read the result at all.

## Open questions

Questions raised by the grill. Answer them on the decision surface (`bridgie open`).
- D1: Who: what kind of lender does the underwriter work for, and where? This decides statement formats, regulation and whether "small business" means sole traders or limited companies.
- D2: Who: is this one person's backlog, or a pattern across underwriters? Right now the frame rests on one friend.
- D3: Expected outcome needs a baseline: how long does reading statements take on one typical application today?
- D4: Volume: how many applications does the underwriter handle per week? This tells us whether the saving is worth the risk to them.
- D5: What they do today: what format do the statements arrive in? The agent can only cite transactions it can read reliably.
- D6: Why now: which purpose-built tools are being phased out or are too expensive, and what does that cost? This claim is our only external trigger.
- D7: Problem in their words: is "a made-up number lands on whoever signs" the firm's actual reason for banning AI, or our reading of it? And would a version with citations to transactions change the firm's answer?
- D8: Expected outcome is only readable with real files. Can the underwriter test the agent on real or properly anonymised statements before 2026-11-29?
- D9: Expected outcome: what would the underwriter count as success? Time saved alone, or something about trust as well?

## Evidence tasks

Things that need real-world evidence before signing.

## Decision log

Every answer and decision, dated.
- 2026-11-16 D1 (question): UK specialist or alternative lender, or a commercial finance broker → recorded [Anoop Vijayan]
- 2026-11-16 D2 (question): Their whole team has the same problem (they've said so) → recorded [Anoop Vijayan]
- 2026-11-16 D3 (question): 1 to 3 hours → recorded [Anoop Vijayan]
- 2026-11-16 D4 (question): More than 15 → recorded [Anoop Vijayan]
- 2026-11-16 D5 (question): An Open Banking feed through a provider — We can't determine what users will share. → recorded [Anoop Vijayan]
- 2026-11-16 D6 (question): Available, but priced above what the firm will pay (give the figure if known) → recorded [Anoop Vijayan]
- 2026-11-16 D7 (question): other — It's confidence and price. They want to see a tool working, and they're worried about data being moved out (GDPR risk etc.). → recorded [Anoop Vijayan]

## Idea

An underwriter friend doing small-business loans cancelled a weekend road trip because he is buried in applications. Most of the time goes on reading months of bank statements line by line before the memo can be written. His firm will not let generic AI near it because a made-up number lands on the person who signs the file, and the tools built for this job are being phased out or cost too much. We want to test whether an agent that computes every figure in code, cites each one to the bank transactions behind it, and leaves the decision to a named human would be trusted to take the statement reading off them.
