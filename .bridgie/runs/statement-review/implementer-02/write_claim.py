import json, sys
c = {
 "task": "T02",
 "claims": [
  {"text": "Full existing suite plus new tests pass (baseline before the change was 223 passed, 1 skipped).",
   "proof_cmd": "python -m pytest -q", "proof_output": "241 passed, 1 skipped in 2.83s"},
  {"text": "New tests: six new Category values, existing values unchanged, CREDIT_BUCKET maps every credit category to exactly one of 8 Buckets per the design table, cash_deposit is in REVENUE_CATEGORIES and REVENUE_CATEGORIES equals the trading-receipts bucket, Transaction new fields default to ('main', None, None), and a checkpoint with Transactions stripped to the old shape resumes via `cma resume`.",
   "proof_cmd": "python -m pytest -q tests/test_models.py", "proof_output": "18 passed in 0.45s"},
  {"text": "An actual checkpoint written by the pre-change code (git archive HEAD~1 src, Transaction without account_key) resumes under the new code and writes the approved memo.",
   "proof_cmd": "git archive HEAD~1 src | tar -x -C oldck/old; PYTHONPATH=oldck/old/src python -I -c '...exec(make.py)' oldck; python oldck/resume.py oldck  (scripts in the run dir oldck/)",
   "proof_output": "writer: .../oldck/old/src/cma/__init__.py has account_key: False\n['review id: old-1 (kept in ']\nreader: .../T02/src/cma/__init__.py has account_key: True\nexit 0\n['APPROVED by C. Later at 2026-11-16 18:03 UTC.']"},
  {"text": "Lint clean.", "proof_cmd": "python -m ruff check src tests", "proof_output": "All checks passed!"},
  {"text": "Evals unchanged: every synthetic memo still grounded (no categoriser produces the new values yet, so no figure moves).",
   "proof_cmd": "python evals/run_evals.py", "proof_output": "9/9 memos fully grounded; model = fake"}
 ],
 "files_changed": ["src/cma/models.py", "tests/test_models.py"],
 "notes": "Commit 3667577 on bridgie-task/statement-review/T02. Names chosen: Category.CASH_DEPOSIT/OWN_ACCOUNT_TRANSFER/DIRECTOR_FUNDING/REFUND_IN/HMRC_REPAYMENT/OTHER_EXPLAINED_IN; Bucket enum (8 members) and CREDIT_BUCKET dict in models.py; Transaction.account_key (default 'main'), rule_id, provider_hint (default None). Bucket is not in graph state, so CHECKPOINT_TYPES is unchanged as designed. Transactions are not dumped into evidence JSON, so evidence is unaffected. Debit-only categories are deliberately absent from CREDIT_BUCKET (a credit in one is an R3 reconciliation failure for C2 to raise); OWN_ACCOUNT_TRANSFER is used for both directions per the design. Tests were run in a venv outside the repo (uv pip install -e '.[dev,durable]'). The test's msgpack rewriter found that a stored Statement holds its lines as plain dicts, so it strips the new keys there."
}
json.dump(c, open(sys.argv[1], "w"), indent=1)
