# Board — v01-pilot-ready

**Vote:** 2 fund if, 1 pass · **average score:** 3.7/10 · 2026-11-08

| Lens | Score | Vote |
|---|---|---|
| offer | 4 | fund if |
| moat | 3 | pass |
| product | 4 | fund if |

## Chair

**Vote:** two FUND IF (offer 4, product 4) and one PASS (moat 3). The board backs a pilot but not a moat.
**Biggest shared risk (all three lenses):** nobody has heard the pain. The problem quote is unsourced (D1), research found no lender complaining about invented figures (only law firms), and Crediflow already sells cited memos with named UK customers.
**Sharpest disagreement:** moat says a copyable method published in a public repo is a reason to PASS now; offer and product say a free pilot doesn't need a moat yet, only a guarantee the analyst can check.
**Strongest version:** an expert-led concierge pilot. The owner takes the lender's own file under a written data-protection agreement and returns a cited draft memo, checked against their analyst's spread. The repo is the credential for consulting, not a SaaS product taking on Crediflow.
**Do first:** run three dated lender/broker interviews (ask about the worst part of spreading, ask for a redacted file sample, ask which vendor they've seen) while fixing the M10 blocker; nothing else should move until both are done.

# Chair — v01-pilot-ready

Vote: moat PASS (3), offer FUND IF (4), product FUND IF (4).

## Shared risks

Raised by two or three lenses, most widely raised first.

1. **The pain hasn't been heard from a buyer** (moat, offer, product). The problem quote has no dated source (D1). The why-now line "lenders are getting burned by invented figures" has no source either (D6). Research found that complaint only in law-firm commentary. The complaints buyers do make are about speed and price (Ocrolus slow, Ocrolus pricey). If interviews don't bring up traceability without prompting, the offer's headline and the moat's one 10x angle both go.
2. **The differentiator is already sold** (moat, offer, product). Crediflow AI (Manchester, seed-funded, customers AddWorking Capital and Iveco Capital) fills the lender's own template with "source traceability". Arya.ai and Heron name UK customers too. No lens has seen a Crediflow demo, so nobody can say whether v0.1 is many times better or only slightly better.
3. **There is no named pilot runner or segment** (moat, offer, product). No person has said yes in principle (D5). Lender vs broker is undecided (D10), and the two get different files, write different memos and face different competitors (Heron has a broker suite; DocuClipper serves small brokers). The week-6 kill clock has no start date.
4. **The lender's file and the lender's redaction break the pipeline** (offer, product). v0.1 reads only the synthetic five-column CSV. UK bank exports are refused (DD/MM dates, newest-first order, BOM, split paid-in/paid-out). Redacting payer names breaks M15 and the FPS CREDIT categoriser. A sole trader's statement is still personal data with the name removed, which runs into the "no real customer data" non-goal (D2, D3). Whoever reshapes the file uses up the afternoon the tool is meant to save, and that step is outside the trace chain.
5. **A figure can be grounded and still wrong, and the guarantee hasn't been measured** (offer, product). The eval has only run on FakeLLM. M10 gives DSCR 99.00 for a loss-making business with no loans (blocker). M15 shows 0.0% for card-settled businesses. Owner money paid back in counts as revenue (DSCR 1.43 → 6.65). All three pass "zero ungrounded numbers" and would still lose the analyst's trust on first read (D4).

Single-lens risks:

- **Moat:** publishing the repo hands over the method, and nothing builds up that a competitor would lack after reading it.
- **Moat:** open-banking feeds (Equifax Share by Bank, Experian, Yapily) could make the statement file itself go away.
- **Moat:** the only distribution channel is the owner's personal contacts.
- **Moat:** nobody has counted the market (number of UK non-bank SME lenders and brokers).
- **Offer:** the only urgency is the owner's week-6 clock, and there's no turnaround promise, so the speed lever is unused.
- **Product:** the saved `--out` memo has [Mn] tags but no metrics table, evidence ids or model/policy/code versions, so it can't answer "where did this figure come from" once the process exits (panel D7).
- **Product:** the draft prompt gives the model policy thresholds that the guard then flags as ungrounded, so with a real model analysts will learn to override the guard (panel D6).
- **Product:** the memo's format may not match the analyst's own template, so they'd have to re-key it.
- **Product:** the success measure has no time baseline.
- **Product:** it isn't decided whether v0.1 is for the analyst or for the repo reader.
- **Board-level (D7):** a sales outcome can kill this engineering bet, while its engineering fitness has no pass date.

## Conditions

- [ ] **evidence** — Get dated notes from at least three UK lenders or brokers. Ask "what is the worst part of spreading a statement today?" before pitching. If untraceable or wrong figures come up without prompting, keep traceability as the headline. If only speed comes up, lead with time to a draft memo. (D1, D6; moat, offer)
- [ ] **owner** — Take "lenders are getting burned" out of why-now until it is sourced. Replace it with nothing, or with the real number of pilots you can run. (offer)
- [ ] **research → evidence** — Write a side-by-side against Crediflow (and Arya.ai/Heron if possible), based on a demo or a conversation with a named customer. Show at least one thing a head of credit counts (hours, errors, audit findings) where v0.1 is many times better. (moat, offer)
- [ ] **owner** — Name the first segment (lender or broker), the pilot firm and one analyst who has said yes in principle, checked against the outreach log. Fix the date the week-6 clock starts. Name the one channel for reaching the next ten firms like it. (D5, D10; moat, product)
- [ ] **evidence** — Get three redacted samples or format descriptions and run each through `ingest` unchanged. Then either support that one format, or write "manual reshape by the owner" into the pilot protocol as a logged step. (D2; offer, product)
- [ ] **evidence + owner** — Redact one statement the way the lender would and list which metrics survive. Agree in writing who anonymises and converts the file, and the data-protection basis, before any file moves. Default: the owner takes on the effort, not the lender. (D3; offer, product)
- [ ] **panel** — Fix M10 with no debt service, M15 for card businesses and invoice-suffixed payers, and unidentified inflows being counted as revenue. Where a fix isn't possible, the memo says "not assessable" instead of quoting a number. If M10 can't be fixed within a week, kill criterion 1 applies. (panel D1, D4, D8; product)
- [ ] **panel** — Make the saved memo complete on its own: metrics table, evidence transaction ids per M-id, and model, policy and code versions. (panel D7; product)
- [ ] **panel → numbers** — Stop passing policy thresholds to the guard as numbers to check. Run the eval suite once with a real model, and publish the grounding rate and redraft count before any outreach. (panel D6; offer, product)
- [ ] **evidence** — Agree the baseline with the pilot lender: their analyst's existing spread of the same statement, a match tolerance, and the analyst's current time to a memo. (D4; offer, product)
- [ ] **owner** — Write the offer as one sentence: free or paid, what comes back, how fast. Claim only "every figure traces to a line in your statement, or we flag the one that doesn't", never that the figures are right. (offer)
- [ ] **owner** — Name one asset the pilot builds up that the public repo doesn't give away (labelled-spread dataset, design-partner agreement, policy-rule library) and make it a pilot deliverable. (moat)
- [ ] **owner** — Split the bet: give engineering fitness its own pass date, and move "no lender agrees by week 6" to lenders-will-pay. (D7; product)
- [ ] **research / numbers** — Count UK non-bank SME lenders and commercial-finance brokers (research), and cost the analyst's afternoon (numbers). (moat)

## Where the board disagrees

1. **Fund now or pass?** Moat votes PASS: deterministic metrics plus an approval stop is a design choice Crediflow, Arya or Heron could ship in one release, and the public repo shows them how. Offer and product vote FUND IF: the bet asks for one pilot, not a defensible business, and a guarantee the analyst can check is enough to start. The board hasn't settled whether moat is the right test for a 2-week, $20 bet. It is the right test for the business the bet sits inside.
2. **Is the public repo an asset or a liability?** Offer treats it as the basis of an honest guarantee: a deterministic pipeline the buyer's own analyst can verify. Moat treats it as giving the method away. Product says it splits the audience: the repo reader judges a portfolio piece, the analyst judges a memo, and v0.1 can't be tuned for both.
3. **What happens if traceability isn't the pain?** Offer says switch the headline to speed and keep going. Moat implies the bet loses its only claim to being 10x better. Speed is where Ocrolus is weak, but Crediflow already markets "underwriting in minutes". So a speed pivot moves the fight onto Crediflow's ground.
4. **What comes first?** Product says engineering first: find the file format, fix M10/M15/revenue, make the memo self-contained, because one wrong figure ends the analyst's trust. Offer says publish a measured grounding rate before outreach. Moat says interviews and the Crediflow comparison come first, because no engineering fixes a pain nobody has. With the week-6 clock running, these orders compete for the same weeks.
5. **Is the missing price data good or bad?** Offer reads "no public UK price" as an advantage: no list price undercuts a free pilot. Moat reads it alongside the uncounted market as a sign nobody knows if there is a business here.

## The strongest version

This isn't what the owner pitched. The pitch is an engineering-fitness bet with a public repo as proof against funded vendors. The strongest business the board can see is an **expert-led concierge pilot** that feeds a consulting practice:

- **Offer:** "Send your statement in whatever format your analysts already get. Under a written data-protection agreement we return a draft memo within 24 hours. Every figure traces to a line in your statement or is flagged, and it's reconciled against your analyst's own spread." The owner takes on the reshaping and redaction. That removes the effort lever from the lender and keeps the trace chain honest.
- **Headline:** whichever pain the three interviews actually name. Traceability is the guarantee. It becomes the headline only if a buyer names it unprompted.
- **What builds up:** each pilot produces a reconciled, analyst-labelled spread and a named design partner. The repo can't give those away, and they make the next pilot faster.
- **What the repo is for:** a credential for selling expertise in auditable LLM tooling for UK credit teams, not a product competing with Crediflow on features. That fits the six-month freelance plan this project exists for. It also resolves both the moat PASS and the repo-reader vs analyst split: the analyst is the customer, and the repo is the marketing.
- **Scope discipline:** split engineering fitness (M10 fix, real-model eval, self-contained memo, pass date) from demand (lenders-will-pay), so each can be killed on its own evidence.

## Memo · offer lens

Score: 4 | Vote: FUND IF
## What is strong
- **Cheap for the lender to say yes to.** The ask is one anonymised statement, with no full underwriting decision and no fee mentioned (bet.md:13, :37). The owner's appetite is 2w / $20 (bet.md:9), so asking costs the owner little too.
- **There is a guarantee the business can honestly make.** "No figure that is not in the metrics table" and "cites every figure to statement lines" (bet.md:37) follow from how a deterministic pipeline works, and the lender's own analyst can check them on their own statement. That is a real reversal of risk, unlike a vendor's "10X" claim (research.md:16).
- **The downside is capped by design.** The memo stops for their analyst to approve (bet.md:37). Research found no vendor that pairs deterministic metrics with an approval stop and a public repo (research.md:6). That is positioning, not proof anyone will pay.
- **No price anchor to beat.** The real incumbent price is the analyst's afternoon (bet.md:25, research.md:36). No competitor publishes a UK price (research.md:30-38), so a free pilot is not undercut by a visible list price.

## What would kill it
1. **The first sentence sells a pain nobody has voiced.** Why-now leads with lenders "getting burned by invented figures" (bet.md:29). Research found no lender complaint about that, only law-firm commentary (research.md:5, :46, :51). The problem quote itself is unevidenced (bet.md:42, research.md:54). The complaints buyers do voice are speed and price (research.md:42-43). *Test:* in the next three lender or broker conversations, ask "what is the worst part of spreading a statement today?" before pitching. If nobody names wrong or untraceable figures unprompted, the offer's headline is wrong.
2. **Effort, the weakest of the four levers, sits on the lender.** To take the pilot they must anonymise under a data-protection basis nobody has named (bet.md:44). They must also deliver the synthetic five-column CSV that v0.1 alone reads (bet.md:43, CAMT/MT940 a non-goal at :13), and give analyst time (bet.md:56). Their own redaction breaks M15 and the FPS CREDIT categoriser rules (bet.md:44), so the memo they judge may be degraded by work they did. *Test:* run D3. Redact one statement the way a lender would and count which metrics survive. Ask three contacts what file actually arrives (D2).
3. **Certainty is unproven, and the differentiator is not unique.** Crediflow already drafts cited memos into the lender's template, with named UK customers (research.md:3, :15). Arya.ai and Heron also name UK customers (research.md:16-17). The eval suite has not been run against a real model (bet.md:55), and a grounded number can still be wrong (bet.md:45). A lender already talking to Crediflow has no reason to switch to a v0.1. *Test:* publish the grounding rate and redraft count before outreach, and ask each prospect which of Crediflow, Arya or Heron they have seen.

Also against: the urgency is invented. The why-now is unsourced (research.md:51), and the only deadline, week 6 (bet.md:12), is the owner's clock, not the buyer's. Speed, the second lever, is not stated anywhere in the bet: there is no turnaround promise.

## Questions for the owner
- What is the pilot offer in the one sentence a head of credit reads first: free or paid, what comes back, and how fast (hours or days)?
- Who does the anonymisation and the conversion to CSV, and under what data-protection basis and agreement (D3)? Will you take the statement in the format they already receive (D2)?
- Do you pilot with lenders or brokers first (D10)? Effort and data protection differ between them.
- Is there a dated note of any lender or broker voicing the figure-traceability pain (D1)?
- Is there an existing analyst spread of the same statement to compare against, and what tolerance counts as a match (D4)?
- How many pilots can you actually service in the window? If the limit is real, it is the only honest scarcity on offer.

## Conditions
1. Base the offer's first sentence on a pain heard from at least one named lender or broker, with a dated note (D1). If the only voiced pain is speed, lead with time to a draft memo and measure that on the synthetic set.
2. Take the effort off the lender. Commit to accept a redacted sample in their native format, or to do the conversion yourself, and agree the data-protection basis in writing before any file moves (D2, D3). If v0.1 scope can't absorb this, say so in the offer.
3. Run the eval suite against a real model and publish the grounding rate and redraft count (bet.md:55) before outreach, so the guarantee rests on a measured number, not on the architecture.
4. Claim only the guarantee you can keep: "every figure in the memo traces to a line in your statement, or we flag the one that doesn't." Do not claim the figures are right. Agree the D4 baseline and tolerance with the pilot lender up front.
5. Remove "lenders are getting burned" from why-now until it is sourced (D6). Replace it with no urgency at all, or a stated real pilot capacity.

## Memo · moat lens

Score: 3 | Vote: PASS
## What is strong
- **The niche is narrow.** "UK non-bank SME lenders and commercial-finance brokers who spread twelve months of statements by hand" (bet.md:15) is a small enough market to own in principle. Nobody has counted how many such firms exist. The research step should get that number.
- **There is one gap in positioning.** Research found "no vendor that pairs deterministic metrics with a human-approval stop and a public repo" (research.md:6). That is the only claim to being different that the research supports.
- **The core problem is one the buyer cares about.** The bet's stated pain is traceability: "when compliance asks where a figure came from nobody can say" (bet.md:23). If that pain is real, it touches regulatory risk, and buyers tend to spend on that, not just on convenience. It is also the one area where the bet could become many times better rather than slightly better.
## What would kill it
1. **A funded incumbent already sells the differentiator.** Crediflow AI (Manchester, seed-funded) "fills the lender's own template with tables, charts and source traceability" and names UK customers AddWorking Capital and Iveco Capital (research.md:15). Research rates its traceability claim as "close" (research.md:6). If Crediflow's memo already cites figures to statement lines, v0.1 is slightly better at best, and slightly better loses. To test: get a Crediflow demo, or ask a named customer whether its memo figures link back to statement lines.
2. **Nothing stops a copy, and the public repo makes copying easier.** Deterministic metrics plus a human-approval stop is a design choice that Crediflow, Arya.ai or Heron could add in a release. Those three already have the parsing, categorisation and lender relationships (research.md:15-17). The bet names "the public repo is the proof" (bet.md:29), but that gives the method away. The bet names no data, switching cost, network, licence or exclusive relationship. To test: list what a competitor would still lack after reading the repo. If the list is empty, there is no moat.
3. **The secret has no evidence behind it.** The plan depends on "lenders are trialling LLM tools and getting burned by invented figures" (bet.md:29). Research found "No lender complains about invented figures"; only law firms raise it (research.md:5). The loudest real complaint is that Ocrolus is slow and pricey (research.md:5), which is a speed and cost problem, not a grounding one. The problem quote itself is unsourced (D1). To test: three lender or broker interviews asking "has an AI tool ever put a wrong figure in a memo, and what did it cost you?", with dated notes.

Further risks: open-banking feeds (Equifax Share by Bank, Experian, Yapily; research.md:24) could make the statement file itself disappear. Distribution so far is the owner's personal contacts, and no one has been named (D5).
## Questions for the owner
- What exactly does Crediflow's "source traceability" do, and what can your memo do that theirs cannot? Answer in terms a head of credit would count: hours saved, errors avoided, findings at a compliance audit.
- Which segment do you start with, lenders or brokers (D10)? Heron has a broker suite, and DocuClipper serves small brokers from $20/month (research.md:4), so neither segment is empty. Where is there no competitor today?
- How will the first hundred customers hear of this, and does that channel get cheaper as you grow? Today the only named channel is BridgeTrade broker conversations (bet.md:23).
- Once the repo is public, what will a customer have with you after a year that they would lose by switching? Examples: their own analyst-labelled spreads, a policy-rule library tuned to them, an FCA-facing audit trail.
- Is the business software revenue or expert consulting with the repo as a calling card? The moat answer differs for each, and this bet does not say which.
- How many UK non-bank SME lenders and commercial-finance brokers exist, and what does an analyst's afternoon of spreading cost (research.md:36)? Nobody has sourced either number. Research should get the count; the numbers step should get the cost.
## Conditions
These are what would turn this into FUND IF:
1. Produce dated notes from at least three UK lenders or brokers, each stating that untraceable or invented figures are a problem they would pay to remove. This settles D1 and D6.
2. Write a side-by-side comparison against Crediflow (and Arya.ai if you can), based on a demo or a customer conversation. It should show at least one thing a buyer counts on which v0.1 is many times better, not slightly better.
3. Name one advantage that builds up over time and that publishing the repo does not give away, such as an exclusive data agreement, a labelled-spread dataset from the pilot lender, or a named design partner. Make it a deliverable of the pilot.
4. Name the first segment and the firm that will run the pilot (D5, D10). Write down the one channel you will use to reach the next ten firms like it.

## Memo · product lens

Score: 4 | Vote: FUND IF
## What is strong
- **The job is clear and the person feels it.** The bet's Problem line names the moment it hurts: an afternoon of spreading, then a figure that's wrong, then nobody can answer compliance. The Expected outcome turns that into three checks the person can see: no figure missing from the table, every figure cited, an analyst approves (bet, Expected outcome).
- **It says what it won't do.** The non-goals are written down: no full underwriting decision, no real customer data, no CAMT/MT940 in v0.1. It is a drafting tool with a human stop, not a credit decision engine.
- **The analyst stays in charge.** The human-approval stop matches how a head of credit already works. The research found no vendor that pairs deterministic metrics with a human-approval stop, though Crediflow's source-traceability claim is close (research, Gap).
- **The plumbing improved quickly.** In run 02, "every figure traceable" went from 0/6 to 6/6 passing, and 94 tests now run (panel-02, before/after table). What is left is about the credit judgement (panel D1, D4, D8–D10), not basic correctness.

## What would kill it
1. **The lender's file can't go in.** v0.1 reads only a synthetic five-column CSV (bet D2). Normal UK bank exports are refused: DD/MM dates, newest-first order, a BOM, or separate paid-in and paid-out columns (panel D13). So someone, probably the owner, has to reshape the file by hand before the traced chain begins. That step is unrecorded and untraced, it eats the afternoon the tool claims to save, and the bet doesn't list it as a non-goal. *Test:* get redacted samples or format descriptions from three contacts, and run each one through `ingest` unchanged.
2. **The analyst's first read shows a cited figure that's wrong.** A loss-making business with no existing loans gets "debt service cover 99.00" and passes the cash rule (panel D1, the blocker). A card-settled café shows "0.0% of inflows" from its top payer (panel D8). Owner money paid back in counts as revenue, which lifts DSCR (cash cover of loan repayments) from 1.43 to 6.65 (panel D4). Each of these passes "zero ungrounded numbers", yet the analyst would say the outcome doesn't match their view, and the bet's second outcome fails (bet D4). One such figure is enough for an analyst to stop trusting the memo. *Test:* set the v0.1 metrics beside the analyst's own spread of the same statement, with an agreed tolerance.
3. **Anonymising the statement breaks the product, or not anonymising it breaks a non-goal.** Removing payer names breaks M15 and the FPS CREDIT categoriser rules. A sole trader's statement is still personal data with the name removed (bet D3), and "real customer data" is a non-goal. Either the pilot runs on damaged data or it crosses its own line. *Test:* redact one statement the way the lender would, and list which metrics still come out.
4. **The saved memo can't answer "where did this figure come from".** The `--out` file has [Mn] tags but no metrics table, no evidence ids, and no model or policy version. The in-memory state is lost when the process exits (panel D7). That is the exact question in the bet's problem quote.
5. **Analysts learn to override the guard.** The draft prompt gives the model the policy thresholds, and the guard then flags those thresholds as ungrounded. Memos written by a real model will regularly reach the analyst marked ungrounded (panel D6). So far only FakeLLM has been evaluated, so this hasn't been seen yet.

## Questions for the owner
- **Who is the user, by name?** Neither of you does credit analysis every week, and no stand-in user is named (bet D5). Who will sit with the first memo, and has anyone agreed in principle? Check the outreach log, not memory.
- **Who is v0.1 really for: the lender's analyst, or the person reading the public repo?** "Why now" says "the public repo is the proof". The pilot is judged from the analyst's side, and a portfolio piece from the reader's side. Pick one for this bet.
- **Lender or broker first (bet D10)?** They get different files and write different memos. Heron sells a separate broker suite.
- **Does the analyst need the memo in their own template?** Crediflow fills the lender's own template (research, Direct competitors). If v0.1's format means re-keying into theirs, the afternoon isn't saved.
- **Where is the analyst's time saving in the success measure?** The problem is "takes an afternoon", but nothing in Expected outcome measures time. How long will the analyst's spread take compared with v0.1 plus review? Nobody has a baseline yet; it has to come from the pilot lender (bet D4).
- **Is the problem quote real (bet D1)?** It comes from BridgeTrade conversations, but lenders-will-pay/bet.md:23 calls it an assumption. Produce dated notes or run one new interview.
- **Does the engineering bet need its own pass date (bet D7)?** At the moment only a sales outcome (week 6) can kill it.

## Conditions
1. **Find out what file arrives before writing any code.** Get three redacted samples or descriptions (bet D2). Then either support that one format in v0.1, or record "manual reshape by the owner" as a written, logged step in the pilot protocol, so the trace chain is honest about where it starts.
2. **Make the saved memo complete on its own.** It must hold the metrics table, the evidence transaction ids for each M-id, and the model, policy and code versions. That way "where did this figure come from" can be answered from the file alone (panel D7).
3. **Fix the figures an analyst would spot in the first minute.** That means M10 with no debt service, M15 for card businesses and invoice-suffixed payers, and inflows the categoriser can't identify being counted as revenue (panel D1, D8, D4). Either fix them, or have the memo say "not assessable" instead of quoting a number. If the M10 blocker can't be fixed in a week, the first kill criterion applies.
4. **Run one redacted statement through before signing up a pilot (bet D3).** Write down which metrics survive, and the data-protection basis for receiving the statement.
5. **Name the analyst and get the comparison material.** Secure a yes in principle from one named analyst, plus their existing spread of the same statement and an agreed match tolerance (bet D4, D5). Add their time-to-memo as a baseline.
6. **Run the eval suite once with a real model, and stop sending thresholds to the guard as numbers to check (panel D6).** Publish the grounding rate and the number of redrafts, as the bet's evidence tasks require.
