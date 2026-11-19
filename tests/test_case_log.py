"""The case log: hash-chained, append-only, one writer at a time (R42, R43; property P15).

P15: for any case log, changing, removing or reordering any entry before the head is
detected on the next load. The logs here are small enough to try every entry and every
kind of tamper exhaustively, which is the property over all logs of those sizes.
"""

import contextlib
import json
import os
import threading
import time

import pytest

from cma import case

EVENTS = ["import", "profile_change", "override", "preview", "pack_write", "sign_off"]


def _log(tmp_path, n):
    log = case.CaseLog(tmp_path / "log.jsonl")
    for i in range(n):
        log.append(EVENTS[i % len(EVENTS)], {"n": i, "file": f"h{i}"}, expected=i)
    return log


def _lines(log):
    return log.path.read_text(encoding="utf-8").splitlines(keepends=True)


def _write(log, lines):
    log.path.write_text("".join(lines), encoding="utf-8")


def _write_head(log, hashes):
    log.head_path.write_text(json.dumps({"count": len(hashes), "hash": hashes[-1] if hashes else case.GENESIS,
                                         "hashes": hashes}, sort_keys=True,
                                        separators=(",", ":")) + "\n", encoding="utf-8")


def _crash_mid_append(log, monkeypatch, cut):
    """Append one more entry, losing power after `cut` bytes of its line reach the disk:
    the line has no newline and the head file was never updated."""
    real = case._write_all

    def torn(fd, data):
        real(fd, data[:cut])
        raise OSError("power lost")

    monkeypatch.setattr(case, "_write_all", torn)
    with pytest.raises(OSError):
        log.append("override", {"n": 99}, expected=log.require_intact().position)
    monkeypatch.undo()


# --- appending -------------------------------------------------------------------------


def test_each_entry_carries_the_hash_of_the_entry_before_it(tmp_path):
    log = _log(tmp_path, 3)
    entries = [json.loads(line) for line in _lines(log)]
    assert [e["seq"] for e in entries] == [1, 2, 3]
    assert entries[0]["prev"] == case.GENESIS
    assert entries[1]["prev"] == entries[0]["hash"]
    assert entries[2]["prev"] == entries[1]["hash"]
    head = log.require_intact()
    assert (head.position, head.hash) == (3, entries[2]["hash"])


def test_append_returns_the_new_head_and_stamps_utc_time(tmp_path):
    log = case.CaseLog(tmp_path / "log.jsonl")
    entry = log.append("import", {"file": "abc"}, expected=0)
    assert entry.seq == 1 and entry.event == "import" and entry.data == {"file": "abc"}
    assert entry.at.endswith("Z")
    assert log.verify().head.position == 1


def test_empty_or_missing_log_is_intact_at_position_zero(tmp_path):
    log = case.CaseLog(tmp_path / "log.jsonl")
    head = log.require_intact()
    assert (head.position, head.hash) == (0, case.GENESIS)


def test_stale_expected_position_is_a_conflict_and_writes_nothing(tmp_path):
    log = _log(tmp_path, 2)
    before = log.path.read_bytes()
    with pytest.raises(case.LogConflict) as e:
        log.append("override", {"line": "T1"}, expected=1)
    assert (e.value.expected, e.value.actual) == (1, 2)
    assert log.path.read_bytes() == before


def test_a_second_writer_is_refused_while_the_lock_is_held(tmp_path):
    log = _log(tmp_path, 1)
    other = case.CaseLog(log.path)
    with log.writer(), pytest.raises(case.CaseLocked):
        other.append("override", {}, expected=1)
    other.append("override", {}, expected=1)  # released
    assert log.require_intact().position == 2


def test_append_refuses_a_broken_log(tmp_path):
    log = _log(tmp_path, 3)
    lines = _lines(log)
    _write(log, [lines[0], lines[2]])
    with pytest.raises(case.CaseLogBroken):
        log.append("override", {}, expected=2)


def test_event_and_data_are_checked(tmp_path):
    log = case.CaseLog(tmp_path / "log.jsonl")
    with pytest.raises(ValueError):
        log.append("", {}, expected=0)
    with pytest.raises(ValueError):
        log.append("import", ["not", "a", "dict"], expected=0)
    assert not log.path.exists() or log.path.read_bytes() == b""


# --- P15: tamper anywhere before the head is detected, and the first bad entry named ---


def _change(entry, field):
    e = dict(entry)
    if field == "data":
        e["data"] = {**e["data"], "n": e["data"]["n"] + 1000}
    elif field == "event":
        e["event"] = "sign_off" if e["event"] != "sign_off" else "import"
    elif field == "at":
        e["at"] = "2000-01-01T00:00:00Z"
    elif field == "prev":
        e["prev"] = "f" * 64
    elif field == "seq":
        e["seq"] = e["seq"] + 7
    elif field == "hash":
        e["hash"] = "0" * 63 + "1"
    return e


def _dump(entry):
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


SIZES = range(2, 7)
FIELDS = ["data", "event", "at", "prev", "seq", "hash"]


@pytest.mark.invariants
@pytest.mark.parametrize("n", SIZES)
@pytest.mark.parametrize("field", FIELDS)
def test_p15_changing_any_entry_before_the_head_is_detected(tmp_path, n, field):
    log = _log(tmp_path, n)
    original = _lines(log)
    for k in range(n - 1):  # every entry before the head
        lines = list(original)
        lines[k] = _dump(_change(json.loads(lines[k]), field))
        _write(log, lines)
        check = case.CaseLog(log.path).verify()
        assert not check.ok and not check.torn
        assert check.first_bad == k + 1, (field, k)


@pytest.mark.invariants
@pytest.mark.parametrize("n", SIZES)
def test_p15_changing_an_entry_and_recomputing_its_hash_is_detected_at_that_entry(tmp_path, n):
    log = _log(tmp_path, n)
    original = _lines(log)
    for k in range(n - 1):
        e = json.loads(original[k])
        e["data"] = {"n": -1}
        e["hash"] = case.entry_hash(e)
        lines = list(original)
        lines[k] = _dump(e)
        _write(log, lines)
        check = log.verify()
        assert not check.ok
        assert check.first_bad == k + 1  # its hash is not the one the head file recorded


@pytest.mark.invariants
@pytest.mark.parametrize("n", SIZES)
def test_p15_removing_any_entry_before_the_head_is_detected(tmp_path, n):
    log = _log(tmp_path, n)
    original = _lines(log)
    for k in range(n - 1):
        _write(log, original[:k] + original[k + 1:])
        check = log.verify()
        assert not check.ok and not check.torn
        assert check.first_bad == k + 1


@pytest.mark.invariants
@pytest.mark.parametrize("n", SIZES)
def test_p15_reordering_any_entries_before_the_head_is_detected(tmp_path, n):
    log = _log(tmp_path, n)
    original = _lines(log)
    for i in range(n):
        for j in range(i + 1, n):
            lines = list(original)
            lines[i], lines[j] = lines[j], lines[i]
            _write(log, lines)
            check = log.verify()
            assert not check.ok
            assert check.first_bad == i + 1, (i, j)


@pytest.mark.invariants
@pytest.mark.parametrize("n", SIZES)
def test_p15_inserting_an_entry_before_the_head_is_detected(tmp_path, n):
    log = _log(tmp_path, n)
    original = _lines(log)
    for k in range(n):
        lines = [*original[:k], original[k], *original[k:]]  # a duplicated entry
        _write(log, lines)
        check = log.verify()
        assert not check.ok
        assert check.first_bad == k + 2


def test_a_garbled_line_before_the_head_is_broken_not_torn(tmp_path):
    log = _log(tmp_path, 3)
    lines = _lines(log)
    _write(log, [lines[0], "{not json\n", lines[2]])
    check = log.verify()
    assert not check.ok and not check.torn and check.first_bad == 2


def test_the_message_names_the_first_bad_entry_and_holds_no_entry_content(tmp_path):
    log = _log(tmp_path, 3)
    lines = _lines(log)
    e = json.loads(lines[1])
    e["data"] = {"secret": "ACME LTD"}
    _write(log, [lines[0], _dump(e), lines[2]])
    with pytest.raises(case.CaseLogBroken) as err:
        log.require_intact()
    assert err.value.first_bad == 2
    assert "entry 2" in str(err.value)
    assert "ACME" not in str(err.value)


# --- a torn last entry (power lost mid-write) ------------------------------------------


