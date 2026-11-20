"""The case log: an append-only, hash-chained record of everything done to a case (R42, R43).

One JSON object per line, written in a single write that ends with its newline:

    {"seq": 3, "prev": "<hash of entry 2>", "at": "2026-11-16T10:04:01.123456Z",
     "event": "override", "data": {...}, "hash": "<sha256 of the other five fields>"}

The first entry's `prev` is GENESIS. Each entry carries the hash of the entry before it
and its own hash, so a changed, removed, inserted or reordered entry anywhere before the
head breaks the chain on the next load, and `verify` names the first entry that does not
hold. A line is read strictly: it must be exactly the canonical bytes the writer
produces, so a repeated member, a number no float can hold, or reformatting is named as
a bad entry rather than reinterpreted or raised.

The chain alone cannot see a history rewritten from some entry onwards with every hash
recomputed, nor the head entry removed. So each append, after its line is on disk, also
replaces a head file beside the log (`log.jsonl.head`: {"count": n, "hash": h,
"hashes": [the hash of every entry, in order]}), and every load compares the chain with
it entry by entry: the first entry whose hash is not the one recorded is named as the
first bad entry, even when every link after it was recomputed, and a chain that ends
before or after the recorded count is broken there. Rewriting the log and the head file
together is not detectable from these files; a sign-off or pack pins the head it saw.

An append can stop half way. A last line without its newline, or a complete last line
the head file does not yet record (power lost between the two writes), is a torn entry:
reported as incomplete, not as tampering, and in the way of appends and sign-off until
someone confirms dropping it (`drop_torn_entry`), which is itself logged. A reader that
does not hold the lock may catch a writer between the two writes, so it looks again
briefly and then takes the lock to read once more. If another writer still holds the
lock, that writer's append is in progress: the reader reports the log as recorded (the
intact entries, `in_progress` set) and never asks anyone to drop a live append. Only a
reader that holds the lock, or that could take it, reports a torn entry.

One writer at a time: every append takes an exclusive, non-blocking lock on a sibling
lock file and re-reads the log under it, and the caller states the position it last
saw. A second tab or terminal that read an older position gets LogConflict and reloads;
one that tries while another holds the lock gets CaseLocked, including another thread
using the same CaseLog object. The operating system drops the lock when its holder
dies, so a crash never leaves the case locked. Forking a process while it holds the lock
is not supported (the tool never does): a child is refused through an inherited CaseLog,
and leaving an inherited block releases nothing. Case folders must be on a local disk: on a network
share the operating system may emulate the lock per process, which does not separate
two CaseLog objects in one process.

Messages name entries by number only and never quote their content: data holds case
details, and errors end up in terminals and logs.
"""

from __future__ import annotations

import errno
import hashlib
import itertools
import json
import os
import signal
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

GENESIS = "0" * 64
HASHED_FIELDS = ("seq", "prev", "at", "event", "data")
ENTRY_FIELDS = frozenset((*HASHED_FIELDS, "hash"))
TORN_DROPPED = "torn_entry_dropped"
HEAD_FIELDS = frozenset(("count", "hash", "hashes"))
READ_RETRIES, READ_PAUSE = 5, 0.02  # seconds; a writer paused for longer is handled in verify
WRITER_WAIT = 0.2  # seconds a writer waits for another writer's hold to end before refusing
READER_WAIT = 10.0  # seconds a writer waits while only readers hold the case (R42: a reader is no writer)
_UNREADABLE = object()


class CaseLogError(Exception):
    """Base for case-log refusals."""


class CaseLogBroken(CaseLogError):
    """The chain does not hold, or the last entry is incomplete. Nothing may be appended
    and no sign-off accepted until it is resolved."""

    def __init__(self, path: Path, first_bad: int, reason: str, torn: bool):
        self.first_bad = first_bad
        self.torn = torn
        self.reason = reason
        super().__init__(f"{path}: case log entry {first_bad} {reason}")


class LogConflict(CaseLogError):
    """The writer's view of the log is stale: someone appended since it last read."""

    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"case log is at entry {actual}, not {expected}: it changed since you loaded it; reload"
        )


class CaseLocked(CaseLogError):
    """Another writer holds the case."""


class SaveNotConfirmed(CaseLocked):
    """A save whose read-back failed and whose head record could not be taken back: it is
    neither confirmed recorded nor left unrecorded (R42, D143). Check the case log, which
    shows whether its entry is there, before anything else is saved."""


class SaveNotRecorded(CaseLocked):
    """A save stopped after its line was written: the case lock file was removed or
    replaced, or the log or head file changed under it. The entry, if on disk, is past the
    recorded entries, as a crash leaves it, and the next holder confirms dropping it (R42)."""


@dataclass(frozen=True)
class Entry:
    seq: int
    prev: str
    at: str
    event: str
    data: dict[str, Any]
    hash: str


@dataclass(frozen=True)
class Head:
    """Where the intact log ends: `position` entries, the last with hash `hash`."""

    position: int
    hash: str


@dataclass(frozen=True)
class LogCheck:
    head: Head  # the end of the intact prefix
    entries: list[Entry] = field(repr=False)
    first_bad: int | None = None  # 1-based entry number; None when the log is intact
    reason: str = ""
    torn: bool = False
    intact_bytes: int = field(default=0, repr=False)
    in_progress: bool = False  # the case is held: `head` is the log as recorded, the rest unchecked
    recorded: int | None = field(default=None, repr=False)  # entries the head file records
    log_bytes: int = field(default=0, repr=False)  # the whole log as read, torn tail included
    log_digest: str = field(default="", repr=False)  # sha256 of those bytes
    checked: bool = True  # False: the case is held and no steady read was had; nothing is claimed

    def as_recorded(self) -> LogCheck:
        """The log as the head file records it, for a failure that lies wholly after the
        recorded entries (which were all walked and matched): what a reader reports while
        the case is held. What follows them is being saved, not checked (R43)."""
        k = self.recorded or 0
        kept = self.entries[:k]
        return LogCheck(Head(k, kept[-1].hash if kept else GENESIS), kept,
                        intact_bytes=sum(len(_line(e)) for e in kept), in_progress=True, recorded=k)

    @property
    def past_recorded(self) -> bool:
        """The first failure is after every entry the head file records."""
        return self.recorded is not None and self.first_bad is not None and self.first_bad > self.recorded

    @property
    def ok(self) -> bool:
        """No entry is bad. While the case is held (`in_progress`) this covers only the
        recorded entries, and with `checked` False it covers nothing."""
        return self.first_bad is None and self.checked


def _digest(raw: bytes | None) -> str:
    return "" if raw is None else hashlib.sha256(raw).hexdigest()


def _canonical(obj: Any) -> bytes:
    # allow_nan=False: NaN and Infinity are not JSON, and another reader of the log
    # (verify, a future hosted version) must be able to recompute every hash.
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _line(entry: Entry) -> bytes:
    """The exact line the writer produced for an entry that was read back intact. Its fields
    are taken as they are, not copied deeply: dataclasses.asdict recurses through the data
    further than the JSON encoder that already accepted it (T12 review 37)."""
    return _canonical({"seq": entry.seq, "prev": entry.prev, "at": entry.at, "event": entry.event,
                       "data": entry.data, "hash": entry.hash}) + b"\n"


def entry_hash(entry: dict[str, Any]) -> str:
    """sha256 over the canonical JSON of the hashed fields (everything but `hash`)."""
    return hashlib.sha256(_canonical({k: entry.get(k) for k in HASHED_FIELDS})).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _unique_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError("duplicate member")
    return obj


