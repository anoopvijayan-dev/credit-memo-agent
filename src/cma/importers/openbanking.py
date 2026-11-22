"""Convert a UK Open Banking (OBIE Account and Transaction API v3.1) transactions file into
the five-column statement format (R36).

The file is what GET /accounts/{AccountId}/transactions returns: one response object
({"Data": {"Transaction": [...]}, "Links": ..., "Meta": ...}), a JSON array of such
responses (one per page), or the pages written one after another. Each transaction carries
TransactionId, Status, CreditDebitIndicator, BookingDateTime, Amount {Amount, Currency},
optionally Balance {Amount, CreditDebitIndicator, Type}, TransactionInformation,
TransactionReference, MerchantDetails and the counterparty's DebtorAccount or
CreditorAccount.

So this module:
- keeps only booked transactions: a pending one is left out and counted, whatever else it
  holds; a status that is neither is refused;
- keeps only GBP: a booked transaction, or a balance, in another currency refuses the
  account, and nothing is ever converted;
- keeps the provider's TransactionId as the line id, so every figure traces back to the bank;
- keeps a transaction that a paged export repeats once, when the repeat is the same in every
  field, and refuses the file when two transactions share an id but differ;
- rebuilds the running balance from the per-line balances (the balance after each line, of a
  booked type), or, when the person supplies one, from a statement balance at the end of a
  date (--statement-balance AMOUNT --at DATE); every per-line balance the file holds is
  checked against the walked one, so a chain that breaks anywhere refuses the file;
- orders lines oldest first by booking date as the bank wrote it, then by the moment booked;
  transactions booked at the same moment are put in an order their per-line balances chain
  in, and refused when no order does. Lines with no balance can sit anywhere among them, so
  in general this is a subset-sum search: an export with a balance on every line, on none or
  on each day's last line is always ordered; one where only some lines at one moment carry a
  balance is ordered when the search finds an order within its limit, and otherwise refused
  saying so (never as a broken chain); the search always ends in bounded time;
- builds the description from TransactionInformation, the merchant's or the counterparty's
  name and the reference, so the categoriser sees what a bank statement would show.

A refusal names the line of the file a transaction's object starts on, never a cell's value
(the importer contract, T28). The balance walk, the check that the output passes the
statement loader and the writing of the statement are the contract's (importers/contract.py).

It reads the file locally and writes a new file. Nothing is sent anywhere.
"""

from __future__ import annotations

import bisect
import json
import json.decoder
import json.scanner
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from ..metrics import AT_LIMIT, at_limit, flows_at_limit, pounds, pounds_text, text_pence
from . import contract
from .contract import ImportRefused

PLAIN_AMOUNT = re.compile(r"^\d+(?:\.\d+)?$")  # OBIE writes an unsigned decimal; the sign is its indicator
SIGNED_AMOUNT = re.compile(r"^[-+]?\d+(?:\.\d{1,2})?$")
# A provider id is kept as it is and written to a CSV text cell, so it may not start with a
# character a spreadsheet reads as a formula (R34) or with a space, nor hold a control character.
FORMULA_OR_SPACE = (*contract.FORMULA_START, " ")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MAX_ID = 210  # OBIE's Max210Text
# A per-line balance of one of these types is the booked balance after the line; an available
# or expected balance can include pending lines or an overdraft limit, so it is not read.
BOOKED_BALANCES = {"ClosingBooked", "InterimBooked", "OpeningBooked", "PreviouslyClosedBooked"}
PER_LINE = "per-line balances"
# Transactions booked at the same moment are put in order by a search for the one order their
# balances chain in; it gives up past this many steps, and the file is refused.
ORDER_STEPS = 500_000
# The meet-in-the-middle that fills a gap gives up when either half has more ways than this.
MEET_LIMIT = 1 << 16
# The first group with a balance tries at most this many starting balances; it lists every
# subset sum of its balance-less lines when it has at most START_SEARCH of them.
START_TRIES = 2_000
START_SEARCH = 16
# With at most this many balance-less lines left, the next balanced line is looked up by the
# sums they reach rather than found by trying every balanced line.
LOOKUP_LOOSE = 18


