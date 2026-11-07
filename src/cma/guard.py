"""Grounding guard.

Reads every number in the memo and checks it against the metrics table. A number that
isn't a metric value, at the precision it was written to, is flagged. A metric value
quoted without its [Mn] tag in the same sentence is also flagged. The memo doesn't
ship until this passes or a named human overrides it.

What counts as a number: an optional currency sign, digits with optional thousands
separators and decimals, then an optional suffix (`k`, `m`, `bn` for scale; `x` for
a ratio; `%`). "£48k" is 48,000; "1.42x" is 1.42; "£1.2m" is 1,200,000.

Exemptions, kept deliberately small:
- four-digit years (2000-2099) written bare, with no currency, suffix, comma or decimal;
- small whole numbers (0-31) written bare, when they match no metric ("three of the
  7 rules"). A bare integer that equals a metric value must still carry its tag.
"""

from __future__ import annotations

import re

from .models import GuardFinding, GuardResult, Metric

NUMBER = re.compile(
    r"(?<![A-Za-z\d.])"
    r"(?P<cur>[£$€])?"
    r"(?P<num>-?\d[\d,]*(?:\.\d+)?)"
    r"(?P<suf>bn|k|m|x|%)?"
    r"(?![A-Za-z\d])",
    re.IGNORECASE,
)
CITATION = re.compile(r"\[(M\d+)\]")
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")

SCALE = {"k": 1e3, "m": 1e6, "bn": 1e9}
PROSE_INT_LIMIT = 31


def _decimals(num: str) -> int:
    return len(num.split(".", 1)[1]) if "." in num else 0


def _matches(written: float, scale: float, decimals: int, metric_value: float) -> bool:
    """True if `metric_value`, rounded the way the memo wrote it, equals `written`."""
    tolerance = 0.5 * 10 ** (-decimals) * scale + 1e-9
    return any(abs(v - written * scale) <= tolerance for v in (metric_value, -metric_value))


def check(memo: str, metrics: list[Metric]) -> GuardResult:
    findings: list[GuardFinding] = []
    missing: set[str] = set()
    checked = 0

    if not memo.strip():
        return GuardResult(
            numbers_checked=0,
            unmatched=[GuardFinding(number_text="", reason="memo is empty")],
        )
    if not CITATION.search(memo):
        findings.append(GuardFinding(number_text="", reason="memo cites no metrics"))

    for sentence in SENTENCE.split(memo):
        cited = set(CITATION.findall(sentence))
        # Citation tags themselves contain digits; blank them out before scanning.
        scan = CITATION.sub(lambda m: " " * len(m.group(0)), sentence)
        for match in NUMBER.finditer(scan):
            raw = match.group(0)
            num, cur, suf = match.group("num"), match.group("cur"), (match.group("suf") or "").lower()
            try:
                written = float(num.replace(",", ""))
            except ValueError:
                continue
            bare = not cur and not suf and "," not in num and "." not in num
            if bare and 2000 <= written <= 2099:
                continue
            checked += 1
            scale = SCALE.get(suf, 1.0)
            decimals = _decimals(num)
            owners = {m.id for m in metrics if _matches(written, scale, decimals, m.value)}
            if owners:
                if not owners & cited:
                    missing |= owners
                continue
            if bare and written.is_integer() and 0 <= written <= PROSE_INT_LIMIT:
                continue
            findings.append(GuardFinding(number_text=raw, reason="not in the metrics table"))

    return GuardResult(numbers_checked=checked, unmatched=findings, missing_citations=sorted(missing))
