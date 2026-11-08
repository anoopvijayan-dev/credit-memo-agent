# security-03 · build/shaper:security · v01-pilot-ready @ fix/v01-panel-c1-c6 (542e55b)

**Verdict: FAIL.** 4 major: guard date exemption lets invented figures through (R4/P2); MCP allow-list defaults to `/`; real-model eval workflow runs any branch's code with the federated credential; real data is one `git add -A` from being committed. Tests 174 pass, offline evals 9/9, P1 held on 540 generated statements.

(Saved by the orchestrator from the seat's hand-back; the harness refused the seat's own write of this file.)

## Major
1. **src/cma/guard.py:55 (and :54) — R4/P2.** DATES exemption blanks any 1–2 digit number next to a month word (case-insensitive, so the modal "may" counts) and any d/d/d triple. All grounded=True: "In May 12 direct debits were returned.", "Sales peaked in June 40% above the monthly average.", "Turnover may 45% fall next year.", "Arrears are 12/34/5678 pounds." The prompt already requires YYYY-MM-DD (graph.py:85), so only ISO dates need exempting.
2. **src/cma/mcp_server.py:26.** Without CMA_DATA_DIR the root is `Path.cwd()`; MCP hosts often launch stdio servers with cwd `/` or `~`. From `/`, any `.csv` on the machine is readable and `transactions_for` returns raw lines. StatementError (ingest.py:99) echoes raw cell values. Fix: refuse to start when CMA_DATA_DIR is unset.
3. **.github/workflows/evals.yml:42 (:20, :63, :71).** Pinned to main for OIDC federation, but checks out free-text `inputs.code_ref` and runs `pip install -e` + `run_evals.py` from it with `id-token: write` and the JWT file already written (ref claim still main). A fork PR ref can copy the token into `evals/results/`, uploaded as a public artifact. `${{ inputs.* }}` is interpolated straight into `run:`.
4. **.gitignore:14 vs README.md:41,44,48 — R8.** Only `private/`, `*.private.csv`, `out/` are ignored; documented flows put `Monzo Data Export.csv`, `memo.md`, `memo.evidence.json`, `reviews.sqlite` (whole Statement, raw descriptions) in the repo root.

## Minor
5. **src/cma/categorise.py:48 — R6.** Self-transfer with "INV 1001" counts as revenue; no round-trip detection.
6. **src/cma/cli.py:106 — R5.** `cma resume` finalises from stored memo and guard result without re-running `guard.check` or re-hashing the statement.
7. **src/cma/cli.py:177 (models.py:160,167) — R5.** Reviewer identity is any typed name; a programmatic caller can set `mode="interactive"`.
8. **src/cma/mcp_server.py:79 — R3 scope.** `transactions_for` hands raw payer-controlled descriptions to an LLM client: prompt-injection channel.
9. **src/cma/importers/monzo.py:141.** Monzo Notes/Description never read, so invoice references never survive; customer transfers become OTHER (M2/M15 understated, M16 inflated).

## Verified OK
- R3: descriptions never reach the drafting model (injection probe); account_name is "Applicant".
- MCP `safe_path` refuses `..`, outside absolutes, other suffixes, symlinks out.
- Checkpoint serializer allow-list, `pickle_fallback=False`; `yaml.safe_load`.
- ci.yml `contents: read`, no keys.

## Not proven
- Frequency with which a real model writes month-adjacent numbers (no API key).
- Git history scan for real data (git forbidden).
- Exploitability of finding 3 depends on repo settings; inferred, not executed.