class OrderUnknown(Exception):
    """The search for an order of same-moment lines gave up before finding one or ruling all out."""

    def __init__(self, lines: int, loose: int):
        super().__init__(lines, loose)
        self.lines, self.loose = lines, loose


class OpenBankingImportError(ImportRefused):
    """An Open Banking file refused; an importer's refusal under the contract (T28)."""


@dataclass
class ImportResult:
    rows: int
    start: date
    end: date
    opening_balance: float
    closing_balance: float
    balance_source: str  # "per-line balances" or "statement balance supplied at DATE"
    pending: int  # pending transactions left out
    repeated: int  # transactions a paged export repeated, kept once


class _Obj(dict):
    """A JSON object, with the offset of its opening brace and any key it gives twice."""

    __slots__ = ("at", "twice")


def _pairs(pairs):
    obj = _Obj(pairs)
    obj.twice = len(obj) != len(pairs)
    return obj


def _scanner(lines_at):
    """A JSON scanner that notes where each object starts, reads numbers exactly, and refuses
    a key given twice (which would let the second silently replace the first) and NaN or
    Infinity, which are not JSON."""
    decoder = json.JSONDecoder(object_pairs_hook=_pairs, parse_float=Decimal)

    def parse_object(s_and_end, *args):
        start = s_and_end[1] - 1
        obj, end = json.decoder.JSONObject(s_and_end, *args)
        obj.at = start
        if obj.twice:
            raise OpenBankingImportError(f"line {lines_at(start)}: a key given more than once in one object")
        return obj, end

    def parse_constant(name):
        raise ValueError(f"{name} is not JSON")

    decoder.parse_object = parse_object
    decoder.parse_constant = parse_constant
    decoder.memo = {}
    return json.scanner.py_make_scanner(decoder)


def _pages(text: str, lines_at) -> list:
    """The response objects the file holds: one, an array of them, or several one after another."""
    scan = _scanner(lines_at)
    ws = json.decoder.WHITESPACE
    docs, idx = [], ws.match(text, 0).end()
    while idx < len(text):
        try:
            doc, idx = scan(text, idx)
        except ImportRefused:
            raise
        except StopIteration as e:
            raise OpenBankingImportError(f"line {lines_at(e.value)}: not JSON") from None
        except (ValueError, RecursionError) as e:  # a JSONDecodeError says where; no cell is quoted
            at = getattr(e, "pos", idx)
            raise OpenBankingImportError(f"line {lines_at(at)}: not JSON") from None
        docs.append(doc)
        idx = ws.match(text, idx).end()
    if not docs:
        raise OpenBankingImportError("line 1: not JSON (the file is empty)")
    pages = []
    for doc in docs:
        pages.extend(doc if isinstance(doc, list) else [doc])
    return pages


def _transactions(pages, lines_at) -> list:
    out = []
    for page in pages:
        data = page.get("Data") if isinstance(page, dict) else None
        txns = data.get("Transaction") if isinstance(data, dict) else None
        if not isinstance(txns, list):
            at = page.at if isinstance(page, _Obj) else 0
            raise OpenBankingImportError(f"line {lines_at(at)}: not an OBIE v3.1 transactions response "
                                         "(no Data.Transaction list)")
        for t in txns:
            if not isinstance(t, _Obj):
                raise OpenBankingImportError(f"line {lines_at(page.at)}: a transaction is not a JSON object")
            out.append(t)
    return out


def _text(t: dict, key: str, line: int, *, required: bool = True) -> str:
    v = t.get(key)
    if v is None and not required:
        return ""
    if not isinstance(v, str):
        what = "is missing" if v is None else "is not text"
        raise OpenBankingImportError(f"line {line}: {key} {what}")
    return v


