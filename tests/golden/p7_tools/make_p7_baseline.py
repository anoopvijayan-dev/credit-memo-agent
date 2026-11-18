"""Build tests/golden/p7_v1_baseline.json from the code as it was BEFORE T01.

Run from a checkout of the pre-change commit (fd7b64c), with the capture produced by
capture_plugin.py (every statement the test suite computes metrics on or categorises
as a whole, and every line the categoriser sees). Install the durable extra so no capturing test is skipped.
From the worktree root, with S a scratch directory:

    git archive fd7b64c | tar -x -C $S/old
    cd $S/old && PYTHONDONTWRITEBYTECODE=1 CAPTURE_OUT=$S/capture.json \
        PYTHONPATH=$S/old/src:<worktree>/tests/golden/p7_tools python -m pytest -q -p capture_plugin
    MADE_AT=fd7b64c PYTHONPATH=$S/old/src python <worktree>/tests/golden/p7_tools/make_p7_baseline.py \
        $S/capture.json $S/p7.json
    # then compare $S/p7.json with gunzip < tests/golden/p7_v1_baseline.json.gz: every key equal

tests/test_p7_history.py runs exactly these steps and the comparison.

The committed golden's "_about" text predates this folder and says the tools were in
the T01 run directory; they are these files, unchanged apart from this docstring.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

from cma import categorise, ingest, metrics, synth
from cma.models import Statement

SYNTH_CASES = [
    *[(a, p, 7, 12, "2026-09-30") for a in synth.ARCHETYPES for p in synth.PROFILES],
    ("cafe", "stretched", 3, 12, "2026-09-30"),
    ("cafe", "good", 7, 3, "2026-09-30"),
    ("cafe", "poor", 7, 4, "2026-09-30"),
    ("cafe", "poor", 7, 2, "2026-09-30"),
]
EXAMPLES = ["examples/cafe_stretched.csv"]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def categories_sha(s: Statement) -> str:
    return sha("\n".join(f"{t.id}|{t.category.value}|{t.reverses or ''}" for t in s.transactions))


def metric_rows(s: Statement, vat_stagger):
    return [
        {"id": m.id, "unit": m.unit, "value": m.value, "evidence_n": len(m.evidence),
         "evidence_sha256": sha("\n".join(m.evidence))}
        for m in metrics.compute(s, vat_stagger=vat_stagger)
    ]


def rows_key(s: dict, vat_stagger) -> str:
    """A statement's lines AND the VAT stagger its figures were computed with: the same
    lines under another stagger give another M13, so they are another case (verify-01
    found the stagger-3 run of a fixture dropped because only its lines were compared)."""
    return json.dumps([[[t["id"], t["date"], t["description"], t["amount"], t["balance"]]
                        for t in s["transactions"]], vat_stagger])


def main(capture_path: str, out_path: str) -> None:
    cap = json.loads(Path(capture_path).read_text())
    out: dict = {
        "_about": (
            "P7 baseline: M1-M16 (id, unit, value, evidence) and categories, computed by the "
            "categoriser as it was before the rule table moved to src/cma/rules/ (T01). "
            "Under CMA_RULESET=v1 every entry must be reproduced exactly. Built by "
            "make_p7_baseline.py in the T01 run directory; do not regenerate from newer code."
        ),
        "made_at_commit": os.environ.get("MADE_AT") or subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip(),
        "synthetic": [], "examples": [], "fixtures": [], "lines": [],
    }
    seen_rows: set[str] = set()
    for archetype, profile, seed, months, end in SYNTH_CASES:
        rows, truth = synth.generate(archetype, profile, seed=seed, months=months, end=date.fromisoformat(end))
        with tempfile.TemporaryDirectory() as d:
            csv_path, _ = synth.write(rows, truth, Path(d), stem="s")
            raw = csv_path.read_bytes()
            s = categorise.categorise(ingest.load_csv(csv_path))
        for v in (None, truth.vat_stagger):
            seen_rows.add(rows_key(s.model_dump(mode="json"), v))
        out["synthetic"].append({
            "archetype": archetype, "profile": profile, "seed": seed, "months": months, "end": end,
            "csv_sha256": hashlib.sha256(raw).hexdigest(),
            "categories_sha256": categories_sha(s),
            "metrics": {str(v): metric_rows(s, v) for v in (None, truth.vat_stagger)},
        })
    for p in EXAMPLES:
        s = categorise.categorise(ingest.load_csv(Path(p)))
        for v in (None, 3):
            seen_rows.add(rows_key(s.model_dump(mode="json"), v))
        out["examples"].append({
            "path": p, "file_sha256": hashlib.sha256(Path(p).read_bytes()).hexdigest(),
            "categories_sha256": categories_sha(s),
            "metrics": {"None": metric_rows(s, None), "3": metric_rows(s, 3)},
        })
    skipped = 0
    for rec in cap["statements"]:
        sd = rec["statement"]
        if rows_key(sd, rec["vat_stagger"]) in seen_rows:
            continue
        given = Statement(**sd)
        fresh = categorise.categorise(Statement(**sd))
        if categories_sha(given) != categories_sha(fresh):
            skipped += 1  # categories set by hand in the test; not the rule table's output
            continue
        seen_rows.add(rows_key(sd, rec["vat_stagger"]))
        out["fixtures"].append({
            "account_name": sd["account_name"],
            "vat_stagger": rec["vat_stagger"],
            "lines": [[t.id, str(t.date), t.description, t.amount, t.balance, t.category.value, t.reverses]
                      for t in fresh.transactions],
            "metrics": metric_rows(fresh, rec["vat_stagger"]),
        })
    # Lines the tests categorise one at a time, outside any statement above.
    in_statements = {(t["description"], t["amount"]) for r in cap["statements"] for t in r["statement"]["transactions"]}
    out["lines"] = sorted({(x["description"], x["amount"], x["category"]) for x in cap["lines"]
                           if (x["description"], x["amount"]) not in in_statements})
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    # One fixture line, metric or synthetic case per text line: diffable, not bloated.
    text = json.dumps(out, separators=(",", ":"))
    text = re.sub(r'(\[\[|\},\{|\],\[|"(?:synthetic|examples|fixtures|lines)":)', lambda m: m.group(0)[:-1] + "\n" + m.group(0)[-1] if m.group(0)[-1] in "[{" else m.group(0) + "\n", text)
    Path(out_path).write_text(text + "\n")
    import cma
    print(f"cma from {cma.__file__}")
    print(f"synthetic={len(out['synthetic'])} examples={len(out['examples'])} "
          f"fixtures={len(out['fixtures'])} hand_categorised_skipped={skipped} lines={len(out['lines'])}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
