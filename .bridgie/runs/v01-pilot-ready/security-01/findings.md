# security-01 · build/shaper:security · v01-pilot-ready

**Verdict: FAIL** — no blocker, 7 major, 6 minor. Every major is fixable in well under a week; none meets the kill criterion. Criteria: R1 R3 R6 R7 R8 R10 P1 pass; R2 R4 R5 R9 P2 fail.

## Major

1. `src/cma/cli.py:66` (R4) — `--yes` approves an ungrounded memo with no human. `cli.py:65-66` never reads `payload["guard_grounded"]`; with a hallucinating model and `--out` it writes a memo containing an invented figure stamped `APPROVED` / `Grounding check: overridden`. Repro: `FakeLLM(hallucinate=True)` → resume `{"approved": True, "reviewer": "cli --yes"}` → `final_memo` contains `£48,250`.
2. `src/cma/graph.py:129` (R5) — finalisation needs no reviewer identity. `review()` accepts a bare bool, `ReviewDecision.reviewer` defaults to `"analyst"` (`models.py:129`), and the interactive CLI (`cli.py:68-70`) never asks who is approving. `Command(resume=True)` finalises as `Reviewed by analyst: APPROVED.` No timestamp either.
3. `src/cma/guard.py:16` (P2/R4) — `(?![A-Za-z\d])` means a digit run followed by a letter is never matched: `£48k`, `£1.2m`, `£500pcm` yield `numbers_checked=0`, grounded. `guard.py:57` also skips any value 2000–2099 as a year, so `£2,050` passes unchecked even with a £ sign.
4. `src/cma/ingest.py:22` (R9) — no validation. Inconsistent balances, duplicate ids, empty ids, `nan`/`inf` are accepted: `M2=nan`, `M5=-inf` reach the policy and the model's table (`£nan`). Duplicate ids make `Statement.by_id()` (`models.py:59`) return the wrong line for an evidence id. Rejections that do occur are raw `KeyError`/`ValueError`.
5. `src/cma/metrics.py:97` (R2) — evidence truncated: M9 `[:200]`, M6 `[:50]` (l.89), M15 `[:200]` (l.112), M1 none (l.75), M10 cites loan lines only (l.100). On `examples/cafe_stretched.csv` M9 cites 200 of 362 transactions it summed. `test_every_metric_cites_evidence` checks existence, not completeness.
6. `src/cma/metrics.py:98` (R1) — M10 is the literal `99.0` when there is no debt. It renders as `99.00` in the table, the model may quote it, the guard grounds it, policy R2 passes on it. A fabricated ratio inside the "deterministic" table.
7. `src/cma/mcp_server.py:48` — all four tools take an unrestricted path. Any MCP client (incl. an LLM agent, which is injectable) can `transactions_for`/`compute_metrics` any readable CSV on the host — another customer's statement in a multi-statement pilot. No root allow-list, no caller identity, no audit log. (Errors are correctly masked; paths are not leaked.)

## Minor

8. `src/cma/mcp_server.py:43` (R7) — caller-supplied `policy_path` turns `cafe_poor` from `fail` to `pass` with a one-rule YAML. `safe_load`, so no code execution.
9. `src/cma/graph.py:133` (R5) — a rejected review still yields `final_memo` with the full body; `cli.py:73-74` writes it to `--out` like an approved one.
10. `src/cma/ingest.py:33` (R3) — `account_name` = filename stem → first line of the prompt (`graph.py:93`). Only caller-controlled free text that reaches the model; descriptions and ids do not (verified by capturing the prompt).
11. `src/cma/categorise.py:24` (R6) — `FPS CREDIT … INVnnnn` from the owner's own account is revenue (round-tripping: `M2=40000` on a zero-net statement). `\bLOAN\b` (l.21) on a drawdown credit gives `M8<0`, `M10=-0.02`, policy fail.
12. `src/cma/metrics.py:59` (R9) — transfer credits summing to zero → unhandled `ZeroDivisionError`.
13. `.github/workflows/ci.yml:21` (R8) — nothing enforces synthetic-only data going forward; no test asserts `examples/*.csv` regenerate from `synth`.

## Verified clean

- R8 today: all ten committed CSVs regenerate line-for-line from `synth.generate(seed=7, end=2026-09-30)` (CRLF/LF only). Description sweep shows fictional counterparties only; regex sweep for sort codes, account numbers, NI numbers, emails, postcodes: nothing. (`PARCELFORCE WORLDWIDE` is a real brand, not customer data.)
- R3: prompt holds account name, period, metrics table, policy lines only.
- R7: YAML, `safe_load`, operators from a fixed dict.
- R10: 38 tests + 9/9 offline evals pass locally with no provider key set; CI installs no `llm` extra.
- Secrets: provider keys read by the SDKs from env only; never logged; `.env` gitignored. MCP error responses do not leak paths or stack traces.

## Not proven / out of reach

- P1 "for any synthetic statement": only the default seed is exercised (nine statements). Marked pass on the tests that exist; a seed sweep would strengthen it.
- Real-model behaviour (grounding rate, redraft count, prompt-injection resilience of a live model) — no API key available and the evidence task is still open.
- No `git` and the repo is not a git checkout here, so history could not be checked for previously committed real data; only the current tree was verified.
