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
import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS = "0" * 64
HASHED_FIELDS = ("seq", "prev", "at", "event", "data")
ENTRY_FIELDS = frozenset((*HASHED_FIELDS, "hash"))
TORN_DROPPED = "torn_entry_dropped"
HEAD_FIELDS = frozenset(("count", "hash", "hashes"))
READ_RETRIES, READ_PAUSE = 5, 0.02  # seconds; a writer paused for longer is handled in verify
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
    in_progress: bool = False  # another writer is appending; `head` is the log as recorded
    recorded: int | None = field(default=None, repr=False)  # entries the head file records

    def as_recorded(self) -> LogCheck:
        """The log as the head file records it, for a failure that lies wholly after the
        recorded entries (which were all walked and matched): what a reader reports while
        another writer's append is landing."""
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
        return self.first_bad is None


def _canonical(obj: Any) -> bytes:
    # allow_nan=False: NaN and Infinity are not JSON, and another reader of the log
    # (verify, a future hosted version) must be able to recompute every hash.
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _line(entry: Entry) -> bytes:
    """The exact line the writer produced for an entry that was read back intact."""
    return _canonical(asdict(entry)) + b"\n"


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


def _read_head(path: Path) -> list[str] | object | None:
    """The hash of every entry the head file records, in order; None if there is no head
    file; _UNREADABLE if it cannot be read or is not exactly what the writer produces."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:  # a directory in its place, no permission: it cannot vouch for the log
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

    hashes: list[str] = recorded if isinstance(recorded, list) else []  # none yet: empty
    n, count = len(entries), len(hashes)

    def at(k: int) -> Head:
        return Head(k, entries[k - 1].hash if k else GENESIS)

    def failed(first_bad: int, reason: str, torn: bool = False) -> LogCheck:
        return LogCheck(at(first_bad - 1), entries[: first_bad - 1], first_bad, reason, torn,
                        ends[first_bad - 1], recorded=None if recorded is _UNREADABLE else count)

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
        return LogCheck(at(n), entries, intact_bytes=ends[-1], recorded=count)
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
    if n < count:
        return failed(n + 1, f"is missing: the head file records {count} entries and the log "
                             f"holds {n}")
    return failed(count + 1, f"was added after the head the head file records (entry {count})")


def _holder_id() -> tuple[int, int]:
    """Who holds a writer lock: the process and the thread. With the process in it, a
    forked child never passes for its parent (forking while holding is not supported; a
    child is refused rather than let in)."""
    return os.getpid(), threading.get_ident()


# The writer locks this process holds, by the lock file's identity (device and inode, so
# two spellings of one path, a symlink included, are the same file), with the (process,
# thread) holding each. A second CaseLog on the same file in the holding thread reads as
# the holder: its own fallback lock would be refused by its own thread, which is not
# another writer.
_HELD: dict[tuple[int, int], tuple[int, int]] = {}
_HELD_GUARD = threading.Lock()


def _file_id(st: os.stat_result) -> tuple[int, int]:
    return st.st_dev, st.st_ino


# The lock file carries the holder's state after the locked byte: "IDLE <pid>" from the
# moment the lock is taken, "APPENDING <pid>" while an entry is written and recorded, and
# "DROPPING <pid>" while a drop writes its repair entry. Only a holder marked as writing
# excuses an unrecorded last entry to a reader (R43), and a drop excuses only its complete
# repair entry, never the torn entry it removes: a holder that only checks the log, or a
# crash that left an entry torn, excuses nothing. Every new holder resets the mark, so one
# a crash left behind does not outlive the lock.
_MARK_AT = 8
_MARK_LEN = 32


def _set_mark(fd: int, state: str) -> None:
    # Best effort: a mark that cannot be written only makes readers report what they see.
    with suppress(OSError):
        os.lseek(fd, _MARK_AT, os.SEEK_SET)
        _write_all(fd, f"{state} {os.getpid()}".encode().ljust(_MARK_LEN))


def _alive(pid: int) -> bool:
    if os.name == "nt":  # pragma: no cover - the mark is reset as the lock is taken
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class CaseLog:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.head_path = self.path.with_name(self.path.name + ".head")
        # Guards the fields below, never held long. Reentrant, so a writer block's
        # cleanup run by the garbage collector or a signal handler inside one of these
        # short regions, on the same thread, cannot deadlock it.
        self._state = threading.RLock()
        self._lock_fd: int | None = None
        self._owner: tuple[int, int] | None = None  # (process id, thread ident) of the holder
        self._lock_depth = 0
        self._lock_id: tuple[int, int] | None = None  # the lock file's identity while held
        self._pid = os.getpid()  # a CaseLog belongs to the process that opened it (R42)

    # --- reading -----------------------------------------------------------------------

    def verify(self) -> LogCheck:
        """Walk the chain from the start, stop at the first entry that does not hold, and
        compare every entry with the head file."""
        me = _holder_id()
        with self._state:
            holder = self._owner == me
        if not holder:
            try:
                lock_id = _file_id(os.stat(self.lock_path))
            except OSError:  # no lock file: nobody in this process holds it
                lock_id = None
            if lock_id is not None:
                with _HELD_GUARD:
                    holder = _HELD.get(lock_id) == me
        if holder:  # nothing else can write: one read is the truth
            return _check(self._read_log(), _read_head(self.head_path))
        for attempt in range(READ_RETRIES):
            if attempt:
                time.sleep(READ_PAUSE)
            # The head file first: a writer replaces it only after its line is on disk, so
            # the log read next is never behind it, and a writer caught between the two
            # writes looks like a torn entry. Look again before believing one.
            before = _read_head(self.head_path)
            raw = self._read_log()
            check = _check(raw, before)
            after = _read_head(self.head_path)
            if not check.torn and after == before:
                return check
        # Still torn, or the head kept moving. Under the lock nothing moves, so if the lock
        # is free, one read there is the truth. An object a forked child inherited still
        # names its parent as holder whether or not the parent has let go, so the lock is
        # tested through a fresh handle: held now by the parent or anyone, it is refused;
        # free, it is taken and the read stands (R43: only a writer holding the case now).
        locker = CaseLog(self.path) if self._pid != os.getpid() else self
        try:
            with locker.writer():
                return _check(self._read_log(), _read_head(self.head_path))
        except CaseLocked:
            pass
        except OSError:  # the lock cannot be taken or tested: no writer is known, the read stands
            return check
        # Another writer holds the case. Judge a read only when the head held still across
        # it: then the read holds every entry that head records, each walked and matched,
        # and a failure at or before them is real whoever holds the case. A failure only
        # after them is excused only while the holder is marked as writing it (R43): an
        # append, or the repair entry of a drop (never the torn entry the drop removes). A
        # holder that only checks, or a stale torn entry, excuses nothing. The mark is read
        # after the log, so the head is looked at once more: if it moved, a write finished
        # in between and the read is stale, so read again.
        for attempt in range(READ_RETRIES + 1):
            if attempt:
                time.sleep(READ_PAUSE)
                before = _read_head(self.head_path)
                raw = self._read_log()
                check = _check(raw, before)
                after = _read_head(self.head_path)
            if after != before:
                continue
            if not check.past_recorded:
                return check
            state = self._holder_writing()
            if state == b"APPENDING" or (state == b"DROPPING" and not check.torn):
                return check.as_recorded()
            if _read_head(self.head_path) == after:
                return check
        # The head never held still: a writer holding the case kept recording entries. The
        # entries the earlier head records were judged. Appends only add lines, so every
        # line the read holds that the later head records must match it too, and a read
        # with fewer lines than the earlier head records is missing entries. A drop
        # replaces the log file, so a read caught across one is judged by the earlier
        # head alone. What follows is that writer's.
        if (isinstance(before, list) and isinstance(after, list) and raw is not None
                and after[: len(before)] == before and self._holder_writing() != b"DROPPING"):
            check = _check(raw, after[: max(len(before), raw.count(b"\n"))])
        return check.as_recorded() if check.past_recorded else check

    def _holder_writing(self) -> bytes | None:
        """What the live holder is marked as writing: APPENDING, DROPPING, or None."""
        try:
            with open(self.lock_path, "rb") as f:
                f.seek(_MARK_AT)
                raw = f.read(_MARK_LEN)
        except OSError:
            return None
        parts = raw.split()
        if len(parts) != 2 or parts[0] not in (b"APPENDING", b"DROPPING") or not parts[1].isdigit():
            return None
        return parts[0] if _alive(int(parts[1])) else None

    def _holder_mid_append(self) -> bool:
        return self._holder_writing() == b"APPENDING"

    @contextmanager
    def _appending(self, state: str = "APPENDING") -> Iterator[None]:
        """Mark the held lock as writing (an append, or a drop's repair entry) while an
        entry is written and recorded."""
        fd = self._lock_fd
        if fd is not None:
            _set_mark(fd, state)
        try:
            yield
        finally:
            if fd is not None:
                _set_mark(fd, "IDLE")

    def _read_log(self) -> bytes | None:
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return b""
        except OSError:  # a directory in its place, no permission
            return None

    def require_intact(self) -> Head:
        """The head of an intact log, or CaseLogBroken naming the first bad entry. Every
        append goes through it; sign-off (T27) must call it and refuse on the exception (R43)."""
        check = self.verify()
        if not check.ok:
            raise CaseLogBroken(self.path, check.first_bad, check.reason, check.torn)
        return check.head

    def entries(self) -> list[Entry]:
        """Every entry of an intact log; CaseLogBroken otherwise."""
        check = self.verify()
        if not check.ok:
            raise CaseLogBroken(self.path, check.first_bad, check.reason, check.torn)
        return check.entries

    # --- writing -----------------------------------------------------------------------

    @contextmanager
    def writer(self) -> Iterator[CaseLog]:
        """Hold the case's single-writer lock; refuses at once if another writer has it.

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
            if self._owner == me:
                self._lock_depth += 1
            elif self._owner is not None:
                raise CaseLocked(f"{self.path}: another window or command is writing to this case")
            else:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
                locked = False
                try:
                    _lock(fd)
                    locked = True
                    lock_id = _file_id(os.fstat(fd))
                    self._lock_fd, self._owner, self._lock_depth, self._lock_id = fd, me, 1, lock_id
                    with _HELD_GUARD:
                        _HELD[lock_id] = me
                    _set_mark(fd, "IDLE")  # whatever a crashed holder left, nothing is mid-append
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
        try:
            yield self
        finally:
            with self._state:
                # Only the holder releases, asked now rather than at entry: a forked
                # child leaving a block it inherited carries its parent's identity from
                # entry, but it is another process and never unlocks its parent's lock.
                if self._owner == _holder_id():
                    self._release_one_level()

    def _release_one_level(self) -> None:
        """Leave one level of this process's writer lock (call under _state)."""
        self._lock_depth -= 1
        if self._lock_depth == 0:
            fd, self._lock_fd, self._owner = self._lock_fd, None, None
            lock_id, self._lock_id = self._lock_id, None
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
        with self.writer():
            check = self.verify()
            if not check.ok:
                raise CaseLogBroken(self.path, check.first_bad, check.reason, check.torn)
            if check.head.position != expected:
                raise LogConflict(expected, check.head.position)
            return self._write(event, snapshot, check)

    def drop_torn_entry(self, *, expected: int, by: str) -> Entry:
        """Cut an incomplete last entry off and record who confirmed it. `expected` is the
        intact position the person was shown. Refused if the last entry is not torn, or
        if the chain is broken before it (that is tampering, not a torn write)."""
        if not isinstance(by, str) or not by.strip():
            raise ValueError("a name is required to drop a torn entry")
        with self.writer():
            check = self.verify()
            if not check.torn:
                if check.ok:
                    raise CaseLogBroken(self.path, check.head.position + 1, "does not exist: nothing is torn", False)
                raise CaseLogBroken(self.path, check.first_bad, check.reason, False)
            if check.head.position != expected:
                raise LogConflict(expected, check.head.position)
            with self._appending("DROPPING"):
                return self._replace_torn(check, by.strip())

    def _replace_torn(self, check: LogCheck, by: str) -> Entry:
        # Write the intact prefix plus the drop entry to a temporary file and rename it over
        # the log, so a crash leaves either the torn log or the repaired one, never a
        # truncated log with no record of who dropped the entry. A crash after the rename
        # and before the head file is replaced leaves the drop entry itself unrecorded: the
        # log loads as torn at the same entry, and dropping it again repairs it.
        intact = self.path.read_bytes()[: check.intact_bytes]
        entry, line = self._encode(TORN_DROPPED, {"seq": check.first_bad, "by": by}, check.head)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            _write_all(fd, intact + line)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            _replace(tmp, self.path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
        _fsync_dir(self.path.parent)
        self._write_head([*(e.hash for e in check.entries), entry.hash])
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

    def _write_head(self, hashes: list[str]) -> None:
        """Replace the head file in one rename, after the last entry it names is on disk."""
        tmp = self.head_path.with_name(self.head_path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            _write_all(fd, _head_bytes(hashes))
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            _replace(tmp, self.head_path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
        _fsync_dir(self.head_path.parent)

    def _write(self, event: str, data: dict[str, Any], check: LogCheck) -> Entry:
        with self._appending():
            return self._write_entry(event, data, check)

    def _write_entry(self, event: str, data: dict[str, Any], check: LogCheck) -> Entry:
        entry, line = self._encode(event, data, check.head)
        created = not self.path.exists()
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            _write_all(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)
        if created:
            _fsync_dir(self.path.parent)  # make the new file's name durable too
        self._write_head([*(e.hash for e in check.entries), entry.hash])
        return entry


# On Windows a reader's open file blocks a rename over it for the moment the read takes
# (no delete sharing), so a rename that meets one is retried briefly, not failed mid-append.
REPLACE_RETRIES = 40
REPLACE_PAUSE = 0.025


def _replace(src: Path, dst: Path) -> None:
    for attempt in range(REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if os.name != "nt" or attempt == REPLACE_RETRIES - 1:
                raise
            time.sleep(REPLACE_PAUSE)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:  # one call in practice; loop on a short write
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
    import msvcrt

    # msvcrt.locking reports a region another process holds as EACCES or EDEADLOCK.
    _CONTENDED = {errno.EACCES, getattr(errno, "EDEADLOCK", errno.EDEADLK)}

    def _lock(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    # flock with LOCK_NB reports a lock another process holds as EWOULDBLOCK (EAGAIN).
    _CONTENDED = {errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES}

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


LOG_NAME = "log.jsonl"


def for_case(folder: Path | str) -> CaseLog:
    """The case log of a case folder (cases/<id>/log.jsonl)."""
    return CaseLog(Path(folder) / LOG_NAME)
