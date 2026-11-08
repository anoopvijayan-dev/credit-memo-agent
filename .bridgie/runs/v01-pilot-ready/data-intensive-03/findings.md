# data-intensive seat, panel run 03 (fix/v01-panel-c1-c6 @ 542e55b)

**Verdict: FAIL.** 1 blocker, 7 major, 4 minor in the money path. Claims D4, D11, D16/D17 and the Monzo importer refuted. pytest 174 passed.

(Saved by the orchestrator from the seat's hand-back; the harness refused the seat's own write of this file.)

## Blocker
- **src/cma/metrics.py:174** — D4 does not reach cover: OTHER credits (director capital injection, savings, HMRC refund) stay in `operating`. One £20k "DIRECTOR CAPITAL INJECTION": DSCR 0.57x → 1.68x, overall FAIL → PASS; M16 7.7% (p3.py A/B).

## Major
- **src/cma/metrics.py:240** — M3 overstated (+8.4%): boundary-month revenue kept, divisor is covered months (p1_m3.py).
- **src/cma/metrics.py:213** — M13 treats a VAT DD returned unpaid (never re-presented) as on time; R5 pass (p3.py C).
- **src/cma/graph.py:222** (load :193) — reused thread inherits redrafts=2, previous guard feedback (another statement's figures in the first prompt), stale review and final_memo (p6_thread.py).
- **src/cma/cli.py:164** — rejected run overwrites NAME.evidence.json beside an approved NAME.md (p5_cli.py).
- **src/cma/guard.py:55** — date exemption accepts any 1–2 digit number before a month word incl. "may" and days > 31: "A further 40 may be drawn" grounded.
- **src/cma/importers/monzo.py:141** — Notes/Description dropped; every inflow OTHER; M2 0, M16 100% (p7_monzo.py).
- **src/cma/importers/monzo.py:158** — with --opening-balance the Balance column is never checked (closing 4,970 vs Monzo's 970).

## Minor
- **src/cma/metrics.py:179** — policy evaluated on rounded values: DSCR 1.2496 stored 1.25, R2 refers instead of failing (p4_round.py).
- **src/cma/ingest.py:117** — float tolerance at exactly 0.01 accepts some 1p errors and rejects others (p8_tol.py).
- **src/cma/importers/monzo.py:163** — Monzo Transaction IDs replaced by positional M00001…; trace to Monzo lost; renumbers on re-import.
- **src/cma/guard.py:58** — wrapped "35. " at line start blanked as a list marker.

## Checked and holding
- Float sums match Decimal at 2dp on all committed statements.
- P1: 0 mismatches over 675 generated statements (M2, M5–M8, M11–M13, M15).
- Ingest rejects duplicate ids, out-of-order, bad/future dates, bad amounts, balance breaks; reverses newest-first.
- Zero debt → M10 n/a, R2 refers; empty months count; no DST exposure.

## Not proven
- R8 (outside lens). Unlinked returned DDs with differing payee text. Monzo foreign-currency row semantics.
