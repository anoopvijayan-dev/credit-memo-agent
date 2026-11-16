# Board — statement-review

**Vote:** 2 fund if, 1 pass · **average score:** 3.7/10 · 2026-11-16

| Lens | Score | Vote |
|---|---|---|
| offer | 4 | fund if |
| moat | 3 | pass |
| product | 4 | fund if |

## Chair

**Vote:** two FUND IF (offer 4, product 4) and one PASS (moat 3). The board would fund a narrower test for one firm, not the bet as titled.
**Biggest shared risk (all three lenses):** on real statements, card revenue from acquirers the tool doesn't know and transfers without an invoice reference go into unclassified inflows (M16), and R9 refers anything over 20% unexplained. The agent may keep the adding-up and hand back the judging, and checking and copying eat what is left of the saving.
**Sharpest disagreement:** moat says citations answer none of the firm's stated objections (confidence, price, GDPR) and the provider could ship them as a feature. Product says citations answer the signer's fear directly. They are talking about different people, the approver and the underwriter, and D10 decides which one the bet is for.
**Strongest version:** a checker that runs inside the firm and fills the firm's own memo fields from the feed it already gets. Every figure is traced, and the lines it can't explain come first. Sell it to the approver on 'nothing leaves' and to the underwriter on 'every figure traced'.
**Do first:** send the approver one message through the friend: what would block a pilot (D10), which product was turned down at what price (D11), and which provider sends the feed and what it already does (D12). Any of the three could end this before you write code.

## Shared risks

**Raised by all three lenses**

1. **The agent keeps the easy part and hands back the hard part.** On real statements, card revenue from acquirers the categoriser doesn't know and bank transfers without an invoice reference go into unclassified inflows, M16 (README:117-118, 125-126). Rule R9 refers any case where more than 20% of money in is unexplained (`src/cma/policies/uk_sme_default.yaml:85-86`). If the friend's 1-3 hours (D3, a guess) go on judging unclear lines rather than adding up clear ones, the underwriter still reads the statement. The target is at best 2x, against a baseline nobody has measured. *Settled by:* the D13 timing split, plus M16 on one real business statement. (moat, offer, product)
2. **The firm's input may already be half done, or unreadable.** Statements arrive as an Open Banking feed through a provider (D5). The tool reads only a five-column CSV or a Monzo export, and lists Open Banking JSON as 'next' (README:66-67, 111). If the provider already categorises transactions with drill-down to the lines, three things follow. The provider owns the pipe and could ship tracing as a feature (moat). Part of the job is already paid for (offer). And nobody has described how a file gets into the tool (product). If the statements are mostly PDFs, the tool can't start. *Settled by:* D12, plus one sample of the provider's output. (moat, offer, product)
3. **The blocker the title names isn't the one the buyer named.** The title bets on 'never invents a number'. The only buyer evidence, D7, names confidence, price and applicant data leaving the firm. Citations answer the signer's fear, not the approver's. The design already answers the data worry better than the pitch does: the model gets only the account name ('Applicant'), the dates and the metrics and policy tables (`src/cma/graph.py:212-218`, `src/cma/ingest.py:140`), and offline mode sends nothing out. *Settled by:* D10, asked of the approver, not the friend. (moat and offer as a risk; product notes the data design as a strength)
4. **Nobody knows who signs.** D1 lumps a specialist lender and a broker together. A broker prepares files for someone else's credit decision, so 'my name on the file' may not belong to the buyer, and the offer changes. *Settled by:* D11. (moat, offer, product)
5. **One source.** 'Their whole team' is the friend's report, passed on by the owner (D2), yet the title speaks for 'small-business underwriters' in general. All three lenses want it either confirmed outside the firm (D15) or written down as a showcase for one firm. (moat, offer, product)

**Raised by two lenses**

6. **Checking and copying eat the saving.** The trust condition has the underwriter check every figure. Today that means matching transaction ids in `memo.evidence.json` against a CSV, or using an MCP client, and approving at a terminal. The output is the agent's own memo, not the firm's template, so the figures still have to be copied across. The bet measures reading time only. (offer, product)
7. **The planned test can't read the planned outcome.** The demo runs on the owner's own statements (D8). They are personal, the importer reads Monzo Flex as a loan (README:67), they have no card settlements from real acquirers, and the friend has no baseline on them. By 2026-11-29 the honest result is a reaction to a demo (D14). (offer, product)
8. **The firm may not pay anything.** The firm has already turned down purpose-built tools on price (D6). The backlog costs the underwriter's weekends, not the firm's budget (offer). Each new lender may also be a slow compliance sale whose cost absorbs the price gap (moat). No price appears anywhere in the bet. (offer, moat)
9. **The promise is wider than the code.** The code can't keep 'no wrong figures' (D9): a correctly added total over wrongly categorised lines is still wrong (regex rules, README:68), and the guard checks figures, not judgements (README:115-116) (offer). The pass/refer/fail outcome comes from an illustrative policy (README:127) and reads like a credit recommendation (product). (offer, product)

