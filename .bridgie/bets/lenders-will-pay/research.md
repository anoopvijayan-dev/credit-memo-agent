# Research — lenders-will-pay

Researched 2026-11-08. - **How crowded:** Moderately, and a UK entrant already exists. Crediflow (Manchester, seed-funded) ships a source-traced credit-memo generator launched Oct 2026; Kaaj (US, memos with KYB and policy exceptions), Arya Cred AI (a UK lender case study) and Digilytics (UK SME lender case study) sit alongside, with extraction vendors (Ocrolus, Codat, Equifax Share by Bank) feeding the same step.
- **Price band:** Almost nothing is published. The only list prices found are Codat Start-up at £25 per active company per month (seen on the Codat plans page) and an Equifax G-Cloud rate of £3.87 to £2.82 per connection (not confirmed as Share by Bank). Ocrolus, Kaaj, Digilytics and Crediflow all say "contact sales".
- **Loudest complaint:** Nothing in the customers' own words from UK lenders or brokers. The nearest are Ocrolus reviewers ("Great Product but a bit Pricey", slow turnaround on complex statements) and brokers' top frustration with lenders, which is lenders changing their minds (56% in a LendInvest survey).
- **Gap I see:** No competitor page I found, other than Crediflow's reported source traceability, says every figure is cited and human-approved. Lender-side demand for that, and for a broker-side buyer, is unproven. The 1,400 figure matches the NACFB member count (1,414 firms, Jan 2026), not firms observed spreading by hand.
- **Freshness:** Most sources are from 2025 to 2026. The Plaid and Crediflow items are Oct to Nov 2026, the Ocrolus accuracy test is from 2019, and several Codat and Ocrolus pages carry no visible date. I read search-result summaries, not the pages themselves (fetch was blocked).

## Direct competitors

Same problem (turn bank statements and financials into a lender credit memo) for credit teams at SME lenders. Prices are quoted as found; none of the memo vendors publish a price. Page dates are as found in search results; where none was visible, "date not shown" is stated.