def _no_constant(token: str) -> Any:
    raise ValueError(f"{token} is not JSON")


def _decode(line: bytes) -> Any:
    """The entry a line holds, read strictly: no duplicate members, no NaN or Infinity,
    and the line must be exactly the canonical bytes the writer produces. Anything a
    lenient decoder would quietly reinterpret (a repeated member, 1e999 read as
    infinity, other spacing or key order) is therefore not the entry that was hashed."""
    obj = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_members,
                     parse_constant=_no_constant)
    if _canonical(obj) != line:
        raise ValueError("not in canonical form")
    return obj


def _parse(line: bytes, n: int, prev: str) -> tuple[Entry | None, str]:
    """Check one complete line as entry number `n` following hash `prev`."""
    try:
        obj = _decode(line)
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
        return None, "is not a readable entry: it is not exactly what the log writes"
    if not isinstance(obj, dict) or set(obj) != ENTRY_FIELDS:
        return None, "has missing or extra fields"
    seq, hsh = obj["seq"], obj["hash"]
    if not isinstance(seq, int) or isinstance(seq, bool) or not isinstance(hsh, str) \
            or not isinstance(obj["prev"], str) or not isinstance(obj["at"], str) \
            or not isinstance(obj["event"], str) or not isinstance(obj["data"], dict):
        return None, "has a field of the wrong type"
    if entry_hash(obj) != hsh:
        return None, "was changed: its hash does not match its content"
    if seq != n:
        return None, f"is out of place: it says it is entry {seq}; an entry is missing, added or reordered"
    if obj["prev"] != prev:
        return None, f"does not follow entry {n - 1}: the hash it carries for it does not match"
    return Entry(**obj), ""


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _read_head(path: Path, *, raise_os: bool = False) -> list[str] | object | None:
    """The hash of every entry the head file records, in order; None if there is no head
    file; _UNREADABLE if it cannot be read or is not exactly what the writer produces.
    With `raise_os` (a save's own re-checks, D159) an operating-system error is raised, so
    the save names it, rather than read as a changed head."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:  # a directory in its place, no permission: it cannot vouch for the log
        if raise_os:
            raise
        return _UNREADABLE
    try:
        if not raw.endswith(b"\n"):
            raise ValueError("no newline")
        obj = _decode(raw[:-1])
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
        return _UNREADABLE
    if not isinstance(obj, dict) or set(obj) != HEAD_FIELDS:
        return _UNREADABLE
    count, hsh, hashes = obj["count"], obj["hash"], obj["hashes"]
    if not isinstance(count, int) or isinstance(count, bool) or not isinstance(hashes, list) \
            or len(hashes) != count or not all(_is_hash(h) for h in hashes) \
            or hsh != (hashes[-1] if hashes else GENESIS):
        return _UNREADABLE
    return hashes


def _head_bytes(hashes: list[str]) -> bytes:
    return _canonical({"count": len(hashes), "hash": hashes[-1] if hashes else GENESIS,
                       "hashes": hashes}) + b"\n"


def _check(raw: bytes | None, recorded: list[str] | object | None) -> LogCheck:
    """Walk the chain in `raw` to the first entry that does not hold, compare every entry
    walked with the head file's record, and name the earlier of the two failures."""
    if raw is None:
        return LogCheck(Head(0, GENESIS), [], 1, "cannot be read: the log file cannot be opened", False, 0)
    pieces = raw.split(b"\n")
    complete, tail = pieces[:-1], pieces[-1]  # tail is non-empty only if torn
    entries: list[Entry] = []
    ends = [0]  # ends[k]: bytes up to and including entry k's newline
    prev = GENESIS
    chain_bad, chain_reason = None, ""
    for n, line in enumerate(complete, start=1):
        entry, reason = _parse(line, n, prev)
        if entry is None:
            chain_bad, chain_reason = n, reason
            break
        entries.append(entry)
        prev = entry.hash
        ends.append(ends[-1] + len(line) + 1)

    if recorded is None and raw:
        # Every writer creates the head file before the first line, so a log with no head
        # beside it was damaged; nothing in it is a torn write to drop.
        return LogCheck(Head(0, GENESIS), [], 1, "cannot be trusted: the head file beside the "
                        "log is missing", False, 0)
    hashes: list[str] = recorded if isinstance(recorded, list) else []  # none yet: empty
    n, count = len(entries), len(hashes)

    def at(k: int) -> Head:
        return Head(k, entries[k - 1].hash if k else GENESIS)

    def failed(first_bad: int, reason: str, torn: bool = False) -> LogCheck:
        return LogCheck(at(first_bad - 1), entries[: first_bad - 1], first_bad, reason, torn,
                        ends[first_bad - 1], recorded=None if recorded is _UNREADABLE else count,
                        log_bytes=len(raw), log_digest=_digest(raw))

    if recorded is _UNREADABLE:
        return failed(1, "cannot be trusted: the head file beside the log is unreadable")
    same = 0
    while same < min(n, count) and entries[same].hash == hashes[same]:
        same += 1
    if same < min(n, count):
        return failed(same + 1, "does not match the head file: the log was rewritten from "
                                "this entry on")
    if chain_bad is not None:
        return failed(chain_bad, chain_reason)
    if n == count and not tail:
        return LogCheck(at(n), entries, intact_bytes=ends[-1], recorded=count, log_bytes=len(raw),
                        log_digest=_digest(raw))
    if n == count:
        return failed(n + 1, "is incomplete: the write did not finish (the process or power "
                             "stopped); confirm dropping it before anything else is recorded "
                             "or signed off", torn=True)
    if n == count + 1 and not tail:
        return failed(n, "is incomplete: it was written but the head file was not updated (the "
                         "process or power stopped); confirm dropping it before anything else "
                         "is recorded or signed off", torn=True)
    if recorded is None:  # entries on disk, and no torn first append explains them
        return failed(1, "cannot be trusted: the head file beside the log is missing")
    if n < count and tail:
        # The head file records this entry, and only part of its line is there: damage to a
        # recorded entry, named incomplete, and not one to drop like a crash's torn write.
        return failed(n + 1, f"is incomplete: the head file records {count} entries and this "
                             "entry's line is cut short")
    if n < count:
        return failed(n + 1, f"is missing: the head file records {count} entries and the log "
                             f"holds {n}")
    return failed(count + 1, f"was added after the head the head file records (entry {count})")


def _refuse_unchecked_save(path: Path, check: LogCheck) -> None:
    """A save whose check judged nothing (its retries ran out, or another writer held the
    case through it) gives no verdict on any entry (P15): it is refused as unchecked, never
    as a broken log at no entry (T12 review 41)."""
    if not check.checked or check.in_progress:
        raise CaseLocked(f"{path}: the case log could not be checked before saving ({check.reason or 'another writer holds it'}); "
                         "nothing was written: try again")


def _held_result(check: LogCheck | None) -> LogCheck:
    """One other writer held the case through a read (R43): only the recorded entries are
    judged. A change, removal or reordering at or before them is broken; what follows them
    is being saved, never intact. With no steady read, nothing is claimed."""
    if check is None:
        return _unchecked("being saved: the log changed during every read", in_progress=True)
    if check.recorded is None:
        # No recorded count (the head record could not be read): no entry can be placed among
        # the recorded ones, so none is named (T12 review 43).
        return _unchecked("being saved: the head record could not be read while another writer "
                          "held the case", in_progress=True)
    if check.first_bad is not None and not check.past_recorded:
        return check
    return check.as_recorded()


