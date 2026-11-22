"""Pseudonymise a statement and its case profile (R46, C9).

`cma pseudonymise` writes a copy of a statement in which personal and business names and
account numbers are replaced by consistent tokens, while the recognised institutions
(acquirers, gateways, lenders, HMRC: every wording the rule tables or the gambling list
recognise) and every id, date, amount and balance are kept. The case profile is tokenised the
same way: a director, shareholder, own account or business name becomes the token its
occurrences in the descriptions become, so the categoriser reads the copy as it reads the
original. The output is labelled as pseudonymised personal data, not anonymous data: the
amounts, dates and balances can identify the business again.

The copy must give the original's figures, buckets, monthly cells and indicators (P10). It is
built to do so, word by word:

- every span of a description that any rule of the v1 or v2 table, its exceptions, the
  gambling operators, an invoice reference or the bank wording the counterparty normaliser
  strips (FPS, BACS, DD...) match is kept as it is;
- every case-profile name is found in the descriptions as the categoriser finds it (whole
  words, any separators between its letters), and each region that profile names cover is
  replaced by the tokens of exactly the names found there, so each kind of profile name
  matches the same lines; a second spelling of one region gets a variant token, so two lines
  name the same counterparty only when they did before;
- every other word becomes a token of its own: one per distinct word, letters for letters and
  digits for a word with a digit, so the counterparty normaliser (M9, M15, round trips,
  reversal links) groups the lines as before.

A profile name that holds recognised wording somewhere (a director named after a lender) is
kept as it is, in the profile and the descriptions, since replacing it would change what the
rules see. Before anything is written, the copy is categorised and computed and compared with
the original, line by line and figure by figure; any difference refuses the copy, naming the
figure, and nothing is written. The token mapping itself is never written.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import categorise as cat
from . import indicators as ind
from . import metrics as met
from . import store
from .importers import contract
from .models import Statement

LABEL = ("PSEUDONYMISED PERSONAL DATA, NOT ANONYMOUS DATA. Names and account numbers in this copy are "
         "replaced by tokens, but its amounts, dates and balances are kept and can identify the business "
         "and the people behind it again. Handle it as personal data.")
LABEL_FILE = "PSEUDONYMISED.txt"
STATEMENT_FILE = "statement.csv"
PROFILE_FILE = store.PROFILE
MANIFEST_FILE = "pseudonymised.json"

# Bank wording the counterparty normaliser and the round-trip check read, kept as written.
_BANK_WORDS = re.compile(r"\s+DD$")
_EXTRA_PATTERNS = (cat.RAIL_PREFIX, cat.OUTGOING_TRANSFER, cat.INVOICE_REF, _BANK_WORDS)
_WORD = re.compile(rf"(?:[^\W_]|[{cat._MARKS}])+")
_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class PseudonymiseError(ValueError):
    """Nothing was written: the input was refused, or the copy would not give the same figures."""


@dataclass
class Pseudonymised:
    """The pseudonymised statement's bytes, the pseudonymised profile (or None) and counts."""

    statement: bytes
    profile: dict[str, Any] | None
    words_replaced: int
    names_replaced: int
    names_kept: int
    label: str = LABEL


def _key(name: str) -> str:
    """One profile name however it is spelt: what the categoriser matches (no separators)."""
    return cat._SEPARATOR_RUN.sub("", cat._fold(name))


@dataclass
class _Tokens:
    """Consistent, distinct tokens. A letter token is eight capitals (QZ and six more), a
    digit token digits only (or N and digits for a word that mixes the two); none is a word
    the original holds, and none is matched by any rule, gambling operator or profile name."""

    taken: set[str]
    blocked: Any  # callable(token) -> bool: True when some matcher reads the token
    letters: dict[str, str] = field(default_factory=dict)
    digits: dict[str, str] = field(default_factory=dict)
    _n_letters: int = 0
    _n_digits: int = 0

    def letter(self) -> str:
        while True:
            n, self._n_letters = self._n_letters, self._n_letters + 1
            body = ""
            for _ in range(6):
                n, r = divmod(n, 26)
                body = _LETTERS[r] + body
            tok = "QZ" + body
            if tok not in self.taken and not self.blocked(tok):
                self.taken.add(tok)
                return tok

    def word(self, word: str) -> str:
        if not any(ch.isdigit() for ch in word):
            if word not in self.letters:
                self.letters[word] = self.letter()
            return self.letters[word]
        if word not in self.digits:
            pure = word.isdigit() and word.isascii()
            while True:
                self._n_digits += 1
                n = str(self._n_digits)
                tok = n.zfill(len(word)) if pure else f"N{n.zfill(4)}"
                if tok not in self.taken and not self.blocked(tok):
                    break
            self.taken.add(tok)
            self.digits[word] = tok
        return self.digits[word]


