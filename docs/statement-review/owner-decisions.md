# statement-review: owner decisions

Every item the Antigravity and Codex re-reviews left open for the owner, with the decision taken.
Under the owner's standing instruction of 2026-11-19, each takes the recommended option unless
the owner chose otherwise; the owner's own calls are marked as such.

| Item | Decision | Done |
|---|---|---|
| A card, gateway, marketplace or delivery platform payout could be paired as an own-account transfer (Codex, T34, T35) | Never paired: a payout is trading income. *Owner's own call, 2026-11-25.* | Fixed, tests |
| A business of several accounts had each account judged by the policy check alone, so a reserve account "failed" | Judged once, as a whole, on the combined figures; each memo section opens with all accounts together | Fixed, tests |
| The `cma run` memo stamps "APPROVED by …" | Kept, with the sentence that it approves the memo's figures and wording, not the lending decision | Fixed, example regenerated |
| A firm rule could reclassify SHARE CAPITAL, UNPAID CHEQUE and BOUNCED DD, which no built-in pattern recognised | Recognised and protected: share capital as director and shareholder funding, an unpaid cheque or bounced direct debit as a returned payment | Fixed, tests |
| HMRC corporation-tax and self-assessment debits | Left as they are: protecting them needs a category of their own, which changes the figures and the evidence schema; a decision for the next bet | Open, recorded |
| The T41 comparison showed ratios and shares at full precision (1.7543859649122806x) | Shown as the memo shows them (1.75x), the difference at the same precision; the exact values stay in the response | Fixed, tests |
| v1 missed hyphenated capital wording such as CAPITAL-INJECTED (Codex, T36) | Protected, with any separator | Fixed, tests |
| Bridgie's two-approver accept step was not run before the merge | Recorded as skipped by the owner; the next bet runs it | Recorded |
| T08: a cash round trip is found only when the withdrawal matches the cash-withdrawal rule | Accepted limitation: an override puts the line right, and the indicator is a prompt, not a figure | Accepted |
| T09, T34: judgement-queue ids mix bare ids and "account:id", so an id holding a colon could collide | Accepted limitation: line ids from every importer hold no colon; a firm's own five-column file is the only route | Accepted |
| T13: a profile change can go unlogged after a crash or an unconfirmed save | Accepted limitation: `cma case check` and `cma case repair` show and settle the log | Accepted |
| T15: on Windows the case lock is released before the folder is renamed out of use during a purge | Accepted limitation: a narrow window, and running the purge again finishes it | Accepted |
| T11: a resume refuses an earlier release's review whose stored money figure carries float drift | Kept: refusing is the safe answer, as T04's review chose | Kept by design |
| T24, T27: a preview or sign-off can bind a firm-rules hash that a promotion in another case changed mid-build | Accepted limitation: it needs two cases worked at the same moment; a sign-off is refused once the inputs differ from the preview | Accepted |
| T27: a sign-off whose second log entry failed after the pack was written | Accepted limitation: it needs a disk failure between two appends; `cma case repair` settles it | Accepted |
| T29: Open Banking lines at the same timestamp whose balances chain in either order leave the opening and closing balance to the file's order | Accepted limitation: it needs two same-second lines that chain both ways; pass `--statement-balance` to fix the order | Accepted |