**Raised by one lens**

10. **Nothing stops a copy.** The code is MIT-licensed and the method is written up in ADR 001. The things that could become a moat don't exist yet: rules tuned on real UK statements, a lender's own policy in the YAML, an archive of signed files. (moat)
11. **The firm has no deadline.** 'Being phased out' dropped out of D6. The only trigger left is volume, which may be a seasonal peak (D16). 2026-11-29 is our time budget, not their deadline. (offer)

## Conditions

- [ ] Ask the approver, through the friend, what would block a pilot (D10). State the data promise exactly as the code behaves: offline mode sends nothing out; with a hosted model, only 'Applicant', the dates and the two tables leave. If the answer is data protection, confirm that a version running inside the firm would be allowed. *evidence* (moat, offer)
- [ ] Rewrite the bet's title and first sentence around that blocker and the underwriter's outcome. Narrow the promise to what the code enforces: every figure lists the transactions behind it, or shows n/a and why. Drop 'no wrong figures' from D9. *owner* (offer, moat)
- [ ] Name the product the firm turned down, the quoted price, who turned it down, and whether it already links each figure to its transactions (D11). If it does, moat's PASS stands. *research + evidence* (moat, offer)
- [ ] Find out whether the firm is a lender or a broker, and who signs the credit decision (D11). *evidence* (moat, offer, product)
- [ ] Name the Open Banking provider, pull one sample output, and write down what reading is left for the agent after it. Count the statement formats across the last 15 applications (D12). *evidence* (moat, offer, product)
- [ ] The friend, not the owner, times 3 real files. Split the time into adding up clear lines and judging unclear ones, and include the time to check and copy the figures (D13). *evidence* (moat, offer, product)
- [ ] Run the categoriser on one real business statement (anonymised, with consent) and record M16 and the R9 outcome. A synthetic case can't test this, because the categoriser already knows the synthetic acquirer names. *numbers* (moat, product)
- [ ] Get the friend's memo template, redacted. Map each field to M1-M16 and list the gaps. The demo output should be fields the friend can paste, not the agent's own memo. *evidence* (product)
- [ ] Write non-goals into the bet: the tool doesn't make the credit decision (drop or hide pass/refer/fail), doesn't extract from PDFs, and covers one underwriter's workflow, not a market. *owner* (product, moat, offer)
- [ ] Pick the outcome you will actually read by 2026-11-29 (D14), with the success bar set by the friend. Say up front whether the demo shows the mechanism or the time saving. Run it on a statement shaped like a business applicant's, not a personal Monzo account. *owner* (offer, product)
- [ ] Either confirm the problem with at least two underwriters at other UK lenders who aren't friends (D15), or record this as a showcase for one firm. *research* or *owner* (moat, offer, product)
- [ ] Name one route to lenders 2-10 and one thing that would make leaving costly after six months, and count the UK specialist SME lenders and brokers. The 2-week test doesn't need this; calling it a business does. *research + owner* (moat)

## Where the board disagrees

1. **Fund or pass.** Moat votes PASS (3): even if the demo works, the provider already in the flow can ship tracing as a feature, and nothing here is hard to copy. Offer and product vote FUND IF (4): the mechanism fits the signer's fear, most of it already exists, and the test costs 2 weeks and $20. Part of the split is what each lens is judging: moat judges a company, offer and product judge a test. But moat's first way to kill it (the provider already does this) would end the test too, and it is the cheapest item on the list to check.
2. **Which wins the firm: citations, or data staying inside.** Product says the trust mechanism answers the signer's fear directly. Moat says citations answer none of the objections in D7. Offer sits between them: citations for the underwriter, the data story for the approver, and the data story is the stronger of the two. The lenses may be talking about two different people, the underwriter who signs and the approver who allows the tool. D10 decides which one the bet is pitched to.
3. **The price gap: opening or warning.** Moat reads incumbents pricing themselves out of specialist lenders as the right shape for a first market. Offer reads the same fact as a firm that won't pay at all, because the backlog lands on the underwriter's weekends, not its budget. D11 and the approver's price ceiling settle it.
4. **What 'done' means on 2026-11-29.** All three agree the hours-per-application metric can't be read on the owner's statements. Product wants the friend to run a demo on a statement shaped like an applicant's and judge the output in their own template. Offer wants the approver to say whether a demo on non-applicant data would lead to a pilot on real files. Moat wants the bet rescoped as a showcase before either. The owner has to pick one (D14).
5. **The board against the pitch.** This one isn't between members. All three read D7 as contradicting the title, and none accepts D9's 'no wrong figures' as a promise the code can keep. The owner's answers so far (D2, D3, D4, D9) are on record with no evidence behind them.

