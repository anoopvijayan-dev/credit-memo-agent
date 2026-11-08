"""Grounding guard.

Reads every number in the memo and checks it against the metrics table and the policy
thresholds. The memo doesn't ship until this passes or a named human overrides it.

The contract the model is given, and this module enforces:

- Every figure is written in digits and followed *immediately* by its tag: "£22,400 [M3]",
  "1.42x [M10]", "-12.3% [M4]", or, for a policy threshold, "1.25x [R2]".
- The figure must equal the tagged value at the precision written, within half a
  percent of the value (so "£48k" is not accepted for £48,400; "£48.4k" is).
- Its unit must fit: £/GBP/pounds only for money metrics, % only for percentages, x for
  ratios, a bare number for counts, days, months or ratios.
- Its sign must match. A negative figure is written with a minus, as in the table.
- Numbers are not written in words ("forty-eight thousand", "five returned debits");
  "one" is the only number word allowed, because it is everywhere in plain English.

What is read as a number: an optional minus, an optional currency (£ $ € GBP EUR USD,
glued or spaced), digits with optional thousands commas and decimals, then an optional
scale or unit (k, m, bn, thousand, million, billion, x, %, per cent, GBP, pounds).
"c.48k" and "~48k" are read as 48k. Digits glued to letters before them are identifiers
(M10, R2, INV1234, T00012) and are skipped.

Exemptions, kept deliberately small:
- four-digit years (1990-2099) written bare;
- ISO dates (2026-09-30), day/month/year dates and dates with a month name ("7 January");
- a list marker at the start of a line ("1. ").
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import GuardFinding, GuardResult, Metric, PolicyResult

TAG = re.compile(r"\[([MR]\d+)\]")
TAG_AFTER = re.compile(r"[ \t]*\[([MR]\d+)\]")
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")

NUMBER = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:(?P<neg>[-\u2212\u2013])\s?)?"
    r"(?P<cur>£|\$|€|(?:GBP|EUR|USD)\s?)?"
    r"(?P<neg2>[-\u2212\u2013])?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<suf>\s?(?:bn|billion|mn|million|thousand)\b|(?:k|m)\b|x\b|\s?%|\s?per\s?cent\b|\s?percent\b"
    r"|\s?(?:GBP|pounds)\b)?",
    re.IGNORECASE,
)
MONTHS = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
          r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
DATES = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b"
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTHS}\b|\b{MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?\b(?!,\d)",
    re.IGNORECASE,
)
LIST_MARKER = re.compile(r"^(\s*)\d+[.)](?=\s)", re.MULTILINE)
NUMBER_WORDS = re.compile(
    r"\b(?:zero|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen"
    r"|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
    r"|hundred|thousand|million|billion|dozen)(?:[-\s](?:one|two|three|four|five|six|seven|eight|nine))?\b",
    re.IGNORECASE,
)

SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}
GBP_WORDS = {"£", "gbp", "pounds"}


@dataclass
class Written:
    text: str
    value: float  # signed, scaled
    decimals: int
    scale: float
    unit: str | None  # "GBP", "pct", "ratio", or None for a bare number
    foreign: bool  # $, €, EUR, USD: never a match for a sterling metric
    end: int  # index just after the figure, to look for its tag


def _blank(text: str, pattern: re.Pattern[str]) -> str:
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def _parse(m: re.Match[str]) -> Written | None:
    num, cur, suf = m.group("num"), (m.group("cur") or "").strip().lower(), (m.group("suf") or "").strip().lower()
    negative = bool(m.group("neg") or m.group("neg2"))
    try:
        base = float(num.replace(",", ""))
    except ValueError:
        return None
    scale = SCALE.get(suf, 1.0)
    unit: str | None = None
    if cur in GBP_WORDS or suf in GBP_WORDS:
        unit = "GBP"
    elif suf == "%" or suf.startswith("per"):
        unit = "pct"
    elif suf == "x":
        unit = "ratio"
    decimals = len(num.split(".", 1)[1]) if "." in num else 0
    value = -base * scale if negative else base * scale
    return Written(m.group(0).strip(), value, decimals, scale, unit, cur in ("$", "€", "eur", "usd"), m.end())


def _unit_fits(w: Written, unit: str) -> bool:
    if w.foreign:
        return False
    if w.unit == "GBP":
        return unit == "GBP"
    if w.unit == "pct":
        return unit == "pct"
    if w.unit == "ratio":
        return unit == "ratio"
    if w.scale != 1.0:  # "48k" with no currency: money or a count
        return unit in ("GBP", "count")
    return unit in ("count", "days", "months", "ratio")


DISPLAY_DECIMALS = {"GBP": 0, "pct": 1, "ratio": 2}


def _value_fits(w: Written, target: float, unit: str) -> bool:
    """Equal at the precision written. Written less precisely than the table shows it
    ("£48k", "1.4x", "11%"), it must also be within half a percent of the value."""
    diff = abs(w.value - target)
    if diff > 0.5 * 10 ** (-w.decimals) * w.scale + 1e-9:
        return False
    as_precise_as_table = w.scale == 1.0 and w.decimals >= DISPLAY_DECIMALS.get(unit, 0)
    return as_precise_as_table or diff <= 0.005 * abs(target) + 1e-9


def _fits(w: Written, target: float, unit: str) -> bool:
    return _unit_fits(w, unit) and _value_fits(w, target, unit)


def _sign_only(w: Written, target: float, unit: str) -> bool:
    flipped = Written(w.text, -w.value, w.decimals, w.scale, w.unit, w.foreign, w.end)
    return target != 0 and _fits(flipped, target, unit)


def check(memo: str, metrics: list[Metric], policy: PolicyResult | None = None) -> GuardResult:
    findings: list[GuardFinding] = []
    missing: set[str] = set()
    checked = 0

    if not memo.strip():
        return GuardResult(numbers_checked=0, unmatched=[GuardFinding(number_text="", reason="memo is empty")])
    if not re.search(r"\[M\d+\]", memo):
        findings.append(GuardFinding(number_text="", reason="memo cites no metrics"))

    by_id = {m.id: m for m in metrics}
    rules = {r.rule_id: r for r in policy.rules} if policy else {}

    text = _blank(_blank(memo, DATES), LIST_MARKER)
    for sentence in SENTENCE.split(text):
        scan = _blank(sentence, TAG)  # tags contain digits; never read them as figures
        for match in NUMBER.finditer(scan):
            w = _parse(match)
            if w is None:
                continue
            bare_int = w.unit is None and w.scale == 1.0 and not match.group("cur") and "," not in match.group("num") \
                and "." not in match.group("num") and not (match.group("neg") or match.group("neg2"))
            if bare_int and 1990 <= w.value <= 2099:
                continue
            checked += 1
            tag = TAG_AFTER.match(sentence, w.end)
            tag_id = tag.group(1) if tag else None

            if tag_id and tag_id.startswith("M"):
                mt = by_id.get(tag_id)
                if mt is None:
                    findings.append(GuardFinding(number_text=w.text, reason=f"tagged {tag_id}, which is not in the metrics table"))
                elif mt.value is None:
                    findings.append(GuardFinding(number_text=w.text, reason=f"{tag_id} has no value (shown as n/a)"))
                elif not _fits(w, mt.value, mt.unit):
                    findings.append(GuardFinding(number_text=w.text, reason=_why(w, mt.value, mt.unit, tag_id, mt.display())))
                continue
            if tag_id and tag_id.startswith("R"):
                r = rules.get(tag_id)
                if r is None:
                    findings.append(GuardFinding(number_text=w.text, reason=f"tagged {tag_id}, which is not a policy rule"))
                elif not any(_fits(w, v, r.unit) for v in r.citable_values()):
                    shown = ", ".join(f"{v:g}" for v in r.citable_values())
                    findings.append(GuardFinding(number_text=w.text, reason=f"does not match {tag_id}'s threshold ({shown}, {r.unit})"))
                continue

            owners = sorted(m.id for m in metrics if m.value is not None and _fits(w, m.value, m.unit))
            if owners:
                # A real figure without its tag right after it: reported as a missing citation.
                missing |= set(owners)
            else:
                findings.append(GuardFinding(number_text=w.text, reason="not in the metrics table or the policy"))

        rest = _blank(scan, NUMBER)
        for word in NUMBER_WORDS.finditer(rest):
            checked += 1
            findings.append(GuardFinding(number_text=word.group(0), reason="number written in words; write figures in digits with their tag"))

    return GuardResult(numbers_checked=checked, unmatched=findings, missing_citations=sorted(missing))


def _why(w: Written, target: float, unit: str, tag_id: str, shown: str) -> str:
    if not _unit_fits(w, unit):
        return f"unit does not fit {tag_id} ({unit}, shown as {shown})"
    if _sign_only(w, target, unit):
        return f"sign is wrong: {tag_id} is {shown}; write it with its minus sign"
    return f"does not match {tag_id} ({shown}) at the precision written"
