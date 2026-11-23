"""Pseudonymise a statement and its case profile (R46, C9).

`cma pseudonymise` writes a copy of a statement in which personal and business names and
account numbers are replaced by consistent tokens, while the recognised institutions
(acquirers, gateways, lenders, HMRC: every wording the rule tables or the gambling list
recognise, and every counterparty a firm rule names) and every id, date, amount and balance
are kept. Every other column the statement loader accepts (a payee, a reference, an account
number) is pseudonymised as the description is, with the same tokens. The case profile is tokenised the
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
  reversal links) groups the lines as before. A word a matcher reads only in part (FINANCE in
  GREENFINANCE, which v1's FINANCE\\b reads) keeps that part alone, and the rest of the word
  becomes a token, so a name never survives inside a longer word.

A profile name that holds recognised wording somewhere (a business called Greenfield Capital,
a director named after a lender) keeps only that wording: its other words become their word
tokens, in the profile and the descriptions alike, so the rules and the profile still read the
copy as they read the original, and the name itself does not survive. Before anything is written, the copy is categorised and computed and compared with
the original, line by line and figure by figure; any difference refuses the copy, naming the
figure, and nothing is written. The token mapping itself is never written.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
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

    def word(self, word: str, key: str | None = None) -> str:
        """The token of `word`; `key`, when given, is what the token is kept consistent by
        (a part of a longer word is keyed by the whole word and its place in it)."""
        key = word if key is None else key
        if not any(ch.isdigit() for ch in word):
            if key not in self.letters:
                self.letters[key] = self.letter()
            return self.letters[key]
        if key not in self.digits:
            pure = word.isdigit() and word.isascii()
            while True:
                self._n_digits += 1
                n = str(self._n_digits)
                tok = n.zfill(len(word)) if pure else f"N{n.zfill(4)}"
                if tok not in self.taken and not self.blocked(tok):
                    break
            self.taken.add(tok)
            self.digits[key] = tok
        return self.digits[key]

    def partly(self, word: str, kept: Sequence[tuple[int, int]]) -> str:
        """`word` with only the parts inside `kept` (spans within the word) left as written and
        every other part replaced by a token: recognised wording inside a longer word (FINANCE
        in GREENFINANCE) stays, and the rest of the word, which may be a name, does not. The
        tokens are keyed by the whole word and its kept parts, so one word is always written
        the same way and two words never the same way."""
        kept = _merge((max(s, 0), min(e, len(word))) for s, e in kept if min(e, len(word)) > max(s, 0))
        if not kept:
            return self.word(word)
        if kept == [(0, len(word))]:
            return word
        out, pos = [], 0
        for i, (s, e) in enumerate([*kept, (len(word), len(word))]):
            if s > pos:
                out.append(self.word(word[pos:s], f"{word}\x00{kept}\x00{i}"))
            out.append(word[s:e])
            pos = e
        return "".join(out)


def _rulesets() -> list[cat.Ruleset]:
    return [cat.load_ruleset("v1"), cat.load_ruleset("v2")]


def _pattern_spans(pattern: re.Pattern[str], text: str) -> Iterable[tuple[int, int]]:
    for m in cat._at_every_position(pattern).finditer(text):
        s, e = m.span(1)
        if e > s:
            yield s, e


def _kept_spans(desc: str, rules: Sequence[cat.Rule], extra: Sequence[re.Pattern[str]]
                ) -> list[tuple[int, int]]:
    """Every span of the folded description some matcher reads: a rule of the v1 or v2 table,
    a firm rule (R20), or one of the extra patterns."""
    spans: list[tuple[int, int]] = []
    for rule in rules:
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
                           thresholds: ind.Thresholds | None = None, label: str = "statement",
                           firm_rules: Sequence[cat.Rule] = ()) -> Pseudonymised:
    """The pseudonymised copy of the five-column statement `data` and of `profile` (a
    validated case profile, or None). `firm_rules` are the firm's own rules (R20), whose
    counterparties are kept and with which the copy is checked, as a case is categorised.
    Every column beyond id, date, amount and balance is pseudonymised, the description and
    any other column the loader accepts (a payee, a reference, an account number) alike.
    Raises PseudonymiseError, and returns nothing, when the statement is refused or the copy
    would not give the same figures (P10)."""
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
    firm_rules = tuple(firm_rules)
    all_rules = [*(r for rs in rulesets for r in rs.rules), *firm_rules]
    extra = (*_EXTRA_PATTERNS, ind._names_regex(tuple(th.value("gambling", "operators"))))

    header, rows, cells = _rows(data)
    # Every text to pseudonymise: each line's description, then every other cell that is not
    # its id, date, amount or balance (R46: a payee or account-number column too). Only the
    # descriptions reach the categoriser, but every cell goes through the same tokens.
    descs = [cat._fold(rows[r][c]) for r, c in cells]
    kept_by_line = [_kept_spans(d, all_rules, extra) for d in descs]

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
        if any(r.pattern.search(tok) for r in all_rules):
            return True
        return any(p.search(tok) for p in extra) or any(p.search(tok) for p in blockers)

    tokens = _Tokens(taken=set(originals), blocked=blocked)

    def within(m: re.Match[str], spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
        """The parts of word `m` that `spans` cover, as spans within the word."""
        return [(s - m.start(), e - m.start()) for s, e in spans if _overlaps(m.span(), (s, e))]

    # A kept name's words, as the descriptions write them (or, for a name no line holds, as its
    # own wording reads); its profile entry is written with the same words (R46, P10).
    name_words: dict[str, dict[str, str]] = {}
    for k in kept_names:
        words: dict[str, str] = {}
        seen = False
        for i, d in enumerate(descs):
            for span in name_spans[k][i]:
                seen = True
                for m in _WORD.finditer(d, span[0], span[1]):
                    words.setdefault(m.group(), tokens.partly(m.group(), within(m, kept_by_line[i])))
        if not seen:
            folded = cat._fold(names[k])
            own = _kept_spans(folded, all_rules, extra)
            for m in _WORD.finditer(folded):
                words.setdefault(m.group(), tokens.partly(m.group(), within(m, own)))
        name_words[k] = words

    name_token = {k: tokens.letter() for k in names if k not in kept_names}
    variants: dict[tuple[str, ...], dict[str, str]] = {}

    out_descs: list[str] = []
    words_replaced = 0
    for i, d in enumerate(descs):
        # A name that holds recognised wording is not kept whole: its words go through the word
        # path below, so only the recognised words stay and the rest become their word tokens,
        # as they do in the profile (R46; T39 pass: "Greenfield Capital Ltd" was kept whole).
        fixed = _merge(kept_by_line[i])
        live = [(k, s) for k in name_token for s in name_spans[k][i]]
        regions = _merge(s for _, s in live)
        edits: list[tuple[int, int, str]] = []
        for region in regions:
            found = sorted({(min(s[0] for kk, s in live if kk == k and _overlaps(s, region)), name_token[k])
                            for k, s in live if _overlaps(s, region)})
            toks = tuple(t for _, t in found)
            # A run of whitespace is one space to the counterparty normaliser, so it is one spelling.
            text = " ".join(d[region[0]:region[1]].split())
            spellings = variants.setdefault(toks, {})
            if text not in spellings:
                spellings[text] = "" if not spellings else tokens.letter()
            edits.append((*region, " ".join(toks + ((spellings[text],) if spellings[text] else ()))))
        for m in _WORD.finditer(d):
            if any(_overlaps(m.span(), f) for f in regions):
                continue
            # A word a rule reads only in part (FINANCE in GREENFINANCE) keeps that part alone.
            new_word = tokens.partly(m.group(), within(m, fixed))
            if new_word != m.group():
                edits.append((m.start(), m.end(), new_word))
                words_replaced += 1
        new = d
        for s, e, text in sorted(edits, reverse=True):
            new = new[:s] + text + new[e:]
        out_descs.append(new)

    for (r, c), d in zip(cells, out_descs, strict=True):
        rows[r][c] = contract.neutralise_formula(d)
    out = _write_rows(header, rows)

    new_profile = None
    if profile is not None:
        def tok(n: str) -> str:
            k = _key(n)
            if k not in kept_names:
                return name_token[k]
            folded = cat._fold(n)
            return _WORD.sub(lambda m: name_words[k].get(m.group()) or tokens.word(m.group()), folded)
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
    difference = differs(original, profile, copy, new_profile, th, firm_rules)
    if difference:
        raise PseudonymiseError(f"the pseudonymised copy would change {difference}; nothing was written")
    replaced = len([k for k in names if k not in kept_names])
    return Pseudonymised(out, new_profile, words_replaced, replaced, len(kept_names))


# The columns the copy keeps as written (R46): every other column is pseudonymised.
_KEPT_COLUMNS = frozenset({"id", "date", "amount", "balance"})


def _rows(data: bytes) -> tuple[list[str], list[list[str]], list[tuple[int, int]]]:
    """The header as written, the statement's rows (blank lines dropped, as the loader drops
    them) and the (row, column) of every cell to pseudonymise, descriptions first. Columns are
    known by their names stripped, as the loader knows them, so a header written with spaces
    around its names is read as the loader reads it."""
    text = data.decode("utf-8-sig")
    records = list(csv.reader(io.StringIO(text, newline="")))
    header = records[0] if records else []
    rows = [r for r in records[1:] if r]
    names = [h.strip() for h in header]
    width = max([len(header), *(len(r) for r in rows)])
    desc = names.index("description")
    others = [c for c in range(width) if c != desc and (c >= len(names) or names[c] not in _KEPT_COLUMNS)]
    cells = [(i, desc) for i, r in enumerate(rows) if desc < len(r)]
    cells += [(i, c) for c in others for i, r in enumerate(rows) if c < len(r) and r[c].strip()]
    return header, rows, cells


def _write_rows(header: list[str], rows: list[list[str]]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
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
            ruleset: cat.Ruleset | None = None, firm_rules: Sequence[cat.Rule] = ()) -> Figures:
    """The figures the case pipeline computes for one statement, its profile and the firm's
    rules (R20; overrides.py categorises a case with firm_rules.for_case())."""
    s = statement.model_copy(deep=True)
    names = store.profile_names(profile) if profile is not None else None
    s = cat.categorise(s, ruleset or cat.active_ruleset(), profile=names, firm_rules=tuple(firm_rules))
    vat = profile.get("vat_stagger") if profile else None
    repay = profile.get("proposed_monthly_repayment") if profile else None
    return Figures(
        lines=[(t.id, t.category.value if t.category else None, t.rule_id, t.reverses) for t in s.transactions],
        metrics=[m.model_dump(mode="json") for m in met.compute(s, vat_stagger=vat, proposed_repayment=repay)],
        monthly=[r.model_dump(mode="json") for r in met.monthly_summary(s)],
        indicators=[i.model_dump(mode="json") for i in ind.raise_indicators(s, thresholds)],
    )


def differs(a: Statement, a_profile: dict[str, Any] | None, b: Statement, b_profile: dict[str, Any] | None,
            thresholds: ind.Thresholds | None = None, firm_rules: Sequence[cat.Rule] = ()) -> str | None:
    """What differs between the two statements' figures, under both rule tables, named
    without quoting a line; None when nothing does."""
    for rs in _rulesets():
        try:
            fa = figures(a, a_profile, thresholds, rs, firm_rules)
            fb = figures(b, b_profile, thresholds, rs, firm_rules)
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
    profile when there is one, the label and a manifest. Nothing is overwritten.

    The files are written into a staging folder beside `out` and the folder is renamed into
    place, so a failure part-way (a full disk, an I/O error) leaves no partial copy at `out`:
    the staging folder is removed, and the next run starts clean."""
    out = Path(out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise PseudonymiseError(f"{out} already exists and is not an empty folder; nothing was written")
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{out.name}.", suffix=".part", dir=out.parent))
    try:
        os.chmod(stage, 0o700)
        names = _write_files(result, stage, source_format=source_format)
        if out.exists():
            out.rmdir()  # empty, checked above
        os.rename(stage, out)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return [out / n for n in names]


def _write_files(result: Pseudonymised, out: Path, *, source_format: str) -> list[str]:
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
        written.append(name)
    return written


def pseudonymise_file(src: Path, fmt: str, out: Path, *, profile: dict[str, Any] | None = None,
                      opening_balance: str | None = None, statement_balance: str | None = None,
                      at: str | None = None, thresholds: ind.Thresholds | None = None,
                      firm_rules: Sequence[cat.Rule] = ()) -> tuple[Pseudonymised, list[Path]]:
    src = Path(src)
    if not src.is_file():
        raise PseudonymiseError(f"no file at {src}")
    data = _converted(src, fmt, opening_balance=opening_balance, statement_balance=statement_balance, at=at)
    result = pseudonymise_statement(data, profile, thresholds=thresholds, label=src.name, firm_rules=firm_rules)
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
