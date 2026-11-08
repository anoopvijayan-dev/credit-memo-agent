# Bridgie panel run 03 — bet v01-pilot-ready — branch fix/v01-panel-c1-c6 @ 542e55b

Six seats, fresh contexts, same requirements as runs 01 and 02, plus a written claim that D1–D19 are fixed.

| seat | verdict | blocker | major | minor |
|---|---|---|---|---|
| reviewer-03 | fail | 1 | 5 | 5 |
| security-03 | fail | 0 | 4 | 5 |
| clean-code-03 | fail | 1 | 3 | 9 |
| platform-03 | fail | 0 | 7 | 3 |
| staff-03 | fail | 1 | 2 | 6 |
| data-intensive-03 | fail | 1 | 7 | 4 |

## Before and after

| | run 01 (main) | run 02 | run 03 |
|---|---|---|---|
| blockers (consolidated) | 1 | 1 | 1 |
| R2 traceable | 0/6 | 6/6 | 5/6 |
| R4 / P2 guard | 0/6 | 0/6 | 0/6 |
| R9 bad input refused | 0/6 | 4/6 | 6/6 |
| P1 truth (seats sweeping 270–675 statements) | — | fail | 6/6 pass |

What held: R1, R3, R5, R6, R7, R9, R10 and P1 passed in every seat that assessed them. D1, D2, D3 (glued units), D8, D9, D10 (stagger), D12, D13, D14 and D18 were not refuted.

## Consolidated run-03 issues

| # | sev | issue | where | seats |
|---|---|---|---|---|
| E1 | **blocker** | OTHER credits (capital injection, savings, HMRC refund) still count as operating cash: one injection flips M10/R2 fail → pass and overall FAIL → PASS while M16 stays under 20%. D4 fixed only for round trips. | metrics.py:174 | 4/6 |
| E2 | major | Date exemption blanks any 1–2 digits beside a month word, incl. the verb "may" and days > 31; d/d/d triples; any number at line start as a list marker. | guard.py:53-58 | 6/6 |
| E3 | major | Monzo importer drops the payment reference, so no Monzo transfer can be revenue (M2 0, M16 100%); positional ids lose the trace to Monzo. | importers/monzo.py:141,163 | 5/6 |
| E4 | major | `--opening-balance` never checked against Monzo's Balance column. | importers/monzo.py:158 | 5/6 |
| E5 | major | A rejected run overwrites the approved memo's `.evidence.json`. | cli.py:164 | 4/6 |
| E6 | major | Real-model workflow runs any `code_ref` with the main-pinned federated token; inputs interpolated into scripts; `| tee` hides the exit code. | evals.yml:42,58,63 | 4/6 |
| E7 | major | Boundary months: M3 divides revenue from dropped months by covered months; a 15-day month counts whole (M1 13, flat business "declines" 15%). | metrics.py:63,240 | 4/6 |
| E8 | major | M13 counts a VAT DD returned unpaid as paid on time. | metrics.py:213 | 2/6 |
| E9 | major | Reusing `--thread` carries redrafts, old guard feedback (another statement's figures), review and final memo. | graph.py:193,222 | 2/6 |
| E10 | major | Two reviewers resuming one review: the second decision is dropped and their CLI writes the first's approval. | cli.py:151 | 1/6 |
| E11 | major | Prompt asks for the n/a reason from the method column; method texts contain numbers the guard rejects. | graph.py:86, metrics.py:150,202 | 1/6 |
| E12 | major | MCP data root defaults to cwd (often `/`); StatementError echoes raw cells to the client. | mcp_server.py:26 | 1/6 |
| E13 | major | `.gitignore` misses the files the README tells users to create (raw export, memo, evidence, checkpoint DB with raw descriptions). | .gitignore | 2/6 |
| E14 | major | Fraction/multiple words (halved, a third, doubled, twice) and "one" pass the guard. | guard.py:59 | 2/6 |
| E15 | minor | [Rn] accepts the threshold value in any role ("cover is 1.25x [R2]"). | guard.py:182 | 1/6 |
| E16 | minor | Policy evaluated on rounded values (DSCR 1.2496 → 1.25 refers instead of failing). | metrics.py:179 | 1/6 |
| E17 | minor | Balance check float tolerance accepts some 1p errors. | ingest.py:117 | 1/6 |
| E18 | minor | M16 evidence lacks its denominator. | metrics.py:273 | 1/6 |
| E19 | minor | `synth.generate` crashes for short statements. | synth.py:221 | 1/6 |
| E20 | minor | `cma resume` trusts the stored guard result and statement. | cli.py:106 | 1/6 |
| E21 | minor | Reviewer identity is asserted, not verified; programmatic callers can set mode. | cli.py:177 | 1/6 |
| E22 | minor | `transactions_for` hands payer-controlled descriptions to an LLM client. | mcp_server.py:79 | 1/6 |
| E23 | minor | A self-transfer with "INV 1001" counts as revenue; no round-trip detection. | categorise.py:48 | 1/6 |
| E24 | minor | Truncated model reply passes; no timeout; provider errors and cp1252/missing file/bad YAML give tracebacks. | llm.py:78, cli.py:80 | 1/6 |
| E25 | minor | R6 description "15%" vs threshold -15, so quoting the description is flagged. | uk_sme_default.yaml:61 | 1/6 |
| E26 | minor | Duplicated normalisers (payer/payee, display formats); dead `Metric.available`; no truth for M3/M9/M10/M16; no tests of policy validation or of R3. | metrics.py:108, graph.py:129, run_evals.py:85 | 2/6 |
| E27 | minor | M4 compares opposite seasons on a 12-month file; M10 is historical, existing debt only — say so. | metrics.py:152 | 1/6 |

## Not proven (any seat)
Real-model grounding rate (no key in the sandbox); real acquirer names (card revenue from real acquirers would land in OTHER/M16); how real Monzo exports show a returned DD; full byte-level R8 audit.