def _try_exclusive(fd: int) -> bool:
    """Take `fd`'s lock alone now; False if anyone else holds it."""
    try:
        _lock(fd)
    except OSError as e:
        if e.errno in _CONTENDED:
            return False
        raise
    return True


def _try_shared(fd: int) -> bool:
    """Take `fd`'s lock shared now; False if a writer holds it alone."""
    try:
        _lock(fd, shared=True)
    except OSError as e:
        if e.errno in _CONTENDED:
            return False
        raise
    return True


class _GateBusy(CaseLocked):
    """Readers hold the gate (or a writer is passing it): a writer waits for them."""


def _unchecked(reason: str, *, in_progress: bool) -> LogCheck:
    """No steady read was had: nothing is claimed about any entry."""
    return LogCheck(Head(0, GENESIS), [], reason=reason, in_progress=in_progress, checked=False)


def _refuse_unchecked(path: Path, check: LogCheck) -> None:
    if not check.checked:
        raise CaseLocked(f"{path}: the log {check.reason.split(': ', 1)[-1]}; nothing could be "
                         "checked yet, try again")


def _holder_id() -> tuple[int, int]:
    """Who holds a writer lock: the process and the thread. With the process in it, a
    forked child never passes for its parent (forking while holding is not supported; a
    child is refused rather than let in)."""
    return os.getpid(), _thread_token()


# Thread idents are reused once a thread ends, so a new thread could pass for one that
# still holds the case (T12 review 18). Each thread gets its own number, never reused.
_THREAD_NUMBERS = itertools.count(1)
_THREAD = threading.local()


def _thread_token() -> int:
    token = getattr(_THREAD, "token", None)
    if token is None:
        token = _THREAD.token = next(_THREAD_NUMBERS)
    return token


# The writer locks this process holds, by the lock file's identity (device and inode, so
# two spellings of one path, a symlink included, are the same file), with the (process,
# thread) holding each. A second CaseLog on the same file in the holding thread reads as
# the holder: its own fallback lock would be refused by its own thread, which is not
# another writer.
@dataclass(frozen=True)
class _Hold:
    """What a holder took: both lock files' identities and its open descriptors on them."""

    owner: tuple[int, int]
    lock_id: tuple[int, int]
    gate_id: tuple[int, int] | None
    lock_fd: int
    gate_fd: int | None

    def in_place(self, lock_path: Path, gate_path: Path) -> bool:
        """Both lock files are still the ones taken. The paths are compared first, in any
        order, and then the files held open: a file replaced (renamed over or removed) has
        no name left, so its link count is zero for good. That last test sees a replacement
        made at any moment before it, whichever file was looked at first, so the order of
        the path checks leaves no window between them (T12 reviews 35 to 38)."""
        if self.gate_id is None or self.gate_fd is None:
            return False
        if _file_id(os.stat(gate_path)) != self.gate_id or _file_id(os.stat(lock_path)) != self.lock_id:
            return False
        if os.fstat(self.lock_fd).st_nlink == 0 or os.fstat(self.gate_fd).st_nlink == 0:
            return False
        # The gate's path once more, after the case lock's: a gate moved aside and replaced
        # after its first look is caught (review 39). A replacement finished within this last
        # step is the window no check closes (R43, revision 22).
        return _file_id(os.stat(gate_path)) == self.gate_id and _file_id(os.stat(lock_path)) == self.lock_id


# (case log's real path, holder) -> the token of the save under way there (review 48).
_SAVING: dict[tuple[str, tuple[int, int]], object] = {}

# Each held case lock's identity -> the hold.
_HELD: dict[tuple[int, int], _Hold] = {}
_HELD_GUARD = threading.RLock()  # reentrant, like _state: a cleanup run by the GC cannot deadlock


def _file_id(st: os.stat_result) -> tuple[int, int]:
    return st.st_dev, st.st_ino


