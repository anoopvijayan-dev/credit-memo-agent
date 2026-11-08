Score: 5 | Vote: FUND IF
## What is strong
- **The output is a finished job, not a data dump.** The bet aims at the memo itself, with every figure cited and human-approved. It sets this against extraction vendors (Ocrolus, Codat Lending, Plaid) that "stop at data and leave the memo to the analyst" (bet, *What they do today*). The research confirms the category stops there: Ocrolus, Codat and Equifax Share by Bank all supply data, not memos.
- **The non-goals are real decisions.** No banks, no replacing the lender's credit decision, no SaaS UI before a paid pilot (bet `non_goals`). The second one keeps a human sign-off, which is also the trust story. The third keeps the first version small enough to finish.
- **The first version can be shipped in weeks and still be whole.** The appetite is 2 weeks and $20 of tokens. The finished demo is one anonymised statement turned into one cited memo (bet, *Expected outcome*: "at least 2 run an anonymised statement through it"). That makes sense before any platform exists.
- **The kill criteria are concrete.** The first one has a number and a date (fewer than 3 of 20 by 2026-12-19). The second can be tested ("no one names a price or a budget owner").
- **Research found a gap in what the person sees.** Apart from Crediflow's reported source traceability, no competitor page found says every figure is cited and human-approved (research, *Gap I see*).

## What would kill it
1. **Two buyers, two different products.** *Who* names both a non-bank lender's credit analyst and a NACFB broker. A broker's job is to get a deal accepted, and the bet itself calls a broker pack "a sales document" (D2). A lender's job is a decision it can defend. The bet also says brokers "often forward raw statements to lenders", so many brokers may never write a memo, and lenders may redo whatever a broker sends. If so, a broker-side memo solves half a problem: the lender re-spreads it anyway. The research found no source either way for brokers forwarding raw statements or lenders redoing broker spreading (research, *Not found*). **Test:** ask 3 brokers what they send, and ask 3 lenders whether they redo it (D2).
2. **The remaining gap may be the part the user won't hand over.** If a 10–50 person lender already gets categorised transactions from Codat, Equifax Share by Bank or Validis (research, *Indirect and substitutes*), spreading is already solved. What's left is the memo's judgement, which D3 calls "the part they least want to hand over". The product then shrinks to a citation layer over someone else's data. **Test:** in each interview ask the stack question, plus "which section of your last memo would you let a tool draft, and which would you never?"
3. **"Cited" may be a feature nobody feels, or one that Crediflow already offers.** The problem statement is marked as an assumption (bet, *Problem in their words*), and the research found no UK analyst or broker quote for it. It's unclear who checks where a figure came from (D4), and limited-company lending sits outside the FCA (D11). Meanwhile Crediflow, launched October 2026, fills the lender's own templates and reportedly offers source traceability (research, competitor table; D9). The edge is unique only if traceability is a felt pain and Crediflow doesn't already meet it. **Test:** run the "last memo you wrote" interview before any demo (D1), and record the first pain named without prompting. If no one mentions provenance or audit, the edge is gone.
- *Also:* no weekly user has been named. The owner can't use the product themselves: the build plan notes an employer in merchant-funding risk and an IP clause (D7). The bet names a role but no person to stand in for the user.

## Questions for the owner
- Which one buyer is the first version for: the lender's analyst or the broker? Whose memo format does it produce?
- Who is the named proxy user? Give one real person, whose last memo you'll rebuild end to end.
- What does the person do, see and wait for? Is the input a PDF statement or an open-banking feed? Does the output arrive in your template or theirs? What does "human-approved" look like on screen: a sign-off per figure, or per memo?
- What does the first version refuse to do? It might skip the credit narrative or recommendation, or stick to one lending product (MCA, term loan, asset finance; D11), or leave out accounts and tax returns (which Crediflow handles).
- When a figure is cited, who clicks the citation, and when? Is it the analyst, the credit committee, or a funder's auditor (D4)?

## Conditions
1. Pick one buyer for the first version and write it into *Who*. Move the other into `non_goals` until a pilot is paid for.
2. Name one proxy user, a real analyst or head of credit, before building the demo. Build the demo from their last memo, anonymised.
3. Run at least 3 of the 5 "last memo" interviews before showing any demo (D1). If provenance or audit isn't among the pains named without prompting, stop and rewrite the problem before going further.
4. Write a one-page flow from the person's side: what goes in, what they wait for, what they check, how they approve, and what format comes out. Name in one line how this differs from Crediflow's "fills your template, with traceability" (D9).
5. Add the missing non-goals: the lending segment (D11), the input type, and whether the memo makes a credit recommendation.
6. Don't run any statement until the data-protection basis is written down (D7). It's a prerequisite for the demo, and the details belong to another lens.
7. No price or hours-saved figure exists in the inputs. Get the price band and budget owner from the interviews (evidence step), and hours per memo from the same interviews (numbers step), not from vendor marketing.