def _money(obj, what: str, line: int) -> int:
    """An {Amount, Currency} object's unsigned amount in whole pence, read exactly; GBP only."""
    if not isinstance(obj, dict):
        raise OpenBankingImportError(f"line {line}: the {what} is missing or not an object")
    currency = obj.get("Currency")
    if currency != "GBP":
        tail = "; lines are never converted" if what == "amount" else ""
        raise OpenBankingImportError(f"line {line}: the {what} is not in GBP; the account is refused{tail}")
    raw = obj.get("Amount")
    text = str(raw) if isinstance(raw, (str, Decimal, int)) and not isinstance(raw, bool) else ""
    p = text_pence(text) if PLAIN_AMOUNT.match(text) else None
    if p is None:
        raise OpenBankingImportError(f"line {line}: the {what} is not a plain amount in whole pence like 12.40")
    if at_limit(p):
        raise OpenBankingImportError(f"line {line}: the {what} is {AT_LIMIT}; no figure could be reported to the penny")
    return p


def _signed(t: dict, key: str, p: int, line: int) -> int:
    indicator = t.get(key)
    if indicator == "Credit":
        return p
    if indicator == "Debit":
        return -p
    raise OpenBankingImportError(f"line {line}: {key} is neither Credit nor Debit")


def _when(raw: str, line: int, today: date) -> tuple[date, datetime]:
    """The booking date as the bank wrote it, and the moment booked (UTC; a time with no offset
    is read as UTC) to order lines within it."""
    try:
        when = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            when = datetime.combine(date.fromisoformat(raw.strip()), datetime.min.time())
        except ValueError:
            raise OpenBankingImportError(f"line {line}: BookingDateTime is not an ISO 8601 date and time") from None
    d = when.date()
    if d > today:
        raise OpenBankingImportError(f"line {line}: BookingDateTime is in the future")
    moment = when.astimezone(timezone.utc) if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return d, moment


def _description(t: dict, amount: int, line: int) -> str:
    """TransactionInformation, then the merchant's or counterparty's name and the reference,
    each added only if the text so far does not already hold it."""
    party = t.get("DebtorAccount" if amount > 0 else "CreditorAccount")
    merchant = t.get("MerchantDetails")
    parts = [t.get("TransactionInformation"),
             merchant.get("MerchantName") if isinstance(merchant, dict) else None,
             party.get("Name") if isinstance(party, dict) else None,
             t.get("TransactionReference")]
    out = ""
    for part in parts:
        if part is None:
            continue
        if not isinstance(part, str):
            raise OpenBankingImportError(f"line {line}: a description field is not text")
        part = " ".join(part.split()).upper()
        if part and part not in out:
            out = f"{out} {part}".strip()
    return out


def _id(t: dict, line: int) -> str:
    tid = t.get("TransactionId")
    if tid is None:
        raise OpenBankingImportError(f"line {line}: a booked transaction has no TransactionId; its id must be kept")
    if not isinstance(tid, str) or not tid:
        raise OpenBankingImportError(f"line {line}: TransactionId is empty or not text")
    if tid.startswith(FORMULA_OR_SPACE) or tid != tid.strip() or CONTROL.search(tid) or len(tid) > MAX_ID:
        raise OpenBankingImportError(f"line {line}: TransactionId starts with a character a spreadsheet reads as "
                                     f"a formula (= + - @), or with a space, holds a control character or is "
                                     f"longer than {MAX_ID} characters")
    return tid


def _balance(t: dict, line: int) -> int | None:
    bal = t.get("Balance")
    if bal is None:
        return None
    if not isinstance(bal, dict):
        raise OpenBankingImportError(f"line {line}: the balance is not an object")
    if bal.get("Type", "InterimBooked") not in BOOKED_BALANCES:
        return None  # an available or expected balance is not the booked balance after the line
    return _signed(bal, "CreditDebitIndicator", _money(bal.get("Amount"), "balance", line), line)


