"""pytest plugin: record every statement the test suite computes metrics on, and every
(description, amount) the categoriser sees, using the code as it is *before* T01.
Output: $CAPTURE_OUT (JSON)."""

import atexit
import json
import os

from cma import categorise, metrics

STATEMENTS: dict[str, dict] = {}
LINES: dict[tuple[str, float], str] = {}

_orig_compute = metrics.compute
_orig_one = categorise.categorise_one


def _one(t):
    c = _orig_one(t)
    LINES[(t.description, t.amount)] = c.value
    return c


def _compute(statement, vat_stagger=None):
    out = _orig_compute(statement, vat_stagger=vat_stagger)
    s = statement.model_dump(mode="json")
    key = json.dumps([s, vat_stagger], sort_keys=True)
    STATEMENTS[key] = {"statement": s, "vat_stagger": vat_stagger}
    return out


categorise.categorise_one = _one
metrics.compute = _compute


@atexit.register
def _dump():
    path = os.environ["CAPTURE_OUT"]
    with open(path, "w") as f:
        json.dump(
            {
                "statements": list(STATEMENTS.values()),
                "lines": [{"description": d, "amount": a, "category": c} for (d, a), c in LINES.items()],
            },
            f,
        )
