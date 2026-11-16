import sys
from pathlib import Path
from typer.testing import CliRunner
import cma, cma.models
from cma.cli import app
d = Path(sys.argv[1])
print("reader:", cma.__file__, "has account_key:", "account_key" in cma.models.Transaction.model_fields)
r = CliRunner().invoke(app, ["resume", "old-1", "--checkpoint-db", str(d / "r.sqlite"), "--reviewer", "C. Later", "--out", str(d / "memo.md")], input="y\n\n")
print("exit", r.exit_code); print([l for l in (d / "memo.md").read_text().splitlines() if "APPROVED" in l])
