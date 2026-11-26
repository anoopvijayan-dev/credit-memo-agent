# statement-review: Codex re-review triage

Written 2026-11-26. Twelve tasks had only ever been reviewed by Claude, the family that wrote
them: T25, T26, T29–T31, T33–T37, T39 and T41. Codex (OpenAI) re-reviewed each read-only
against `main` at 5b881cf. It failed all twelve: 2 blockers, 53 majors and 19 minors. The
blockers and every finding that can move a money figure were checked against the code; the
rest are listed as not yet checked.

## Fixed

| Task | Finding | Fix |
|---|---|---|
| T34, T35 | A card, gateway, marketplace or delivery platform payout could be paired with an unrelated debit of the same amount as an own-account transfer, removing it from trading receipts | Owner decision: a payout is never paired. Tests: a £100 Stripe payout beside a £100 payment out stays in trading receipts; five payout wordings are never paired |
| T31 | A blank cell in a mapped balance column was filled in from the line before, though the five-column loader needs a balance on every line (R38) | Refused, naming the line, with the way round it (no balance column and `--opening-balance`) |
| T31 | A month-name date format read a numeric month, so a format listed earlier could win over the one that matches | A month-name format reads only a cell holding a month name |
| T25, T26 | A pseudonymised copy kept an id such as `=1` as written, which a spreadsheet opens as a formula | Refused: the copy keeps ids as written, so it cannot make such an id safe |

## Real, narrow: not changed

- T36 (blocker as reported): under the frozen v1 ruleset only, `CAPITAL-INJECTED` with a hyphen is matched by v1's capital pattern but not protected from a firm rule. v2, the default, is not affected.
- T29 (blocker as reported): in an Open Banking import, two lines at the same timestamp whose balances chain in either order leave the opening and closing balance to the file's order.

## Design, or already open

- T33: combined money in (M17) counts the credit half of an own-account transfer, as R1 asks; own-account transfers in (M19) is reported beside it.
- T34: a credit that two debits could pair with is left unpaired and listed for a person, on purpose (R9).
- T26: payer-written wording such as REFUND sets the category: that is how the rule table works.
- T25, T26, T34: the firm-rules hash a sign-off binds (already open for the owner); T34: judgement-queue ids that mix local and cross-account forms (already open, T09).

## Not yet checked

About 35 findings, mostly concurrency, workspace-page and pseudonymisation details. Each is in the Codex replies kept with the session; none was found to move a money figure in the sample checked.