@pytest.mark.invariants
@pytest.mark.parametrize("cut", [1, 10, -2, -1])
def test_p15_torn_last_entry_is_detected_and_named(tmp_path, monkeypatch, cut):
    log = _log(tmp_path, 2)
    _crash_mid_append(log, monkeypatch, cut)  # no newline: the write never finished
    check = log.verify()
    assert not check.ok and check.torn and check.first_bad == 3
    assert check.head.position == 2  # the intact part
    with pytest.raises(case.CaseLogBroken) as err:
        log.require_intact()
    assert err.value.torn and "entry 3" in str(err.value) and "incomplete" in str(err.value)


@pytest.mark.invariants
def test_p15_a_lock_error_is_not_a_live_writer_so_a_torn_entry_is_still_reported(tmp_path, monkeypatch):
    # T12 review verify-28: a lock subsystem error (here ENOLCK) was treated as another
    # writer holding the lock, so a torn log was reported as its intact prefix.
    import errno
    log = _log(tmp_path, 2)
    _crash_mid_append(log, monkeypatch, 5)
    monkeypatch.setattr(case, "READ_PAUSE", 0)

    def broken_lock(fd, shared=False):
        raise OSError(errno.ENOLCK, "no locks available")

    monkeypatch.setattr(case, "_lock", broken_lock)
    with pytest.raises(OSError) as err, log.writer():
        pass
    assert not isinstance(err.value, case.CaseLocked)
    check = log.verify()
    assert not check.ok and check.torn and check.first_bad == 3


@pytest.mark.invariants
def test_p15_a_contended_lock_still_reads_as_another_writer(tmp_path, monkeypatch):
    import errno
    log = _log(tmp_path, 1)

    def held(fd, shared=False):
        raise OSError(errno.EWOULDBLOCK, "held")

    monkeypatch.setattr(case, "_lock", held)
    with pytest.raises(case.CaseLocked), log.writer():
        pass


@pytest.mark.invariants
def test_p15_a_failed_unlock_still_releases_the_lock(tmp_path, monkeypatch):
    # T12 review: if the explicit unlock raised, the descriptor was never closed, so the
    # lock stayed held and blocked later appends until the process exited.
    import errno
    log = _log(tmp_path, 1)
    closed = []
    real_close = os.close

    def broken_unlock(fd):
        raise OSError(errno.EIO, "unlock failed")

    def tracking_close(fd):
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(case, "_unlock", broken_unlock)
    monkeypatch.setattr(case.os, "close", tracking_close)
    with log.writer():
        held = log._lock_fd
    assert held in closed and log._lock_fd is None
    monkeypatch.undo()
    other = case.CaseLog(log.path)  # a fresh handle, as another command would open
    other.append("override", {}, expected=1)
    assert len(other.entries()) == 2


@pytest.mark.invariants
@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork is POSIX only")
def test_p15_a_forked_child_cannot_write_on_its_parents_lock(tmp_path):
    # T12 review: the owner was the thread ident alone, which a forked child inherits,
    # so the child entered the writer without a lock and both could append at seq 2.
    log = _log(tmp_path, 1)
    with log.writer():
        pid = os.fork()
        if pid == 0:  # the child: report through the exit code only
            try:
                with log.writer():
                    os._exit(1)
            except case.CaseLocked:
                os._exit(0)
            except BaseException:
                os._exit(2)
        _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    assert len(log.entries()) == 1


@pytest.mark.invariants
@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork is POSIX only")
def test_p15_a_forked_child_leaving_the_writer_does_not_release_its_parents_lock(tmp_path):
    # T12 review: a child forked inside a writer block ran the block's cleanup on the way
    # out and unlocked the shared open file, so the parent wrote on without its lock.
    log = _log(tmp_path, 1)
    pid = None
    try:
        with log.writer():
            pid = os.fork()
            if pid:
                _, status = os.waitpid(pid, 0)
                assert os.waitstatus_to_exitcode(status) == 0
                with pytest.raises(case.CaseLocked), case.CaseLog(log.path).writer():
                    pass
    finally:
        if pid == 0:  # the child left the block normally; report and stop here
            os._exit(0)
    with case.CaseLog(log.path).writer():  # released once the parent leaves
        pass


@pytest.mark.invariants
def test_p15_an_interrupt_after_the_lock_is_taken_leaves_no_lock(tmp_path, monkeypatch):
    # Pre-review: KeyboardInterrupt after flock but before the descriptor was recorded
    # leaked it, and every writer in the process was refused from then on.
    log = _log(tmp_path, 1)
    real = case._lock

    def lock_then_interrupt(fd, shared=False):
        real(fd)
        raise KeyboardInterrupt

    monkeypatch.setattr(case, "_lock", lock_then_interrupt)
    with pytest.raises(KeyboardInterrupt), log.writer():
        pass
    monkeypatch.undo()
    assert log._lock_fd is None and log._owner is None
    case.CaseLog(log.path).append("override", {}, expected=1)


@pytest.mark.invariants
def test_p15_cleanup_reentering_the_state_lock_on_its_thread_does_not_hang(tmp_path):
    # Pre-review: a writer block finalised by the garbage collector (or a signal handler)
    # while its thread was inside a short state-lock region deadlocked on that lock.
    log = _log(tmp_path, 1)

    def reenter():
        with log._state, log.writer():
            pass

    t = threading.Thread(target=reenter, daemon=True)
    t.start()
    t.join(5)
    assert not t.is_alive()


def test_a_rename_blocked_by_a_windows_reader_is_retried_not_failed(tmp_path, monkeypatch):
    # Pre-review: on Windows a reader's open file blocks a rename over it for a moment; the
    # head file's rename after an append must wait it out, not leave a torn entry.
    real, calls = os.replace, []

    def busy_twice(src, dst):
        calls.append(dst)
        if len(calls) <= 2:
            raise PermissionError(13, "in use by a reader")
        real(src, dst)

    src, dst = tmp_path / "a.tmp", tmp_path / "a"
    src.write_bytes(b"x")
    monkeypatch.setattr(case.os, "replace", busy_twice)
    monkeypatch.setattr(case, "REPLACE_PAUSE", 0)
    monkeypatch.setattr(case.os, "name", "nt")
    case._replace(src, dst)
    monkeypatch.setattr(case.os, "name", "posix")
    assert dst.read_bytes() == b"x" and len(calls) == 3
    src.write_bytes(b"y")
    calls.clear()
    with pytest.raises(PermissionError):  # elsewhere a permission error is real at once
        case._replace(src, dst)
    assert len(calls) == 1