def _rulesets() -> list[cat.Ruleset]:
    return [cat.load_ruleset("v1"), cat.load_ruleset("v2")]


def _pattern_spans(pattern: re.Pattern[str], text: str) -> Iterable[tuple[int, int]]:
    for m in cat._at_every_position(pattern).finditer(text):
        s, e = m.span(1)
        if e > s:
            yield s, e


def _kept_spans(desc: str, rulesets: Sequence[cat.Ruleset], extra: Sequence[re.Pattern[str]]
                ) -> list[tuple[int, int]]:
    """Every span of the folded description some matcher reads."""
    spans: list[tuple[int, int]] = []
    for rs in rulesets:
        for rule in rs.rules:
            spans += cat._spans(rule, desc)
            spans += _pattern_spans(rule.pattern, desc)
            for p in (rule.unless, rule.requires):
                if p is not None:
                    spans += _pattern_spans(p, desc)
    for p in extra:
        for m in p.finditer(desc):
            if m.end() > m.start():
                spans.append(m.span())
        spans += _pattern_spans(p, desc)
    return spans


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _merge(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for s, e in sorted(spans):
        if out and s < out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def _profile_names(profile: dict[str, Any] | None) -> list[str]:
    if not profile:
        return []
    names = [*profile.get("directors", []), *profile.get("shareholders", []), *profile.get("own_accounts", [])]
    if profile.get("business_name"):
        names.append(profile["business_name"])
    return names


def pseudonymise_statement(data: bytes, profile: dict[str, Any] | None = None, *,
                           thresholds: ind.Thresholds | None = None, label: str = "statement") -> Pseudonymised:
    """The pseudonymised copy of the five-column statement `data` and of `profile` (a
    validated case profile, or None). Raises PseudonymiseError, and returns nothing, when
    the statement is refused or the copy would not give the same figures (P10)."""
    try:
        original = contract.checked(data, label)
    except contract.ImportRefused as e:
        raise PseudonymiseError(str(e)) from None
    if profile is not None:
        try:
            profile = store.validate_profile(profile)
        except store.CaseStoreError as e:
            raise PseudonymiseError(f"the case profile: {e}") from None
    th = thresholds or ind.shipped()
    rulesets = _rulesets()
    extra = (*_EXTRA_PATTERNS, ind._names_regex(tuple(th.value("gambling", "operators"))))

    header, rows = _rows(data)
    descs = [cat._fold(r["description"]) for r in rows]
    kept_by_line = [_kept_spans(d, rulesets, extra) for d in descs]

    # Each distinct profile name (by its spelling-free key) and its spans in each line.
    names: dict[str, str] = {}
    for n in _profile_names(profile):
        names.setdefault(_key(n), n)
    name_spans: dict[str, list[list[tuple[int, int]]]] = {}
    for key, n in names.items():
        p = re.compile(cat.names_pattern([n]))
        name_spans[key] = [list(_pattern_spans(p, d)) for d in descs]

    # A name that meets recognised wording anywhere is kept as it is, and so is every name
    # whose region meets it; repeated until nothing more is kept.
    kept_names: set[str] = set()
    while True:
        grown = False
        for i in range(len(descs)):
            fixed = list(kept_by_line[i]) + [s for k in kept_names for s in name_spans[k][i]]
            live = [(k, s) for k in names if k not in kept_names for s in name_spans[k][i]]
            for region in _merge(s for _, s in live):
                if any(_overlaps(region, f) for f in fixed):
                    for k, s in live:
                        if _overlaps(s, region) and k not in kept_names:
                            kept_names.add(k)
                            grown = True
        if not grown:
            break

    originals = {w for d in descs for w in _WORD.findall(d)}
    originals |= {w for n in names.values() for w in _WORD.findall(cat._fold(n))}
    blockers = [re.compile(cat.names_pattern([n])) for k, n in names.items()]

    def blocked(tok: str) -> bool:
        if any(r.pattern.search(tok) for rs in rulesets for r in rs.rules):
            return True
        return any(p.search(tok) for p in extra) or any(p.search(tok) for p in blockers)

    tokens = _Tokens(taken=set(originals), blocked=blocked)
    name_token = {k: tokens.letter() for k in names if k not in kept_names}
    variants: dict[tuple[str, ...], dict[str, str]] = {}

    out_descs: list[str] = []
    words_replaced = 0
    for i, d in enumerate(descs):
        fixed = _merge(list(kept_by_line[i]) + [s for k in kept_names for s in name_spans[k][i]])
        live = [(k, s) for k in name_token for s in name_spans[k][i]]
        regions = _merge(s for _, s in live)
        edits: list[tuple[int, int, str]] = []
        for region in regions:
            found = sorted({(min(s[0] for kk, s in live if kk == k and _overlaps(s, region)), name_token[k])
                            for k, s in live if _overlaps(s, region)})
            toks = tuple(t for _, t in found)
            text = d[region[0]:region[1]]
            spellings = variants.setdefault(toks, {})
            if text not in spellings:
                spellings[text] = "" if not spellings else tokens.letter()
            edits.append((*region, " ".join(toks + ((spellings[text],) if spellings[text] else ()))))
        taken = fixed + regions
        for m in _WORD.finditer(d):
            if any(_overlaps(m.span(), f) for f in taken):
                continue
            edits.append((m.start(), m.end(), tokens.word(m.group())))
            words_replaced += 1
        new = d
        for s, e, text in sorted(edits, reverse=True):
            new = new[:s] + text + new[e:]
        out_descs.append(new)

    for r, d in zip(rows, out_descs, strict=True):
        r["description"] = contract.neutralise_formula(d)
    out = _write_rows(header, rows)

    new_profile = None
    if profile is not None:
        def tok(n: str) -> str:
            k = _key(n)
            return n if k in kept_names else name_token[k]
        new_profile = dict(profile)
        for f in store.LIST_FIELDS:
            new_profile[f] = [tok(n) for n in profile[f]]
        if profile.get("business_name"):
            new_profile["business_name"] = tok(profile["business_name"])
        if profile.get("label"):
            new_profile["label"] = "PSEUDONYMISED " + tokens.letter()
        try:
            new_profile = store.validate_profile(new_profile)
        except store.CaseStoreError as e:  # two names that only the separators told apart
            raise PseudonymiseError(f"the pseudonymised profile would not be valid: {e}; nothing was written") from None

    try:
        copy = contract.checked(out, label)
    except contract.ImportRefused as e:
        raise PseudonymiseError(f"the pseudonymised statement would be refused: {e}; nothing was written") from None
    difference = differs(original, profile, copy, new_profile, th)
    if difference:
        raise PseudonymiseError(f"the pseudonymised copy would change {difference}; nothing was written")
    replaced = len([k for k in names if k not in kept_names])
    return Pseudonymised(out, new_profile, words_replaced, replaced, len(kept_names))


def _rows(data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    text = data.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    return list(reader.fieldnames or []), [dict(r) for r in reader]


def _write_rows(header: list[str], rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=header)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


@dataclass
class Figures:
    """What P10 compares: each line's category, rule and reversal, the figures (the buckets
    among them), the monthly cells and the indicators."""

    lines: list[tuple[str, str | None, str | None, str | None]]
    metrics: list[dict]
    monthly: list[dict]
    indicators: list[dict]


def figures(statement: Statement, profile: dict[str, Any] | None, thresholds: ind.Thresholds | None = None,
            ruleset: cat.Ruleset | None = None) -> Figures:
    """The figures the case pipeline computes for one statement and its profile."""
    s = statement.model_copy(deep=True)
    names = store.profile_names(profile) if profile is not None else None
    s = cat.categorise(s, ruleset or cat.active_ruleset(), profile=names)
    vat = profile.get("vat_stagger") if profile else None
    repay = profile.get("proposed_monthly_repayment") if profile else None
    return Figures(
        lines=[(t.id, t.category.value if t.category else None, t.rule_id, t.reverses) for t in s.transactions],
        metrics=[m.model_dump(mode="json") for m in met.compute(s, vat_stagger=vat, proposed_repayment=repay)],
        monthly=[r.model_dump(mode="json") for r in met.monthly_summary(s)],
        indicators=[i.model_dump(mode="json") for i in ind.raise_indicators(s, thresholds)],
    )


def differs(a: Statement, a_profile: dict[str, Any] | None, b: Statement, b_profile: dict[str, Any] | None,
            thresholds: ind.Thresholds | None = None) -> str | None:
    """What differs between the two statements' figures, under both rule tables, named
    without quoting a line; None when nothing does."""
    for rs in _rulesets():
        try:
            fa, fb = figures(a, a_profile, thresholds, rs), figures(b, b_profile, thresholds, rs)
        except (met.AmountError, met.ReconciliationError) as e:
            return f"the figures under {rs.id} ({e.__class__.__name__})"
        for (ida, *ca), (idb, *cb) in zip(fa.lines, fb.lines, strict=True):
            if ida != idb or ca != cb:
                return f"the category of line {ida} under {rs.id}"
        for ma, mb in zip(fa.metrics, fb.metrics, strict=True):
            if ma != mb:
                return f"figure {ma['id']} under {rs.id}"
        if fa.monthly != fb.monthly:
            return f"the monthly summary under {rs.id}"
        if fa.indicators != fb.indicators:
            return f"the indicators under {rs.id}"
    return None


# --- the command ---------------------------------------------------------------------------


def _converted(src: Path, fmt: str, *, opening_balance: str | None, statement_balance: str | None,
               at: str | None) -> bytes:
    """The five-column statement a source file gives, through its importer."""
    from .importers import bankcsv, monzo, openbanking, truelayer

    base, colon, bank_profile = fmt.partition(":")
    if base not in store.FORMATS or (colon and base != store.BANK_CSV) or (base == store.BANK_CSV and not bank_profile):
        raise PseudonymiseError(f"unknown format {fmt!r}; use one of csv, monzo, openbanking, truelayer "
                                f"or {store.BANK_CSV}:PROFILE")
    if base == "csv":
        return src.read_bytes()
    with tempfile.TemporaryDirectory() as work:
        os.chmod(work, 0o700)
        raw = Path(work) / ("export.json" if base in store.BALANCE_FORMATS else "export.csv")
        dst = Path(work) / "statement.csv"
        raw.write_bytes(src.read_bytes())
        try:
            if base == "monzo":
                monzo.convert(raw, dst, opening_balance)
            elif base == store.BANK_CSV:
                bankcsv.convert(raw, dst, mapping=bankcsv.profile(bank_profile), opening_balance=opening_balance)
            else:
                importer = openbanking if base == "openbanking" else truelayer
                importer.convert(raw, dst, statement_balance=statement_balance, at=at)
        except contract.ImportRefused as e:
            raise PseudonymiseError(f"import refused: {str(e).replace(str(raw), src.name)}") from None
        return dst.read_bytes()


def write(result: Pseudonymised, out: Path, *, source_format: str) -> list[Path]:
    """Write the copy into a new folder `out`, readable by its owner only: the statement, the
    profile when there is one, the label and a manifest. Nothing is overwritten."""
    out = Path(out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise PseudonymiseError(f"{out} already exists and is not an empty folder; nothing was written")
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    files: dict[str, bytes] = {STATEMENT_FILE: result.statement, LABEL_FILE: (result.label + "\n").encode()}
    if result.profile is not None:
        body = yaml.safe_dump(result.profile, sort_keys=False, allow_unicode=True)
        files[PROFILE_FILE] = (f"# {result.label}\n" + body).encode("utf-8")
    files[MANIFEST_FILE] = json.dumps({
        "pseudonymised_personal_data": True, "anonymous": False, "label": result.label,
        "source_format": source_format, "words_replaced": result.words_replaced,
        "profile_names_replaced": result.names_replaced, "profile_names_kept": result.names_kept,
        "kept": "every id, date, amount and balance, and every wording the rule tables recognise",
    }, indent=2).encode("utf-8") + b"\n"
    written = []
    for name, data in files.items():
        p = out / name
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        written.append(p)
    return written


def pseudonymise_file(src: Path, fmt: str, out: Path, *, profile: dict[str, Any] | None = None,
                      opening_balance: str | None = None, statement_balance: str | None = None,
                      at: str | None = None, thresholds: ind.Thresholds | None = None) -> tuple[Pseudonymised, list[Path]]:
    src = Path(src)
    if not src.is_file():
        raise PseudonymiseError(f"no file at {src}")
    data = _converted(src, fmt, opening_balance=opening_balance, statement_balance=statement_balance, at=at)
    result = pseudonymise_statement(data, profile, thresholds=thresholds, label=src.name)
    return result, write(result, out, source_format=fmt)


__all__ = [
    "LABEL",
    "PseudonymiseError",
    "Pseudonymised",
    "differs",
    "figures",
    "pseudonymise_file",
    "pseudonymise_statement",
    "write",
]