def _order(group: list[tuple], before: int | None, budget: list[int] | None = None) -> list[tuple] | None:
    """Lines booked at the same moment, in an order their per-line balances chain in from
    `before` (the walked balance before them, None if not yet known); None if no order does.

    A line with no balance constrains nothing but by its amount, and any left over can go after
    the last line with a balance. So an order is a sequence of the lines with a balance, and
    for each, the lines with none that go just before it: they must add up to the gap between
    the balance before it and the one the walk has reached (none is needed when the gap is
    zero, as when every line has a balance). The search runs over the balanced lines, without
    recursion, trying the next line's own gap first; a gap is filled by the loose lines left,
    found by their amounts (all of them, none, one, all but one, or a meet-in-the-middle over
    their amounts), and a state already searched without success is not searched again. Lines
    keep the file's order wherever the balances leave it free.
    """
    if all(x.balance is None for _, x in group):
        return group
    if len(group) == 1:  # one line: it chains from `before` or no order does
        x = group[0][1]
        return group if before is None or before + x.amount == x.balance else None
    bal_ids = [i for i, (_, x) in enumerate(group) if x.balance is not None]
    loose_ids = [i for i, (_, x) in enumerate(group) if x.balance is None]
    amt = [x.amount for _, x in group]
    pre = {i: group[i][1].balance - amt[i] for i in bal_ids}
    post = {i: group[i][1].balance for i in bal_ids}
    by_pre: dict[int, list[int]] = {}
    for i in bal_ids:
        by_pre.setdefault(pre[i], []).append(i)
    posts = set(post.values())
    heads = [i for i in bal_ids if pre[i] not in posts]  # where a chain with no known start begins
    if not loose_ids and len(heads) > 1:
        return None  # with every line balanced, nothing can come before a head: two cannot both be first
    filled: dict[tuple, list[tuple[int, ...]]] = {}
    gave_up = [False]  # a gap the search could not fill or rule out: "no order" cannot then be said

    def fills(rem: tuple[int, ...], gap: int):
        """Ways to take loose lines from `rem` (file order) adding up to `gap`, cheapest first;
        worked out once for each remainder and gap."""
        key = (rem, gap)
        if key not in filled:
            filled[key] = list(_fills(rem, gap))
        return filled[key]

    def _fills(rem: tuple[int, ...], gap: int):
        if gap == 0:
            return [()]
        total = sum(amt[i] for i in rem)
        found: dict[tuple, None] = {}
        if total == gap:
            found[rem] = None
        by_amount: dict[int, int] = {}
        for i in rem:  # one line, the first of each amount
            by_amount.setdefault(amt[i], i)
        for a, i in by_amount.items():
            if a == gap:
                found[(i,)] = None
            if total - a == gap:
                found[tuple(j for j in rem if j != i)] = None
        for take in _meet(rem, gap):
            found[take] = None
        return sorted(found, key=len)  # the fewest lines first: balanced lines sit close together

    def _meet(rem, gap):
        # Subsets by amount: lines of one amount are interchangeable, so only how many of each.
        counts: dict[int, list[int]] = {}
        for i in rem:
            counts.setdefault(amt[i], []).append(i)
        amounts = sorted(counts, key=lambda a: -len(counts[a]))
        half = [amounts[0::2], amounts[1::2]]
        sizes = [1, 1]
        for h in (0, 1):
            for a in half[h]:
                sizes[h] *= len(counts[a]) + 1
        if max(sizes) > MEET_LIMIT:
            gave_up[0] = True  # too many ways to try: this gap is neither filled nor ruled out
            return
        work[0] += sizes[0] + sizes[1]
        tables = []
        for h in (0, 1):
            table: dict[int, list[tuple]] = {0: [()]}
            for a in half[h]:
                grown: dict[int, list[tuple]] = {}
                for sm, picks in table.items():
                    for k in range(len(counts[a]) + 1):
                        grown.setdefault(sm + k * a, []).extend((*p, (a, k)) for p in picks)
                table = grown
            tables.append(table)
        left, right = tables
        for sm, lpicks in left.items():
            for rp in right.get(gap - sm, ()):
                for lp in lpicks:
                    take = {a: k for a, k in (*lp, *rp) if k}
                    if take:
                        yield tuple(sorted(i for a, k in take.items() for i in counts[a][:k]))

    reached: dict[tuple, set] = {}

    def _reach(rem: tuple[int, ...]) -> set:
        if rem not in reached:
            sums = {0}
            for j in rem:
                sums |= {r + amt[j] for r in sums}
            reached[rem] = sums
        return reached[rem]

    def nexts(bal, used: int, rem: tuple[int, ...]):
        """(balanced line, the loose lines before it) to try next, the zero gaps first."""
        if bal is None:
            free = [i for i in bal_ids if not used >> i & 1]
            # With every line balanced, a chain starts at its head (or anywhere, round a cycle).
            first = [i for i in heads if i in free] if (heads and not loose_ids) else \
                [i for i in heads if i in free] + [i for i in free if i not in heads]
            for i in first:
                yield i, ()  # the walk starts at this line's own balance
            return
        for i in by_pre.get(bal, ()):
            if not used >> i & 1:
                yield i, ()
        if not rem:
            return  # no loose line left: only a line with no gap can come next
        free = [i for i in bal_ids if not used >> i & 1 and pre[i] != bal]
        if len(rem) <= LOOKUP_LOOSE:  # few left: only the lines whose gap a sum of them reaches
            reach = _reach(rem)
            cands = [i for i in free if pre[i] - bal in reach]
        else:
            cands = free
        work[0] += len(free)
        # Every way on, across the lines that could come next, the fewest balance-less lines first.
        options = sorted(((len(take), n, i, take) for n, i in enumerate(cands) for take in fills(rem, pre[i] - bal)),
                         key=lambda o: (o[0], o[1]))
        work[0] += len(cands) + len(options)  # finding them is work too: it counts against the limit
        for _, _, i, take in options:
            yield i, take

    def _may_finish(bal, used: int, loose_left: int) -> bool:
        """A balanced line left that no other line left, nor the walk now, leads to needs a
        balance-less line before it; with fewer of those left than such lines, no order is left."""
        free = [i for i in bal_ids if not used >> i & 1]
        if len(free) <= loose_left:
            return True
        free_posts = {post[i] for i in free}
        need = sum(1 for i in free if pre[i] != bal and pre[i] not in free_posts)
        return need <= loose_left

    dead: set = set()
    steps = 0
    work = [0]  # the ways on listed, and the subsets the meet-in-the-middle built, so far
    rem0 = tuple(loose_ids)
    # One frame per balanced line placed: (its iterator of choices, the state it was made in).
    stack = [(nexts(before, 0, rem0), before, 0, rem0)]
    chosen: list[tuple[int, tuple[int, ...]]] = []
    while stack:
        it, bal, used, rem = stack[-1]
        if len(chosen) == len(bal_ids):
            seq = [j for i, take in chosen for j in (*take, i)] + list(rem)  # the loose lines left, last
            return [group[j] for j in seq]
        step = next(it, None)
        if step is None:
            dead.add((bal, used, rem))
            stack.pop()
            if chosen:
                chosen.pop()
            continue
        steps += 1 + work[0]
        if budget is not None:
            budget[0] -= 1 + work[0]
        work[0] = 0
        if steps > ORDER_STEPS or (budget is not None and budget[0] < 0):
            raise OrderUnknown(len(group), len(loose_ids))
        i, take = step
        left = tuple(j for j in rem if j not in set(take)) if take else rem
        state = (post[i], used | 1 << i, left)
        if state in dead:
            continue
        if not _may_finish(post[i], used | 1 << i, len(left)):
            dead.add(state)
            continue
        chosen.append((i, take))
        stack.append((nexts(post[i], used | 1 << i, left), *state))
    if gave_up[0]:
        raise OrderUnknown(len(group), len(loose_ids))
    return None


