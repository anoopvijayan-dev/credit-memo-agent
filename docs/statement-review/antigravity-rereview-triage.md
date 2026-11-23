# statement-review: Antigravity re-review triage

Written 2026-11-23, after the bet merged into `main` (cb7d2af, PR #7).

## What this is

While the Codex reviewer was at its usage limit, tasks T04 to T37 were reviewed only by Claude, the same model family that wrote them. Antigravity (Google's model) then re-reviewed them read-only as a second family. Each review read the task's merged diff, the requirements it lists, and the code. This file lists every blocker and major finding, each checked against the code, and what was done about it.

- **Re-reviewed:** 24 tasks: T04–T22, T24, T27, T28, T32 and T43.
- **Not re-reviewed:** T25, T26, T29–T31, T33–T37, T39 and T41. Antigravity's quota ran out on them, and the owner chose to merge without waiting. They had Claude's review only.
- **Reviewed by Antigravity directly through Bridgie:** T38 and T40.
- **Fixes:** every finding marked "fixed" has a regression test that fails on the old code. Every fix is on `main`.
- **Owner decisions:** findings marked "noted for owner" are open questions. The others marked "noted" are outside the requirements' guarantees, or too narrow to change now.

## Findings

| Task | Sev | Finding | Verdict | Action |
|---|---|---|---|---|
| T04 | blocker | cash_heavy_month: pence × Decimal threshold rounds at 28 digits | real (needs a 29-digit threshold) | fixed (Fraction), test (435db39) |
| T04 | blocker | cash_round_trip: same | real | fixed, test (435db39) |
| T04 | major | cma run: --out checked before review, replaced after; another run's memo overwritten | real | fixed (hard-link place, refuses), test (435db39) |
| T04 | major | bank CSV: field-too-long refused before the £10tn limit is reported | correct, both refuse; only the message differs | noted, not changed |
| T08 | major ×2 | cash_heavy_month / cash_round_trip rounding | same as T04 | already fixed |
| T08 | major | gambling indicator ignores the shipped operator list for its illustrative label | real (R14) | fixed, test (307c88e) |
| T08 | major | cash round trip found only when the withdrawal matched the cash_withdrawal rule (not OWNER DRAWINGS ATM, not after an override) | design limitation | noted for owner |
| T09 | major | judgement queue mixes bare ids and "account:id"; a line id that itself contains "savings:S2" can collide | real but contrived (ids with colons) | noted for owner |
| T17 | blocker | a resume after a draft made under the offline lock was treated as unlocked once firm.yaml was unlocked | real (R29 rev 32) | fixed, tests (eebb2ea) |
| T17 | major | MCP: _BUILDING_FOR_RUN held during serve(); a child task calling sse_app() under the lock gets an app | contrived (needs code inside the serving task) | noted |
| T17 | major | a new run on a resumed thread kept the last resume's lock in its evidence | real | fixed, test (eebb2ea) |
| T12 | major ×3 | case log reports under lock files swapped mid-read, Windows rename retries, finalisers raising inside a save | outside R42's guarantees (revision 23 scope) | noted |
| T13 | major | proposed repayment "1500,50" had its comma stripped: stored £150,050.00 (100×), feeds M28 | real, money | fixed (comma only groups thousands), tests (e3065ff) |
| T13 | major | an unquoted YAML amount read as a float: fractional pence rounded away | real | fixed (profile loader reads decimals as typed), tests (e3065ff) |
| T13 | major | update_profile skips logging when case.yaml already holds the new profile after a crash or an unconfirmed save | real, needs a crash or SaveNotConfirmed plus repair | noted for owner |
| T13 | major | undo runs after the case lock was lost and can overwrite the next writer's file | outside R42's guarantees (a lock file removed mid-save) | noted |
| T14 | major | sync-folder warning (R32): OneDrive variables looked up case-sensitively; on Windows they are upper case, so never matched | real (Windows) | fixed, tests |
| T14 | major | a sync root reached through a symlink was not resolved before matching | real | fixed, test |
| T15 | blocker | Windows: purge reads the ledger through a second handle while holding its mandatory lock; every purge after the first fails | real (Windows) | fixed, test |
| T15 | blocker | two purges racing: the second recreated cases/<id> holding only its lock files | real | fixed, test |
| T15 | major | a symlinked cases/<id> or .purging-<id> was followed, deleting files outside the case store | real (data safety) | fixed, tests |
| T15 | major | a short write to the ledger left a torn line | real | fixed, test |
| T15 | major | two purges deleting .purging-<id> at once: the loser reports a failure | real, harmless (purge again finishes) | noted |
| T15 | major | Windows: the case lock is released before the folder is renamed out of use | real (Windows), narrow window | noted for owner |
| T15 | major | race tests did not exercise the real paths | fair | new tests reproduce both races |
| T16 | blocker | add_override looked a line up without own-account pairing: a mis-paired Stripe payout could not be overridden back to card receipts (no-op), and an override to own_account_transfer unpaired its partner | real, money | fixed, tests (8d40406) |
| T16 | major | cma case figures: a ReconciliationError, StatementError or AmountError reached the user as a traceback | real | fixed (exit 6 / exit 2, as cma run), test |
| T16 | major | cma case figures lists nothing for a stale override whose account's statement file is missing | real, needs a statement file deleted by hand | noted |
| T16 | major | P3 tests compare the same cached computation with itself | fair | covered by T38's P13 tests on generated packs and T16's one-at-a-time test |
| T18 | — | pass (3 minor) | — | — |
| T28 | major | bank CSV and Monzo refusals quoted repeated header cells; in a headerless file that is a counterparty's name | real (privacy) | fixed (positions on line 1), test |
| T28 | major | a refusal after a description with an embedded line break names the wrong source line | real, cosmetic | noted |
| T19 | major | combined figures refused, yet each account's figures stayed in /api/case and the page data | real (R3) | fixed, test |
| T19 | major | a profile change between reading case.yaml and computing figures can show a profile that disagrees with the figures | real, narrow (a concurrent profile save during a page load) | noted |
| T05 | major | under v1, CAPITAL INJECTION / CAPITAL CONTRIBUTION (v2's own wording) were not protected from a profile name | real | fixed, tests |
| T05 | major | a repayment such as 1e999999999 raised decimal.Overflow; YAML float rounding | real; float part already fixed (e3065ff) | fixed (limit checked before arithmetic), tests |
| T05 | major | profile read twice during a page load | same as T19 | noted |
| T32, T20, T43, T21 | — | pass (minor only) | — | — |
| T22 | blocker | add_override ignores own-account pairing | same as T16's | already fixed (8d40406) |
| T22 | major | _BARE cache key reads firm_rules separately from the computation; a promotion on another thread mid-request can cache marks under the new hash | real, narrow (concurrent promotion during a page load) | noted |
| T10 | major | evals compared money with abs(a-b) < 0.01 in floats, passing some 1p differences | real | fixed (to the penny), test |
| T11 | major | resume refuses an earlier release's review whose stored money figure carries float drift | by design (T04 review 59: a band cannot be found in fractions of a penny; refusing is the safe answer) | noted for owner |
| T11 | major | the R53 rev 28 test passes for the wrong reason | fair | noted |
| T24, T27 | major | a preview or sign-off can bind a firm_rules_sha256 that a concurrent promotion in another case changed mid-build (the pack reads firm_rules.yaml twice without the firm-rules lock) | real, narrow (another case promotes during this case's preview) | noted for owner |
| T24 | major | the grounding guard's 1e-9 slack is below float rounding for an M4 of about 200,000,000% | real, needs an absurd revenue trend | noted |
| T27 | major ×2 | a sign-off whose signed_off entry failed after pack_written can be completed after the inputs changed, and the workspace says "nothing was recorded" then | real, needs a disk failure between the two appends | noted for owner |

## Also open for the owner

- The older `cma run` memo still stamps "APPROVED by …".
- A firm rule can still reclassify some wordings no built-in pattern recognises: "SHARE CAPITAL", HMRC corporation-tax and self-assessment debits, "UNPAID CHEQUE" and "BOUNCED DD".
- In the T41 comparison, ratios and shares show the tool's raw value (for example 1.7543859649122806x), not its displayed value (1.75x).
- Bridgie's two-approver accept step was not run. The owner merged directly.
