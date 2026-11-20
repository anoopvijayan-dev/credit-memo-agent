## Business overview
The statements cover 12 [M1] months. Total revenue over the period was £313,135 [M2], averaging £26,112 [M3] a month.

## Revenue
Revenue moved 10.8% [M4], comparing the latest quarter with the earliest. The largest single payer accounted for n/a [M15] of transfer revenue. Unclassified money in was 2.5% [M16] of inflows.

## Cash position
The lowest balance reached was -£19,760 [M5], and the account closed overdrawn on 165 [M6] days. Net operating cash flow was £54,192 [M9].

## Obligations and conduct
Loan repayments totalled £13,800 [M8] and owner drawings £30,000 [M11], giving debt service cover after drawings of 1.75x [M10]. Returned direct debits in the period: 1 [M7]. VAT paid was £36,158 [M12] with late or missed payments of 0 [M13]; PAYE paid was £19,114 [M14].

## Recommendation
Policy outcome: refer. The case needs an analyst decision before any offer: R3 (limit 2 [R3]) is refer; R4 (limit 15 [R4]) is refer.


---
APPROVED by A. Analyst (automated, no interactive review) at 2026-11-20 14:06 UTC. Notes: Approved by script (--yes); the draft was not reviewed interactively.
Grounding check: passed (17 numbers checked, 0 unmatched, 0 missing citations).

## Appendix: the figures behind this memo

Statement: cafe_stretched.csv, 389 transactions, 2025-10-01 to 2026-09-30, sha256 796b4519fc7a592e
Model: fake:offline-template. Policy: uk_sme_default, sha256 334ca78d52759938, overall refer. VAT stagger: 3.
Evidence: every transaction id behind each figure is in the .evidence.json file written beside this memo.

| id | metric | value | method | lines |
|---|---|---|---|---|
| M1 | Months covered | 12 | Days from the first transaction to the last, in months, to the nearest month. | 2 |
| M2 | Total revenue | £313,135.22 | Card and gateway settlements, transfers in that carry an invoice reference, and cash paid in. | 261 |
| M3 | Average monthly revenue | £26,112.47 | Total revenue divided by the exact length of the period in months (quiet months still count). | 261 |
| M4 | Revenue trend | 10.8% | Average revenue of the latest quarter of whole calendar months against the earliest quarter, as a percentage change. On a year-long statement these are opposite seasons. | 132 |
| M5 | Lowest balance | -£19,759.76 | Lowest running balance after any transaction in the period. | 1 |
| M6 | Days overdrawn | 165 | Calendar days that closed overdrawn, carrying the balance over days with no transactions. | 130 |
| M7 | Returned direct debits | 1 | Lines marked as unpaid direct debits. | 1 |
| M8 | Debt service | £13,800.00 | Loan and finance repayments, net of any that were returned unpaid. | 12 |
| M9 | Net operating cash flow | £54,192.22 | Revenue in, minus all money out, excluding loans and drawings. Money in that is not explained as trading is left out, except that money sent back by someone the business paid by transfer nets off what it sent them. | 364 |
| M10 | Debt service cover | 1.75x | Historical cover of existing debt only: net operating cash flow after owner drawings, divided by debt service. It does not include the facility being applied for. | 388 |
| M11 | Owner drawings | £30,000.00 | Transfers to the owner or directors and dividends. | 12 |
| M12 | VAT paid | £36,157.63 | Payments to HMRC VAT, net of any returned unpaid. | 4 |
| M13 | Late or missed VAT payments | 0 | VAT quarters in the business's stagger group with no payment that stuck (not returned unpaid) between the quarter end and the Direct Debit collection days after the due date. | 4 |
| M14 | PAYE paid | £19,113.75 | Payments to HMRC PAYE/NI, net of any returned unpaid. | 12 |
| M15 | Top customer share of transfer revenue | n/a | No customer transfers: revenue is card or gateway settled, which hides who the customers are. | 0 |
| M16 | Non-trading money in (share) | 2.5% | Share of money in, less lending drawdowns, returned direct debits and own-account transfers, that is not trading receipts; it is left out of operating cash flow. | 262 |
| M17 | Total money in | £321,325.13 | Every credit in the period; the money-in buckets (M2, M19-M25) add up to it. | 263 |
| M18 | Cash deposits within trading receipts | £0.00 | Cash paid in at a counter or machine. Part of M2, not added to it again. | 0 |
| M19 | Own-account transfers in | £0.00 | Credits moved in from the applicant's own pots, savings or accounts. | 0 |
| M20 | Director and shareholder funding | £0.00 | Credits from directors or shareholders, and capital introduced. | 0 |
| M21 | Lending drawdowns | £0.00 | Loans and merchant cash advances paid in. | 0 |
| M22 | Refunds and returned payments in | £189.91 | Refunds, reversals and direct debits returned unpaid. | 1 |
| M23 | HMRC repayments | £0.00 | Credits from HMRC. | 0 |
| M24 | Other explained money in | £0.00 | Grants, insurance claims, interest and cashback. | 0 |
| M25 | Unexplained money in | £8,000.00 | Credits no rule, firm rule or override explains, and credits whose wording is ambiguous. | 1 |
| M26 | Unexplained share of money in | 2.5% | Unexplained money in (M25) as a share of money in less lending drawdowns, returned direct debits and own-account transfers, the denominator M16 uses. | 262 |

| id | rule | metric | requirement | outcome | detail |
|---|---|---|---|---|---|
| R1 | At least 12 months of statements | M1 | >= 12 | pass | Months covered is 12; rule requires >= 12. |
| R2 | Debt service cover, after owner drawings, of at least 1.25x | M10 | >= 1.25x; referred below 1.50x | pass | Debt service cover is 1.75x; rule requires >= 1.25. |
| R3 | No more than 2 returned direct debits in the period | M7 | <= 2; referred above 0 | refer | Returned direct debits is 1; rule requires <= 2, referred above 0. |
| R4 | Fewer than 15 days overdrawn | M6 | < 15 | refer | Days overdrawn is 165; rule requires < 15. |
| R5 | No late or missed VAT payments | M13 | == 0 | pass | Late or missed VAT payments is 0; rule requires == 0. |
| R6 | Revenue trend no worse than -15% | M4 | >= -15.0% | pass | Revenue trend is 10.8%; rule requires >= -15. |
| R7 | Top customer below 60% of transfer revenue | M15 | < 60.0% | pass | Top customer share of transfer revenue cannot be computed (No customer transfers: revenue is card or gateway settled, which hides who the customers are.); policy says pass when missing. |
| R8 | Positive net operating cash flow over the period | M9 | > £0 | pass | Net operating cash flow is £54,192; rule requires > 0. |
| R9 | No more than 20% of money in left unexplained | M16 | <= 20.0% | pass | Non-trading money in (share) is 2.5%; rule requires <= 20. |