## The strongest version

The board can see a narrower, better business than the one pitched.

- **The buyer and the user are different people, so give each their own reason.** To the approver: it runs on the firm's own machine. In offline mode nothing leaves; with a hosted model only 'Applicant', the dates and two tables of figures leave. To the underwriter: every figure lists the transactions behind it, or says n/a and why.
- **It fills in the firm's own memo.** It takes the export the firm already receives (the provider's feed, once an adapter exists) and fills the fields of the friend's template, each linked to its lines.
- **It treats the unclear lines as the work, not a failure.** This is the chair's reading, not any one lens's. All three lenses fear that M16 hands the hard part back. If the D13 split shows the time goes on judging, the useful product lists the lines it can't explain first: unknown acquirers, transfers without references, and, with new code, large one-off credits. The underwriter judges those first and relies on the traced rest.
- **It stays out of the decision.** No pass/refer/fail, no PDFs, no market claim.
- **What keeps a lender is built up locally.** Each lender's own policy YAML and categorisation rules (its acquirers, its applicants' reference habits) build up in the tool. That is the only switching cost any lens could see, and it grows with use.
- **Scope: one firm first.** Success is the approver asking for a pilot on real files. It stays a one-firm showcase until two underwriters outside the friend's firm report the same problem.

It is not worth building if the provider already gives totals linked to their lines, if the product the firm turned down already traces figures, or if the approver requires vendor onboarding wherever the tool runs. All three can be found out this week, before any code is written.

## Memo · offer lens

Score: 4 | Vote: FUND IF
## What is strong
- **There's a guarantee the code can actually keep, and it answers the fear they named.** 'If the AI makes up a number, it's my name on the file' (bet, Problem in their words). Money is added up only in code; every metric records the transaction ids behind it; a figure that can't be computed shows as n/a with the reason; a guard reads every number in the memo back; nothing is final until a named reviewer approves (README:5-8, 69, 72; ADR 001, Decision 1-5). Generic AI, which the firm bans (bet, What they do today), can't make that promise.
- **The data story is stronger than the bet uses.** The draft prompt carries the account name, the period, the metrics table and the policy table (src/cma/graph.py:212-218), and the CLI leaves the account name as 'Applicant' (src/cma/graph.py:188, src/cma/ingest.py:140). No transaction lines or payee names reach a model, and the offline template model (examples/cafe_stretched.memo.md:24) sends nothing out at all. That speaks to the GDPR worry in D7 more directly than citations do.
- **The first step costs the firm no data.** D8 plans the demo on the owner's own statements, so seeing it carries no applicant-data risk. That is an honest risk reversal for the first look (kill 3 covers what it can't prove).
- **The outcome is vivid, if it holds.** A cancelled weekend and 'most of each application' spent on statements (bet, Problem in their words) gives a clear outcome: get the weekends back. It is second-hand paraphrase and unmeasured (D3, D13).

