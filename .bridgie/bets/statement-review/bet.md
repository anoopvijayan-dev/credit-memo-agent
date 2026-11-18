---
id: statement-review
title: "Small-business underwriters lose most of each application to reading bank statements, and would hand that part to an agent if it never invents a number"
lane: engineering
risk: money
owner: Anoop Vijayan
status: signed
confidence: anecdote
appetite: { time: 2w, tokens_usd: 20 }
kill_criteria: []
non_goals: []
created: 2026-11-15
signed_at: "2026-11-16T16:11:11.193Z"
signed_by: Anoop Vijayan
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
- D10: The real blocker (D7 against the title). The title assumes the firm would hand over statement reading 'if it never invents a number'. D7 says the actual objections are price, wanting to see it working, and applicant data leaving the firm (GDPR). If data leaving the firm is what blocks it, citations don't matter; where the tool runs does. Ask whoever would approve a tool (operations, compliance or head of credit, not the friend) what would block a pilot.
- D11: Buyer and price (D6, D1). D6 says purpose-built tools exist but cost more than the firm will pay. No product or price is named, and the claim that tools are 'being phased out' has dropped. If the firm won't pay the existing tools' price, who pays for this one, and how much? D1 also merged 'specialist lender' and 'broker'. A broker prepares files for a lender and may not sign the credit decision, which changes who carries the 'my name on the file' risk. Get the product name, the quoted price and who turned it down.
- D12: Input format (D5). D5 says statements come as an Open Banking feed through a provider, then adds 'we can't determine what users will share'. The repo reads only CSV and Monzo exports; the README lists Open Banking JSON as 'next' and has no plan for PDFs. An Open Banking feed usually arrives structured and is often already categorised by the provider, which leaves less reading for the agent to take over. PDFs are where extraction errors, the real 'invented numbers', happen. Ask the underwriter to count the formats across their last 15 applications.
- D13: Where the 1-3 hours go (D3, D4). D3 and D4 together imply 15-45 hours a week on statement reading alone, at more than 15 applications a week, which leaves little time for memos and calls; these look like guesses. More importantly, the agent marks anything it can't classify as unclassified and refers it to the underwriter (per the README, card acquirers it doesn't recognise and bank transfers without an invoice reference land in M16). If the time goes on judging unclear lines (related-party transfers, director loans, cash deposits) rather than adding up clear ones, the agent hands the hard part back. Ask the underwriter to time 3 real files and split the time between the two.
- D14: Can the outcome be read by 2026-11-29 (D8, D9)? The metric is hours per application on comparable real files, but D8 says the test will use our own statements as a demo. Our statements don't look like a small-business applicant's (volume, card settlements, VAT, loans), and the underwriter has no baseline from reading them the usual way. The D9 success bar was also set by the owner, not the underwriter. By 2026-11-29, the honest result is the underwriter's reaction to a demo. Pick the metric you will actually read.
- D15: Evidence beyond one source (D2). 'Their whole team has the same problem' is the friend's report, relayed by the owner, so it is still one source. Before building for 'small-business underwriters' in general, talk to at least two underwriters at other UK lenders who aren't friends, and ask what they use today and what it costs.
- D16: Why now (D6, volume). With 'being phased out' gone from D6, the only trigger left is rising application volume, and a cancelled weekend in November could be a quarter-end or seasonal peak. Get monthly application counts for the last 12 months from the underwriter's queue.
- D18: Offline mode and the approver (sharpens D10; raised by offer and moat). Would the firm's approver, not the friend, treat a version that runs on the firm's own machine as outside its ban on generic AI and inside its GDPR bar? Offline mode sends nothing out. With a hosted model, only 'Applicant', the dates and the metrics and policy tables leave (src/cma/graph.py:212-218).
- D19: The product the firm turned down (sharpens D11; raised by moat and offer). Moat's PASS rests partly on this. If the incumbent already links each figure to its transactions, this tool is a cheaper copy. Name the product, the quote and who turned it down.
- D20: The Open Banking provider (sharpens D12; raised by all three lenses). Which provider sends the feed, and what does its output already give the underwriter? Pull one sample. If it already categorises transactions with drill-down to the lines, little reading is left for the agent to take over.
- D21: The memo template (raised by product). Which fields does the friend's memo template ask for, and how many map to M1-M16? Get a redacted blank template. This decides whether the output saves copying or adds to it.
- D22: The friend's own success bar (extends D13 and D9; raised by offer and product). The owner set D9. After timing 3 real files, what does the friend say would make them use this on a live file? Count checking and copying time, not only reading.
- D23: The demo statement (extends D8 and D14; raised by product and offer). The planned demo uses the owner's own statements. They are personal, the importer reads Monzo Flex as a loan, and the categoriser knows only the synthetic acquirer names, so the demo can't show how much lands in M16. What will the demo run on?
- D24: Lenders 2 to 10 (raised by moat). After the friend's firm, how would the next lenders hear of this, and what would one lose by leaving after six months? Moat found nothing that makes leaving costly today.
- D25: E1 and E5 ask what the firm's approver thinks. The reply only says 'the IT/compliance team would still need to approve it' and that an on-machine version 'could be attractive'. That is the friend guessing what the approver thinks, not the approver speaking. Nothing changes on E1/E5 until someone who can approve a pilot answers. Who should be asked, and through whom?
- D26: Several figures in the reply are hedged estimates ('perhaps 9 would…', 'I'd estimate…', 'Assuming we're a direct lender'). They also match a reply from a different person about an Indian lender. To count any of them as data rather than anecdote, they need to come from the firm's own records. Which one should the friend firm up first?
- D28: Pass/refer/fail in the firm-facing tool (R25). The reply says 'I wouldn't want AI making the final lending decision', the policy thresholds are illustrative (README:127), and the board asked to drop or hide the verdict. The design hides the overall outcome in the workspace and the pack unless the firm turns on its own policy; cma run keeps it. Which do you want for the showcase?
- D29: Where it runs (Option D, R29). D14 says 'think of this as a SaaS'. The reply says a tool that runs on the firm's own machine without sending customer data out 'could be attractive', and that IT/compliance must approve it. The design is local-first: an installed package, a workspace reachable only from the same machine, and the offline lock on by default. A hosted version later would reuse the same workspace behind sign-in and need a data processing agreement. Is local-first the direction for the showcase?
- D30: First Open Banking importer (R36, R37). E7 is open: nobody knows the firm's provider, and the reply only names TrueLayer, Yapily and Plaid as common. The design builds the UK OBIE v3.1 standard shape and TrueLayer, and waits for a sample before Plaid or Yapily. Which first?
- D31: Cash deposits (R1, R4, R14). The reply asks to 'highlight ... large cash deposits'. The design keeps every cash deposit out of trading receipts until the underwriter moves it, which understates a cash business such as a café. This is a lending judgement, not an engineering one.
- D32: Timing the underwriter (R49, R50). The bet's outcome is minutes per application. The workspace can record locally when a case is opened, worked on and signed off, and report it without applicant data. It measures a person, so it needs their agreement. The design has it off by default.
- D33: What ships by 2026-11-29, 13 days away. Tier 1 (37 requirements): money-in buckets, real UK acquirers and lenders, monthly summary, indicators, single-account cases, the workspace with click-through and overrides, the pack and sign-off, the offline lock, and a synthetic case shaped like a business. Tier 2 (17): importers, multiple accounts, firm rules, verify, pseudonymiser, timing, report, comparison. The appetite is 2 weeks and $20 of tokens.
- D35: Cash limits (R14, R55, D31). D31 says to flag cash deposits 'above a threshold' but gives no figure. The thresholds file ships illustrative values that the firm can edit, and the workspace and pack call them illustrative. The design default is a single deposit of £1,000 or more, plus any month where cash is more than half of trading receipts. A café banking £1,500 a day would have every deposit flagged at £1,000, which fills the judgement queue. A higher limit such as £2,500 lets a one-off £2,000 injection through unflagged. Which default for the showcase?