@pytest.mark.invariants
@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_p15_the_holder_sees_a_torn_entry_through_another_spelling_of_the_path(tmp_path, monkeypatch):
    # T12 review: a second CaseLog opened through a symlinked folder was not recognised as
    # the holder, so its refused fallback lock excused a torn entry as an append landing.
    log = _log(tmp_path, 2)
    _crash_mid_append(log, monkeypatch, 10)
    alias = tmp_path / "alias"
    try:
        os.symlink(log.path.parent, alias, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not permitted here")
    with log.writer(), pytest.raises(case.CaseLogBroken):
        case.CaseLog(alias / log.path.name).require_intact()


@pytest.mark.invariants
@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork is POSIX only")
def test_p15_a_forked_childs_reader_reports_a_torn_entry_once_its_parent_has_let_go(tmp_path, monkeypatch):
    # R43 (revision 5): only a writer holding the case now excuses an incomplete entry. An
    # object inherited across a fork still names the parent after the parent lets go.
    log = _log(tmp_path, 2)
    _crash_mid_append(log, monkeypatch, 10)
    monkeypatch.setattr(case, "READ_PAUSE", 0)
    go_r, go_w = os.pipe()
    pid = None
    with log.writer():
        pid = os.fork()
        if pid == 0:
            code = 2
            try:
                os.close(go_w)
                os.read(go_r, 1)  # the parent has let go
                check = log.verify()
                code = 0 if check.torn and not check.in_progress and check.first_bad == 3 else 1
            finally:
                os._exit(code)
        os.close(go_r)
    os.write(go_w, b"x")
    os.close(go_w)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0


@pytest.mark.invariants
def test_p15_a_held_cases_recorded_entries_are_judged_strictly_and_an_unsteady_read_claims_nothing(
        tmp_path, monkeypatch):
    # R43 (revision 6, D73): while anyone holds the case, the entries the log records as
    # complete are judged from every read; nothing after them is called intact or broken.
    log = _log(tmp_path, 4)
    lines = _lines(log)
    tampered = "".join([*lines[:2], lines[2].replace('"n":2', '"n":7'), *lines[3:]]).encode()
    holder, taken, leave = case.CaseLog(log.path), threading.Event(), threading.Event()

    def hold():
        with holder.writer():
            taken.set()
            leave.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    assert taken.wait(5)
    real_head, calls = case._read_head, {"n": 0}

    def head_keeps_moving(path):
        calls["n"] += 1
        hashes = real_head(path)
        return hashes[:2] if calls["n"] % 2 else hashes  # never the same twice in a row

    try:
        monkeypatch.setattr(case, "READ_PAUSE", 0)
        monkeypatch.setattr(case.CaseLog, "_read_log", lambda self: tampered)
        check = case.CaseLog(log.path).verify()  # entry 3 is recorded: damage is named
        assert not check.ok and check.first_bad == 3 and not check.in_progress
        monkeypatch.setattr(case, "_read_head", head_keeps_moving)
        check = case.CaseLog(log.path).verify()  # entry 3 is past the head it was read with
        assert not check.checked and not check.ok and check.first_bad is None and check.in_progress
        with pytest.raises(case.CaseLocked):
            case.CaseLog(log.path).require_intact()
    finally:
        monkeypatch.undo()
        leave.set()
        t.join(10)


@pytest.mark.invariants
@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork is POSIX only")
def test_p15_a_forked_child_cannot_write_through_an_inherited_log_even_when_it_was_free(tmp_path):
    # T12 review / R42: a CaseLog belongs to the process that opened it; the child's
    # writes through it are refused whether or not the lock was held at the fork.
    log = _log(tmp_path, 1)
    pid = os.fork()
    if pid == 0:
        code = 2
        try:
            log.append("override", {}, expected=1)
            code = 1
        except case.CaseLocked:
            code = 0
        finally:
            os._exit(code)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    assert len(log.entries()) == 1
    log.append("override", {}, expected=1)  # the parent's own handle still writes


def _stale_unrecorded(log, k):
    """k recorded entries plus a complete line the head file does not record: the power
    went between writing the line and replacing the head file."""
    head = log.head_path.read_bytes()
    log.append("override", {"n": k}, expected=k)
    log.head_path.write_bytes(head)


@pytest.mark.invariants
def test_p15_a_reader_caught_across_a_drop_does_not_call_a_healthy_log_rewritten(tmp_path, monkeypatch):
    # Pre-review: a drop replaces the log file; a reader that read the old log and then
    # the new head judged the old unrecorded line against the drop entry's hash.
    log = _log(tmp_path, 3)
    _stale_unrecorded(log, 3)
    go, done, release, held = threading.Event(), threading.Event(), threading.Event(), threading.Event()

    def dropper():
        d = case.CaseLog(log.path)
        with d.writer():
            held.set()
            go.wait(5)
            d.drop_torn_entry(expected=3, by="A. Underwriter")
            done.set()
            release.wait(5)

    t = threading.Thread(target=dropper)
    t.start()
    assert held.wait(5)  # the reader starts while the dropper holds the case
    reader, calls, real = case.CaseLog(log.path), {"n": 0}, case.CaseLog._read_log

    def read(self):
        raw = real(self)
        if self is reader:
            calls["n"] += 1
            if calls["n"] == 1:  # the drop lands between this log read and the head
                go.set()
                done.wait(5)
        return raw

    monkeypatch.setattr(case.CaseLog, "_read_log", read)
    try:
        check = reader.verify()
    finally:
        monkeypatch.undo()
        release.set()
        t.join(10)
    assert check.ok, check.reason
    assert case.CaseLog(log.path).verify().ok


@pytest.mark.invariants
def test_p15_a_reader_during_a_slow_append_reports_it_being_saved_never_torn(tmp_path, monkeypatch):
    # An append whose head file is slow: the case is held, so the reader reports the
    # recorded entries and the new line as being saved; once free, the log is whole.
    log = _log(tmp_path, 3)
    written, go = threading.Event(), threading.Event()
    writer = case.CaseLog(log.path)
    real_head = case.CaseLog._write_head

    def slow_head(self, hashes):
        if self is writer:
            written.set()
            go.wait(5)
        real_head(self, hashes)

    monkeypatch.setattr(case.CaseLog, "_write_head", slow_head)
    monkeypatch.setattr(case, "READ_PAUSE", 0)
    t = threading.Thread(target=lambda: writer.append("override", {"n": 3}, expected=3))
    t.start()
    assert written.wait(5)
    try:
        check = case.CaseLog(log.path).verify()
    finally:
        go.set()
        t.join(10)
    assert check.in_progress and check.ok and not check.torn and check.head.position == 3
    after = case.CaseLog(log.path).verify()
    assert after.ok and not after.in_progress and after.head.position == 4


@pytest.mark.invariants
def test_p15_a_torn_entry_is_being_saved_while_held_and_named_once_the_case_is_free(tmp_path, monkeypatch):
    # R43 (revision 6): while a drop holds the case, a reader reports the recorded entries
    # and the rest as being saved, never intact; free, a torn entry is named.
    log = _log(tmp_path, 3)
    _crash_mid_append(log, monkeypatch, 10)
    monkeypatch.setattr(case, "READ_PAUSE", 0)
    free = case.CaseLog(log.path).verify()
    assert free.torn and free.first_bad == 4 and not free.in_progress
    inside, go = threading.Event(), threading.Event()
    dropper = case.CaseLog(log.path)
    real = case.CaseLog._replace_torn

    def slow(self, check, by):
        inside.set()
        go.wait(5)
        return real(self, check, by)

    monkeypatch.setattr(case.CaseLog, "_replace_torn", slow)
    t = threading.Thread(target=lambda: dropper.drop_torn_entry(expected=3, by="A. Underwriter"))
    t.start()
    assert inside.wait(5)
    try:
        held = case.CaseLog(log.path).verify()
    finally:
        go.set()
        t.join(10)
    assert held.in_progress and held.first_bad is None and held.head.position == 3
    done = case.CaseLog(log.path).verify()
    assert done.ok and done.head.position == 4 and done.entries[-1].event == case.TORN_DROPPED


@pytest.mark.invariants
def test_p15_entries_removed_while_another_writer_appends_are_still_named(tmp_path, monkeypatch):
    # Entries the log records as complete are judged strictly even while another writer
    # holds the case mid-append: removing them is named, never excused as being saved.
    log = _log(tmp_path, 3)
    written, go = threading.Event(), threading.Event()
    writer = case.CaseLog(log.path)
    real_head = case.CaseLog._write_head

    def slow_head(self, hashes):
        if self is writer:
            written.set()
            go.wait(5)
        real_head(self, hashes)

    monkeypatch.setattr(case.CaseLog, "_write_head", slow_head)
    t = threading.Thread(target=lambda: writer.append("override", {"n": 3}, expected=3))
    t.start()
    assert written.wait(5)
    lines = log.path.read_bytes().split(b"\n")
    log.path.write_bytes(b"\n".join(lines[:2]) + b"\n")  # recorded entries 3 and 4 cut away
    try:
        check = case.CaseLog(log.path).verify()
    finally:
        go.set()
        t.join(10)
    assert not check.ok and check.first_bad == 3 and not check.in_progress


def test_torn_last_entry_blocks_appends_until_dropped(tmp_path, monkeypatch):
    log = _log(tmp_path, 2)
    lines = _lines(log)
    _crash_mid_append(log, monkeypatch, 15)
    with pytest.raises(case.CaseLogBroken):
        log.append("override", {}, expected=2)
    log.drop_torn_entry(expected=2, by="A. Underwriter")
    head = log.require_intact()
    entries = log.entries()
    assert head.position == 3
    assert entries[-1].event == "torn_entry_dropped"
    assert entries[-1].data == {"seq": 3, "by": "A. Underwriter"}
    assert entries[-1].prev == json.loads(lines[1])["hash"]


def test_drop_torn_entry_refuses_when_nothing_is_torn_or_the_chain_is_broken(tmp_path):
    log = _log(tmp_path, 3)
    with pytest.raises(case.CaseLogBroken):
        log.drop_torn_entry(expected=3, by="A")
    lines = _lines(log)
    _write(log, [lines[1], lines[0], lines[2][:5]])  # broken before the torn tail
    _write_head(log, [])
    with pytest.raises(case.CaseLogBroken) as err:
        log.drop_torn_entry(expected=0, by="A")
    assert not err.value.torn and err.value.first_bad == 1


def test_a_crash_before_the_rename_while_dropping_leaves_the_torn_log_unchanged(tmp_path, monkeypatch):
    log = _log(tmp_path, 2)
    _crash_mid_append(log, monkeypatch, 15)
    before = log.path.read_bytes()

    def crash(*_a, **_k):
        raise OSError("power lost")

    monkeypatch.setattr(case.os, "replace", crash)
    with pytest.raises(OSError):
        log.drop_torn_entry(expected=2, by="A. Underwriter")
    assert log.path.read_bytes() == before  # still torn, still names entry 3
    assert log.verify().torn and log.verify().first_bad == 3
    assert not (tmp_path / "log.jsonl.tmp").exists()
    monkeypatch.undo()
    log.drop_torn_entry(expected=2, by="A. Underwriter")
    assert log.require_intact().position == 3


def test_data_that_is_not_strict_json_is_refused(tmp_path):
    log = case.CaseLog(tmp_path / "log.jsonl")
    for bad in ({"x": float("nan")}, {"x": float("inf")}, {"x": object()}):
        with pytest.raises(ValueError):
            log.append("override", bad, expected=0)
    assert not log.path.exists()


def test_new_log_and_lock_files_are_private_to_their_owner(tmp_path):
    import stat

    if os.name == "nt":
        pytest.skip("POSIX permissions")
    log = _log(tmp_path, 1)
    assert stat.S_IMODE(log.path.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE(log.lock_path.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE(log.head_path.stat().st_mode) & 0o077 == 0


# --- hardener round 17 -----------------------------------------------------------------


@pytest.mark.contracts
def test_retrying_an_append_that_already_succeeded_writes_nothing_twice(tmp_path):
    # The expected position is the idempotency key: a client that lost the reply and
    # retries the same write gets a conflict, not a second copy of the entry.
    log = _log(tmp_path, 1)
    log.append("override", {"line": "T1"}, expected=1)
    before = (log.path.read_bytes(), log.head_path.read_bytes())
    with pytest.raises(case.LogConflict):
        log.append("override", {"line": "T1"}, expected=1)
    assert (log.path.read_bytes(), log.head_path.read_bytes()) == before


def test_data_with_text_that_is_not_utf8_encodable_is_refused(tmp_path):
    log = case.CaseLog(tmp_path / "log.jsonl")
    with pytest.raises(ValueError):
        log.append("override", {"reason": "\ud800"}, expected=0)
    assert not log.path.exists()


@pytest.mark.invariants
def test_p15_a_complete_last_entry_without_its_newline_is_still_torn(tmp_path, monkeypatch):
    log = _log(tmp_path, 1)
    _crash_mid_append(log, monkeypatch, -1)
    check = log.verify()
    assert check.torn and check.first_bad == 2


def test_the_lock_holds_against_another_process(tmp_path):
    import subprocess
    import sys

    log = _log(tmp_path, 1)
    script = (
        "import sys; from cma import case\n"
        "try:\n"
        "    case.CaseLog(sys.argv[1]).append('override', {}, expected=1)\n"
        "except case.CaseLocked:\n"
        "    sys.exit(3)\n"
    )
    # The child must import this checkout's cma, not whatever is installed: put the
    # directory that holds the imported package first on its path.
    from pathlib import Path

    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        [str(Path(case.__file__).resolve().parents[1]), os.environ.get("PYTHONPATH", "")])}
    run = [sys.executable, "-c", script, str(log.path)]
    with log.writer():
        held = subprocess.run(run, check=False, env=env, capture_output=True, text=True)
    assert held.returncode == 3, held.stderr
    free = subprocess.run(run, check=False, env=env, capture_output=True, text=True)
    assert free.returncode == 0, free.stderr
    assert log.require_intact().position == 2


# --- findings from verification round 3 ------------------------------------------------


def _sharing_threads_race(log, gate_name):
    """Two threads share one CaseLog at position 1; the first pauses inside the lock."""
    import threading

    inside, release = threading.Event(), threading.Event()
    real = getattr(log, gate_name)

    def paused(*a, **k):
        if threading.current_thread().name == "first":
            inside.set()
            release.wait(5)
        return real(*a, **k)

    setattr(log, gate_name, paused)
    results = {}

    def go(name):
        try:
            results[name] = log.append("override", {"by": name}, expected=1)
        except case.CaseLogError as e:
            results[name] = e

    first = threading.Thread(target=go, args=("first",), name="first")
    first.start()
    assert inside.wait(5)
    second = threading.Thread(target=go, args=("second",), name="second")
    second.start()
    second.join(5)
    release.set()
    first.join(5)
    return results


def test_threads_sharing_one_instance_do_not_share_the_lock(tmp_path):
    log = _log(tmp_path, 1)
    results = _sharing_threads_race(log, "_write")
    assert isinstance(results["first"], case.Entry)
    assert isinstance(results["second"], case.CaseLocked)
    check = log.verify()
    assert check.ok and check.head.position == 2


def test_a_lock_held_by_one_thread_is_not_reentered_by_another(tmp_path):
    import threading

    log = _log(tmp_path, 1)
    got = {}
    with log.writer():
        t = threading.Thread(target=lambda: got.setdefault("r", _try_append(log)))
        t.start()
        t.join(5)
        log.append("override", {}, expected=1)  # the holder may still nest
    assert isinstance(got["r"], case.CaseLocked)
    assert log.require_intact().position == 2


def _try_append(log):
    try:
        return log.append("override", {}, expected=1)
    except case.CaseLogError as e:
        return e


@pytest.mark.invariants
def test_p15_a_duplicate_member_hiding_a_change_before_the_head_is_detected(tmp_path):
    log = _log(tmp_path, 3)
    lines = _lines(log)
    # A second "event" member placed before the real one: a lenient decoder keeps the
    # last value, so the hash still matches, but the bytes say something else first.
    lines[1] = lines[1].replace('"event":', '"event":"sign_off","event":', 1)
    _write(log, lines)
    check = log.verify()
    assert not check.ok and not check.torn and check.first_bad == 2


@pytest.mark.parametrize("token", ["1e999", "-1e999", "NaN", "Infinity", "-Infinity"])
def test_a_non_finite_number_in_a_complete_entry_is_named_not_raised(tmp_path, token):
    log = _log(tmp_path, 3)
    lines = _lines(log)
    e = json.loads(lines[1])
    lines[1] = _dump({**e, "data": {"x": 0}}).replace('{"x":0}', '{"x":' + token + "}")
    _write(log, lines)
    check = log.verify()
    assert not check.ok and not check.torn and check.first_bad == 2
    with pytest.raises(case.CaseLogBroken) as err:
        log.require_intact()
    assert err.value.first_bad == 2


def test_deeply_nested_data_in_a_complete_entry_is_named_not_raised(tmp_path):
    log = _log(tmp_path, 3)
    lines = _lines(log)
    e = json.loads(lines[1])
    deep = "[" * 100_000 + "]" * 100_000
    lines[1] = _dump({**e, "data": {"x": 0}}).replace('{"x":0}', '{"x":' + deep + "}")
    _write(log, lines)
    check = log.verify()
    assert not check.ok and check.first_bad == 2


def test_an_entry_not_in_canonical_form_is_named(tmp_path):
    log = _log(tmp_path, 3)
    lines = _lines(log)
    e = json.loads(lines[1])
    lines[1] = json.dumps(e, sort_keys=True) + "\n"  # same members, other spacing
    _write(log, lines)
    check = log.verify()
    assert not check.ok and not check.torn and check.first_bad == 2


# --- hardener round 9 ----------------------------------------------------------------


@pytest.mark.parametrize("bad", [{"a": (1, 2)}, {"m": {1: "z"}}, {"m": {True: "z"}}, {"m": {None: 1}}])
def test_data_that_would_not_read_back_unchanged_is_refused(tmp_path, bad):
    # json.dumps quietly turns tuples into lists and 1/True/None keys into strings, so the
    # Entry append returned would not be the entry every later reader sees.
    log = case.CaseLog(tmp_path / "log.jsonl")
    with pytest.raises(ValueError):
        log.append("override", bad, expected=0)
    assert not log.path.exists()


def test_the_entry_append_returns_is_the_entry_read_back(tmp_path):
    log = case.CaseLog(tmp_path / "log.jsonl")
    written = log.append("override", {"a": [1, 2.5, None, True], "m": {"k": "v"}, "s": "é"}, expected=0)
    assert log.entries() == [written]


# --- the head file (head hash and count), compared on load -----------------------------


def test_each_append_records_the_head_hash_and_count(tmp_path):
    log = _log(tmp_path, 3)
    head = json.loads(log.head_path.read_text(encoding="utf-8"))
    hashes = [json.loads(line)["hash"] for line in _lines(log)]
    assert head == {"count": 3, "hash": hashes[2], "hashes": hashes}


def _rebuild(entries):
    """Rewrite a chain from scratch so every hash and every link is consistent again."""
    out, prev = [], case.GENESIS
    for seq, e in enumerate(entries, start=1):
        body = {"seq": seq, "prev": prev, "at": e["at"], "event": e["event"], "data": e["data"]}
        body["hash"] = case.entry_hash(body)
        out.append(_dump(body))
        prev = body["hash"]
    return out


def _chain_alone_holds(lines, tmp_path):
    """The rebuilt lines, with a head file of their own, load as an intact log."""
    alone = case.CaseLog(tmp_path / "alone" / "log.jsonl")
    alone.path.parent.mkdir(exist_ok=True)
    _write(alone, lines)
    _write_head(alone, [json.loads(line)["hash"] for line in lines])
    return alone.verify().ok


@pytest.mark.invariants
@pytest.mark.parametrize("n", SIZES)
def test_p15_a_rebuilt_chain_that_does_not_match_the_head_file_is_detected(tmp_path, n):
    log = _log(tmp_path, n)
    original = [json.loads(line) for line in _lines(log)]
    for k in range(n - 1):  # change, remove, or reorder at entry k+1, then rebuild
        changed = [dict(e) for e in original]
        changed[k]["data"] = {"n": -1}
        removed = original[:k] + original[k + 1:]
        reordered = list(original)
        reordered[k], reordered[k + 1] = reordered[k + 1], reordered[k]
        for kind, entries in (("changed", changed), ("removed", removed), ("reordered", reordered)):
            rebuilt = _rebuild(entries)
            assert _chain_alone_holds(rebuilt, tmp_path), (kind, k)  # every link is consistent
            _write(log, rebuilt)
            check = case.CaseLog(log.path).verify()
            assert not check.ok and not check.torn, (kind, k)
            # The history diverges at entry k+1: that entry is named, and only the entries
            # before it, unchanged, are returned as the intact prefix.
            assert check.first_bad == k + 1, (kind, k)
            assert check.head.position == k, (kind, k)
            assert [e.hash for e in check.entries] == [e["hash"] for e in original[:k]], (kind, k)
            with pytest.raises(case.CaseLogBroken) as err:
                case.CaseLog(log.path).require_intact()
            assert err.value.first_bad == k + 1 and "head file" in str(err.value), (kind, k)


@pytest.mark.invariants
@pytest.mark.parametrize("n", SIZES)
def test_p15_a_rebuilt_chain_with_any_entry_removed_names_that_entry(tmp_path, n):
    # The reviewer's case: remove entry k, renumber and rehash the survivors, leave the
    # head file alone. The log is one entry short, but the first bad entry is k, not n.
    log = _log(tmp_path, n)
    original = [json.loads(line) for line in _lines(log)]
    for k in range(n):
        _write(log, _rebuild(original[:k] + original[k + 1:]))
        check = case.CaseLog(log.path).verify()
        expected = k + 1  # entry k+1 now holds what was entry k+2, or is missing if k was last
        assert not check.ok and not check.torn and check.first_bad == expected, k
        assert len(check.entries) == k


@pytest.mark.invariants
@pytest.mark.parametrize("n", SIZES)
def test_p15_removing_the_head_entry_is_detected_against_the_head_file(tmp_path, n):
    log = _log(tmp_path, n)
    _write(log, _lines(log)[:-1])
    check = case.CaseLog(log.path).verify()
    assert not check.ok and not check.torn and check.first_bad == n


@pytest.mark.invariants
def test_p15_an_entry_added_after_the_recorded_head_is_detected(tmp_path):
    log = _log(tmp_path, 3)
    _write_head(log, [json.loads(_lines(log)[0])["hash"]])
    check = log.verify()
    assert not check.ok and not check.torn and check.first_bad == 2


@pytest.mark.invariants
def test_p15_a_missing_or_unreadable_head_file_is_detected(tmp_path):
    log = _log(tmp_path, 3)
    log.head_path.unlink()
    check = log.verify()
    assert not check.ok and not check.torn and check.first_bad == 1
    assert "head file" in check.reason and "missing" in check.reason
    log.head_path.write_text("{not json\n", encoding="utf-8")
    check = log.verify()
    assert not check.ok and "head file" in check.reason


@pytest.mark.invariants
def test_p15_an_entry_written_but_not_recorded_in_the_head_file_is_torn(tmp_path, monkeypatch):
    # Power lost after the line reached the disk but before the head file was replaced.
    log = _log(tmp_path, 2)
    monkeypatch.setattr(case.CaseLog, "_write_head", lambda *_a: (_ for _ in ()).throw(OSError("power lost")))
    with pytest.raises(OSError):
        log.append("override", {"n": 99}, expected=2)
    monkeypatch.undo()
    check = log.verify()
    assert check.torn and check.first_bad == 3 and check.head.position == 2
    log.drop_torn_entry(expected=2, by="A. Underwriter")
    entries = log.entries()
    assert len(entries) == 3 and entries[-1].event == case.TORN_DROPPED


def test_a_reader_during_an_append_does_not_report_a_torn_entry(tmp_path, monkeypatch):
    # The line is on disk, the head file not yet: another reader retries rather than alarm.
    import threading

    log = _log(tmp_path, 2)
    reader = case.CaseLog(log.path)
    real = case.CaseLog._write_head
    seen = {}

    def slow_head(self, head):
        t = threading.Thread(target=lambda: seen.setdefault("check", reader.verify()))
        t.start()
        t.join(0.03)  # the reader runs while the head file is still behind
        real(self, head)
        t.join(5)

    monkeypatch.setattr(case.CaseLog, "_write_head", slow_head)
    log.append("override", {"n": 99}, expected=2)
    # Either the finished append (the head caught up within the retries) or, on a slow
    # machine, the log as recorded with this append in progress: never a torn entry.
    check = seen["check"]
    assert check.ok and not check.torn
    assert check.head.position == 3 or (check.in_progress and check.head.position == 2)


def test_data_changed_by_another_thread_mid_append_cannot_break_the_entry(tmp_path, monkeypatch):
    log = case.CaseLog(tmp_path / "log.jsonl")
    shared = {"v": 1}
    real = case.entry_hash

    def racing(entry):
        h = real(entry)
        shared["v"] = 2  # another thread changes the caller's dict after hashing
        return h

    monkeypatch.setattr(case, "entry_hash", racing)
    written = log.append("override", shared, expected=0)
    monkeypatch.undo()
    assert log.require_intact().position == 1
    assert log.entries() == [written] and written.data == {"v": 1}


# --- findings from verification round 19 -----------------------------------------------


def test_a_reader_never_reports_a_live_append_as_torn_however_long_the_writer_pauses(tmp_path, monkeypatch):
    # The writer's line is on disk and its head file is not, and it stays paused far past
    # the reader's retry window while still holding the lock.
    import threading

    log = _log(tmp_path, 2)
    reader = case.CaseLog(log.path)
    real = case.CaseLog._write_head
    on_disk, release = threading.Event(), threading.Event()

    def paused_head(self, hashes):
        on_disk.set()
        release.wait(10)
        real(self, hashes)

    monkeypatch.setattr(case.CaseLog, "_write_head", paused_head)
    writer = threading.Thread(target=lambda: log.append("override", {"n": 99}, expected=2))
    writer.start()
    try:
        assert on_disk.wait(5)
        for _ in range(3):  # each verify waits out its whole retry window, the writer still paused
            check = reader.verify()
            assert check.ok and not check.torn and check.in_progress
            assert check.head.position == 2  # the log as recorded
            # never CaseLogBroken, so nobody is asked to drop it; and not "whole" either,
            # since only the recorded entries could be checked (pre-review, R43 rev 6)
            with pytest.raises(case.CaseLocked):
                reader.require_intact()
    finally:
        release.set()
        writer.join(5)
    check = reader.verify()
    assert check.ok and not check.in_progress and check.head.position == 3


@pytest.mark.invariants
def test_p15_a_torn_entry_with_the_lock_free_is_still_reported_by_a_reader(tmp_path, monkeypatch):
    log = _log(tmp_path, 2)
    _crash_mid_append(log, monkeypatch, 10)
    check = case.CaseLog(log.path).verify()  # no writer: the reader takes the lock and looks
    assert check.torn and check.first_bad == 3 and not check.in_progress


def test_a_lock_holder_still_sees_a_torn_entry_while_a_reader_sees_it_recorded(tmp_path, monkeypatch):
    # Sign-off holds the lock: for it a torn entry is torn, whoever else is reading.
    log = _log(tmp_path, 2)
    _crash_mid_append(log, monkeypatch, 10)
    with log.writer():
        assert log.verify().torn
        with pytest.raises(case.CaseLogBroken):
            log.require_intact()
        # Pre-review: a second CaseLog in the holding thread (for_case builds one) is the
        # holder too. Its own fallback lock is refused by its own thread, which is not
        # another writer, so it must not excuse the torn entry as an append in progress.
        with pytest.raises(case.CaseLogBroken):
            case.CaseLog(log.path).require_intact()
        # R43 (revision 6, D73): another thread reading while this one holds the case
        # reports the recorded entries and the rest as being saved, never intact or broken
        seen = []
        t = threading.Thread(target=lambda: seen.append(case.CaseLog(log.path).verify()))
        t.start()
        t.join(10)
        assert seen and seen[0].in_progress and seen[0].first_bad is None and seen[0].head.position == 2


def test_a_crash_after_the_rename_while_dropping_leaves_a_torn_log_that_drops_again(tmp_path, monkeypatch):
    log = _log(tmp_path, 2)
    _crash_mid_append(log, monkeypatch, 15)
    monkeypatch.setattr(case.CaseLog, "_write_head", lambda *_a: (_ for _ in ()).throw(OSError("power lost")))
    with pytest.raises(OSError):
        log.drop_torn_entry(expected=2, by="A. Underwriter")
    monkeypatch.undo()
    # The log bytes changed (the drop entry is on disk), the head file did not: the drop
    # entry is itself an unrecorded entry 3, still torn and still in the way of appends.
    check = log.verify()
    assert check.torn and check.first_bad == 3 and check.head.position == 2
    with pytest.raises(case.CaseLogBroken):
        log.append("override", {}, expected=2)
    log.drop_torn_entry(expected=2, by="A. Underwriter")
    entries = log.entries()
    assert len(entries) == 3 and entries[-1].event == case.TORN_DROPPED


@pytest.mark.invariants
def test_p15_a_head_file_that_cannot_be_read_is_named_not_raised(tmp_path):
    log = _log(tmp_path, 3)
    log.head_path.unlink()
    log.head_path.mkdir()  # a read error, not malformed bytes
    with pytest.raises(case.CaseLogBroken) as err:
        log.require_intact()
    assert err.value.first_bad == 1 and "head file" in str(err.value)


def test_a_log_file_that_cannot_be_read_is_named_not_raised(tmp_path):
    log = case.CaseLog(tmp_path / "log.jsonl")
    log.path.mkdir()
    with pytest.raises(case.CaseLogBroken) as err:
        log.require_intact()
    assert err.value.first_bad == 1


@pytest.mark.invariants
def test_p15_a_head_file_whose_hashes_disagree_with_its_count_or_head_is_unreadable(tmp_path):
    log = _log(tmp_path, 2)
    hashes = [json.loads(line)["hash"] for line in _lines(log)]
    for bad in ({"count": 3, "hash": hashes[1], "hashes": hashes},
                {"count": 2, "hash": hashes[0], "hashes": hashes},
                {"count": 2, "hash": hashes[1]}):
        log.head_path.write_text(json.dumps(bad, sort_keys=True, separators=(",", ":")) + "\n",
                                 encoding="utf-8")
        check = log.verify()
        assert not check.ok and check.first_bad == 1 and "head file" in check.reason


# --- findings from verification round 26 -----------------------------------------------


def test_a_reader_never_reports_a_healthy_log_broken_while_appends_keep_landing(tmp_path):
    # Another window appends without pause while this one checks again and again: every
    # answer is intact, being saved, or nothing claimed; never broken.
    log = _log(tmp_path, 2)
    stop = threading.Event()

    def keep_appending():
        w = case.CaseLog(log.path)
        while not stop.is_set():
            with contextlib.suppress(case.CaseLocked):
                w.append("override", {"n": 1}, expected=w.require_intact().position)

    t = threading.Thread(target=keep_appending)
    t.start()
    try:
        checks = [case.CaseLog(log.path).verify() for _ in range(40)]
    finally:
        stop.set()
        t.join(10)
    assert all(c.first_bad is None for c in checks), [c.reason for c in checks if c.first_bad]
    assert case.CaseLog(log.path).verify().ok


def test_a_torn_log_is_still_named_when_the_reader_cannot_create_the_lock_file(tmp_path, monkeypatch):
    log = _log(tmp_path, 2)
    _crash_mid_append(log, monkeypatch, 10)
    log.lock_path.unlink()
    real_open = case.os.open

    def no_create(path, flags, *a):
        if os.fspath(path) == os.fspath(log.lock_path):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real_open(path, flags, *a)

    monkeypatch.setattr(case.os, "open", no_create)
    with pytest.raises(case.CaseLogBroken) as err:
        case.CaseLog(log.path).require_intact()
    assert err.value.first_bad == 3 and err.value.torn


# --- loading a case: the production path (cma case check) -------------------------------


def _case_dir(tmp_path, n):
    folder = tmp_path / "cases" / "c1"
    folder.mkdir(parents=True)
    log = case.for_case(folder)
    for i in range(n):
        log.append(EVENTS[i % len(EVENTS)], {"n": i, "secret": "ACME LTD"}, expected=i)
    return folder, log


def _check_cli(folder):
    from typer.testing import CliRunner

    from cma.cli import app

    return CliRunner().invoke(app, ["case", "check", str(folder)])


def test_loading_a_case_opens_its_log_beside_the_profile(tmp_path):
    folder, log = _case_dir(tmp_path, 2)
    assert log.path == folder / "log.jsonl"
    r = _check_cli(folder)
    assert r.exit_code == 0, r.output
    assert "intact" in r.output and "2 entries" in r.output


@pytest.mark.invariants
@pytest.mark.parametrize("tamper", ["change", "remove", "reorder", "rebuild"])
def test_p15_loading_a_case_detects_a_tampered_log_and_names_the_first_bad_entry(tmp_path, tamper):
    folder, log = _case_dir(tmp_path, 4)
    lines = _lines(log)
    if tamper == "change":
        lines[1] = lines[1].replace('"n":1', '"n":7')
    elif tamper == "remove":
        del lines[1]
    elif tamper == "reorder":
        lines[1], lines[2] = lines[2], lines[1]
    else:  # every hash and link recomputed with entry 2 changed: only the head file sees it
        entries = [json.loads(line) for line in lines]
        entries[1]["data"]["n"] = 7
        lines = _rebuild(entries)
    _write(log, lines)
    r = _check_cli(folder)
    assert r.exit_code == 3, r.output
    assert "entry 2" in r.output
    assert "ACME" not in r.output  # entries are named by number, never quoted


@pytest.mark.invariants
def test_p15_loading_a_case_with_a_torn_last_entry_names_it_and_says_to_confirm_dropping_it(tmp_path, monkeypatch):
    folder, log = _case_dir(tmp_path, 2)
    _crash_mid_append(log, monkeypatch, 10)
    r = _check_cli(folder)
    assert r.exit_code == 3, r.output
    assert "entry 3" in r.output and "incomplete" in r.output


def test_loading_a_folder_that_is_not_a_case_is_refused(tmp_path):
    r = _check_cli(tmp_path / "nope")
    assert r.exit_code == 2


# --- hardener round 23: the `cma case check` exit-code contract --------------------------


@pytest.mark.contracts
def test_case_check_exit_codes_are_0_intact_2_not_a_case_3_broken(tmp_path):
    # A new case folder with no log yet is intact at entry 0 and the check writes nothing.
    folder = tmp_path / "cases" / "c0"
    folder.mkdir(parents=True)
    r = _check_cli(folder)
    assert r.exit_code == 0 and "0 entries" in r.output, r.output
    assert sorted(p.name for p in folder.iterdir()) == []
    # A log that cannot be read is broken (exit 3), named by entry number, not raised.
    (folder / case.LOG_NAME).mkdir()
    r = _check_cli(folder)
    assert r.exit_code == 3 and "entry 1" in r.output, r.output
    # A path that is a file, not a case folder, is refused (exit 2).
    stray = tmp_path / "stray.txt"
    stray.write_text("x", encoding="utf-8")
    assert _check_cli(stray).exit_code == 2


@pytest.mark.contracts
def test_case_check_while_another_writer_appends_reports_the_recorded_log_not_a_break(tmp_path, monkeypatch):
    # A torn tail while another command holds the lock is that command's append landing.
    folder, log = _case_dir(tmp_path, 2)
    _crash_mid_append(log, monkeypatch, 10)
    taken, leave = threading.Event(), threading.Event()

    def other_command():  # another command holds the case: a thread, not this one
        held = case.for_case(folder)
        with held.writer():
            taken.set()
            leave.wait(10)

    t = threading.Thread(target=other_command)
    t.start()
    assert taken.wait(10)
    try:
        r = _check_cli(folder)
    finally:
        leave.set()
        t.join(10)
    # D75: being saved is never intact: exit 4, the recorded entries named, the rest unchecked
    assert r.exit_code == 4, r.output
    assert "2 recorded entries" in r.output and "saved" in r.output and "not" in r.output
    assert "Case log broken" not in r.output and "intact" not in r.output
    r = _check_cli(folder)  # the lock free again: the torn entry is named
    assert r.exit_code == 3 and "entry 3" in r.output and "incomplete" in r.output


def test_case_check_says_try_again_when_nothing_could_be_checked(tmp_path, monkeypatch):
    # R43 (revision 6): with no steady read while another holds the case, nothing is
    # claimed: neither intact (exit 0) nor broken (exit 3).
    folder, _ = _case_dir(tmp_path, 2)
    unchecked = case.LogCheck(case.Head(0, case.GENESIS), [], in_progress=True, checked=False)
    monkeypatch.setattr(case.CaseLog, "verify", lambda self: unchecked)
    r = _check_cli(folder)
    assert r.exit_code == 4 and "being saved" in r.output and "intact" not in r.output


@pytest.mark.invariants
def test_p15_with_the_lock_untestable_an_unsteady_read_never_calls_a_healthy_log_broken(tmp_path, monkeypatch):
    # Pre-review (rev 6): with a lock error and appends landing in every read, the reader
    # fell back to one unsteady read and reported "added after the head" as a break.
    import errno
    log = _log(tmp_path, 3)
    writer = case.CaseLog(log.path)
    reader = case.CaseLog(log.path)
    real_read = case.CaseLog._read_log

    def busy(self):
        if self is reader:
            for _ in range(2):  # two appends land between the reader's head read and log read
                c = case._check(real_read(writer), case._read_head(writer.head_path))
                writer._write_entry("override", {"x": 1}, c)
        return real_read(self)

    def no_locks(fd, shared=False):
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(case, "READ_PAUSE", 0)
    monkeypatch.setattr(case.CaseLog, "_read_log", busy)
    monkeypatch.setattr(case, "_lock", no_locks)
    check = reader.verify()
    monkeypatch.undo()
    assert check.first_bad is None, check.reason  # never broken
    assert not check.checked  # and nothing claimed past what it could check
    assert case.CaseLog(log.path).verify().ok


@pytest.mark.invariants
def test_p15_require_intact_refuses_while_another_holds_the_case_whatever_follows(tmp_path):
    # Pre-review (rev 6): require_intact returned the recorded head as "intact" while an
    # unchecked line followed it; sign-off must hold the case, and nobody else gets a head.
    log = _log(tmp_path, 2)
    with open(log.path, "ab") as f:
        f.write(b'{"garbage": true}\n')
    holder, taken, leave = case.CaseLog(log.path), threading.Event(), threading.Event()

    def hold():
        with holder.writer():
            taken.set()
            leave.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    assert taken.wait(5)
    try:
        with pytest.raises(case.CaseLocked):
            case.CaseLog(log.path).require_intact()
    finally:
        leave.set()
        t.join(10)
    with pytest.raises(case.CaseLogBroken):  # free: the bad line is named
        case.CaseLog(log.path).require_intact()


@pytest.mark.invariants
def test_p15_an_abandoned_writer_block_closed_on_another_thread_releases_the_lock(tmp_path):
    # Pre-review (rev 6): a block finalised on a thread other than its owner released
    # nothing, so the lock was held until the process exited.
    log = _log(tmp_path, 1)

    def block():
        with log.writer():
            yield

    gen = block()
    next(gen)  # the lock is held inside the suspended block
    with pytest.raises(case.CaseLocked), case.CaseLog(log.path).writer():
        pass
    t = threading.Thread(target=gen.close)  # finalised elsewhere, as a garbage collector may
    t.start()
    t.join(10)
    with case.CaseLog(log.path).writer():  # released
        pass


@pytest.mark.invariants
def test_p15_a_held_intact_log_is_being_saved_never_intact(tmp_path):
    # T12 review 7 / R43: while anyone holds the case the log is never called intact, even
    # with nothing after the recorded entries; case check exits 4 (D75).
    folder, _ = _case_dir(tmp_path, 2)
    holder, taken, leave = case.for_case(folder), threading.Event(), threading.Event()

    def hold():
        with holder.writer():
            taken.set()
            leave.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    assert taken.wait(5)
    try:
        check = case.for_case(folder).verify()
        r = _check_cli(folder)
    finally:
        leave.set()
        t.join(10)
    assert check.in_progress and check.first_bad is None and check.head.position == 2
    assert r.exit_code == 4 and "intact" not in r.output
    assert _check_cli(folder).exit_code == 0  # free: intact


@pytest.mark.invariants
def test_p15_no_verdict_comes_from_a_read_across_which_the_head_moved(tmp_path, monkeypatch):
    # T12 review 7 / R43: entries are judged only from a steady read; damage seen only in
    # reads where the head moved yields no verdict at all, not "broken" and not "intact".
    log = _log(tmp_path, 4)
    lines = _lines(log)
    tampered = "".join([lines[0].replace('"n":0', '"n":9'), *lines[1:]]).encode()
    holder, taken, leave = case.CaseLog(log.path), threading.Event(), threading.Event()

    def hold():
        with holder.writer():
            taken.set()
            leave.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    assert taken.wait(5)
    real_head, calls = case._read_head, {"n": 0}

    def head_keeps_moving(path):
        calls["n"] += 1
        hashes = real_head(path)
        return hashes[:3] if calls["n"] % 2 else hashes

    try:
        monkeypatch.setattr(case, "READ_PAUSE", 0)
        monkeypatch.setattr(case.CaseLog, "_read_log", lambda self: tampered)
        monkeypatch.setattr(case, "_read_head", head_keeps_moving)
        check = case.CaseLog(log.path).verify()
    finally:
        monkeypatch.undo()
        leave.set()
        t.join(10)
    assert not check.checked and check.first_bad is None and not check.ok


@pytest.mark.invariants
def test_p15_an_untestable_lock_with_unsteady_reads_blames_no_other_window(tmp_path, monkeypatch):
    # T12 review 7 / R43: a lock that cannot be tested is not another holder, so with no
    # steady read the reader claims nothing and does not say another window is saving.
    import errno
    folder, _ = _case_dir(tmp_path, 3)
    real_head, calls = case._read_head, {"n": 0}

    def head_keeps_moving(path):
        calls["n"] += 1
        hashes = real_head(path)
        return hashes[:2] if calls["n"] % 2 else hashes

    def no_locks(fd, shared=False):
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(case, "READ_PAUSE", 0)
    monkeypatch.setattr(case, "_lock", no_locks)
    monkeypatch.setattr(case, "_read_head", head_keeps_moving)
    check = case.for_case(folder).verify()
    r = _check_cli(folder)
    monkeypatch.undo()
    assert not check.checked and not check.in_progress and check.first_bad is None
    assert r.exit_code == 4 and "another window" not in r.output and "could not be checked" in r.output


@pytest.mark.invariants
@pytest.mark.skipif(not case._SHARED_LOCKS, reason="shared locks are POSIX only")
@pytest.mark.parametrize("torn", [False, True])
def test_p15_two_checks_at_once_never_see_each_other_as_a_save(tmp_path, monkeypatch, torn):
    # Pre-review (rev 7): a check held the lock exclusively, so a second check saw the case
    # held and reported "being saved" (exit 4) for an intact or a torn log. A check is not a
    # case-log writer: checks share the lock.
    log = _log(tmp_path, 3)
    if torn:
        _crash_mid_append(log, monkeypatch, 10)
    other, taken, leave = case.CaseLog(log.path), threading.Event(), threading.Event()

    def checking():
        with other._reading():
            taken.set()
            leave.wait(10)

    t = threading.Thread(target=checking)
    t.start()
    assert taken.wait(5)
    try:
        check = case.CaseLog(log.path).verify()
    finally:
        leave.set()
        t.join(10)
    assert not check.in_progress and check.checked
    assert (check.torn and check.first_bad == 4) if torn else check.ok


@pytest.mark.invariants
@pytest.mark.skipif(not case._SHARED_LOCKS, reason="shared locks are POSIX only")
def test_p15_a_save_waits_out_a_long_check_instead_of_being_refused(tmp_path):
    # Pre-review (rev 7) / R42: only a writer refuses a writer. A check longer than
    # WRITER_WAIT used to make an overlapping save fail with CaseLocked.
    log = _log(tmp_path, 1)
    taken = threading.Event()

    def long_check():
        with log._reading():
            taken.set()
            time.sleep(case.WRITER_WAIT * 3)

    t = threading.Thread(target=long_check)
    t.start()
    assert taken.wait(5)
    case.CaseLog(log.path).append("override", {}, expected=1)  # waits, then saves
    t.join(10)
    assert len(log.entries()) == 2


def test_the_held_lock_registry_is_reentrant_on_its_thread():
    # Pre-review (rev 7): like _state, so a writer block finalised by the garbage collector
    # inside a registry region on the same thread cannot deadlock it.
    done = threading.Event()

    def reenter():
        with case._HELD_GUARD, case._HELD_GUARD:
            done.set()

    t = threading.Thread(target=reenter, daemon=True)
    t.start()
    assert done.wait(5)


@pytest.mark.invariants
def test_p15_a_recorded_entry_whose_line_is_cut_short_is_named_incomplete_not_missing(tmp_path):
    # T12 review 8 / R43: the head file records entry 3 and only part of its line is left.
    log = _log(tmp_path, 3)
    raw = log.path.read_bytes()
    log.path.write_bytes(raw[: len(raw) - 12])  # entry 3's line loses its end and newline
    check = log.verify()
    assert not check.ok and check.first_bad == 3 and "incomplete" in check.reason
    assert not check.torn  # a recorded entry is damage, not a crash's torn write to drop
    with pytest.raises(case.CaseLogBroken):
        log.drop_torn_entry(expected=2, by="A. Underwriter")


@pytest.mark.invariants
def test_p15_a_writer_that_starts_while_a_check_reads_without_a_lock_file_is_seen(tmp_path, monkeypatch):
    # T12 review 8 / R43: with no lock file the check read without the lock; a writer that
    # created and took the lock meanwhile must make it probe again, not call the log intact.
    log = _log(tmp_path, 2)
    log.lock_path.unlink()
    taken, leave = threading.Event(), threading.Event()

    def writer_starts():
        with case.CaseLog(log.path).writer():
            taken.set()
            leave.wait(10)

    reader, real, started = case.CaseLog(log.path), case.CaseLog._read_log, {}

    def read(self):
        if self is reader and not started:
            started["t"] = threading.Thread(target=writer_starts)
            started["t"].start()
            assert taken.wait(5)
        return real(self)

    monkeypatch.setattr(case.CaseLog, "_read_log", read)
    try:
        check = reader.verify()
    finally:
        monkeypatch.undo()
        leave.set()
        started["t"].join(10)
    assert check.in_progress and not (check.ok and not check.in_progress)


@pytest.mark.invariants
def test_p15_a_holder_that_lets_go_during_the_read_leaves_its_incomplete_entry_named(tmp_path, monkeypatch):
    # T12 review 9 / R43: the case was held at the probe and free by the read; "being saved"
    # no longer stands, so the reader reads the free case and names the incomplete entry.
    log = _log(tmp_path, 2)
    _crash_mid_append(log, monkeypatch, 10)
    monkeypatch.setattr(case, "READ_PAUSE", 0)
    taken, leave, gone = threading.Event(), threading.Event(), threading.Event()

    def hold_then_let_go():
        with case.CaseLog(log.path).writer():
            taken.set()
            leave.wait(10)
        gone.set()

    t = threading.Thread(target=hold_then_let_go)
    t.start()
    assert taken.wait(5)
    reader, real = case.CaseLog(log.path), case.CaseLog._read_log

    def read(self):
        if self is reader and not leave.is_set():
            leave.set()  # the holder lets go during this read
            assert gone.wait(5)
        return real(self)

    monkeypatch.setattr(case.CaseLog, "_read_log", read)
    try:
        check = reader.verify()
    finally:
        monkeypatch.undo()
        leave.set()
        t.join(10)
    assert check.torn and check.first_bad == 3 and not check.in_progress


@pytest.mark.invariants
def test_p15_a_lost_head_file_never_makes_a_committed_entry_droppable(tmp_path):
    # T12 review 9: with the head file gone, a one-entry log looked like a crashed first
    # append, and its only committed entry could be dropped.
    log = _log(tmp_path, 1)
    log.head_path.unlink()
    check = log.verify()
    assert not check.ok and not check.torn and "head file" in check.reason
    with pytest.raises(case.CaseLogBroken):
        log.drop_torn_entry(expected=0, by="A. Underwriter")


@pytest.mark.invariants
def test_p15_a_crash_during_the_first_append_is_still_a_torn_entry_to_drop(tmp_path, monkeypatch):
    # The head file is written (empty) before the first line, so this crash stays repairable.
    log = case.CaseLog(tmp_path / "log.jsonl")
    real = case._write_all

    def torn_line(fd, data):  # the head file is written whole; the entry's line is cut
        if data.startswith(b'{"count"'):
            return real(fd, data)
        real(fd, data[:10])
        raise OSError("power lost")

    monkeypatch.setattr(case, "_write_all", torn_line)
    with pytest.raises(OSError):
        log.append("override", {"n": 0}, expected=0)
    monkeypatch.undo()
    assert log.head_path.exists()
    check = log.verify()
    assert check.torn and check.first_bad == 1
    log.drop_torn_entry(expected=0, by="A. Underwriter")
    assert [e.event for e in log.entries()] == [case.TORN_DROPPED]


@pytest.mark.invariants
def test_p15_a_case_that_changes_hands_on_every_attempt_gets_no_verdict(tmp_path, monkeypatch):
    # T12 review 10 / R43: held at each probe and free by the end of each read, so neither
    # "being saved" (nobody held it through the read) nor a free-case verdict stands.
    log = _log(tmp_path, 2)
    _crash_mid_append(log, monkeypatch, 10)
    monkeypatch.setattr(case, "READ_PAUSE", 0)
    monkeypatch.setattr(case.CaseLog, "_still_held", lambda self: False)

    def always_held(self):
        raise case.CaseLocked("held at the probe")
        yield  # pragma: no cover

    monkeypatch.setattr(case.CaseLog, "_reading", contextlib.contextmanager(always_held))
    check = case.CaseLog(log.path).verify()
    monkeypatch.undo()
    assert not check.checked and not check.in_progress and check.first_bad is None
    assert case.CaseLog(log.path).verify().torn  # free and steady: the entry is named


@pytest.mark.invariants
def test_p15_each_writer_hold_counts_once_and_checks_never_count(tmp_path):
    log = _log(tmp_path, 1)
    start = case._read_generation(log.lock_path)
    with log.writer(), log.writer():  # one hold, nested: counted once
        pass
    case.CaseLog(log.path).verify()  # a check takes the lock shared and does not count
    assert case._read_generation(log.lock_path) == start + 1


@pytest.mark.invariants
def test_p15_two_holds_around_a_read_are_never_taken_for_one(tmp_path, monkeypatch):
    # T12 review 11 / R43: held before and after the read, but by different writers (the
    # count moved): the read may have been of a free case, so it is not "being saved".
    log = _log(tmp_path, 2)
    _crash_mid_append(log, monkeypatch, 10)
    monkeypatch.setattr(case, "READ_PAUSE", 0)
    counts = iter(range(1000))

    def held_now(self):
        raise case.CaseLocked("held")
        yield  # pragma: no cover

    monkeypatch.setattr(case.CaseLog, "_reading", contextlib.contextmanager(held_now))
    monkeypatch.setattr(case.CaseLog, "_still_held", lambda self: True)
    monkeypatch.setattr(case, "_read_generation", lambda path: next(counts))
    check = case.CaseLog(log.path).verify()
    monkeypatch.undo()
    assert not check.checked and not check.in_progress and check.first_bad is None


@pytest.mark.invariants
def test_p15_a_writer_that_cannot_count_its_hold_leaves_no_lock(tmp_path, monkeypatch):
    import errno
    log = _log(tmp_path, 1)

    def cannot_count(fd):
        raise OSError(errno.EIO, "write failed")

    monkeypatch.setattr(case, "_bump_generation", cannot_count)
    with pytest.raises(OSError), log.writer():
        pass
    monkeypatch.undo()
    case.CaseLog(log.path).append("override", {}, expected=1)  # the lock was let go
