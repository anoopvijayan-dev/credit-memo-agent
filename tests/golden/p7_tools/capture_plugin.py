"""pytest plugin: record every statement the test suite computes metrics on or categorises
as a whole, and every (description, amount) the categoriser sees, using the code as it is
*before* T01. A statement categorised but never passed to metrics.compute is recorded
too, with no VAT stagger, so make_p7_baseline.py gives it an M1-M16 baseline (verify-10:
the two-credit Monzo invoice fixture was otherwise captured only as single lines). So is
every statement ingest.load_csv returns, before any category is set, marked "loaded" so
the baseline categorises it with the rule table (verify-11: the newest-first and
byte-order-mark fixtures in tests/test_ingest.py are only loaded).
Output: $CAPTURE_OUT (JSON)."""

import atexit
import json
import os

from cma import categorise, ingest, metrics

STATEMENTS: dict[str, dict] = {}
LINES: dict[tuple[str, float], str] = {}

_orig_compute = metrics.compute
_orig_one = categorise.categorise_one
_orig_categorise = categorise.categorise
_orig_load_csv = ingest.load_csv


def _record(statement, vat_stagger, loaded=False):
    s = statement.model_dump(mode="json")
    key = json.dumps([s, vat_stagger, loaded], sort_keys=True)
    STATEMENTS[key] = {"statement": s, "vat_stagger": vat_stagger, "loaded": loaded}


def _one(t):
    c = _orig_one(t)
    LINES[(t.description, t.amount)] = c.value
    return c


def _compute(statement, vat_stagger=None):
    out = _orig_compute(statement, vat_stagger=vat_stagger)
    _record(statement, vat_stagger)
    return out


def _categorise(statement):
    out = _orig_categorise(statement)
    _record(out, None)
    return out


def _load_csv(*args, **kwargs):
    out = _orig_load_csv(*args, **kwargs)
    _record(out, None, loaded=True)
    return out


ingest.load_csv = _load_csv
categorise.categorise_one = _one
categorise.categorise = _categorise
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