def _starts(group: list[tuple]):
    """The balances the walk may have reached before `group`, the first group with a per-line
    balance, when nothing before it says: the balance before one of its balanced lines less the
    lines with no balance that may come before that line (none, all, one, all but one, then by
    a bounded search over their amounts), each once, the likeliest first."""
    loose = [x.amount for _, x in group if x.balance is None]
    total = sum(loose)
    posts = {x.balance for _, x in group if x.balance is not None}
    tiers: list[list[int]] = [[], []]  # the first balanced line is a head unless the balances cycle
    for _, x in group:
        pre = None if x.balance is None else x.balance - x.amount
        if pre is not None and pre not in tiers[0] and pre not in tiers[1]:
            tiers[0 if pre not in posts else 1].append(pre)
    sums = [0, total, *loose, *(total - a for a in loose)]
    if len(loose) <= START_SEARCH:  # every sum of a subset of them, by amount
        reach = {0}
        for a in loose:
            reach |= {r + a for r in reach}
        sums += sorted(reach, key=abs)
    seen: set[int] = set()
    for pres in tiers:  # heads, then the rest
        for sm in sums:  # the simplest choice for every such line before a harder one for any
            for pre in pres:
                if pre - sm not in seen:
                    seen.add(pre - sm)
                    yield pre - sm