## Evidence tasks

Things that need real-world evidence before signing.
- [ ] E1: The real blocker (D7 against the title). The title assumes the firm would hand over statement reading 'if it never invents a number'. D7 says the actual objections are price, wanting to see it working, and applicant data leaving the firm (GDPR). If data leaving the firm is what blocks it, citations don't matter; where the tool runs does. Ask whoever would approve a tool (operations, compliance or head of credit, not the friend) what would block a pilot. (from D10)
- [ ] E2: Buyer and price (D6, D1). D6 says purpose-built tools exist but cost more than the firm will pay. No product or price is named, and the claim that tools are 'being phased out' has dropped. If the firm won't pay the existing tools' price, who pays for this one, and how much? D1 also merged 'specialist lender' and 'broker'. A broker prepares files for a lender and may not sign the credit decision, which changes who carries the 'my name on the file' risk. Get the product name, the quoted price and who turned it down. (from D11)
- [ ] E3: Input format (D5). D5 says statements come as an Open Banking feed through a provider, then adds 'we can't determine what users will share'. The repo reads only CSV and Monzo exports; the README lists Open Banking JSON as 'next' and has no plan for PDFs. An Open Banking feed usually arrives structured and is often already categorised by the provider, which leaves less reading for the agent to take over. PDFs are where extraction errors, the real 'invented numbers', happen. Ask the underwriter to count the formats across their last 15 applications. (from D12)
- [ ] E4: Why now (D6, volume). With 'being phased out' gone from D6, the only trigger left is rising application volume, and a cancelled weekend in November could be a quarter-end or seasonal peak. Get monthly application counts for the last 12 months from the underwriter's queue. (from D16)
- [ ] E5: Offline mode and the approver (sharpens D10; raised by offer and moat). Would the firm's approver, not the friend, treat a version that runs on the firm's own machine as outside its ban on generic AI and inside its GDPR bar? Offline mode sends nothing out. With a hosted model, only 'Applicant', the dates and the metrics and policy tables leave (src/cma/graph.py:212-218). (from D18)
- [ ] E6: The product the firm turned down (sharpens D11; raised by moat and offer). Moat's PASS rests partly on this. If the incumbent already links each figure to its transactions, this tool is a cheaper copy. Name the product, the quote and who turned it down. (from D19)
- [ ] E7: The Open Banking provider (sharpens D12; raised by all three lenses). Which provider sends the feed, and what does its output already give the underwriter? Pull one sample. If it already categorises transactions with drill-down to the lines, little reading is left for the agent to take over. (from D20)
- [ ] E8: The memo template (raised by product). Which fields does the friend's memo template ask for, and how many map to M1-M16? Get a redacted blank template. This decides whether the output saves copying or adds to it. (from D21)
- [ ] E9: The friend's own success bar (extends D13 and D9; raised by offer and product). The owner set D9. After timing 3 real files, what does the friend say would make them use this on a live file? Count checking and copying time, not only reading. (from D22)
- [ ] E10: Where the 1-3 hours go (D3, D4). D3 and D4 together imply 15-45 hours a week on statement reading alone, at more than 15 applications a week, which leaves little time for memos and calls; these look like guesses. More importantly, the agent marks anything it can't classify as unclassified and refers it to the underwriter (per the README, card acquirers it doesn't recognise and bank transfers without an invoice reference land in M16). If the time goes on judging unclear lines (related-party transfers, director loans, cash deposits) rather than adding up clear ones, the agent hands the hard part back. Ask the underwriter to time 3 real files and split the time between the two. (from D13)

