"""Grounding guard.

Reads every number in the memo and checks it against the metrics table. A number that
isn't a metric value (allowing for rounding and formatting) is flagged. A metric value
quoted without its [Mn] tag in the same sentence is also flagged. The memo doesn't
ship until this passes or a human overrides it.
"""

from __future__ import annotations

import math
import re

from .models import GuardFinding, GuardResult, Metric

NUMBER = re.compile(r"(?<![A-Za-z])[£$]?-?\d[\d,]*(?:\.\d+)?%?(?![A-Za-z\d])")
CITATION = re.compile(r"\[(M\d+)\]")
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")

# Small integers appear in ordinary prose ("three months", "2 of the 7 rules").
# Counting words are allowed up to this value; anything larger must be a metric.
PROSE_INT_LIMIT = 31
YEARS = {float(y) for y in range(2000, 2100)}


def _variants(value: float) -> set[float]:
    out = {value, float(math.floor(value)), float(math.ceil(value))}
    for nd in (1, 2):
        out.add(round(value, nd))
    if value != 0:
        out.add(round(value / 1000, 1))  # "£22.4k"
    return out


def _parse(raw: str) -> float | None:
    text = raw.replace("£", "").replace("$", "").replace(",", "").rstrip("%")
    try:
        return float(text)
    except ValueError:
        return None


def check(memo: str, metrics: list[Metric]) -> GuardResult:
    lookup: dict[float, set[str]] = {}
    for m in metrics:
        for v in _variants(m.value) | _variants(-m.value):
            lookup.setdefault(v, set()).add(m.id)

    findings: list[GuardFinding] = []
    missing: set[str] = set()
    checked = 0
    for sentence in SENTENCE.split(memo):
        cited = set(CITATION.findall(sentence))
        for match in NUMBER.finditer(sentence):
            raw = match.group(0)
            value = _parse(raw)
            if value is None or value in YEARS:
                continue
            checked += 1
            plain_int = value.is_integer() and abs(value) <= PROSE_INT_LIMIT and "£" not in raw and "%" not in raw
            owners = lookup.get(value, set())
            if owners:
                if not owners & cited and not plain_int:
                    missing |= owners
                continue
            if plain_int:
                continue
            findings.append(GuardFinding(number_text=raw, reason="not in the metrics table"))
    return GuardResult(numbers_checked=checked, unmatched=findings, missing_citations=sorted(missing))