class CaseLog:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        # Every writer passes this gate, briefly and alone, to take the case; a reader holds
        # it shared through its read, so no writer can take the case mid-read (R43).
        self.gate_path = self.path.with_name(self.path.name + ".gate")
        self.head_path = self.path.with_name(self.path.name + ".head")
        # Guards the fields below, never held long. Reentrant, so a writer block's
        # cleanup run by the garbage collector or a signal handler inside one of these
        # short regions, on the same thread, cannot deadlock it.
        self._state = threading.RLock()
        self._lock_fd: int | None = None
        self._gate_fd: int | None = None  # the gate passed, kept open while the case is held
        self._owner: tuple[int, int] | None = None  # (process id, thread ident) of the holder
        self._lock_depth = 0
        self._lock_id: tuple[int, int] | None = None  # the lock file's identity while held
        self._gate_id: tuple[int, int] | None = None  # the gate's identity when the case was taken
        self._pid = os.getpid()  # a CaseLog belongs to the process that opened it (R42)
        self._head_renamed = False  # the last head record's rename was done

    # --- reading -----------------------------------------------------------------------

    def verify(self) -> LogCheck:
        """Walk the chain from the start, stop at the first entry that does not hold, and
        compare every entry with the head file."""
        me = _holder_id()
        # This thread holds the case (through this object or another): nothing else can
        # write, so one read is the truth, but only while the lock files it took are still
        # the case's. A read across a replaced one falls through to a reader's checks
        # (T12 review 24).
        with self._state:
            hold = (_Hold(me, self._lock_id, self._gate_id, self._lock_fd, self._gate_fd)
                    if self._owner == me and self._lock_id is not None and self._lock_fd is not None
                    else None)
        if hold is None:
            try:
                lock_id = _file_id(os.stat(self.lock_path))
            except OSError:  # no lock file: nobody in this process holds it
                lock_id = None
            if lock_id is not None:
                with _HELD_GUARD:
                    held = _HELD.get(lock_id)
                if held is not None and held.owner == me:
                    hold = held

        def ours() -> bool:
            try:
                # Both lock files, as P15 asks, checked so that no order leaves a window
                # (_Hold.in_place, review 38).
                return hold is not None and hold.in_place(self.lock_path, self.gate_path)
            except OSError:
                return False

        def case_lock_ours() -> bool:
            try:
                return (hold is not None and _file_id(os.stat(self.lock_path)) == hold.lock_id
                        and os.fstat(hold.lock_fd).st_nlink > 0
                        and _file_id(os.stat(self.lock_path)) == hold.lock_id)
            except OSError:
                return False

        if ours():
            check = _check(self._read_log(), _read_head(self.head_path))
            if ours():
                return check
        # Otherwise the read is taken again as a reader takes it, through the gate as it now
        # is, checking the lock files that read relies on (P15, T12 review 44). A case lock
        # that reader finds held, and that is still this thread's own, is no other writer
        # (review 43): the read under the gate is the truth.
        # Hold the gate shared, so no writer can take the case during this read; then try
        # the case lock shared. Free: one read under it is the truth. Held: the writer that
        # holds it cannot be replaced while the gate is held, so after a steady read the
        # lock tells whether that one writer held the case throughout (still held) or let
        # it go (now free, and taken here for a true read). Fresh descriptors throughout,
        # so an object a forked child inherited tests the locks as they are now.
        seen_held = False  # the case lock was seen held by another (not only a busy gate)
        for attempt in range(READ_RETRIES):
            if attempt:
                time.sleep(READ_PAUSE)
            try:
                with self._shared(self.gate_path) as gate_fd:
                    # What the reader holds counts only while the lock files it holds are
                    # still the case's: a gate or case lock replaced during the read lets a
                    # writer in through the new file, so the read proves nothing and is
                    # taken again (T12 review 23).
                    def in_place(*held: tuple[int, Path]) -> bool:
                        # Each file held, then the first again, so neither is left with only
                        # a sample taken before the other's (T12 review 39). A replacement
                        # finished within this check's last step is the window no check
                        # closes (R43 revision 22).
                        order = [*held, *held[:1]] if len(held) > 1 else list(held)
                        try:
                            return all(_file_id(os.fstat(fd)) == _file_id(os.stat(p)) for fd, p in order)
                        except OSError:
                            return False

                    try:
                        case_fd = os.open(self.lock_path, _PROBE_FLAGS)
                    except FileNotFoundError:
                        # Behind the gate with no case lock file: no writer holds the case,
                        # and none can take it while the gate is held, so one read is the
                        # truth.
                        check = _check(self._read_log(), _read_head(self.head_path))
                        if in_place((gate_fd, self.gate_path)) and not self.lock_path.exists():
                            return check
                        continue
                    locked = False
                    try:
                        held = ((gate_fd, self.gate_path), (case_fd, self.lock_path))
                        if _try_shared(case_fd):
                            locked = True
                            check = _check(self._read_log(), _read_head(self.head_path))
                            if in_place(*held):
                                return check
                            continue
                        if case_lock_ours() and in_place(*held):
                            check = _check(self._read_log(), _read_head(self.head_path))
                            if case_lock_ours() and in_place(*held):
                                return check
                            continue
                        check = self._steady()
                        if _try_shared(case_fd):  # its writer let go during the read
                            locked = True
                            check = _check(self._read_log(), _read_head(self.head_path))
                            if in_place(*held):
                                return check
                            continue
                        seen_held = True
                        if not in_place(*held):
                            continue
                        return _held_result(check)
                    finally:
                        if locked:  # released before closing: on Windows a close may not
                            with suppress(OSError):  # free the lock at once
                                _unlock(case_fd)
                        os.close(case_fd)
            except CaseLocked:
                # A writer is at the gate (passing it, or paused there): it does not hold the
                # case yet. If the case lock is free, holding it shared keeps that writer
                # out too, so one read under it is the truth; if it is held, look again.
                try:
                    case_fd = os.open(self.lock_path, _PROBE_FLAGS)
                except FileNotFoundError:
                    continue
                except OSError:
                    return self._untested()
                try:
                    if _try_shared(case_fd):
                        try:
                            check = _check(self._read_log(), _read_head(self.head_path))
                            # Only while the lock held shared is still the case's lock file
                            # (T12 review 24); otherwise a writer took the new one: look again.
                            try:
                                same = _file_id(os.fstat(case_fd)) == _file_id(os.stat(self.lock_path))
                            except OSError:  # removed during the read: look again, never
                                same = False  # fall through to an unprotected read (review 25)
                            if same:
                                return check
                            continue
                        finally:
                            with suppress(OSError):
                                _unlock(case_fd)
                except OSError:
                    return self._untested()
                finally:
                    os.close(case_fd)
                if case_lock_ours():
                    # The case lock found held is this thread's own (a holder reading again
                    # after its gate changed): a writer at the gate cannot take the case from
                    # it, so the read is the truth (T12 review 45).
                    check = _check(self._read_log(), _read_head(self.head_path))
                    if case_lock_ours():
                        return check
                    continue
                seen_held = True
                # Gate and case both held: the recorded entries are final whoever holds the
                # case, so a fault at or before them in a steady read is named now (P15).
                check = self._steady()
                # Only with the head read (a known recorded count) and the fault within the
                # entries it records: an unreadable or missing head proves no recorded entry
                # changed, so it is nothing to name from such a read.
                if (check is not None and check.first_bad is not None and check.recorded is not None
                        and check.first_bad <= check.recorded):
                    return check
                continue
            except FileNotFoundError:
                # No gate: no writer has ever started on this case (each creates it first),
                # and a check writes nothing. A steady read is the truth, unless a writer
                # started meanwhile: then look again.
                check = self._steady()
                if check is not None and not self.gate_path.exists():
                    return check
                continue
            except OSError:
                return self._untested()
        if seen_held:
            return _unchecked("being saved: writers kept taking the case during every read",
                              in_progress=True)
        # No attempt saw the case held, only a writer at the gate (or one starting where
        # there was no gate): nothing is being saved that the reader knows of (R43).
        return _unchecked("could not be checked: a writer was taking the case at every attempt",
                          in_progress=False)

    @contextmanager
    def _shared(self, path: Path) -> Iterator[int]:
        """Hold `path` locked shared through a fresh descriptor: readers never exclude each
        other; CaseLocked while a writer holds it alone. A missing file raises
        FileNotFoundError and nothing is created (a check writes nothing)."""
        fd = os.open(path, _PROBE_FLAGS)
        try:
            if not _try_shared(fd):
                raise CaseLocked(f"{self.path}: another window or command is writing to this case")
            try:
                yield fd
            finally:
                with suppress(OSError):
                    _unlock(fd)
        finally:
            os.close(fd)

    def _steady(self) -> LogCheck | None:
        """A read across which the head file did not change, or None if every read moved:
        it holds every entry the head records, each walked and matched."""
        for attempt in range(READ_RETRIES):
            if attempt:
                time.sleep(READ_PAUSE)
            check, still = self._look()
            if still:
                return check
        return None

    def _untested(self) -> LogCheck:
        """The lock cannot be taken or tested: no holder is known, so a steady read is
        believed as it stands (an incomplete entry reported as incomplete). With no
        steady read, nothing is claimed, and no other window is blamed."""
        check = self._steady()
        if check is not None:
            return check
        return _unchecked("could not be checked: the case lock cannot be taken and the log "
                          "changed during every read", in_progress=False)

    def _look(self) -> tuple[LogCheck, bool]:
        """One read judged by the head read before it, and whether the head held still."""
        before = _read_head(self.head_path)
        check = _check(self._read_log(), before)
        return check, _read_head(self.head_path) == before

    def _read_log(self) -> bytes | None:
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return b""
        except OSError:  # a directory in its place, no permission
            return None

    def require_intact(self) -> Head:
        """The head of a log checked whole, or CaseLogBroken naming the first bad entry.
        Sign-off (T27) must call it while holding the case (writer()) and refuse on the
        exception (R43). Called while another holds the case, it refuses with CaseLocked:
        only the recorded entries could be checked, so the log is not known whole."""
        check = self.verify()
        _refuse_unchecked(self.path, check)
        if not check.ok:
            raise CaseLogBroken(self.path, check.first_bad, check.reason, check.torn)
        if check.in_progress:
            raise CaseLocked(f"{self.path}: another window or command is saving to this case; "
                             "only its recorded entries could be checked, try again")
        return check.head

    def entries(self) -> list[Entry]:
        """Every entry of an intact log; CaseLogBroken otherwise. While another holds the
        case, the recorded entries; CaseLocked if no steady read was had."""
        check = self.verify()
        _refuse_unchecked(self.path, check)
        if not check.ok:
            raise CaseLogBroken(self.path, check.first_bad, check.reason, check.torn)
        return check.entries

    # --- writing -----------------------------------------------------------------------

    @contextmanager
    def writer(self, *, wait: float | None = None, create: bool = True) -> Iterator[CaseLog]:
        """Hold the case's single-writer lock; refuses if another writer still holds it after
        `wait` seconds (WRITER_WAIT by default). While only readers hold the case (a check
        takes the lock shared), it waits for them, up to READER_WAIT.

        The thread that holds it may nest (a sign-off holds the lock across its checks and
        its append). Any other writer is refused, whether it is another process, another
        CaseLog on the same file, or another thread using this same instance: the lock
        belongs to a thread, not to the object. Forking a process while it holds the lock is
        not supported: a child writing through an inherited CaseLog is refused, and leaving
        a block it inherited releases nothing."""
        if self._pid != os.getpid():
            raise CaseLocked(f"{self.path}: this case log was opened by another process; open "
                             "the case again in this one")
        me = _holder_id()
        with self._state:
            # The depth this thread already holds: whatever interrupts entry (even between
            # _enter returning and this frame noting it), the exit releases what was added.
            depth_before = self._lock_depth if self._owner == me else 0
        start = time.monotonic()
        deadline = start + (WRITER_WAIT if wait is None else wait)
        try:
            while True:
                try:
                    with self._state:
                        self._enter(me, create)
                    break
                except CaseLocked as e:
                    # Another writer: wait briefly, then refuse (R42). Only readers: they
                    # are no writer and finish their read, so wait for them (READER_WAIT).
                    limit = start + READER_WAIT if isinstance(e, _GateBusy) else deadline
                    if time.monotonic() >= limit:
                        raise
                    time.sleep(READ_PAUSE)
            yield self
        finally:
            with self._state:
                # Released by the process that holds it, whichever thread runs this: an
                # abandoned block may be finalised by the garbage collector on another
                # thread. A forked child leaving a block it inherited is another process
                # and never unlocks its parent's lock. Only a level this entry added.
                if self._owner == me and me[0] == os.getpid() and self._lock_depth > depth_before:
                    self._release_one_level()

    def _enter(self, me: tuple[int, int], create: bool = True) -> bool:
        """Take or join the lock for `me` (call under _state); True once it is held. With
        `create` False a missing lock file raises FileNotFoundError and nothing is made."""
        if self._owner == me:
            self._lock_depth += 1
        elif self._owner is not None:
            raise CaseLocked(f"{self.path}: another window or command is writing to this case")
        else:
            if create:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_RDWR | (os.O_CREAT if create else 0)
            # Pass the gate alone to take the case, so no writer takes it while a reader
            # holds the gate through a read (R43); leave the gate as soon as the case lock
            # is taken or refused. The gate is created before the case lock, so a missing
            # gate means no writer has ever started here.
            gate = os.open(self.gate_path, flags, 0o600)
            kept = False
            try:
                if not _try_exclusive(gate):
                    raise _GateBusy(f"{self.path}: another window or command is checking or "
                                    "writing to this case")
                try:
                    gate_id = _file_id(os.fstat(gate))
                    if gate_id != _file_id(os.stat(self.gate_path)):
                        raise CaseLocked(f"{self.path}: the case gate changed while it was passed")
                    self._take_case_lock(me, flags)
                    # The gate stays open, unlocked, while the case is held: its link count
                    # tells whether it was replaced (review 38).
                    self._gate_id, self._gate_fd, kept = gate_id, gate, True
                    with _HELD_GUARD:
                        _HELD[self._lock_id] = _Hold(me, self._lock_id, gate_id, self._lock_fd, gate)
                finally:
                    with suppress(OSError):
                        _unlock(gate)
            finally:
                if not kept:
                    os.close(gate)
        return True

    def _take_case_lock(self, me: tuple[int, int], flags: int) -> None:
        """Take the case lock alone for `me` and record it (call under _state, at the gate)."""
        fd = os.open(self.lock_path, flags, 0o600)
        locked = False
        try:
            _lock(fd)
            locked = True
            lock_id = _file_id(os.fstat(fd))
            if lock_id != _file_id(os.stat(self.lock_path)):
                # The file was replaced between opening and locking it: this lock guards
                # nothing, so refuse rather than hold beside the holder of the new one.
                raise CaseLocked(f"{self.path}: the case lock file changed while it was taken")
            self._lock_fd, self._owner, self._lock_depth, self._lock_id = fd, me, 1, lock_id
            with _HELD_GUARD:
                _HELD[lock_id] = _Hold(me, lock_id, None, fd, None)
        except BaseException as e:
            # Whatever interrupts this (a lock error, or KeyboardInterrupt between
            # two steps) leaves no lock behind: once the descriptor is recorded
            # the lock is released, before that the descriptor is closed.
            if self._lock_fd == fd:
                self._lock_depth = 1
                self._owner = me
                self._release_one_level()
            else:
                os.close(fd)
            # Only contention means another writer holds the lock. Any other lock
            # error (no locks available, an I/O error) is not evidence of a writer:
            # it propagates, so a reader falls back to the plain read and a torn
            # entry is reported, never excused as some writer's append.
            if not locked and isinstance(e, OSError) and e.errno in _CONTENDED:
                raise CaseLocked(f"{self.path}: another window or command is writing to this case") from e
            raise

    def _require_lock_in_place(self) -> None:
        """Before a write: the lock this writer holds is still the case's lock file. If the
        file was deleted or replaced, another writer could hold the new one, so nothing is
        written (P15: at most one writer holds the case)."""
        # Both lock files (R42): the case lock this writer holds, and the gate it passed,
        # since a reader that finds a new gate cannot know this writer holds the case.
        # The gate first and the case lock last, immediately before the step: the case lock is
        # what a second writer would hold, so no read follows its check (T12 review 35).
        try:
            in_place = (self._lock_id is not None and self._lock_fd is not None
                        and _Hold(self._owner or (0, 0), self._lock_id, self._gate_id, self._lock_fd,
                                  self._gate_fd).in_place(self.lock_path, self.gate_path))
        except FileNotFoundError:
            in_place = False
        # Any other error (the folder cannot be read) is raised as it is, and the save names
        # it (D159): a lock that cannot be examined is not shown removed or replaced.
        if not in_place:
            # The step is refused; what the save as a whole wrote is for the save to say,
            # since it may already have written its line (T12 review 28).
            raise CaseLocked(f"{self.path}: a case lock file was removed or replaced while this "
                             "window held the case; the step was refused")

    def _release_one_level(self) -> None:
        """Leave one level of this process's writer lock (call under _state)."""
        self._lock_depth -= 1
        if self._lock_depth == 0:
            fd, self._lock_fd, self._owner = self._lock_fd, None, None
            gate_fd, self._gate_fd = self._gate_fd, None
            if gate_fd is not None:
                with suppress(OSError):
                    os.close(gate_fd)
            lock_id, self._lock_id = self._lock_id, None
            self._gate_id = None
            if lock_id is not None:
                with _HELD_GUARD:
                    _HELD.pop(lock_id, None)
            if fd is not None:
                # Closing the descriptor releases the lock on every platform, so it runs
                # even when the explicit unlock fails; skipping it would leave the lock
                # held and block later appends until the process exits.
                try:
                    _unlock(fd)
                except OSError:
                    pass
                finally:
                    os.close(fd)

    @contextmanager
    def _one_save(self) -> Iterator[None]:
        """One save at a time per holder: a save started inside another on the same thread
        (from a signal handler, a finaliser or a callback) is refused, since the writer lock
        admits it and the outer save's check would then be stale (T12 review 20)."""
        # Keyed by the case folder as the file system names it and the thread, shared by every
        # CaseLog object, so a save begun inside another through a second handle to the same
        # case (another object, another spelling of the path) is refused too (review 48).
        key = (os.path.realpath(self.path), _holder_id())
        token = object()
        try:
            # One step: setdefault tests and registers together, so no signal handler can run
            # between the two and start a save that passes the test too (review 38). Inside
            # the region that removes it, so an interruption just after cannot leave the
            # thread unable to save again (review 42); only this save's own token is removed.
            if _SAVING.setdefault(key, token) is not token:
                raise CaseLogError(f"{self.path}: another save on this case log is under way in "
                                   "this thread; this save was refused")
            yield
        finally:
            if _SAVING.get(key) is token:
                del _SAVING[key]

    @contextmanager
    def _os_errors_as_not_recorded(self) -> Iterator[None]:
        """Every step of a save after its check (D154): an operating-system error (a full
        disk, a failed sync, a rename refused) reaches the caller as the save not recorded,
        naming the error, never raw. Where the head record was already published, _publish
        has answered not confirmed instead."""
        self._save_confirmed = False
        self._line_at: int | None = None  # where this save's line starts, once it is being written
        self._repair_landed = False  # a repair's replacement log is in place
        self._head_renamed = False
        try:
            yield
        except OSError as e:
            if self._save_confirmed:
                raise  # confirmed and settled: a held handler's error is its own (review 43)
            raise SaveNotRecorded(f"{self.path}: the save could not be completed ({e.__class__.__name__}: "
                                  f"{e}); the save was not recorded") from e
        except (SaveNotRecorded, SaveNotConfirmed, GeneratorExit):
            raise
        except BaseException as e:  # Ctrl-C after the line is still this save's report (review 32)
            if self._save_confirmed:
                raise  # confirmed and settled: a held signal's handler is delivered as it is
            # Anything else that stops a save once its line has started to reach the log (a
            # nested save refused from a signal handler, say) is still this save's report:
            # never another operation's words, which may say nothing was written (review 29).
            if self._repair_landed and not self._head_renamed:
                raise SaveNotRecorded(f"{self.path}: the repair was interrupted after its repaired log was "
                                      f"put in place ({e.__class__.__name__}); the repair was not recorded: "
                                      "the log reads torn at the same entry, and dropping it again repairs "
                                      "it") from None
            if self._line_at is None and not self._head_renamed:
                raise
            if self._line_at is not None:
                try:
                    written = os.stat(self.path).st_size > self._line_at
                except OSError:
                    written = True  # cannot tell: never claim nothing was written
            else:
                written = True
            if not written and not self._head_renamed:
                raise
            what = e.__class__.__name__
            if self._head_renamed:
                raise SaveNotConfirmed(f"{self.path}: the save was interrupted after its head record was "
                                       f"published ({what}), so whether the save is in the log is not "
                                       "confirmed: check the case log before anything else is saved") from None
            raise SaveNotRecorded(f"{self.path}: the save was interrupted after its entry was written "
                                  f"({what}); the save was not recorded: the next holder confirms "
                                  "dropping it") from None

    def append(self, event: str, data: dict[str, Any], *, expected: int) -> Entry:
        """Append one entry if the log is intact and still at `expected` entries."""
        if not isinstance(event, str) or not event.strip():
            raise ValueError("event must be a non-empty string")
        if not isinstance(data, dict):
            raise ValueError("data must be a mapping")
        try:
            # Plain JSON only: json.dumps would quietly turn a tuple into a list and a 1,
            # True or None key into a string, so the Entry returned here would differ from
            # the one every later reader sees.
            blob = _canonical(data)
            same = json.loads(blob) == data
        except (TypeError, ValueError, RecursionError) as e:
            raise ValueError(f"data is not plain JSON: {e}") from e
        if not same:
            raise ValueError("data is not plain JSON: it would not read back unchanged "
                             "(use lists, not tuples, and string keys)")
        # From here on only the copy read back from those bytes is used: the caller's dict
        # may be shared, and a change to it between hashing and writing must not reach the log.
        snapshot = json.loads(blob)
        with self._one_save(), self.writer():
            check = self.verify()
            _refuse_unchecked_save(self.path, check)
            if not check.ok:
                raise CaseLogBroken(self.path, check.first_bad, check.reason, check.torn)
            if check.head.position != expected:
                raise LogConflict(expected, check.head.position)
            with self._os_errors_as_not_recorded():
                return self._write(event, snapshot, check)

    def drop_torn_entry(self, *, expected: int, by: str) -> Entry:
        """Cut an incomplete last entry off and record who confirmed it. `expected` is the
        intact position the person was shown. Refused if the last entry is not torn, or
        if the chain is broken before it (that is tampering, not a torn write)."""
        if not isinstance(by, str) or not by.strip():
            raise ValueError("a name is required to drop a torn entry")
        with self._one_save(), self.writer():
            check = self.verify()
            _refuse_unchecked_save(self.path, check)
            if not check.torn:
                if check.ok:
                    raise CaseLogBroken(self.path, check.head.position + 1, "does not exist: nothing is torn", False)
                raise CaseLogBroken(self.path, check.first_bad, check.reason, False)
            if check.head.position != expected:
                raise LogConflict(expected, check.head.position)
            with self._os_errors_as_not_recorded():
                return self._replace_torn(check, by.strip())

    def _replace_torn(self, check: LogCheck, by: str) -> Entry:
        self._require_lock_in_place()
        # Write the intact prefix plus the drop entry to a temporary file and rename it over
        # the log, so a crash leaves either the torn log or the repaired one, never a
        # truncated log with no record of who dropped the entry. A crash after the rename
        # and before the head file is replaced leaves the drop entry itself unrecorded: the
        # log loads as torn at the same entry, and dropping it again repairs it.
        raw = self.path.read_bytes()
        if len(raw) != check.log_bytes or _digest(raw) != check.log_digest:
            raise LogConflict(check.head.position, check.head.position + 1)
        intact = raw[: check.intact_bytes]
        entry, line = self._encode(TORN_DROPPED, {"seq": check.first_bad, "by": by}, check.head)
        recorded = [e.hash for e in check.entries]

        def unchanged() -> None:
            # Before the rename over the log (R42, revision 13): the lock is the one taken,
            # the log has not grown and the head file still records what the check read, so
            # the repair never replaces a save made since it looked (T12 review 21).
            # The log's bytes, not only its length (revision 14): a log rewritten to the same
            # length since the check is not the one the repair read.
            if self._log_digest() != check.log_digest or _read_head(self.head_path, raise_os=True) != recorded:
                raise LogConflict(check.head.position, check.head.position + 1)
            self._require_lock_in_place()  # last, immediately before the rename (review 34)
            # Marked once every check passed, immediately before the rename, so a refusal
            # here stays the repair's own conflict and an interruption right after the rename
            # is still the repair's report (reviews 31, 41).
            self._repair_landed = True

        fd, tmp = _staging_file(self.path)
        try:
            _write_all(fd, intact + line)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            _replace(tmp, self.path, before=unchanged)  # each attempt (D108)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        _fsync_dir(self.path.parent)
        self._publish(recorded, entry)
        return entry

    @staticmethod
    def _encode(event: str, data: dict[str, Any], head: Head) -> tuple[Entry, bytes]:
        """The entry and its line. The line is checked as every reader will check it, so
        the hash is over exactly the bytes written, and the Entry returned is the one read
        back from them."""
        body = {"seq": head.position + 1, "prev": head.hash, "at": _utc_now(), "event": event, "data": data}
        line = _canonical({**body, "hash": entry_hash(body)})
        entry, reason = _parse(line, head.position + 1, head.hash)
        if entry is None:  # pragma: no cover - only if the encoder and the reader disagree
            raise CaseLogError(f"{reason}; nothing was written")
        return entry, line + b"\n"

    def _log_digest(self) -> str:
        """The log's digest for a save's re-check; an operating-system error is raised, so
        the save names it (D159), never read as a changed log."""
        try:
            return _digest(self.path.read_bytes())
        except FileNotFoundError:
            return _digest(b"")

    def _create_empty_head(self) -> None:
        """The head record a first append writes before its line. It is created, never
        renamed over: a first append working from a stale check finds a head record already
        there and stops, so it can never reset a head that records entries (T12 review 22)."""
        fd, tmp = _staging_file(self.head_path)
        try:
            try:
                _write_all(fd, _head_bytes([]))
                os.fsync(fd)
            finally:
                os.close(fd)
            self._require_lock_in_place()
            try:
                os.link(tmp, self.head_path)  # fails if a head record exists
            except FileExistsError:
                raise LogConflict(0, 1) from None
        finally:
            tmp.unlink(missing_ok=True)
        _fsync_dir(self.head_path.parent)

    def _write_head(self, hashes: list[str], *, was: list[str] | None = None) -> None:
        """Replace the head file in one rename, after the last entry it names is on disk.
        With `was`, the head file must still record exactly those entries just before the
        rename, or nothing is published."""
        fd, tmp = _staging_file(self.head_path)
        try:
            _write_all(fd, _head_bytes(hashes))
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            # Before each step that changes a file a reader reads (R42, D108): the rename
            # that publishes the head record, the empty first one included. The temporary
            # file is read by nobody.
            stage = ["checks"]

            def before() -> None:
                stage[0] = "checks"
                if was is not None and _read_head(self.head_path, raise_os=True) != was:
                    raise SaveNotRecorded(f"{self.path}: the head file changed while this save was "
                                          "being recorded; the save was not recorded")
                # The lock last, immediately before the rename, after every read the step
                # makes (T12 review 34).
                self._require_lock_in_place()
                stage[0] = "rename"
            _replace(tmp, self.head_path, before=before)
            self._head_renamed = True
        except BaseException as e:
            # Past its checks, only the rename's own OSError proves it did not happen; any
            # other interruption there may have come after it, so the head may be published
            # (review 31): never reported as not recorded.
            if stage[0] == "rename" and not isinstance(e, OSError):
                self._head_renamed = True
            tmp.unlink(missing_ok=True)
            raise
        _fsync_dir(self.head_path.parent)

    def _write(self, event: str, data: dict[str, Any], check: LogCheck) -> Entry:
        return self._write_entry(event, data, check)

    def _write_entry(self, event: str, data: dict[str, Any], check: LogCheck) -> Entry:
        self._require_lock_in_place()
        entry, line = self._encode(event, data, check.head)
        if not check.entries and not self.head_path.exists():
            # The head file exists before the first line does, so a crash during the first
            # append leaves a torn entry under an empty head, and a missing head beside any
            # entry is always damage, never a crash to repair (T12 review 9).
            self._create_empty_head()
        created = not self.path.exists()
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            if os.fstat(fd).st_size != check.intact_bytes or self._log_digest() != check.log_digest:
                # Something was added to the log since this save checked it: writing now would
                # put a second entry at the same position (T12 review 20).
                raise LogConflict(check.head.position, check.head.position + 1)
            self._line_at = check.intact_bytes
            # After the open and immediately before the line is written (D108): a lock
            # removed while the log was being opened stops the write.
            try:
                _write_all(fd, line, before=self._require_lock_in_place)
            except CaseLocked as e:
                # Refused partway through a short write: part of the line is on disk, past
                # the recorded entries, as a crash leaves it. The save is not recorded, and
                # never reported as nothing written (T12 review 26). What stopped it is said
                # in the save's own words, never chained: the refusal may be another
                # operation's, from a signal handler, saying nothing was written (review 42).
                if os.fstat(fd).st_size > check.intact_bytes:
                    try:
                        self._require_lock_in_place()
                        why = f"the write was interrupted ({e.__class__.__name__})"
                    except CaseLocked:
                        why = "a case lock file was removed or replaced while this save was being written"
                    except OSError as err:
                        why = f"the case lock files could not be examined ({err.__class__.__name__})"
                    raise SaveNotRecorded(f"{self.path}: {why}; part of its entry was written and the save "
                                          "was not recorded: the next holder confirms dropping it") from None
                raise
            os.fsync(fd)
        finally:
            os.close(fd)
        if created:
            _fsync_dir(self.path.parent)  # make the new file's name durable too
        self._publish([e.hash for e in check.entries], entry)
        return entry

    def _publish(self, recorded: list[str], entry: Entry) -> None:
        with _interrupts_deferred():
            self._publish_now(recorded, entry)

    def _publish_now(self, recorded: list[str], entry: Entry) -> None:
        """Record `entry` after the `recorded` entries, then confirm the log as read now holds
        it: a writer is told its save was recorded only if an intact log holds the entry at
        its position, so a log that reads intact never lacks a save reported as recorded
        (R42, revision 13). Stopped after the line, the save says it was not recorded."""
        self._head_renamed = False
        try:
            self._write_head([*recorded, entry.hash], was=recorded)
        except SaveNotRecorded:
            raise
        except CaseLocked as e:
            # Its own words: the lock check's "nothing was written" is untrue once the line
            # is on disk (T12 review 27).
            raise SaveNotRecorded(f"{self.path}: a case lock file was removed or replaced while this "
                                  "save was being recorded; its entry was written and the save was not "
                                  "recorded: the next holder confirms dropping it") from e
        except OSError as e:
            # Failed before the head record's rename, the save is not recorded; after it (the
            # folder could not be synced), the head may list it though it may not last, so
            # it is not confirmed (T12 review 25). Which of the two is known from the rename
            # itself, never inferred from a head that may not be readable (review 28).
            if not self._head_renamed:
                raise SaveNotRecorded(f"{self.path}: the head record could not be written ({e}); "
                                      "the save was not recorded") from e
            raise SaveNotConfirmed(f"{self.path}: the head record could not be made durable ({e}), so "
                                   "whether the save is in the log is not confirmed: check the case log "
                                   "before anything else is saved") from e
        failure = ["the log changed while this save was being recorded"]
        confirmed = False
        try:
            try:
                raw = self.path.read_bytes()
            except FileNotFoundError:
                raw = b""
            now = _check(raw, _read_head(self.head_path, raise_os=True))
            held = now.entries[entry.seq - 1].hash if now.ok and len(now.entries) >= entry.seq else None
            # The read-back counts only while both lock files are still the ones taken, as any
            # holder's read does (T12 review 45); otherwise it confirms nothing.
            if self._locks_replaced():
                failure[0] = "a case lock file was removed or replaced during the read-back"
                confirmed = self._fresh_read_holds(entry)
            else:
                confirmed = held == entry.hash
            self._save_confirmed = confirmed
        except OSError as e:  # the read-back itself failed: named, never read as a change (D159)
            failure[0] = f"the log could not be read back ({e.__class__.__name__}: {e})"
            # A lock file replaced during that failed read still calls for the fresh read
            # (P15, T12 review 47), whatever stopped the first.
            try:
                if self._locks_replaced():
                    confirmed = self._fresh_read_holds(entry)
                    self._save_confirmed = confirmed
            except Exception:
                confirmed = False
        except BaseException as e:  # interrupted, Ctrl-C included (reviews 30, 32)
            failure[0] = f"the read-back was interrupted ({e.__class__.__name__})"
        finally:
            # Anything short of a confirmed read-back, an interruption anywhere before the
            # answer included, goes on to the take-back (review 33).
            if not confirmed:
                self._take_back(recorded, entry, failure[0])

    def _locks_replaced(self) -> bool:
        """A lock file this holder took is no longer the case's (an error examining them is
        raised, to be named)."""
        try:
            self._require_lock_in_place()
        except CaseLocked:
            return True
        return False

    def _fresh_read_holds(self, entry: Entry) -> bool:
        """A fresh read, through the gate and case lock as they now are (P15, T12 review 46),
        finds an intact log holding `entry` where its head record lists it."""
        again = self.verify()
        held = (again.entries[entry.seq - 1].hash
                if again.ok and not again.in_progress and len(again.entries) >= entry.seq else None)
        return held == entry.hash

    def _take_back(self, recorded: list[str], entry: Entry, failure: str) -> NoReturn:
        """Not told recorded, so not left recorded either (P15): the head record goes back to
        the entries before this one, if it is still the one this save published, and the entry
        is again past the recorded entries, as a crash leaves it (T12 review 23). If the head
        record changed meanwhile, it is left alone."""
        refused: BaseException | None = None
        try:
            # Its own record only (`was`), and like every head rename only while both lock
            # files are still the ones taken: under interference a take-back could otherwise
            # replace a head another writer has since published and been told is recorded
            # (T12 review 25). Refused, the save is not confirmed (D143).
            self._write_head(recorded, was=[*recorded, entry.hash])
        except BaseException as e:  # whatever stops it, Ctrl-C and GeneratorExit included (31-33)
            refused = e
        if refused is not None:
            # One answer per report (D164): what stopped the take-back is named in the save's
            # own words, never quoted, since a quoted refusal can say the save was or was not
            # recorded; nor is it chained, for the same reason.
            if isinstance(refused, OSError):
                why = f"{refused.__class__.__name__}: {refused.strerror or refused}"
            elif isinstance(refused, SaveNotRecorded):
                why = "its head record is no longer this save's own"
            elif isinstance(refused, CaseLocked):
                why = "a case lock file was removed or replaced"
            else:
                why = refused.__class__.__name__
            raise SaveNotConfirmed(
                f"{self.path}: {failure}, and its head record could not be taken back ({why}), so "
                "whether the save is in the log is not confirmed: check the case log before "
                "anything else is saved") from None
        raise SaveNotRecorded(f"{self.path}: {failure}; the save was not recorded")