| Name | What | Price | Link |
|---|---|---|---|
| Crediflow AI (Manchester, UK) | Spreading plus credit-memo drafting. FlowSpread pulls figures from PDFs, tax returns and bank statements; a spreading tool was released in June and a credit-memo generator in October (2026), filling the lender's own templates with tables, charts and source traceability. Customers named: AddWorking Capital, Iveco Capital. Raised a seed round (reported £1.5m). All claims are vendor-reported. | No public pricing (GetApp lists none) | [Business Cloud](https://businesscloud.co.uk/?p=267360), [StartupMag](https://www.startupmag.co.uk/funding/crediflow-ai-2026-seed-funding/), [GetApp UK](https://www.getapp.co.uk/software/2096761/crediflow-ai) |
| Kaaj (US; seed $3.8M announced Nov 2025) | Document intake, KYB, bank statement analysis, fraud checks, policy review and credit-memo generation for SMB lenders. Memos include an executive summary, cashflow analysis, risk signals and policy exceptions linked to past precedent. Claims to automate 60 to 80% of pre-decision prep, with underwriters keeping final authority. No UK customer found. | Not published; pricing page says it depends on segment, monthly application volume, document package and integrations. No per-underwriter seat fee. | [Kaaj pricing](https://kaaj.ai/pricing), [Kaaj SMB lending](https://kaaj.ai/solutions/smb-lending) |
| Arya.ai Cred AI | Statement analysis that reads transactions in context (e.g. separating true revenue from other inflows). UK lender MaxCap case study says decision time fell from 2 to 3 days to a couple of hours (vendor-reported). | Not found | [Arya MaxCap case study](https://arya.ai/success-stories/uk-lender-maxcap-increases-underwriting-productivity-with-cred-ai), [Arya Cred AI](https://arya.ai/cred-ai) |
| Digilytics (UK-facing) | AI and OCR bank statement processing for a UK SME lender (unnamed case study); claims above 95% accuracy and about 99% coverage (self-reported). Equifax UK partnership. No credit-memo feature confirmed. | Custom, set after scoping; no list price | [Digilytics UK SME case study](https://digi-marketing-website-v2.uksouth.cloudapp.azure.com/2025/06/02/), [Digilytics SME lending page](https://digi-marketing-website-v2.uksouth.cloudapp.azure.com/sme-lenders/) |

## Indirect and substitutes

- **Extraction and categorisation vendors that stop at data:** Ocrolus (US-based; I found no UK office or expansion news) bills by statement, page or book count on custom contracts ([Ocrolus pricing explainer](https://addy.com/blog/ocrolus-pricing)). Codat offers categorised bank data and has partnered with Plaid on UK SME underwriting ([Codat business lending](https://codat.io/business-lending/), [Plaid and Codat UK partnership, 11 Mar 2021](https://plaid.com/en-gb/blog/plaid-and-codat-partner-to-improve-small-business-lending-in-the-uk/)).
- **Open-banking and credit-bureau feeds:** Equifax Share by Bank replaces manual statement review with an API data feed ([Equifax Share by Bank](https://www.equifax.co.uk/business/product/share-by-bank/)). Credit Kudos is an FCA-authorised credit reference agency and Open Banking provider; Atom bank has used it for small-business underwriting ([Atom bank and Credit Kudos](https://www.atombank.co.uk/newsroom/atom-bank-credit-kudos-partnership)). Validis supplies accounting data that Equifax combines with Open Banking data ([Equifax open banking brochure](https://assets.equifax.com/assets/unitedkingdom/open_banking_debt_commercial_brochure.pdf)). These reduce the need to spread statements by hand, which is the D3 'why it's tolerable' risk.
- **Scoring vendors:** Plaid's LendScore 2 and Instant Link were announced 6 Oct 2026, claiming a 42% stronger prediction of repayment than traditional credit data alone (Plaid's own testing, unverified) ([Plaid blog, 6 Oct 2026](https://plaid.com/blog/instant-link-next-generation-lendscore/), [Plaid fall 2026 release](https://plaid.com/whats-new/fall-2026/)). I found no memo-writing product from Plaid; it supplies a score, not a memo.
- **Lender workflow platforms:** Shawbrook Bank moved to nCino to address SME lending bottlenecks ([FinTech Global, 2 Feb 2026](https://fintech.global/2026/02/02/shawbrook-bank-tackles-sme-lending-bottlenecks-with-ncino/)). Atom bank used a process automation engine to shorten commercial loan origination ([IT Brief UK](https://itbrief.co.uk/story/atom-bank-cuts-commercial-loan-times-with-automation)).
- **Doing nothing / manual:** Experian's Metro Bank case study says SME commercial lending has long been mostly manual, with inconsistent, slow decisions ([Experian](https://www.experian.co.uk/blogs/latest-thinking/small-business/how-metro-bank-supported-more-smes/)).
- **Broker forwards raw statements:** Not found as a documented practice. The bet states it as background; I found no trade-press or forum source for it either way.

## Prices

What a UK lender pays today, as found:

- Codat Start-up plan: £25 per active company per month outside the US ($30 in the US), monthly billing, no minimums, all products included. Enterprise is quote-only ([Codat plans](https://www.codat.io/plans/); summary from a search result, page not opened). An older figure of £500 or $700 per month appears elsewhere and looks outdated; I did not find the source page.
- Equifax UK: a G-Cloud 14 pricing document shows volume-tiered per-connection rates of £3.87 (lowest tier) and £2.82 (higher tier) plus set-up fees. The snippet does not name Share by Bank, so I cannot confirm this is its price ([Equifax G-Cloud 14 pricing document, 15 Jul 2025](https://assets.applytosupply.digitalmarketplace.service.gov.uk/g-cloud-14/documents/579897/493191492297171-pricing-document-2025-07-15-1158.pdf)).
- Ocrolus: no official price sheet. A directory lists a free trial of up to 100 pages (citing an Ocrolus FAQ, not a current official page) ([Software Advice listing](https://www.softwareadvice.com/loan-servicing/ocrolus-profile)). A competitor, DocuClipper, says its page-based plans start from $20 per month and contrasts them with Ocrolus enterprise pricing (marketing, treat with caution) ([DocuClipper](https://www.docuclipper.com/blog/ocrolus-alternatives/)).
- Kaaj, Digilytics, Crediflow, Arya: no published prices (see table above).
- Analyst time (the cost the bet displaces): no UK figure found for hours per deal. A US-vendor claim that credit teams spend 30 to 40% of their time writing memos is marketing ([HyperVerge](https://hyperverge.co/blog/automating-credit-assessment-memos-cam-the-next-big-leap-in-credit-decisioning/)).

## What their customers complain about

There is very little in lenders' or brokers' own words about spreading or memos. The items below are the closest, with their limits stated.

- Price: an Ocrolus review is titled "Great Product but a bit Pricey" (Capterra, US reviewers; not UK) ([Capterra Ocrolus reviews](https://www.capterra.com/p/232462/Ocrolus/reviews/)).
- Speed on difficult inputs: an Ocrolus reviewer notes longer waits for more complicated bank statements; another lists inability to download Excel files ([Capterra Ocrolus reviews](https://www.capterra.com/p/232462/Ocrolus/reviews/)).
- Accuracy: a 2019 test by Sequence found duplicated transactions when Ocrolus advertised a 100% accuracy guarantee. One tester, 2019, so weak evidence about today ([Sequence review](https://www.sequenceinc.com/fraudfiles/ocrolus-software-review/)).
- Broker frustration with lenders: in a LendInvest survey at the NACFB Commercial Finance Expo, 56% of brokers said lenders changing their mind on a deal frustrates them most ([Financial Reporter](https://2025.financialreporter.co.uk/specialist-lending/lenders-changing-their-mind-is-brokers-biggest-frustration.html)). This is not about spreading, but it shows where brokers say their pain is.
- Stricter underwriting: 34% of commercial finance brokers cited it as the main problem in an October 2024 survey ([Mortgage Solutions](https://www.mortgagesolutions.co.uk/specialist-lending/commercial-finance/2024/10/10/commercial-brokers-face-funding-difficulties-due-to-changing-lender-policies-survey-finds/)).
- Accounts vs bank statements: an adviser column says financial accounts often tell a different story from recent bank statements, and lenders will eventually ask ([Bridging and Commercial](https://www.bridgingandcommercial.co.uk/article-desc-14314_making-sure-a-‘perfect-client’-isn’t-declined)).
- Regulator view on AI in credit: commentators say hallucinations could misinform credit assessments and that senior managers carry accountability if they cannot show reasonable governance ([Freeths, 2026](https://www.freeths.co.uk/insights-events/legal-articles/2026/probability-is-not-a-bug-how-ai-risk-challenges-fintech-regulation-and-legal-accountability/)). These are law-firm commentary, mostly about consumer credit, not lender complaints.

## Not found

- Any quote from a UK credit analyst or broker saying "spreading takes an afternoon" or "compliance asks where a figure came from" (the bet's problem statement remains an assumption).
- Public pricing for Crediflow, Kaaj, Digilytics, Arya Cred AI and Ocrolus.
- Any source for hours per deal spent spreading by hand in UK lending.
- A UK forum thread or trade-press source for lenders redoing a broker's spreading.
- Evidence that large MCA providers (Liberis, 365 Business Finance, Nucleus) are building their own tooling. This is in the bet's background and I did not test it.
- Whether the British Business Bank Growth Guarantee Scheme audits lenders' credit files (D4); the results addressed accreditation and the 70% guarantee only.
- Any Ocrolus UK presence or Kaaj UK customer.
- Lender-specific complaints about Codat; G2 shows 28 reviews, 4.6 average, mostly from small-business integrators, and 'not enough data' on cons ([G2 Codat](https://www.g2.com/products/codat)).
- Any competitor page I read directly: fetch was blocked, so the product claims here come from search-result summaries and are vendor-reported.
- Plaid or others shipping cited memos: not found as of this search, so D5's reversal risk is unconfirmed but real (Crediflow already does it).

## Sources

- [Business Cloud, Crediflow](https://businesscloud.co.uk/?p=267360)
- [StartupMag, Crediflow seed](https://www.startupmag.co.uk/funding/crediflow-ai-2026-seed-funding/)
- [GetApp UK, Crediflow](https://www.getapp.co.uk/software/2096761/crediflow-ai)
- [Kaaj pricing](https://kaaj.ai/pricing)
- [Kaaj SMB lending](https://kaaj.ai/solutions/smb-lending)
- [Arya MaxCap case study](https://arya.ai/success-stories/uk-lender-maxcap-increases-underwriting-productivity-with-cred-ai)
- [Arya Cred AI](https://arya.ai/cred-ai)
- [Digilytics UK SME case study](https://digi-marketing-website-v2.uksouth.cloudapp.azure.com/2025/06/02/)
- [Digilytics SME lending page](https://digi-marketing-website-v2.uksouth.cloudapp.azure.com/sme-lenders/)
- [Ocrolus pricing explainer (Addy)](https://addy.com/blog/ocrolus-pricing)
- [Software Advice, Ocrolus](https://www.softwareadvice.com/loan-servicing/ocrolus-profile)
- [DocuClipper, Ocrolus alternatives](https://www.docuclipper.com/blog/ocrolus-alternatives/)
- [Capterra Ocrolus reviews](https://www.capterra.com/p/232462/Ocrolus/reviews/)
- [Sequence, Ocrolus review (2019)](https://www.sequenceinc.com/fraudfiles/ocrolus-software-review/)
- [Codat business lending](https://codat.io/business-lending/)
- [Codat plans](https://www.codat.io/plans/)
- [G2 Codat](https://www.g2.com/products/codat)
- [Plaid and Codat UK partnership (11 Mar 2021)](https://plaid.com/en-gb/blog/plaid-and-codat-partner-to-improve-small-business-lending-in-the-uk/)
- [Plaid blog, 6 Oct 2026](https://plaid.com/blog/instant-link-next-generation-lendscore/)
- [Plaid fall 2026 release](https://plaid.com/whats-new/fall-2026/)
- [Equifax Share by Bank](https://www.equifax.co.uk/business/product/share-by-bank/)
- [Equifax open banking brochure](https://assets.equifax.com/assets/unitedkingdom/open_banking_debt_commercial_brochure.pdf)
- [Equifax G-Cloud 14 pricing document](https://assets.applytosupply.digitalmarketplace.service.gov.uk/g-cloud-14/documents/579897/493191492297171-pricing-document-2025-07-15-1158.pdf)
- [Atom bank and Credit Kudos](https://www.atombank.co.uk/newsroom/atom-bank-credit-kudos-partnership)
- [IT Brief UK, Atom bank](https://itbrief.co.uk/story/atom-bank-cuts-commercial-loan-times-with-automation)
- [FinTech Global, Shawbrook and nCino](https://fintech.global/2026/02/02/shawbrook-bank-tackles-sme-lending-bottlenecks-with-ncino/)
- [Experian, Metro Bank case study](https://www.experian.co.uk/blogs/latest-thinking/small-business/how-metro-bank-supported-more-smes/)
- [HyperVerge, credit memo automation](https://hyperverge.co/blog/automating-credit-assessment-memos-cam-the-next-big-leap-in-credit-decisioning/)
- [Financial Reporter, lenders changing their mind](https://2025.financialreporter.co.uk/specialist-lending/lenders-changing-their-mind-is-brokers-biggest-frustration.html)
- [Mortgage Solutions, commercial brokers funding difficulties (Oct 2024)](https://www.mortgagesolutions.co.uk/specialist-lending/commercial-finance/2024/10/10/commercial-brokers-face-funding-difficulties-due-to-changing-lender-policies-survey-finds/)
- [Bridging and Commercial, perfect client](https://www.bridgingandcommercial.co.uk/article-desc-14314_making-sure-a-‘perfect-client’-isn’t-declined)
- [Freeths, AI risk and FinTech regulation (2026)](https://www.freeths.co.uk/insights-events/legal-articles/2026/probability-is-not-a-bug-how-ai-risk-challenges-fintech-regulation-and-legal-accountability/)
- [NACFB, broker membership tops 1,400 firms](https://nacfb.org/nacfb-broker-membership-tops-1400-firms-following-another-year-of-growth/)
- [FCA response to FSB super-complaint (limited-company lending outside perimeter)](https://www.fca.org.uk/publications/corporate-documents/fca-response-fsbs-super-complaint-requiring-personal-guarantees-business-loans)
- [British Business Bank, Growth Guarantee Scheme FAQs](https://www.british-business-bank.co.uk/about/frequently-asked-questions/growth-guarantee-scheme)

NACFB member count: 1,414 firms in Jan 2026, up from 1,338 in Jan 2025 (search-result summary of the NACFB announcement; the cited page may carry an earlier 1,400 headline).