def _first_order(groups: list[list[tuple]], k: int, cache: dict) -> list[tuple] | None:
    """Order groups[k], the first group with a per-line balance, when the walked balance before
    it is not known. Its own order fixes the balance it ends on, and from there the balance
    before every later group is fixed too (the balance after a group is the balance before it
    plus its amounts, whatever their order). So the start taken is the likeliest one that
    orders this group and every later group with a balance; each order found is kept in
    `cache`, by group and balance before it, for the walk to use."""
    group = groups[k]
    totals = [sum(x.amount for _, x in g) for g in groups]
    later = [m for m in range(k + 1, len(groups)) if any(x.balance is not None for _, x in groups[m])]
    gave_up = False
    fallback = None  # an order of this group alone, should no start let the rest be ordered

    budget = [ORDER_STEPS]  # one budget for every start tried, so the search ends in bounded time

    def ordered(m: int, before: int):
        key = (m, before)
        if key not in cache:
            cache[key] = _order(groups[m], before, budget)
        return cache[key]

    # A later group with a balance on every line pins the start: the walk must reach it at the
    # balance before whichever of its lines comes first, so those are the only starts there are.
    pinned = None
    for m in later:
        if all(x.balance is not None for _, x in groups[m]):
            offset = sum(totals[k:m])
            posts = {x.balance for _, x in groups[m]}
            pres = sorted({x.balance - x.amount for _, x in groups[m]}, key=lambda p: p in posts)  # heads first
            pinned = [p - offset for p in pres]
            break

    for tries, start in enumerate(pinned if pinned is not None else _starts(group)):
        if tries >= START_TRIES:
            gave_up = True
            break
        try:
            order = ordered(k, start)
            if order is None:
                continue
            fallback = fallback or order
            before, at, ok = start + totals[k], k + 1, True
            for m in later:
                before += sum(totals[at:m])
                at = m
                if ordered(m, before) is None:
                    ok = False
                    break
            if ok:
                return order
        except OrderUnknown:
            gave_up = True  # this start was neither shown to serve nor ruled out
            if budget[0] < 0:
                break  # the budget is spent
    loose = sum(x.balance is None for _, x in group)
    if gave_up or (pinned is None and loose > START_SEARCH):
        # Past START_SEARCH balance-less lines, only the likeliest starts were tried: that none
        # served does not show that no order does.
        raise OrderUnknown(len(group), loose)
    return fallback  # a later group then names where its chain breaks; None if this one cannot be ordered


