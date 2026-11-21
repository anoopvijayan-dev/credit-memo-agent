"""`cma case purge` (R35): the case folder is deleted, and the firm-level ledger
(purge_ledger.jsonl at the case root) gains one line holding only the case id, the purge
time and who purged it."""

import json
import os
import re
import threading

import pytest
from typer.testing import CliRunner

from cma import case, store
from cma.cli import app

runner = CliRunner()
DIRECTOR = "Jane Sentinel-Doe"
LABEL = "Acme Sentinel Bakery"
DESCRIPTION = "CARD SETTLEMENT SENTINELPAY"
STATEMENT = ("id,date,description,amount,balance\n"
             f"L1,2026-01-05,{DESCRIPTION},500.00,1500.00\n"
             "L2,2026-01-06,DD COUNCIL,-100.00,1400.00\n")


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    return root


def _full_case(home, tmp_path):
    """A case with a profile, an imported statement and several log entries."""
    case_id = store.new_case(label=LABEL)
    folder = store.case_folder(case_id)
    store.update_profile(folder, lambda p: {**p, "directors": [DIRECTOR], "business_name": LABEL})
    src = tmp_path / "statement.csv"
    src.write_text(STATEMENT)
    store.import_file(folder, src, "csv")
    return case_id, folder


def _ledger(home):
    path = home / store.PURGE_LEDGER
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _all_files(root):
    return [os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs] + \
           [os.path.join(d, s) for d, ss, _ in os.walk(root) for s in ss]


@pytest.mark.contracts
def test_purge_deletes_every_file_of_the_case_and_ledger_holds_no_personal_data(home, tmp_path):
    case_id, folder = _full_case(home, tmp_path)
    assert (folder / "inputs").iterdir()  # an input file, 0400, is among what goes
    (tmp_path / "other").mkdir()
    other_id, other = _full_case(home, tmp_path / "other")

    record = store.purge_case(case_id)

    assert not folder.exists()
    assert not [p for p in _all_files(home) if case_id in p]  # no tombstone, nothing named by it
    assert other.is_dir() and case.for_case(other).verify().ok  # another case is untouched
    lines = _ledger(home)
    assert lines == [{"case": case_id, "purged_at": record.purged_at, "by": record.by}]
    assert set(lines[0]) == {"case", "purged_at", "by"}
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z", lines[0]["purged_at"])
    raw = (home / store.PURGE_LEDGER).read_text(encoding="utf-8")
    for personal in (DIRECTOR, LABEL, DESCRIPTION, "SENTINEL", "Sentinel", "500.00", "1500.00"):
        assert personal not in raw
    assert other_id not in raw


@pytest.mark.contracts
def test_ledger_is_appended_and_owner_only(home, tmp_path):
    first, _ = _full_case(home, tmp_path)
    second = store.new_case()
    store.purge_case(first)
    store.purge_case(second)
    assert [line["case"] for line in _ledger(home)] == [first, second]
    if os.name == "posix":
        assert (home / store.PURGE_LEDGER).stat().st_mode & 0o777 == 0o600


