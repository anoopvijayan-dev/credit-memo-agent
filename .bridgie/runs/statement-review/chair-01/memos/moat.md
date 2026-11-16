Score: 3 | Vote: PASS
## What is strong
- **A clear design that fits the signer's fear.** Money is added up only in code. The model gets a table and never sees a transaction. Every figure carries a tag back to its statement lines, and nothing is final until a named person approves it (ADR 001, Decisions 1–5; README "What's in the box"). If the real blocker is applicant data leaving the firm (D7), two things fit better than a hosted product: "the model receives a table, not a statement" (ADR 001 point 2), and the tool can already run with the offline model and no outside call (README Quick start; `src/cma/llm.py`). That is the most likely source of an edge.
- **A starting segment that might be small enough to own.** That is UK specialist or alternative lenders (D1) for whom the purpose-built tools are "priced above what the firm will pay" (D6). Incumbents pricing themselves out of a segment is the right shape for a first market. Its size is unknown, and the research step has to get it.
- **A cheap test:** 2 weeks and $20 (appetite).

## What would kill it
1. **The provider already in the flow ships the one feature.** D5 says statements arrive as "an Open Banking feed through a provider", and D12 notes that such feeds often arrive already categorised. That provider owns the data pipe and the lender relationship. For them, linking each total to its transactions is a feature, not a company. *Test:* name the provider, pull one sample output, and check whether it already gives categorised totals you can drill into.
2. **The advantage the plan names is not what blocks the buyer.** The title says the firm would hand over statement reading "if it never invents a number". The only buyer evidence (D7) says the objections are confidence, price and GDPR, and citations answer none of those directly. If the approver says any outside processing needs a data processing agreement and vendor onboarding (D10), every new lender is a slow compliance sale, and that sales cost can absorb the price gap that is the only opening (D6). *Test:* put the D10 question to whoever approves tools, not to the friend.
3. **It is at best 2x better, and probably less on real files.** The target is "at least half the time cut" (Expected outcome), which is 2x, not 10x, against a baseline the owner guessed (D3: 1 to 3 hours). Card revenue from acquirers it doesn't recognise and bank transfers without an invoice reference count as unclassified (M16, README "Known limits"). Rule R9 refers any case with more than 20% of money in unexplained (`src/cma/policies/uk_sme_default.yaml`). If most real files get referred, the underwriter reads the statement anyway (D13). *Test:* time 3 real files, split between adding up clear lines and judging unclear ones (D13). Run the categoriser on one anonymised real statement and record M16.
4. **Nothing stops a copy.** The method is written down in ADR 001 and the code is MIT-licensed (README "Licence"). The things that could become a moat don't exist yet: rules tuned on real UK statements, each lender's own policy encoded in the YAML, and an archive of signed files in the evidence format. The README says "Everything here is synthetic" and "Policy thresholds are illustrative".
5. **A market of one.** D2 ("their whole team") is the friend's report relayed by the owner, and D15 is still open. D1 merges lender and broker. A broker may not sign the credit decision (D11), so the "my name on the file" fear may not belong to the buyer.

## Questions for the owner
- Which Open Banking provider sends the feed (D5), and what does its output already do?
- Which statement-analysis product did the firm price, at what figure, and does it trace each figure to transactions (D11)?
- Lender or broker: whose name goes on the credit decision?
- After the friend's firm, how would lenders 2 to 10 hear of this, and through whom? Does that route get cheaper or more expensive per lender as it grows?
- If a lender stopped using it a year in, what would it lose? From the bet and the repo today, nothing.
- What do you believe about this market that the incumbents don't? ADR 001's "most open examples ... hand the model the raw statement" is a view of open-source demos, not of the paid products the firm turned down.
- Numbers this lens needs that nobody has yet:
  - how many UK specialist SME lenders and brokers there are (research);
  - the incumbent's price (research or evidence, D11);
  - the share of reading time spent on unclear lines (evidence, D13);
  - M16 on one real statement (evidence).

## Conditions
What would move this to FUND IF. Each can be done or found out before signing:
1. Name the incumbent product or products, the quoted price, and whether they already link each figure to its transactions. If they do, this is a cheaper copy and PASS stands.
2. Record the approver's answer to D10. If it is data protection, confirm that a pilot would be allowed for a version that runs inside the firm, where the model sees only the metrics table or no model is called.
3. Pull one sample from the Open Banking provider (D12) and write down what reading would be left for the agent after it.
4. Re-scope the bet to what the evidence supports. Either make it a showcase for one firm (D15 option 3), with "not a market claim" as a non-goal, or confirm the problem with at least two underwriters outside the friend's firm (D15).
5. Name one distribution channel for the next ten lenders. Also name one thing that would make leaving costly for a lender after six months, such as its own policy and categorisation rules living in the tool.
