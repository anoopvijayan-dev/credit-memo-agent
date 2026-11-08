# Research — v01-pilot-ready

Researched 2026-11-08. - Crowded: at least five vendors already sell parts of this to UK lenders. Crediflow AI already drafts cited credit memos into the lender's own template (UK, seed-funded), so "cited memo" is not unique.
- Price band: none public. Ocrolus, Crediflow, Heron and Arya.ai all quote on request. The only public figure is a competitor's claim of DocuClipper from $20/month.
- Loudest complaint: Ocrolus is slow and pricey. No lender complains about invented figures in anything I found; only law firms raise it.
- Gap: I found no vendor that pairs deterministic metrics with a human-approval stop and a public repo. That is positioning, not proof anyone pays for it, and Crediflow's source-traceability claim is close.
- Freshness: vendor pages and Plaid are Oct 2026; review sites are mostly 2025 or undated. Fetch was blocked, so nothing was read in full and no page dates were checked.

## Direct competitors

Source note: the fetch tool was blocked in this run, so every row below comes from search-result summaries of the linked pages. I did not open the pages. Treat each as "a search result says", and check the page date before you rely on it.

| name | what | price | link |
|---|---|---|---|
| Crediflow AI (Manchester) | Financial spreading, bank statement analysis, and a credit-memo generator that fills the lender's own template with tables, charts and source traceability. Customers named in results: AddWorking Capital, Iveco Capital. | No public pricing (GetApp UK lists "No pricing info"). Seen 2026-11-08. | https://www.startupmag.co.uk/funding/crediflow-ai-2026-seed-funding/ ; https://www.getapp.co.uk/software/2096761/crediflow-ai ; https://dealroom.co/news/169312-crediflow-ai-raises-1-5m-to-turn-loan-underwriting-into-minutes/ |
| Arya.ai Cred AI | Statement analyser and credit-memo automation. A UK lender, MaxCap, is the named case study. The "10X" figure is the vendor's own claim. | No public pricing found. | https://arya.ai/success-stories/uk-lender-maxcap-increases-underwriting-productivity-with-cred-ai ; https://arya.ai/blog/ai-credit-memo-automation |
| Heron Data | Parses statements, categorises each line against a customer scorecard, and cross-checks revenue against financial statements. Has a broker suite. Outfund is a named UK customer. Heron says it serves 65+ lenders and fintechs. | No public pricing found. | https://herondata.io/parse-financial-statements ; https://herondata.io/case-studies/increasing-underwriting-speed-for-outfund |
| Ocrolus | Statement extraction, analytics and fraud detection. It stops at the data and leaves the memo to the analyst. One competitor says it supports North American documents only. | Custom, no list price. Third-party sources disagree on the billing unit (per statement, per page, per report). | https://www.capterra.co.uk/software/1015028/ocrolus ; https://www.extend.ai/resources/ocrolus-review-pricing-features-alternatives |
| DocuClipper | Statement extraction. 365 Business Finance, a UK SME lender, is a named user. Extraction only, no memo. | Competitor-reported: "page-based plans from $20/mo". This is DocuClipper's own claim, seen via a third-party comparison page. | https://www.docuclipper.com/customer-stories/365-business-finance-docuclipper/ ; https://www.docuclipper.com/blog/ocrolus-alternatives/ |

## Indirect and substitutes

