import sys
from datetime import date
from pathlib import Path
from typer.testing import CliRunner
import cma, cma.models
from cma import synth
from cma.cli import app
d = Path(sys.argv[1])
print("writer:", cma.__file__, "has account_key:", "account_key" in cma.models.Transaction.model_fields)
rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30)); csv, _ = synth.write(rows, truth, d)
r = CliRunner().invoke(app, ["run", str(csv), "--checkpoint-db", str(d / "r.sqlite"), "--thread", "old-1"], input="\x04")
print([l for l in r.output.splitlines() if "review id" in l])
