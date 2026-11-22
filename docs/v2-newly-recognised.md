# Lines v2 newly recognises

v2 is the default categorisation table (`CMA_RULESET` unset); `CMA_RULESET=v1` brings
back the original, frozen table. R10 asks the change to list every line a new rule now
recognises. These are every line in the committed corpus (the synthetic cases, the
committed example, the test fixtures and the single lines in the P7 golden,
`tests/golden/p7_v1_baseline.json.gz`) whose category v2 gives differently from v1. The
test `test_p7_v2_changes_only_the_listed_lines_and_m1_m16_only_where_listed`
(tests/test_rules_v2.py) recomputes the difference and checks it is exactly this list, and
`tests/test_v2_default.py` checks this page against it.

Every one of them is non-trading under both tables, so M1 to M16 keep their ids, units,
values and evidence on every committed case and fixture (P7). The new figures move: in the
committed example (`examples/cafe_stretched.*`), `FPS CREDIT DIRECTOR CAPITAL INTRODUCED`
(£8,000.00) leaves unexplained money in (M25) for director and shareholder funding (M20),
so M26 goes from 2.5% to 0.0%. In the generator's truth files that line is now
`director_funding`; the statements themselves are byte-identical.

| description | direction | v1 category | v2 category |
|---|---|---|---|
| `BACS CREDIT HMRC VAT REPAYMENT` | in | other | hmrc_repayment |
| `FPS CREDIT A DIRECTOR LOAN ACCOUNT` | in | loan_drawdown | director_funding |
| `FPS CREDIT CAPITAL INJECTION` | in | other | director_funding |
| `FPS CREDIT DIRECTOR CAPITAL INJECTION` | in | other | director_funding |
| `FPS CREDIT DIRECTOR CAPITAL INTRODUCED` | in | other | director_funding |
| `FPS CREDIT J SMITH CAPITAL INTRODUCED` | in | other | director_funding |
| `HMRC VAT REPAYMENT` | in | other | hmrc_repayment |
| `POT TRANSFER OUT HOLIDAY POT` | out | supplier | own_account_transfer |

A line a case profile's name or an own-account pairing decides is new behaviour under
either table (R10, R54), so it is not in this list: no committed case carries a profile.