@pytest.mark.contracts
def test_purge_records_the_operating_system_user(home, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_os_user", lambda: "underwriter1")
    case_id = store.new_case()
    assert store.purge_case(case_id).by == "underwriter1"
    assert _ledger(home)[0]["by"] == "underwriter1"


@pytest.mark.contracts
def test_purging_an_unknown_or_malformed_case_writes_no_ledger_line(home):
    store.new_case()
    for bad in ("0123456789abcdef", "../cases", "x"):
        with pytest.raises(store.CaseStoreError):
            store.purge_case(bad)
    assert not (home / store.PURGE_LEDGER).exists()


@pytest.mark.contracts
def test_purge_is_refused_while_another_writer_holds_the_case(home, tmp_path):
    case_id, folder = _full_case(home, tmp_path)
    held, release = threading.Event(), threading.Event()

    def hold():
        with case.for_case(folder).writer():
            held.set()
            release.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    try:
        held.wait(5)
        with pytest.raises(case.CaseLocked):
            store.purge_case(case_id, wait=0.1)
        assert folder.is_dir()
        assert not (home / store.PURGE_LEDGER).exists()
    finally:
        release.set()
        t.join()
    store.purge_case(case_id)
    assert not folder.exists()


@pytest.mark.contracts
def test_a_purge_interrupted_after_the_rename_is_finished_by_purging_again(home, tmp_path, monkeypatch):
    case_id, folder = _full_case(home, tmp_path)
    real = store._remove_tree
    calls = []

    def fail_once(path):
        calls.append(path)
        if len(calls) == 1:
            raise OSError(5, "Input/output error", str(path))
        real(path)

    monkeypatch.setattr(store, "_remove_tree", fail_once)
    with pytest.raises(store.CaseStoreError, match="purge again"):
        store.purge_case(case_id)
    assert not folder.exists()  # the case is out of use at once
    assert not (home / store.PURGE_LEDGER).exists()  # not recorded until every file is gone
    with pytest.raises(store.CaseStoreError):
        store.case_folder(case_id)
    store.purge_case(case_id)
    assert not [p for p in _all_files(home) if case_id in p]
    assert [line["case"] for line in _ledger(home)] == [case_id]


@pytest.mark.contracts
def test_purge_works_on_a_case_whose_log_is_broken(home, tmp_path):
    case_id, folder = _full_case(home, tmp_path)
    log = folder / case.LOG_NAME
    log.write_bytes(log.read_bytes().replace(b"case_created", b"case_creatxd"))
    store.purge_case(case_id)
    assert not folder.exists()


@pytest.mark.contracts
def test_cli_case_purge_asks_then_deletes_and_records(home, tmp_path):
    case_id, folder = _full_case(home, tmp_path)
    refused = runner.invoke(app, ["case", "purge", case_id], input="n\n")
    assert refused.exit_code == 1 and folder.is_dir()
    assert not (home / store.PURGE_LEDGER).exists()

    done = runner.invoke(app, ["case", "purge", case_id], input="y\n")
    assert done.exit_code == 0, done.output
    assert case_id in done.output
    assert not folder.exists()
    assert [line["case"] for line in _ledger(home)] == [case_id]
    for personal in (DIRECTOR, LABEL, DESCRIPTION):
        assert personal not in done.output

    again = runner.invoke(app, ["case", "purge", case_id, "--yes"])
    assert again.exit_code == 2
    assert len(_ledger(home)) == 1


@pytest.mark.contracts
def test_cli_finishes_an_interrupted_purge(home, tmp_path, monkeypatch):
    case_id, _folder = _full_case(home, tmp_path)
    real = store._remove_tree
    monkeypatch.setattr(store, "_remove_tree", lambda p: (_ for _ in ()).throw(OSError(13, "Permission denied")))
    first = runner.invoke(app, ["case", "purge", case_id, "--yes"])
    assert first.exit_code == 2 and "purge again" in first.output
    assert not (home / store.PURGE_LEDGER).exists()
    monkeypatch.setattr(store, "_remove_tree", real)
    second = runner.invoke(app, ["case", "purge", case_id, "--yes"])
    assert second.exit_code == 0, second.output
    assert not [p for p in _all_files(home) if case_id in p]
    assert [line["case"] for line in _ledger(home)] == [case_id]


@pytest.mark.contracts
def test_a_purge_whose_ledger_write_fails_is_recorded_by_purging_again(home, tmp_path, monkeypatch):
    case_id, folder = _full_case(home, tmp_path)
    real = store._append_ledger

    def full_disk(*_a):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store, "_append_ledger", full_disk)
    with pytest.raises(store.CaseStoreError, match="purge again"):
        store.purge_case(case_id)
    assert not folder.exists()
    monkeypatch.setattr(store, "_append_ledger", real)
    store.purge_case(case_id)  # the emptied folder marks the unfinished purge
    assert [line["case"] for line in _ledger(home)] == [case_id]
    assert not [p for p in _all_files(home) if case_id in p]


@pytest.mark.contracts
def test_a_purge_that_finished_but_kept_its_mark_writes_no_second_ledger_line(home, tmp_path, monkeypatch):
    case_id = store.new_case()
    real_rmdir = type(home).rmdir
    calls = []

    def fail_once(self):
        calls.append(self)
        if len(calls) == 1:
            raise OSError(5, "Input/output error")
        real_rmdir(self)

    monkeypatch.setattr(type(home), "rmdir", fail_once)
    with pytest.raises(store.CaseStoreError, match="purge again"):
        store.purge_case(case_id)
    store.purge_case(case_id)
    assert [line["case"] for line in _ledger(home)] == [case_id]


@pytest.mark.contracts
def test_a_purge_racing_another_never_recreates_the_case_or_records_it_twice(home, monkeypatch):
    case_id = store.new_case()
    folder = store.case_folder(case_id)
    store.purge_case(case_id)
    # A second purge that found the case before the first moved it.
    monkeypatch.setattr(store, "purge_target", lambda _c, _r=None: folder)
    with pytest.raises(store.CaseStoreError, match="no case"):
        store.purge_case(case_id)
    assert not folder.exists()
    assert len(_ledger(home)) == 1


def test_purges_finishing_together_record_the_case_once(tmp_path):
    """R35: the ledger was checked for the case and then appended to in two steps, so two purges
    of one case finishing together could both record it. The check and the append are one step
    under an exclusive lock on the ledger."""
    import threading

    root = tmp_path / "root"
    root.mkdir()
    line = {"case": "0123456789abcdef", "purged_at": "2026-11-21T00:00:00.000000Z", "by": "someone"}
    start = threading.Barrier(8)

    def finish():
        start.wait()
        store._append_ledger(root, dict(line))

    threads = [threading.Thread(target=finish) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = (root / store.PURGE_LEDGER).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and '"case":"0123456789abcdef"' in lines[0]
