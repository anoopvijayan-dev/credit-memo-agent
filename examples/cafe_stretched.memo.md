## Business overview
The statements cover 12 [M1] months. Total revenue over the period was £313,135 [M2], averaging £26,095 [M3] a month.

## Revenue
Revenue moved 10.8% [M4], comparing the latest quarter with the earliest. The largest single payer accounted for n/a [M15] of transfer revenue. Unclassified money in was 0.0% [M16] of inflows.

## Cash position
The lowest balance reached was -£19,760 [M5], and the account closed overdrawn on 222 [M6] days. Net operating cash flow was £54,192 [M9].

## Obligations and conduct
Loan repayments totalled £13,800 [M8] and owner drawings £30,000 [M11], giving debt service cover after drawings of 1.75x [M10]. Returned direct debits in the period: 1 [M7]. VAT paid was £36,158 [M12] with late or missed payments of 0 [M13]; PAYE paid was £19,114 [M14].

## Recommendation
Policy outcome: refer. The case needs an analyst decision before any offer: R3 (limit 2 [R3]) is refer; R4 (limit 15 [R4]) is refer.


---
APPROVED by A. Analyst (automated, no interactive review) at 2026-11-08 11:18 UTC. Notes: Approved by script (--yes); the draft was not reviewed interactively.
Grounding check: passed (17 numbers checked, 0 unmatched, 0 missing citations).

## Appendix: the figures behind this memo

Statement: cafe_stretched.csv, 388 transactions, 2025-10-01 to 2026-09-30, sha256 6d523309b1856a19
Model: fake:offline-template. Policy: uk_sme_default, sha256 85cb9bd701364c09, overall refer. VAT stagger: 3.
Evidence: every transaction id behind each figure is in the .evidence.json file written beside this memo.

| id | metric | value | method | lines |
|---|---|---|---|---|
| M1 | Months covered | 12 | Calendar months from 2025-10-01 to 2026-09-30; a first or last month counts only if 15+ days of it are inside the period. | 2 |
| M2 | Total revenue | £313,135 | Card and gateway settlements, plus transfers in that carry an invoice reference. | 261 |
| M3 | Average monthly revenue | £26,095 | Total revenue divided by months covered (months with no revenue still count). | 261 |
| M4 | Revenue trend | 10.8% | Average monthly revenue of the last three covered months versus the first three, as a percentage. | 132 |
| M5 | Lowest balance | -£19,760 | Lowest running balance after any transaction in the period. | 1 |
| M6 | Days overdrawn | 222 | Calendar days that closed below zero, carrying the balance over days with no transactions. | 174 |
| M7 | Returned direct debits | 1 | Lines marked as unpaid direct debits. | 1 |
| M8 | Debt service | £13,800 | Loan and finance repayments, net of any that were returned unpaid. | 12 |
| M9 | Net operating cash flow | £54,192 | All money in minus all money out, excluding loan repayments, drawdowns and owner drawings. | 364 |
| M10 | Debt service cover | 1.75x | Net operating cash flow after owner drawings, divided by debt service. | 388 |
| M11 | Owner drawings | £30,000 | Transfers to the owner or directors and dividends. | 12 |
| M12 | VAT paid | £36,158 | Payments to HMRC VAT, net of any returned unpaid. | 4 |
| M13 | Late or missed VAT payments | 0 | Quarters (stagger 3) with no VAT payment between the quarter end and 3 working days after the due date. | 4 |
| M14 | PAYE paid | £19,114 | Payments to HMRC PAYE/NI, net of any returned unpaid. | 12 |
| M15 | Top customer share of transfer revenue | n/a | No customer transfers: revenue is card or gateway settled, which hides who the customers are. | 0 |
| M16 | Unclassified inflows | 0.0% | Share of money in (excluding loan drawdowns and returned DDs) that is neither revenue nor known financing. | 0 |

| id | rule | metric | requirement | outcome | detail |
|---|---|---|---|---|---|
| R1 | At least 12 months of statements | M1 | >= 12 | pass | Months covered is 12; rule requires >= 12. |
| R2 | Debt service cover, after owner drawings, of at least 1.25x | M10 | >= 1.25x; referred below 1.5x | pass | Debt service cover is 1.75x; rule requires >= 1.25. |
| R3 | No more than 2 returned direct debits in the period | M7 | <= 2; referred above 0 | refer | Returned direct debits is 1; rule requires <= 2, referred above 0. |
| R4 | Fewer than 15 days overdrawn | M6 | < 15 | refer | Days overdrawn is 222; rule requires < 15. |
| R5 | No late or missed VAT payments | M13 | == 0 | pass | Late or missed VAT payments is 0; rule requires == 0. |
| R6 | Revenue not declining by more than 15% | M4 | >= -15% | pass | Revenue trend is 10.8%; rule requires >= -15. |
| R7 | Top customer below 60% of transfer revenue | M15 | < 60% | pass | Top customer share of transfer revenue cannot be computed (No customer transfers: revenue is card or gateway settled, which hides who the customers are.); policy says pass when missing. |
| R8 | Positive net operating cash flow over the period | M9 | > £0 | pass | Net operating cash flow is £54,192; rule requires > 0. |
| R9 | No more than 20% of money in left unexplained | M16 | <= 20% | pass | Unclassified inflows is 0.0%; rule requires <= 20. |