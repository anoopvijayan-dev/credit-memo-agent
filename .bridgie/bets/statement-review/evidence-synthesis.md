# Evidence synthesis — statement-review

2026-11-16. One source: the friend's written reply, which the owner flags as possibly templated or AI-assisted. It partly answers E3, E6, E8, E9 and E10, but every figure in it is an estimate and none of E1–E10 is closed.
It moves the value claim from *reading* statements to *interpreting* them. By the friend's own estimate, most of the time goes on deciding which credits are genuine trading income, and the agent currently sends that work to M16.
It contradicts D3 (1–3 h; the reply says 45–60 min for a straightforward file) and D5 (Open Banking; the reply says roughly 9 of 15 files are PDF, a format the repo can't read). Proposed tier: anecdote.

## What the material shows

Everything below comes from one written reply. Quotes are verbatim, and every figure is the friend's estimate.

**Where the time goes (E10, partly answered; D3 contradicted)**
- "For a straightforward application with 6–12 months of statements, I'd estimate around 45–60 minutes… multiple accounts, it can easily take 90 minutes or more." This conflicts with the owner's D3 answer of 1–3 hours per application.
- The friend's split: "10–20 minutes calculating monthly credits, debits, average balances and existing repayments"; "25–45 minutes understanding unusual transactions, internal transfers, director loans, cash deposits and other unclear payments"; "10–20 minutes reconciling everything and preparing the credit summary."
- "The basic calculations aren't the hardest part. It's identifying what is genuine trading income and what isn't." They repeat the point in the closing note: "The biggest challenge isn't extracting transactions… It's interpreting the transactions correctly."
- By their own estimate, the largest block is exactly the work D13 feared the agent hands back through M16 (unclassified inflows). The bet title says the work is *reading*; the friend says it is *judging*.

**Statement formats (E3, partly answered; D5 contradicted)**
- "If I take 15 applications as an example, perhaps 9 would involve PDF statements, 4 would come through Open Banking and 2 would be a combination." This is an illustration ("perhaps"), not a count from their queue, and it is the figure that matches the other reply word for word.
- If it holds, PDFs are the majority format. The repo reads only CSV and Monzo exports and has no PDF plan (per the README, as cited in D12).

**Open Banking provider (E7, not answered)**
- "Providers such as TrueLayer, Yapily or Plaid are commonly encountered in the UK market… I'd need to confirm the exact provider." These are named as examples from the market, not as the firm's provider.
- "we might also get categorisation and cash-flow summaries. Even with categorised data, I wouldn't blindly trust everything." Their examples: Stripe/SumUp settlements, and transfers between the business's own accounts double-counting turnover.

**Blockers to a pilot (E1/E5, the friend's view only)**
- "Data protection and accuracy would probably be the biggest concerns." "The firm would need confidence that data is secure, access is controlled and there's an audit trail."
- "A tool that operates entirely on our own machine or within our own environment, without transmitting customer data externally, could be attractive. But the IT/compliance team would still need to approve it."
- Proposed route to a trial: "start with synthetic or properly anonymised sample statements and compare the results with our existing process."

**Incumbents and price (E2/E6, partly answered)**
- Named: "LendingMetrics, AccountScore and other specialist vendors", described as "established providers offering bank statement analysis". The reply doesn't say whether either was the product the firm evaluated.
- "I couldn't give you a reliable quote without checking what was evaluated internally." "For a small lender, the concern is often the overall commercial commitment, rather than just the cost of analysing one statement."
- "We wouldn't necessarily need another large underwriting platform if all we're trying to solve is bank-statement review."

**Lender or broker, and who signs (E2, hedged)**
- "Assuming we're a direct lender, the underwriting team assesses applications and makes credit recommendations." "Smaller loans might be approved within delegated authority, while larger or higher-risk cases go to a senior credit manager or credit committee." Because this opens with "assuming", it doesn't settle the lender-or-broker question in D1/D11.

**Memo fields (E8, a generic list, not the firm's template)**
The friend lists 13 fields of "a typical Credit Appraisal Memo". Here is how they match the repo's metrics (names from `src/cma/metrics.py:249-289`):
- Monthly turnover and bank credits → M2, M3, M4 (partial: their "genuine trading income" excludes director loans and own-account transfers)
- Average and minimum balances → M5 for the minimum only; no metric for the average
- Existing loans, overdrafts, commitments → M8, M6
- Returned direct debits → M7
- VAT/HMRC and arrears → M12, M13, M14
- Cash-flow affordability and debt service → M9, M10
- Director loans and intercompany transfers → M11 covers owner drawings out only; no metric for inbound director loans or intercompany credits
- Trading history → M1 (months covered) only
- Business details and Companies House number, loan amount/purpose/term, credit history and CCJs, key risks, recommendation → no metric (the recommendation is left to the human by design)
- Roughly 6–8 of the 13 touch M1–M16, and the gaps sit on the friend's own priority: separating genuine income from the rest.

**What would make them use it (E9, stated before any timing)**
- "For me, accuracy is more important than just saving time." "I'd want to click any number in the report and see exactly which transactions make up that figure."
- Wanted: exclude internal transfers; flag director loans, unusual credits and large cash deposits; recognise loan repayments and merchant cash advances; flag returned DDs and persistent overdraft use; "Allow me to manually correct categories"; "Produce a summary I can use directly in my credit memo."
- "If it reduced a 60-minute review to 25–30 minutes while maintaining accuracy, that would be a strong selling point." This is close to the owner's D9 bar (a 50–58% cut).
- "I wouldn't want AI making the final lending decision. I'd want it assisting the underwriter." This matches the bet's human-signs design.
- "If a tool could reliably tell me that £80k entered the business account but only £55k represented genuine trading receipts, explain the remaining £25k, and let me verify everything, that would be genuinely useful."

**Volume and why now (E4, not answered)**
- "I'd say we've experienced some busier periods, but I wouldn't put a percentage on it without checking our application volumes." "The issue isn't just volume. A complicated case can take much longer than three straightforward applications." This weakens volume as a trigger. The friend attributes the backlog to case complexity, not growth.

## What it does not show

- **No measured number.** Every time, count and share is an estimate. Nothing was timed, counted from the queue or taken from firm records.
- **No approver.** E1 and E5 ask what the person who would approve a pilot thinks. This reply is the friend predicting that person's view.
- **No product the firm turned down, and no price.** It names two vendors as market examples, not as the one evaluated, and gives no quote (E2, E6). It also doesn't say whether those vendors already link each figure to its transactions, which is what moat's PASS depends on.
- **No provider and no sample feed** (E7).
- **No actual template.** The 13 fields describe "a typical" memo, not a redacted copy of the firm's (E8).
- **No 12-month volume series** (E4).
- **Firm type unconfirmed.** "Assuming we're a direct lender" leaves D1/D11 open.
- **D2 ('whole team has the same problem') is not addressed.** The reply speaks only for this one underwriter.
- **Independence is uncertain.** The owner records that this reply matches, section for section and number for number, a reply from a different person about an Indian lender. One small additional signal points the same way: "Credit Appraisal Memo" (CAM) is the usual term in Indian lending, while UK-specific details such as Companies House and HMRC are also present. Neither proves the reply was templated. It means the figures can't be treated as this firm's own until they are re-confirmed from records.
- **Nothing about the agent itself.** The friend hasn't seen the tool. Their statements describe what they'd want, not whether this build delivers it.

## Sources

| Source | Count | Independent of owner? | Used for |
|---|---|---|---|
| Written reply from the underwriter friend (pasted verbatim by owner, 2026-11-16) | 1 | Yes, though possibly templated (see caveat) | All quotes above |
| Approver (IT, compliance or head of credit) | 0 | – | – |
| Other underwriters | 0 | – | – (D15 skipped by owner's choice) |
| Firm records, timings, counts | 0 | – | – |
| Near-duplicate reply about an Indian lender (WhatsApp group) | not counted | Different market; its match with this reply is the reason both are discounted | Caveat only |

**Total: 1 anecdotal source, 0 measured.**

Evidence task status after this material:
- E1, E4, E5, E7: still open, nothing usable.
- E2, E6: partly answered (vendor names and the commercial-commitment concern), but no product evaluated and no quote.
- E3: partly answered (an estimated majority of PDFs), needs a real count.
- E8: partly answered (a generic field list mapped above), needs the firm's redacted template.
- E9: partly answered (bar stated: 60 → 25–30 min, every figure clickable, categories editable), needs restating after real timing.
- E10: partly answered (an estimated split with judging as the largest block), needs 3 timed files.
