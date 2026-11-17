set -e
R=/home/user/credit-memo-agent/.bridgie/runs/statement-review/hardener-08; P=$R/probe; PY=$R/venv/bin/python
cd $P
PYTHONPATH=$1 $PY -c "
from datetime import date; from pathlib import Path; from cma import synth
rows,truth=synth.generate('cafe','stretched',end=date(2026,9,30)); print(synth.write(rows,truth,Path('.'))[0])" > csvpath
CSV=$(cat csvpath)
echo '--- run with NEW code (writes checkpoint) ---'
printf '\x04' | PYTHONPATH=$1 $PY -m cma.cli run "$CSV" --checkpoint-db reviews.sqlite --thread fwd 2>&1 | grep -E 'review id' || true
echo '--- resume with OLD code (rollback) ---'
printf 'y\n\n' | PYTHONPATH=$2 $PY -m cma.cli resume fwd --checkpoint-db reviews.sqlite --reviewer "C. Rollback" --out memo.md 2>&1 | tail -3; echo rc=$?
grep -o 'APPROVED by C. Rollback' memo.md