## What would kill it
1. **The payer doesn't count the value.** The firm already turned down purpose-built tools on price (D6), and the backlog's cost 'falls on the underwriter's own time, not on the firm's budget' (bet, What they do today, Alternative). When the person who gains isn't the one who pays, no price looks small to the buyer, and no price is stated anywhere in the bet. *Test:* ask the approver (D10) what the backlog costs the firm (time to decision, applications lost or delayed) and what they would pay per application or per month (D11). If both answers are 'nothing', this is a free tool for one person, not an offer.
2. **Net saving after checking falls under half.** The trust condition has the underwriter check every figure (bet, Expected outcome), and the agent hands back what it can't classify: transfers without an invoice reference and card revenue from unknown acquirers land in M16 and refer the case (README:117-118, 125-126). If most of the 1-3 hours (D3) goes on unclear lines rather than sums (D13), the agent keeps the easy part and returns the hard part. *Test:* time 3 real files split into clear and unclear lines (D13), then time one check of an agent memo against its citations. The saving is the old time minus checking time minus handback time.
3. **Certainty is the weakest of the four levers, and the planned demo doesn't raise it.** The outcome is attractive but unmeasured, speed on a real file is unmeasured, and effort is high (a pip-installed CLI and a fixed CSV layout, README:26, 66, 120-121), but certainty on their own files is lowest. The firm wants to see it working (D7). Its input is an Open Banking feed (D5) the tool can't read yet (README:111). The categoriser knows only synthetic acquirer names (README:125-126), and all testing is synthetic (README:131). The demo uses the owner's statements, which don't look like an applicant's (D8, D14). The title also bets on 'never invents a number', while D7 names confidence, price and data leaving the firm. *Test:* ask the approver whether a demo on non-applicant data would get them to a pilot on their own files, and get the provider and format of the feed (D12).

Lower down: no real urgency on their side (see Questions), and lender versus broker is unsettled (D1, D11), which changes who buys and who carries sign-off risk.

## Questions for the owner
- Who at the firm approves a new tool, and have they been asked what would block a pilot (D10)? The friend can want this; only the approver can switch.
- Lender or broker (D1, D11)? For a broker the value is faster file prep for someone else's decision, which is a different offer.
- Which Open Banking provider does the firm use, what does it pay, and does the provider already categorise transactions (D12)? If so, part of this job is already bought.
- Which product was too expensive, at what quoted price, and who turned it down (D11)? Without a price anchor nobody can say this offer is cheap.
- Would the firm count offline mode (deterministic code, template prose, no AI vendor) as outside its generic-AI ban (bet, What they do today)? If yes, the lead line becomes 'no AI vendor, nothing leaves your machine'.
- Is there a real date on their side, such as a provider contract renewal or a volume peak (D16)? None is in the bet. 'Being phased out' has dropped out (D6), and 2026-11-29 is our appetite, not their deadline; don't present it as one.
- What would the underwriter, not the owner, call success? The D9 bar was set by the owner (D14).

## Conditions
1. Get the approver's own answer on what would block a pilot (D10), through the friend, before signing. Rewrite the title and the offer's first sentence around that blocker and the underwriter's outcome. The README's first sentence (README:3) speaks to a risk committee, which is neither of them.
2. Get one price anchor: the product and quote the firm turned down, or its ceiling per application or per month (D11). If neither exists, record this as a showcase for one firm (D15, third option) and drop 'would hand that part to an agent' as a market claim.
3. Make the guarantee only as wide as the code enforces: every figure lists the transactions it came from, or says n/a and why (README:69). Don't promise 'no wrong figures' (D9). A correctly added figure over wrongly categorised lines (regex rules, README:68) is still wrong, and the guard doesn't check judgements or small whole numbers in prose (README:115-116; ADR 001, Consequences). Promising more puts the cost on the underwriter's signature, the exact risk they named.
4. State the data promise exactly as the code behaves: offline mode sends nothing out; with an outside model only 'Applicant', the dates and the two tables leave (src/cma/graph.py:212-218). Have the approver say whether that meets their GDPR bar before the demo.
5. Get the D13 timing split and the D12 format count from the underwriter before signing. Together they decide whether a 50% cut is reachable at all.

## Memo · moat lens

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

## Memo · product lens

Score: 4 | Vote: FUND IF
## What is strong
- **The trust mechanism answers the person's fear head-on.** The bet's one quoted worry is "if the AI makes up a number, it's my name on the file" (bet, Problem in their words). The repo is built around exactly that. Money is added up only in code, every figure carries a tag, a guard reads the memo back, and nothing is final until a named person approves (ADR 001:17-28; README.md:5-8). The committed example shows what the person would get: 16 figures, each with a method and a count of the lines behind it (examples/cafe_stretched.memo.md:27-44).
- **The product already refuses some things, which is a real product stance.** The model never sees the transactions (ADR 001:20). Lines it can't match stay `OTHER` and are never guessed (README.md:68). A figure it can't compute shows n/a with the reason (README.md:69). Because only the metrics table goes to the model, and an offline model exists (src/cma/llm.py:28), the data-leaving-the-firm worry in D7 is partly answered by design, even though nobody designed it for that.
- **The person sets the bar for trust, not the builder.** The bet's trust condition has the underwriter saying they would sign (bet, Expected outcome). That judges the result from the person's side.
- **The appetite is small** (2w, $20), and most of the machinery exists, so a demo is shippable in the window.