## Decision log

Every answer and decision, dated.
- 2026-11-16 D1 (question): UK specialist or alternative lender, or a commercial finance broker → recorded [Anoop Vijayan]
- 2026-11-16 D2 (question): Their whole team has the same problem (they've said so) → recorded [Anoop Vijayan]
- 2026-11-16 D3 (question): 1 to 3 hours → recorded [Anoop Vijayan]
- 2026-11-16 D4 (question): More than 15 → recorded [Anoop Vijayan]
- 2026-11-16 D5 (question): An Open Banking feed through a provider — We can't determine what users will share. → recorded [Anoop Vijayan]
- 2026-11-16 D6 (question): Available, but priced above what the firm will pay (give the figure if known) → recorded [Anoop Vijayan]
- 2026-11-16 D7 (question): other — It's confidence and price. They want to see a tool working, and they're worried about data being moved out (GDPR risk etc.). → recorded [Anoop Vijayan]
- 2026-11-16 D8 (question): other — We need to use our own statements and showcase it to get it run with the firm. → recorded [Anoop Vijayan]
- 2026-11-16 D9 (question): At least half the statement-reading time saved, with no wrong figures → recorded [Anoop Vijayan]
- 2026-11-16 D14 (question): other — Send documents and get demo approval: build a working model to showcase its usability, then get approval to onboard it to the firm. Think of this as a SaaS. → recorded [Anoop Vijayan]
- 2026-11-16 D15 (question): Skip it and treat this as a showcase for one firm, not a market bet → recorded [Anoop Vijayan]
- 2026-11-16 D23 (question): The owner's own statements, presented as a demo of the mechanism, not the time saving → recorded [Anoop Vijayan]
- 2026-11-16 D24 (question): None planned: this is a showcase for one firm → recorded [Anoop Vijayan]
- 2026-11-16 D10 (question): Don't know → evidence task E1 created [Anoop Vijayan]
- 2026-11-16 D11 (question): Don't know → evidence task E2 created [Anoop Vijayan]
- 2026-11-16 D12 (question): Don't know → evidence task E3 created [Anoop Vijayan]
- 2026-11-16 D16 (question): Don't know → evidence task E4 created [Anoop Vijayan]
- 2026-11-16 D18 (question): Don't know → evidence task E5 created [Anoop Vijayan]
- 2026-11-16 D19 (question): Don't know → evidence task E6 created [Anoop Vijayan]
- 2026-11-16 D20 (question): Don't know → evidence task E7 created [Anoop Vijayan]
- 2026-11-16 D21 (question): Don't know → evidence task E8 created [Anoop Vijayan]
- 2026-11-16 D22 (question): Don't know → evidence task E9 created [Anoop Vijayan]
- 2026-11-16 D13 (question): Don't know (timing not done yet) → evidence task E10 created [Anoop Vijayan]
- 2026-11-16 D27 (confidence): accept anecdote → confidence set to anecdote [Anoop Vijayan]
- 2026-11-16 D25 (question): Ask the friend to pass three written questions to their head of credit or compliance → recorded [Anoop Vijayan]
- 2026-11-16 D26 (question): E4: monthly application counts for the last 12 months → recorded [Anoop Vijayan]
- 2026-11-16 D17 (sign): sign → bet signed [Anoop Vijayan]
- 2026-11-16 D28 (question): Show it, labelled as illustrative thresholds, not the firm's policy → recorded [Anoop Vijayan]
- 2026-11-16 D29 (question): Local-first now; hosted only after the approver asks (design default) → recorded [Anoop Vijayan]
- 2026-11-16 D30 (question): OBIE v3.1 and TrueLayer (design default) → recorded [Anoop Vijayan]
- 2026-11-16 D31 (question): Counted as trading receipts, with deposits above a threshold flagged → recorded [Anoop Vijayan]
- 2026-11-16 D32 (question): Don't build it; time files with a stopwatch → recorded [Anoop Vijayan]
- 2026-11-16 D33 (question): Tier 1 only by 2026-11-29 (design default) → recorded [Anoop Vijayan]
- 2026-11-16 D34 (approve_design): request changes — Reshape with the owner's answers: count cash deposits as trading receipts, with deposits above a threshold flagged (D31); show pass/refer/fail, labelled as illustrative thresholds, not the firm's policy (D28); don't build underwriter timing, time files with a stopwatch (D32). Everything else as designed. → changes requested: Reshape with the owner's answers: count cash deposits as trading receipts, with deposits above a threshold flagged (D31); show pass/refer/fail, labelled as illustrative thresholds, not the firm's policy (D28); don't build underwriter timing, time files with a stopwatch (D32). Everything else as designed.; re-run bridgie shape statement-review [Anoop Vijayan]
- 2026-11-16 D35 (question): £2,500 per deposit and 50% of a month's trading receipts → recorded [Anoop Vijayan]
- 2026-11-16 D36 (approve_design): approve → design approved [Anoop Vijayan]
- 2026-11-16 D37 (ack_risk): ack → risk paths acknowledged [Anoop Vijayan]
- 2026-11-17 D38 (unblock): retry with guidance — Fix the 2 majors: reject duplicate YAML keys when loading a rule table; make the legacy-resume ruleset lookup fail before the review is recorded, or fall back to v1. Test the missing-table path and hash the file bytes. Remove claims the runner cannot prove (wheel install, historical replay, no-skip suite) from the claim instead of asserting them. → task T01 gets one more attempt [Anoop Vijayan]
- 2026-11-18 D39: superseded (re-planned; the task it was about was replaced) [Anoop Vijayan]
- 2026-11-18 D40 (unblock): retry with guidance — Pin v1's SHA-256 in code and refuse a v1 table that does not match it. Build the P7 fixture list by globbing every statement file under tests/ and examples/ (not from what tests happen to compute), with a test asserting the count. Mark the evidence-ruleset test so the runner runs it by name. → task T01 gets one more attempt [Anoop Vijayan]
- 2026-11-18 D41 (unblock): retry with guidance — Pin v1's SHA-256 in code and refuse a v1 table that does not match it. Build the P7 fixture list by globbing every statement file under tests/ and examples/ (not from what tests happen to compute), with a test asserting the count. Mark the evidence-ruleset test so the runner runs it by name. → task T01 gets one more attempt [Anoop Vijayan]
- 2026-11-18 D42 (unblock): take over manually — Owner delegated to Claude: add .gitattributes so the rule tables keep LF line endings on Windows, with a test, then verify. → task T01 handed to Anoop Vijayan; commit in its worktree, then bridgie verify statement-review --task T01 [Anoop Vijayan]
- 2026-11-18 D43 (unblock): take over manually — Owner delegated to Claude: mark the unknown-id and path-rejection tests as contracts so the runner names them, then verify. → task T01 handed to Anoop Vijayan; commit in its worktree, then bridgie verify statement-review --task T01 [Anoop Vijayan]

## Idea

An underwriter friend doing small-business loans cancelled a weekend road trip because he is buried in applications. Most of the time goes on reading months of bank statements line by line before the memo can be written. His firm will not let generic AI near it because a made-up number lands on the person who signs the file, and the tools built for this job are being phased out or cost too much. We want to test whether an agent that computes every figure in code, cites each one to the bank transactions behind it, and leaves the decision to a named human would be trusted to take the statement reading off them.

## Risk log

- 2026-11-16 risk data → money: requirement R1 is tagged risk: money
