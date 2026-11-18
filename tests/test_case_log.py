"""The case log: hash-chained, append-only, one writer at a time (R42, R43; property P15).

P15: for any case log, changing, removing or reordering any entry before the head is
detected on the next load. The logs here are small enough to try every entry and every
kind of tamper exhaustively, which is the property over all logs of those sizes.
"""

import json
import os

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
    assert seen["check"].ok and seen["check"].head.position == 3


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
            reader.require_intact()  # no CaseLogBroken, so nobody is asked to drop it
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
        assert case.CaseLog(log.path).verify().in_progress


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


def test_a_reader_never_reports_a_healthy_log_broken_while_appends_keep_landing(tmp_path, monkeypatch):
    # Every read attempt sees a head, then two more committed appends before it reads the
    # log; on the last attempt another writer also holds the lock, so the reader cannot
    # take it to look under it. Each extra entry is a complete, recorded append.
    from contextlib import ExitStack

    log = _log(tmp_path, 2)
    reader = case.CaseLog(log.path)
    other, holder = case.CaseLog(log.path), case.CaseLog(log.path)
    real = case.CaseLog._read_log
    calls = {"n": 0}

    def busy(self):
        if self is reader:
            calls["n"] += 1
            for _ in range(2):
                other.append("override", {"n": calls["n"]}, expected=other.require_intact().position)
            if calls["n"] == case.READ_RETRIES:
                stack.enter_context(holder.writer())
        return real(self)

    with ExitStack() as stack:
        monkeypatch.setattr(case.CaseLog, "_read_log", busy)
        check = reader.verify()
    monkeypatch.undo()
    assert check.ok and not check.torn and check.in_progress
    assert 2 <= check.head.position <= 2 + 2 * case.READ_RETRIES
    assert [e.seq for e in check.entries] == list(range(1, check.head.position + 1))
    assert reader.verify().ok  # once the writers are gone, the whole log is intact


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