## What would kill it
1. **It hands the hard part back.** For real statements, card revenue from acquirers it doesn't recognise and bank transfers without an invoice reference land in M16 (unclassified inflows) and refer the case (README.md:117-118, 125-126). Things an analyst notices from raw lines, such as a one-off large credit or an odd counterparty, are invisible by design (ADR 001:38-41). If the underwriter's 1-3 hours (D3) go on judging unclear lines rather than adding up clear ones (D13), the agent does the part they don't mind and leaves the part that cost the weekend. *Test:* the friend times 3 real files and splits the time between "adding up" and "judging" (D13). Then run the agent on one statement shaped like an applicant's and see how much lands in M16.
2. **Checking the figures costs as much as reading the statement.** The trust condition asks the underwriter to check every figure before signing (bet, Expected outcome). From their side, checking means opening `memo.evidence.json` and matching transaction ids against a CSV (README.md:37-40), or using an MCP client (README.md:74). Approving happens at a terminal prompt (src/cma/cli.py:249-262). One figure, total revenue, rests on 261 lines (examples/cafe_stretched.memo.md:30). On top of that, the output is the agent's own memo with illustrative policy outcomes (README.md:127; memo line 14), not the firm's template. So the underwriter still copies the figures into their own memo. The bet only measures reading time, but the time that counts is reading plus checking plus copying. *Test:* in the timed run, include the checking and copying time, and have the friend do it, not the owner.
3. **The first step from the person's side doesn't fit.** The agent reads a five-column CSV or a Monzo export (README.md:66-67, 111). D5 says applications arrive as an Open Banking feed through a provider, and that "we can't determine what users will share". If the provider already categorises transactions, there is less reading left for the agent to take over (D12). If it's mostly PDFs, the agent can't start. Either way, nobody has described the first five minutes for the underwriter: where the file comes from and how it gets in. *Test:* D12, a count of formats across the last 15 applications, with the provider named and whether it already categorises.

Also relevant but less severe: the 2-week test runs on the owner's own statements (D8). The Monzo importer reads Monzo Flex, a personal product, as a loan (README.md:67), so these may not look like an SME applicant's file at all. As D14 says, the outcome the bet defines (hours per application on comparable files) can't be read by 2026-11-29.

## Questions for the owner
- What happens right after the statements are read? Does the underwriter type figures into the firm's memo template, write prose, or key them into a loan system? Can you get a redacted blank template?
- Which of M1-M16 does that template actually ask for, and what does the friend look for that isn't in the 16? Covenants, related-party transfers, gambling or cash patterns?
- How would the friend want to check a cited figure: on screen, in a spreadsheet, or printed? Who runs the CLI in the demo, the friend or you?
- Does the friend sign the credit decision, or prepare files for someone who does (D11)? That changes whose fear the trust mechanism is answering.
- The bet's `non_goals` is empty. What is this explicitly *not*? The credit decision? The firm's policy? PDF extraction? A product for other lenders?
- Who is the proxy user, by name, and will they give two hours inside the window? You won't use this weekly yourself, and the bet names only "one friend".

## Conditions
1. **Write non-goals into the bet before signing.** At minimum: it doesn't make or recommend the credit decision (drop or hide the pass/refer/fail outcome, since the policy is illustrative, README.md:127); no PDF extraction in this bet; one underwriter's workflow, not "small-business underwriters" in general (D15).
2. **Get D12 and D13 answered by the friend**: the formats across the last 15 applications, and the adding-up versus judging split on 3 timed files. If the files are mostly PDFs, or the time mostly goes on judging, re-scope the bet to that part or kill it.
3. **Get the friend's memo template, redacted.** Map each field to M1-M16 and list the gaps. The demo's output should be filled-in fields the friend can paste, not the agent's own memo format.
4. **Choose the outcome you can actually read (D14), with the success bar set by the friend.** D9 was answered by the owner. Count checking and copying time in the timed run, not just reading time.
5. **Demo on an applicant-shaped statement.** Use a synthetic `trades` or `ecommerce` case from `synth.py`, or a consenting small business's export, not a personal Monzo account. If you use your own statements, say up front that the demo shows the mechanism, not the time saving.
