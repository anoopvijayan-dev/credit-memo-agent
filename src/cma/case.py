"""The case log: an append-only, hash-chained record of everything done to a case (R42, R43).

One JSON object per line, written in a single write that ends with its newline:

    {"seq": 3, "prev": "<hash of entry 2>", "at": "2026-11-16T10:04:01.123456Z",
     "event": "override", "data": {...}, "hash": "<sha256 of the other five fields>"}

The first entry's `prev` is GENESIS. Each entry carries the hash of the entry before it
and its own hash, so a changed, removed, inserted or reordered entry anywhere before the
head breaks the chain on the next load, and `verify` names the first entry that does not
hold. A last line without its newline is a torn entry (the process or the power stopped
mid-write); it is reported as incomplete, not as tampering, and stays in the way of
appends and sign-off until someone confirms dropping it (`drop_torn_entry`), which is
itself logged. A line is read strictly: it must be exactly the canonical bytes the
writer produces, so a repeated member, a number no float can hold, or reformatting is
named as a bad entry rather than reinterpreted or raised. Removing the head itself
cannot be seen from the file alone: whoever needs to pin the head (a sign-off, a pack)
records its position and hash.

One writer at a time: every append takes an exclusive, non-blocking lock on a sibling
lock file and re-reads the log under it, and the caller states the position it last
saw. A second tab or terminal that read an older position gets LogConflict and reloads;
one that tries while another holds the lock gets CaseLocked, including another thread
using the same CaseLog object. The operating system drops the lock when its holder
dies, so a crash never leaves the case locked.

Messages name entries by number only and never quote their content: data holds case
details, and errors end up in terminals and logs.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS = "0" * 64
HASHED_FIELDS = ("seq", "prev", "at", "event", "data")
ENTRY_FIELDS = frozenset((*HASHED_FIELDS, "hash"))
TORN_DROPPED = "torn_entry_dropped"


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

    @property
    def ok(self) -> bool:
        return self.first_bad is None


def _canonical(obj: Any) -> bytes:
    # allow_nan=False: NaN and Infinity are not JSON, and another reader of the log
    # (verify, a future hosted version) must be able to recompute every hash.
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


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


class CaseLog:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self._state = threading.Lock()  # guards the three fields below, never held long
        self._lock_fd: int | None = None
        self._owner: int | None = None  # thread ident of the holder
        self._lock_depth = 0

    # --- reading -----------------------------------------------------------------------

    def verify(self) -> LogCheck:
        """Walk the chain from the start and stop at the first entry that does not hold."""
        raw = self.path.read_bytes() if self.path.exists() else b""
        pieces = raw.split(b"\n")
        complete, tail = pieces[:-1], pieces[-1]  # tail is non-empty only if torn
        entries: list[Entry] = []
        prev, offset = GENESIS, 0
        for n, line in enumerate(complete, start=1):
            entry, reason = _parse(line, n, prev)
            if entry is None:
                return LogCheck(Head(n - 1, prev), entries, n, reason, False, offset)
            entries.append(entry)
            prev, offset = entry.hash, offset + len(line) + 1
        if tail:
            n = len(complete) + 1
            reason = ("is incomplete: the write did not finish (the process or power stopped); "
                      "confirm dropping it before anything else is recorded or signed off")
            return LogCheck(Head(n - 1, prev), entries, n, reason, True, offset)
        return LogCheck(Head(len(entries), prev), entries, intact_bytes=offset)

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
        belongs to a thread, not to the object."""
        me = threading.get_ident()
        with self._state:
            if self._owner == me:
                self._lock_depth += 1
            elif self._owner is not None:
                raise CaseLocked(f"{self.path}: another window or command is writing to this case")
            else:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
                try:
                    _lock(fd)
                except OSError as e:
                    os.close(fd)
                    raise CaseLocked(f"{self.path}: another window or command is writing to this case") from e
                self._lock_fd, self._owner, self._lock_depth = fd, me, 1
        try:
            yield self
        finally:
            with self._state:
                self._lock_depth -= 1
                if self._lock_depth == 0:
                    fd, self._lock_fd, self._owner = self._lock_fd, None, None
                    if fd is not None:
                        _unlock(fd)
                        os.close(fd)

    def append(self, event: str, data: dict[str, Any], *, expected: int) -> Entry:
        """Append one entry if the log is intact and still at `expected` entries."""
        if not isinstance(event, str) or not event.strip():
            raise ValueError("event must be a non-empty string")
        if not isinstance(data, dict):
            raise ValueError("data must be a mapping")
        try:
            _canonical(data)
        except (TypeError, ValueError) as e:
            raise ValueError(f"data is not plain JSON: {e}") from e
        with self.writer():
            head = self.require_intact()
            if head.position != expected:
                raise LogConflict(expected, head.position)
            return self._write(event, data, head)

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
            # Write the intact prefix plus the drop entry to a temporary file and rename it
            # over the log, so a crash leaves either the torn log or the repaired one, never
            # a truncated log with no record of who dropped the entry.
            intact = self.path.read_bytes()[: check.intact_bytes]
            entry, line = self._encode(TORN_DROPPED, {"seq": check.first_bad, "by": by.strip()}, check.head)
            tmp = self.path.with_name(self.path.name + ".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                _write_all(fd, intact + line)
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                os.replace(tmp, self.path)
            except OSError:
                tmp.unlink(missing_ok=True)
                raise
            _fsync_dir(self.path.parent)
            return entry

    @staticmethod
    def _encode(event: str, data: dict[str, Any], head: Head) -> tuple[Entry, bytes]:
        body = {"seq": head.position + 1, "prev": head.hash, "at": _utc_now(), "event": event, "data": data}
        entry = Entry(**body, hash=entry_hash(body))
        return entry, _canonical({**body, "hash": entry.hash}) + b"\n"

    def _write(self, event: str, data: dict[str, Any], head: Head) -> Entry:
        entry, line = self._encode(event, data, head)
        created = not self.path.exists()
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            _write_all(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)
        if created:
            _fsync_dir(self.path.parent)  # make the new file's name durable too
        return entry


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

    def _lock(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