- Excel spreading plus a Word memo. This is the bet's own stated status quo. I found no external source for how common it is.
- Open-banking feeds instead of PDFs or CSVs: Equifax Share by Bank (https://www.equifax.co.uk/business/product/share-by-bank/), Experian Commercial Acumen (https://www.experian.co.uk/blogs/latest-thinking/automated-credit-decisions/making-the-right-decisions-on-sme-credit-fast/), Yapily Data Plus business transaction categorisation (https://yapily.com/blog/introducing-data-plus-advanced-business-transaction-categorisation-elevate-sme-lending-decisions). If the lender takes a live feed, nobody sends a statement file.
- Plaid LendScore 2 (blog dated 6 Oct 2026): a cash-flow credit score delivered through Plaid's CRA under the FCRA. This is US consumer-oriented, so it is a substitute for the credit decision and not a memo tool: https://plaid.com/blog/instant-link-next-generation-lendscore/
- Doing nothing, or a general LLM with a prompt. I found no source on how many lenders do this.

## Prices

What the buyer pays today. No competitor publishes a UK price.

- Ocrolus: custom quote only. Review sites show no price. https://www.capterra.co.uk/software/1015028/ocrolus
- Crediflow AI: "No pricing info". https://www.getapp.co.uk/software/2096761/crediflow-ai
- Heron Data: no price in any result.
- DocuClipper: from $20/month, claimed by a competitor-comparison blog, so it is unverified: https://www.docuclipper.com/blog/ocrolus-alternatives/
- The analyst's afternoon is the real incumbent price. I found no sourced salary or hours figure.

The price band is not observable. Enterprise vendors quote by sales call. The only public number is a US$20/month entry tool for small brokers.

## What their customers complain about

- Ocrolus is slow on complicated statements. A reviewer says some banks "could take 30-1 hour to extract". https://www.capterra.com/p/232462/Ocrolus/reviews/
- Ocrolus is pricey. One review is headlined "Great Product but a bit Pricey". https://www.getapp.com.au/software/2049438/ocrolus
- Ocrolus is poor at communicating releases. A GetApp reviewer says they were not told when new analytics were released. https://www.getapp.com.au/software/2049438/ocrolus
- Ocrolus has missing information and cost concerns. This comes from a competitor's blog, so treat it as marketing. https://www.extend.ai/resources/ocrolus-review-pricing-features-alternatives
- The risk of invented figures appears in law-firm commentary, not lender voices: hallucinations "can misinform credit or affordability assessments". https://www.freeths.co.uk/insights-events/legal-articles/2026/probability-is-not-a-bug-how-ai-risk-challenges-fintech-regulation-and-legal-accountability/
- The FCA reportedly found firms overstating the accuracy of their AI models. The date on this report is unclear, so check the primary FCA publication. https://healthcareandprotection.com/fca-wants-ai-services-to-add-warning-on-financial-advice-and-finds-firms-overstating-accuracy-of-models/

## Not found

- Any live page showing a UK lender "trialling LLM memo tools and getting burned by invented figures" (D6). The closest are law-firm commentary and a UK Finance blog on generative AI drafting tools. The UK Finance blog turned up only as a search-result summary, with no URL I can cite.
- Any public price for Crediflow, Heron, Arya.ai or Ocrolus UK.
- Any customer review of Crediflow or Heron in the customers' own words. Only vendor and aggregator pages turned up.
- Any evidence of the bet's problem quote, "nobody can say where a figure came from" (D1). That belongs in interview notes, not on the web.
- Any evidence on which file formats UK lenders or brokers receive (D2), on pilot data-protection basis (D3), or on named pilot runners (D5). These need the owner.
- Codat and Truelayer: not searched in this run. A Codat-style cited-memo product is untested.
- Primary pages: the fetch tool was blocked, so no page was read in full and no page dates were checked.

## Sources

- https://www.startupmag.co.uk/funding/crediflow-ai-2026-seed-funding/
- https://www.getapp.co.uk/software/2096761/crediflow-ai
- https://dealroom.co/news/169312-crediflow-ai-raises-1-5m-to-turn-loan-underwriting-into-minutes/
- https://arya.ai/success-stories/uk-lender-maxcap-increases-underwriting-productivity-with-cred-ai
- https://arya.ai/blog/ai-credit-memo-automation
- https://herondata.io/parse-financial-statements
- https://herondata.io/case-studies/increasing-underwriting-speed-for-outfund
- https://www.capterra.co.uk/software/1015028/ocrolus
- https://www.capterra.com/p/232462/Ocrolus/reviews/
- https://www.getapp.com.au/software/2049438/ocrolus
- https://www.extend.ai/resources/ocrolus-review-pricing-features-alternatives
- https://www.docuclipper.com/customer-stories/365-business-finance-docuclipper/
- https://www.docuclipper.com/blog/ocrolus-alternatives/
- https://www.equifax.co.uk/business/product/share-by-bank/
- https://www.experian.co.uk/blogs/latest-thinking/automated-credit-decisions/making-the-right-decisions-on-sme-credit-fast/
- https://yapily.com/blog/introducing-data-plus-advanced-business-transaction-categorisation-elevate-sme-lending-decisions
- https://plaid.com/blog/instant-link-next-generation-lendscore/
- https://www.freeths.co.uk/insights-events/legal-articles/2026/probability-is-not-a-bug-how-ai-risk-challenges-fintech-regulation-and-legal-accountability/
- https://healthcareandprotection.com/fca-wants-ai-services-to-add-warning-on-financial-advice-and-finds-firms-overstating-accuracy-of-models/
