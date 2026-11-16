"""The case log: hash-chained, append-only, one writer at a time (R42, R43; property P15).

P15: for any case log, changing, removing or reordering any entry before the head is
detected on the next load. The logs here are small enough to try every entry and every
kind of tamper exhaustively, which is the property over all logs of those sizes.
"""

import json

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


@pytest.mark.parametrize("n", SIZES)
def test_p15_changing_an_entry_and_recomputing_its_hash_is_detected_at_the_next(tmp_path, n):
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
        assert check.first_bad == k + 2  # the entry whose link to it no longer holds


@pytest.mark.parametrize("n", SIZES)
def test_p15_removing_any_entry_before_the_head_is_detected(tmp_path, n):
    log = _log(tmp_path, n)
    original = _lines(log)
    for k in range(n - 1):
        _write(log, original[:k] + original[k + 1:])
        check = log.verify()
        assert not check.ok and not check.torn
        assert check.first_bad == k + 1


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


@pytest.mark.parametrize("cut", [1, 10, -2, -1])
def test_torn_last_entry_is_detected_and_named(tmp_path, cut):
    log = _log(tmp_path, 3)
    lines = _lines(log)
    _write(log, [*lines[:2], lines[2][:cut]])  # no newline: the write never finished
    check = log.verify()
    assert not check.ok and check.torn and check.first_bad == 3
    assert check.head.position == 2  # the intact part
    with pytest.raises(case.CaseLogBroken) as err:
        log.require_intact()
    assert err.value.torn and "entry 3" in str(err.value) and "incomplete" in str(err.value)


def test_torn_last_entry_blocks_appends_until_dropped(tmp_path):
    log = _log(tmp_path, 3)
    lines = _lines(log)
    _write(log, [*lines[:2], lines[2][:15]])
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
    with pytest.raises(case.CaseLogBroken) as err:
        log.drop_torn_entry(expected=0, by="A")
    assert not err.value.torn and err.value.first_bad == 1


def test_a_crash_while_dropping_a_torn_entry_leaves_the_torn_log_unchanged(tmp_path, monkeypatch):
    log = _log(tmp_path, 3)
    lines = _lines(log)
    _write(log, [*lines[:2], lines[2][:15]])
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
    import os
    import stat

    if os.name == "nt":
        pytest.skip("POSIX permissions")
    log = _log(tmp_path, 1)
    assert stat.S_IMODE(log.path.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE(log.lock_path.stat().st_mode) & 0o077 == 0


def test_a_complete_head_without_its_newline_is_still_torn(tmp_path):
    log = _log(tmp_path, 2)
    _write(log, [*_lines(log)[:1], _lines(log)[1].rstrip("\n")])
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
    with log.writer():
        held = subprocess.run([sys.executable, "-c", script, str(log.path)], check=False)
    assert held.returncode == 3
    free = subprocess.run([sys.executable, "-c", script, str(log.path)], check=False)
    assert free.returncode == 0
    assert log.require_intact().position == 2