# On Windows a reader's open file blocks a rename over it for the moment the read takes
# (no delete sharing), so a rename that meets one is retried briefly, not failed mid-append.
REPLACE_RETRIES = 40
REPLACE_PAUSE = 0.025



@contextmanager
def _interrupts_deferred() -> Iterator[None]:
    """Hold signals off while a save publishes its head record, reads it back and, if need
    be, takes it back: a handler run between finding the read-back failed and starting the
    take-back would otherwise leave a save recorded that was never confirmed (T12 reviews
    34, 35). Ctrl-C is held whatever its handler, and every other signal whose handler is
    Python code. A signal that arrives meanwhile is handed to its own handler once the log is
    settled, unless the save is already reporting its own answer. Only the main thread runs
    signal handlers, so only there is anything held."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    pending: list[int] = []
    previous: dict[int, Any] = {}
    try:
        # Every signal this platform has, not a list (review 36): any whose handler is
        # Python code, and Ctrl-C whatever its handler.
        for signum in sorted(signal.valid_signals()):
            try:
                handler = signal.getsignal(signum)
                if signum == signal.SIGINT or callable(handler):
                    previous[signum] = signal.signal(signum, lambda n, frame: pending.append(n))
            except (ValueError, OSError, TypeError):  # one the platform will not let us set
                continue
    except BaseException:
        _restore(previous)
        raise
    try:
        yield
    except BaseException:
        _restore(previous)
        raise  # the save's own answer stands
    _restore(previous)
    for signum in pending:  # the save is confirmed and the log settled: deliver as they came
        handler = previous.get(signum)
        if callable(handler):
            handler(signum, None)
        elif handler == signal.SIG_DFL:
            signal.raise_signal(signum)


def _restore(previous: dict[int, Any]) -> None:
    for signum, handler in previous.items():
        with suppress(ValueError, OSError, TypeError):
            signal.signal(signum, handler)


def _staging_file(target: Path) -> tuple[int, Path]:
    """A new temporary file beside `target`, under a name of its own: a shared staging name
    would let a writer that lost its lock reach a file another writer renamed into place
    (T12 review 18). Created exclusively, readable only by its owner."""
    fd, name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    return fd, Path(name)


def _replace(src: Path, dst: Path, before: Callable[[], None] | None = None) -> None:
    """Rename `src` over `dst`, retrying a rename a Windows reader blocks for a moment;
    `before` runs immediately before every attempt (the lock re-check, D108)."""
    for attempt in range(REPLACE_RETRIES):
        if before is not None:
            before()
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if os.name != "nt" or attempt == REPLACE_RETRIES - 1:
                raise
            time.sleep(REPLACE_PAUSE)


def _write_all(fd: int, data: bytes, before: Callable[[], None] | None = None) -> None:
    """Write all of `data`; `before` runs immediately before every write call, so a short
    write continued in a second call re-checks the lock too (D108)."""
    view = memoryview(data)
    while view:  # one call in practice; loop on a short write
        if before is not None:
            before()
        view = view[os.write(fd, view):]


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":  # pragma: no cover - Windows cannot open a directory for fsync
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


if os.name == "nt":  # pragma: no cover - exercised on Windows only
    import ctypes
    import msvcrt
    from ctypes import wintypes

    # LockFileEx, not msvcrt.locking: msvcrt has no shared lock, and a check must share the
    # case with other checks (R43) while a writer takes it alone (R42). One byte, per handle;
    # closing the handle, or the process ending, releases it.
    class _Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", wintypes.HANDLE)]

    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_Overlapped)]
    _KERNEL32.LockFileEx.restype = wintypes.BOOL
    _KERNEL32.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                       wintypes.DWORD, ctypes.POINTER(_Overlapped)]
    _KERNEL32.UnlockFileEx.restype = wintypes.BOOL
    _FAIL_IMMEDIATELY, _EXCLUSIVE = 0x1, 0x2
    _ERROR_LOCK_VIOLATION = 33  # another handle holds a conflicting lock

    _CONTENDED = {errno.EACCES}
    _SHARED_LOCKS = True
    _PROBE_FLAGS = os.O_RDONLY

    def _win_error() -> OSError:
        code = ctypes.get_last_error()
        return OSError(errno.EACCES if code == _ERROR_LOCK_VIOLATION else errno.EIO,
                       ctypes.FormatError(code))

    def _lock(fd: int, shared: bool = False) -> None:
        flags = _FAIL_IMMEDIATELY | (0 if shared else _EXCLUSIVE)
        if not _KERNEL32.LockFileEx(msvcrt.get_osfhandle(fd), flags, 0, 1, 0,
                                    ctypes.byref(_Overlapped())):
            raise _win_error()

    def _unlock(fd: int) -> None:
        if not _KERNEL32.UnlockFileEx(msvcrt.get_osfhandle(fd), 0, 1, 0, ctypes.byref(_Overlapped())):
            raise _win_error()

else:
    import fcntl

    # flock with LOCK_NB reports a lock another process holds as EWOULDBLOCK (EAGAIN).
    _CONTENDED = {errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES}

    _SHARED_LOCKS = True
    _PROBE_FLAGS = os.O_RDONLY  # a reader needs only read access to take a shared lock

    def _lock(fd: int, shared: bool = False) -> None:
        fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


LOG_NAME = "log.jsonl"


def for_case(folder: Path | str) -> CaseLog:
    """The case log of a case folder (cases/<id>/log.jsonl)."""
    return CaseLog(Path(folder) / LOG_NAME)