def _first_break(group: list[tuple], before: int | None) -> contract.Line:
    """The first line, in the order the file dates them, whose per-line balance does not follow."""
    bal = before
    for _, x in group:
        after = None if bal is None else bal + x.amount
        if x.balance is not None and after is not None and x.balance != after:
            return x
        bal = x.balance if x.balance is not None else after
    return group[0][1]


def _statement_balance(raw: str | None, at_raw: str | None) -> tuple[int, date] | None:
    if raw is None and at_raw is None:
        return None
    if raw is None:
        raise OpenBankingImportError("--at needs --statement-balance: the balance at the end of that date")
    if at_raw is None:
        raise OpenBankingImportError("--statement-balance needs --at: the date whose closing balance it is")
    text = raw.strip()
    p = text_pence(text) if SIGNED_AMOUNT.match(text) else None
    if p is None:
        raise OpenBankingImportError("--statement-balance is not a plain amount like -12.40")
    if at_limit(p):
        raise OpenBankingImportError(f"--statement-balance is {AT_LIMIT}; no figure could be reported to the penny")
    try:
        at = date.fromisoformat(at_raw.strip())
    except ValueError:
        raise OpenBankingImportError("--at is not a date like 2026-04-30") from None
    return p, at


def convert(src: Path, dst: Path, *, statement_balance: str | None = None, at: str | None = None,
            today: date | None = None) -> ImportResult:
    """`statement_balance` is the balance at the end of the date `at`, as the person gave it
    (read exactly); both or neither."""
    today = today or date.today()
    supplied = _statement_balance(statement_balance, at)
    data = Path(src).read_bytes()  # read once
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        line = data.count(b"\n", 0, e.start) + 1
        raise OpenBankingImportError(f"line {line}: a byte that is not UTF-8") from None
    breaks = [i for i, c in enumerate(text) if c == "\n"]

    def lines_at(offset: int) -> int:
        return bisect.bisect_left(breaks, offset) + 1

    txns = _transactions(_pages(text, lines_at), lines_at)
    if not txns:
        raise OpenBankingImportError(f"{src}: no transactions")

    booked: list[tuple] = []  # ((date, moment, file position), Line)
    first: dict[str, tuple[int, dict]] = {}  # each kept id's line and object
    pending = repeated = 0
    pending_ids: set[str] = set()  # a pending transaction a page repeats is counted once
    account = None
    for pos, t in enumerate(txns):
        line = lines_at(t.at)
        status = t.get("Status")
        if status == "Pending":
            tid = t.get("TransactionId")
            if not (isinstance(tid, str) and tid in pending_ids):
                pending += 1  # left out, whatever else it holds
            if isinstance(tid, str):
                pending_ids.add(tid)
            continue
        if status != "Booked":
            raise OpenBankingImportError(f"line {line}: Status is neither Booked nor Pending")
        acc = t.get("AccountId")
        if acc is not None:
            if account is None:
                account = (acc, line)
            elif acc != account[0]:
                raise OpenBankingImportError(f"line {line}: another account's transaction (the first is on line "
                                             f"{account[1]}); import one account per file")
        tid = _id(t, line)
        if tid in first:
            if first[tid][1] == t:
                repeated += 1  # a page repeated it: kept once
                continue
            raise OpenBankingImportError(f"line {line}: transaction id repeated from line {first[tid][0]} with "
                                         "different content")
        first[tid] = (line, t)
        amount = _signed(t, "CreditDebitIndicator", _money(t.get("Amount"), "amount", line), line)
        d, moment = _when(_text(t, "BookingDateTime", line), line, today)
        x = contract.Line(line=line, id=tid, date=d, description=_description(t, amount, line), amount=amount,
                          balance=_balance(t, line))
        booked.append(((d, moment, pos), x))
    # A transaction one page shows pending and another booked (it settled between fetches) is
    # kept as booked, so it was not left out.
    pending -= len(pending_ids & first.keys())
    if not booked:
        raise OpenBankingImportError(f"{src}: no booked transactions ({pending} pending left out)")
    reached = flows_at_limit([x.amount for _, x in booked])
    if reached:
        raise OpenBankingImportError(f"{' and '.join(reached)} {'total' if len(reached) > 1 else 'totals'} "
                                     f"{AT_LIMIT}: no figure could be reported to the penny")

    # Oldest first. A file written newest first keeps its lines booked at one moment in the
    # reverse of the order it wrote them, before their balances order them.
    newest_first = (booked[0][0][:2] > booked[-1][0][:2])
    booked.sort(key=lambda b: (b[0][0], b[0][1], -b[0][2] if newest_first else b[0][2]))

    # Lines booked at one moment, in the order their per-line balances chain in. The chain is
    # the per-line balances' own, so the order is found before any opening balance is known.
    lines: list[contract.Line] = []
    walked: int | None = None  # the balance after the lines ordered so far, once a per-line one is seen
    groups: list[list[tuple]] = []
    i = 0
    while i < len(booked):
        j = i
        while j < len(booked) and booked[j][0][:2] == booked[i][0][:2]:
            j += 1
        groups.append(booked[i:j])
        i = j
    cache: dict = {}
    for k, same in enumerate(groups):
        first_balanced = walked is None and any(x.balance is not None for _, x in same)
        try:
            if first_balanced:
                group = _first_order(groups, k, cache)
            else:
                group = cache[(k, walked)] if (k, walked) in cache else _order(same, walked)
        except OrderUnknown as e:
            raise OpenBankingImportError(
                f"line {same[0][1].line}: {e.lines} transactions are booked at the same moment and {e.loose} of "
                "them carry no balance; no order of them that keeps the balance chain was found within the "
                "search limit, so the file was not imported (it may be valid: an export with a balance on every "
                "line, or on none, is always read)") from None
        if group is None and len(same) == 1:
            group = same  # one line that does not follow: the walk below names it
        if group is None:
            raise OpenBankingImportError(f"line {_first_break(same, walked).line}: no order of the "
                                         "transactions booked at the same time keeps the balance chain; the "
                                         "export may be missing transactions")
        for _, x in group:
            walked = x.balance if walked is None else walked + x.amount
            lines.append(x)

    # The opening balance: from the statement balance supplied, else the first per-line balance;
    # with both, they must agree.
    k = next((k for k, x in enumerate(lines) if x.balance is not None), None)
    implied = None if k is None else lines[k].balance - sum(x.amount for x in lines[:k + 1])
    if supplied is not None:
        bal_at, at_date = supplied
        start, end = lines[0].date, lines[-1].date
        if not start - timedelta(days=1) <= at_date <= end:
            raise OpenBankingImportError(f"--at {at_date} is outside the file's period, {start - timedelta(days=1)} "
                                         f"(the day before its first transaction) to {end}")
        before_at = sum(x.amount for x in lines if x.date <= at_date)
        opening = bal_at - before_at
        source = f"statement balance supplied at {at_date}"
        if implied is not None and implied != opening:
            raise OpenBankingImportError(f"--statement-balance {pounds_text(bal_at)} at {at_date} disagrees with "
                                         f"the per-line balances, which give {pounds_text(implied + before_at)} "
                                         f"then (line {lines[k].line}); leave it out to use them")
    elif implied is not None:
        opening, source = implied, PER_LINE
    else:
        raise OpenBankingImportError(f"{src}: no per-line balances; pass --statement-balance AMOUNT --at DATE "
                                     "(a balance from the bank's statement, at the end of a date)")
    try:
        balances = contract.walk(lines, opening, column="the per-line balance")
        contract.write_statement(dst, lines, balances, label=src)
    except ImportRefused as e:  # said as this importer's refusal
        raise OpenBankingImportError(str(e)) from None
    return ImportResult(rows=len(lines), start=lines[0].date, end=lines[-1].date,
                        opening_balance=pounds(opening), closing_balance=pounds(balances[-1]),
                        balance_source=source, pending=pending, repeated=repeated)
