"""The whole case flow through the command line, as a person runs it: make a case, import a
bank's CSV download, see the figures, preview, sign off, verify the pack, check the log.

Every step writes or re-reads the hash-chained case log, so this is the test that shows the
tool works on a platform at all. It runs everywhere; CI also runs it on Windows, where a
text-mode write once made `cma case new` refuse every save (see test_binary_writes.py).
"""

import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

# Laid out as HSBC's download is: no header line, newest first, signed amounts, a thousands
# separator inside quotes.
CSV = ("15/03/2026,CARD PAYMENT TO TESCO STORES,-23.40\n"
       '03/03/2026,SQUARE PAYMENTS SETTLEMENT,"1,250.00"\n'
       "28/02/2026,DD HMRC VAT,-310.55\n"
       "02/02/2026,SQUARE PAYMENTS SETTLEMENT,980.10\n")

def _cma(tmp: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(SRC), "CMA_HOME": str(tmp / "cma"), "PYTHONIOENCODING": "utf-8",
           "COLUMNS": "200"}
    return subprocess.run([sys.executable, "-m", "cma.cli", *args], input=stdin, capture_output=True,
                          text=True, encoding="utf-8", env=env, cwd=tmp, timeout=300)


def test_a_case_from_new_to_a_verified_pack(tmp_path):
    (tmp_path / "hsbc.csv").write_bytes(CSV.encode())

    made = _cma(tmp_path, "case", "new", "--demo", "--label", "end to end")
    assert made.returncode == 0, made.stdout + made.stderr
    case_id = made.stdout.strip().splitlines()[-1].strip()
    case = tmp_path / "cma" / "cases" / case_id
    assert case.is_dir(), made.stdout

    imported = _cma(tmp_path, "case", "import", case_id, "hsbc.csv", "--format", "bank-csv:hsbc",
                    "--account", "current", "--opening-balance", "500.00")
    assert imported.returncode == 0, imported.stdout + imported.stderr
    assert "4 transactions, 2026-02-02 to 2026-03-15" in " ".join(imported.stdout.split())

    figures = _cma(tmp_path, "case", "figures", case_id)
    assert figures.returncode == 0, figures.stdout + figures.stderr
    assert "£2,230.10" in figures.stdout  # total revenue: the two settlements

    preview = _cma(tmp_path, "case", "preview", case_id)
    assert preview.returncode == 0, preview.stdout + preview.stderr

    signed = _cma(tmp_path, "case", "sign", case_id, stdin="Tester\ny\n")
    assert signed.returncode == 0, signed.stdout + signed.stderr
    [pack] = (case / "packs").iterdir()
    assert sorted(p.name for p in pack.iterdir()) == ["evidence.json", "memo.md", "monthly.csv", "record.html"]

    verified = _cma(tmp_path, "verify", str(pack / "evidence.json"))
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert "no mismatch" in verified.stdout

    checked = _cma(tmp_path, "case", "check", str(case))
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "case log intact: 5 entries" in checked.stdout
    assert b"\r\n" not in (case / "log.jsonl").read_bytes()


def test_a_business_with_a_reserve_account_is_judged_as_a_whole(tmp_path):
    """A café with a trading account and a reserve account: the reserve has no trading of its
    own, so judged alone it 'failed' and showed late VAT payments it never had to make. The
    memo now judges the business as a whole, and the pack still verifies."""
    made = _cma(tmp_path, "generate", "--archetype", "cafe", "--profile", "stretched", "--out", str(tmp_path / "gen"),
                "--feature", "all", "--case")
    assert made.returncode == 0, made.stdout + made.stderr
    [case] = (tmp_path / "cma" / "cases").iterdir()
    preview = _cma(tmp_path, "case", "preview", case.name)
    assert preview.returncode == 0, preview.stdout + preview.stderr
    signed = _cma(tmp_path, "case", "sign", case.name, stdin="Tester\ny\n")
    assert signed.returncode == 0, signed.stdout + signed.stderr
    [pack_dir] = (case / "packs").iterdir()
    memo = (pack_dir / "memo.md").read_text(encoding="utf-8")
    policy_part = memo.split("## Policy check (illustrative)", 1)[1]
    assert "### All accounts" in policy_part
    assert "### Account reserve" not in policy_part and "### Account main" not in policy_part
    assert policy_part.count("Overall outcome:") == 1
    assert "All accounts: " in memo.split("## Policy check", 1)[0]  # each section opens with the whole business
    verified = _cma(tmp_path, "verify", str(pack_dir / "evidence.json"))
    assert verified.returncode == 0 and "no mismatch" in verified.stdout, verified.stdout + verified.stderr
