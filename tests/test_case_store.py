"""The case store (R31): the case root, `cma case new`, `profile` and `import`.

Permission tests: the root and every folder under it are 0700 and every file 0600 (inputs
0400). Git-ignore tests: a root inside a git working tree that does not ignore it is
refused before anything is made. Log-entry tests: each command appends to the case log."""

import csv
import os
import shutil
import stat
import subprocess

import pytest
from typer.testing import CliRunner

from cma import case, store
from cma.cli import app

runner = CliRunner()
STATEMENT = ("id,date,description,amount,balance\n"
             "L1,2026-01-05,CARD SETTLEMENT STRIPE,500.00,1500.00\n"
             "L2,2026-01-06,DD COUNCIL,-100.00,1400.00\n")
MONZO_HEADER = ["Transaction ID", "Date", "Time", "Type", "Name", "Amount", "Currency"]
posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    return root


def _statement(tmp_path, body=STATEMENT, name="statement.csv"):
    p = tmp_path / name
    p.write_text(body)
    return p


def _monzo(tmp_path):
    p = tmp_path / "monzo.csv"
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(MONZO_HEADER)
        w.writerow(["tx_001", "05/01/2026", "12:00:00", "Faster payment", "Acme Ltd", "250.00", "GBP"])
        w.writerow(["tx_002", "06/01/2026", "09:00:00", "Card payment", "Corner Shop", "-12.40", "GBP"])
    return p


def _events(folder):
    return [(e.event, e.data) for e in case.for_case(folder).entries()]


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})


# --- permissions -----------------------------------------------------------------------


@posix_only
@pytest.mark.contracts
def test_case_root_folders_and_files_are_owner_only(home, tmp_path):
    old = os.umask(0o022)  # an ordinary umask would leave files readable by others
    try:
        cid = store.new_case(label="Cafe", demo=True)
        folder = home / "cases" / cid
        store.import_file(folder, _statement(tmp_path), "csv")
        store.set_profile(folder, {**store.load_profile(folder), "directors": ["Jane Doe"]})
    finally:
        os.umask(old)
    for d in (home, home / "cases", folder, folder / "inputs", folder / "statements"):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700, d
    for f in folder.rglob("*"):
        if f.is_file():
            mode = stat.S_IMODE(f.stat().st_mode)
            assert mode & 0o077 == 0, (f, oct(mode))  # nothing for group or others
    (inp,) = (folder / "inputs").iterdir()
    assert stat.S_IMODE(inp.stat().st_mode) == 0o400  # the original, read-only


@posix_only
@pytest.mark.contracts
def test_an_existing_root_open_to_others_is_made_owner_only(home):
    home.mkdir(mode=0o755)
    os.chmod(home, 0o755)
    store.new_case()
    assert stat.S_IMODE(home.stat().st_mode) == 0o700


@pytest.mark.contracts
def test_case_ids_are_random_and_never_the_label(home):
    a, b = store.new_case(label="Acme Ltd"), store.new_case(label="Acme Ltd")
    assert a != b and store.CASE_ID.match(a) and "acme" not in a.lower()


# --- git ignore ------------------------------------------------------------------------


@needs_git
@pytest.mark.contracts
def test_root_inside_a_git_tree_that_does_not_ignore_it_is_refused(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    monkeypatch.setenv("CMA_HOME", str(repo / "cases-here"))
    with pytest.raises(store.CaseRootInGit, match="does not ignore it"):
        store.new_case()
    assert not (repo / "cases-here").exists()  # refused before anything was made
    r = runner.invoke(app, ["case", "new"])
    assert r.exit_code == 2 and "does not ignore" in " ".join(r.output.split())  # Rich wraps long lines
    assert not (repo / "cases-here").exists()


@needs_git
@pytest.mark.contracts
@pytest.mark.parametrize("pattern", ["cases-here/", "cases-here", "/cases-here/", "private/"])
def test_root_inside_a_git_tree_that_ignores_it_is_allowed(tmp_path, monkeypatch, pattern):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text(pattern + "\n")
    root = repo / ("private/cma" if pattern == "private/" else "cases-here")
    monkeypatch.setenv("CMA_HOME", str(root))
    cid = store.new_case()
    assert (root / "cases" / cid / "case.yaml").is_file()


@needs_git
@pytest.mark.contracts
def test_root_that_is_the_git_tree_itself_is_refused(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    monkeypatch.setenv("CMA_HOME", str(repo))
    with pytest.raises(store.CaseRootInGit):
        store.new_case()


@pytest.mark.contracts
def test_root_in_a_tree_git_cannot_be_asked_about_is_refused(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)  # looks like a work tree; git finds no repository
    monkeypatch.setenv("CMA_HOME", str(repo / "cma"))
    monkeypatch.setenv("PATH", str(tmp_path / "no-git-here"))  # and no git to ask
    with pytest.raises(store.CaseRootInGit, match="could not"):
        store.new_case()
    assert not (repo / "cma").exists()


@needs_git
@pytest.mark.contracts
def test_existing_root_later_unignored_is_refused_by_every_command(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text("cma/\n")
    monkeypatch.setenv("CMA_HOME", str(repo / "cma"))
    cid = store.new_case()
    (repo / ".gitignore").write_text("")
    with pytest.raises(store.CaseRootInGit):
        store.case_folder(cid)
    r = runner.invoke(app, ["case", "profile", cid])
    assert r.exit_code == 2


# --- cma case new ----------------------------------------------------------------------


@pytest.mark.contracts
def test_case_new_logs_its_creation_and_writes_the_profile(home):
    r = runner.invoke(app, ["case", "new", "--demo", "--label", "Cafe trial"])
    assert r.exit_code == 0, r.output
    cid = r.output.strip()
    folder = home / "cases" / cid
    p = store.load_profile(folder)
    assert p["demo"] is True and p["label"] == "Cafe trial"
    ((event, data),) = _events(folder)
    assert event == "case_created" and data["case"] == cid and data["demo"] is True
    assert data["profile_sha256"] == store.profile_sha256(p)
    assert "Cafe trial" not in str(data)  # the log records the profile's hash, not its text
    assert runner.invoke(app, ["case", "check", str(folder)]).exit_code == 0


# --- cma case profile ------------------------------------------------------------------


@pytest.mark.contracts
def test_profile_change_is_validated_written_and_logged(home):
    cid = store.new_case()
    r = runner.invoke(app, ["case", "profile", cid, "--director", "Jane Doe", "--shareholder", "Acme Holdco",
                            "--own-account", "ACME HOLDINGS", "--proposed-repayment", "1,500.5",
                            "--business-name", "Acme Cafe Ltd", "--vat-stagger", "2"])
    assert r.exit_code == 0, r.output
    folder = home / "cases" / cid
    p = store.load_profile(folder)
    assert p["directors"] == ["Jane Doe"] and p["shareholders"] == ["Acme Holdco"]
    assert p["own_accounts"] == ["ACME HOLDINGS"] and p["proposed_monthly_repayment"] == "1500.50"
    assert p["vat_stagger"] == 2 and p["demo"] is False
    events = _events(folder)
    assert [e for e, _ in events] == ["case_created", "profile_changed"]
    assert events[1][1] == {"fields": ["business_name", "directors", "own_accounts", "proposed_monthly_repayment",
                                       "shareholders", "vat_stagger"],
                            "profile_sha256": store.profile_sha256(p)}
    names = store.profile_names(p)
    assert names.directors == ("Jane Doe", "Acme Holdco") and names.own_accounts == ("ACME HOLDINGS",)
    # The same values again change nothing, so nothing more is logged.
    assert runner.invoke(app, ["case", "profile", cid, "--vat-stagger", "2"]).exit_code == 0
    assert len(_events(folder)) == 2


@pytest.mark.contracts
@pytest.mark.parametrize("body, match", [
    ("director: [Jane]\n", "unknown profile field"),
    ("directors: [Jane]\ndirectors: [John]\n", "more than once"),
    ("directors: ['---']\n", "names no one"),
    ("directors: Jane\n", "list of names"),
    ("directors: [Jane, JANE]\n", "twice"),
    ("demo: yes please\n", "true or false"),
    ("vat_stagger: 4\n", "1, 2 or 3"),
    ("vat_stagger: 1.0\n", "1, 2 or 3"),
    ("vat_stagger: true\n", "1, 2 or 3"),
    ("proposed_monthly_repayment: 12.345\n", "two decimals"),
    ("proposed_monthly_repayment: -5\n", "more than"),
    ("proposed_monthly_repayment: 10000000000000\n", "10,000,000,000,000"),
    ("proposed_monthly_repayment: true\n", "amount"),
    ("[unclosed\n", "not valid YAML"),
])
def test_a_malformed_profile_is_refused_and_nothing_changes(home, tmp_path, body, match):
    cid = store.new_case()
    folder = home / "cases" / cid
    before = (folder / "case.yaml").read_bytes()
    src = tmp_path / "profile.yaml"
    src.write_text(body)
    with pytest.raises(store.CaseStoreError, match=match):
        store.parse_profile(src.read_bytes())
    r = runner.invoke(app, ["case", "profile", cid, "--from", str(src)])
    assert r.exit_code == 2
    assert (folder / "case.yaml").read_bytes() == before and len(_events(folder)) == 1


@pytest.mark.contracts
def test_profile_file_replaces_the_whole_profile(home, tmp_path):
    cid = store.new_case(label="x")
    src = tmp_path / "p.yaml"
    src.write_text("demo: true\ndirectors: [Jane Doe]\nproposed_monthly_repayment: 1500.50\n")
    assert runner.invoke(app, ["case", "profile", cid, "--from", str(src)]).exit_code == 0
    p = store.load_profile(home / "cases" / cid)
    assert p["label"] is None and p["demo"] is True and p["proposed_monthly_repayment"] == "1500.50"


@pytest.mark.contracts
def test_unknown_case_id_is_refused(home):
    assert runner.invoke(app, ["case", "profile", "../../etc"]).exit_code == 2
    assert runner.invoke(app, ["case", "profile", "0" * 16]).exit_code == 2


# --- cma case import -------------------------------------------------------------------


@pytest.mark.contracts
def test_csv_import_keeps_the_original_by_hash_and_the_statement_per_account(home, tmp_path):
    import hashlib

    cid = store.new_case()
    folder = home / "cases" / cid
    src = _statement(tmp_path)
    r = runner.invoke(app, ["case", "import", cid, str(src), "--format", "csv"])
    assert r.exit_code == 0, r.output
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    assert (folder / "inputs" / f"{sha}.csv").read_bytes() == src.read_bytes()
    assert (folder / "statements" / "main.csv").read_bytes() == src.read_bytes()
    events = _events(folder)
    assert [e for e, _ in events] == ["case_created", "import"]
    assert events[1][1] == {"account": "main", "format": "csv", "input_sha256": sha,
                            "statement_sha256": sha, "lines": 2, "start": "2026-01-05", "end": "2026-01-06"}
    assert runner.invoke(app, ["case", "check", str(folder)]).exit_code == 0


@pytest.mark.contracts
def test_monzo_import_converts_into_the_account_statement(home, tmp_path):
    from cma import ingest

    cid = store.new_case()
    folder = home / "cases" / cid
    r = runner.invoke(app, ["case", "import", cid, str(_monzo(tmp_path)), "--format", "monzo",
                            "--account", "monzo", "--opening-balance", "100.00"])
    assert r.exit_code == 0, r.output
    s = ingest.load_csv(folder / "statements" / "monzo.csv")
    assert [t.id for t in s.transactions] == ["tx_001", "tx_002"]
    assert s.transactions[-1].balance == pytest.approx(337.60)
    ((event, data),) = _events(folder)[1:]
    assert event == "import" and data["format"] == "monzo" and data["account"] == "monzo" and data["lines"] == 2
    assert data["statement_sha256"] != data["input_sha256"]
    assert not [p for p in folder.iterdir() if p.name.startswith("tmp")]  # no work folder left


@pytest.mark.contracts
def test_importing_the_same_file_again_leaves_the_case_unchanged(home, tmp_path):
    cid = store.new_case()
    folder = home / "cases" / cid
    src = _statement(tmp_path)
    store.import_file(folder, src, "csv")
    head = case.for_case(folder).verify().head
    r = runner.invoke(app, ["case", "import", cid, str(src), "--format", "csv", "--account", "other"])
    assert r.exit_code == 0 and "already imported" in " ".join(r.output.split())
    assert case.for_case(folder).verify().head == head
    assert sorted(p.name for p in (folder / "statements").iterdir()) == ["main.csv"]


@pytest.mark.contracts
@pytest.mark.parametrize("body", [
    "id,date,description,amount,balance\nL1,2026-01-05,X,abc,1.00\n",  # not a number
    "id,date,description,amount,balance\nL1,2026-01-05,X,10000000000000.00,1.00\n",  # at the R2 limit
])
def test_a_refused_statement_leaves_the_case_unchanged(home, tmp_path, body):
    cid = store.new_case()
    folder = home / "cases" / cid
    r = runner.invoke(app, ["case", "import", cid, str(_statement(tmp_path, body)), "--format", "csv"])
    assert r.exit_code == 2, r.output
    assert list((folder / "inputs").iterdir()) == [] and list((folder / "statements").iterdir()) == []
    assert len(_events(folder)) == 1
    assert not [p for p in folder.iterdir() if p.name.startswith("tmp")]  # no work folder left


@pytest.mark.contracts
def test_a_second_statement_for_one_account_is_refused(home, tmp_path):
    cid = store.new_case()
    folder = home / "cases" / cid
    store.import_file(folder, _statement(tmp_path), "csv")
    other = _statement(tmp_path, STATEMENT.replace("L2", "L3"), "other.csv")
    with pytest.raises(store.CaseStoreError, match="already has a statement"):
        store.import_file(folder, other, "csv")
    store.import_file(folder, other, "csv", account="savings")
    assert [e for e, _ in _events(folder)] == ["case_created", "import", "import"]


@pytest.mark.contracts
@pytest.mark.parametrize("account", ["../x", "Main", "", "a" * 33])
def test_an_account_key_that_is_not_a_plain_key_is_refused(home, tmp_path, account):
    folder = home / "cases" / store.new_case()
    with pytest.raises(store.CaseStoreError, match="account key"):
        store.import_file(folder, _statement(tmp_path), "csv", account=account)


@pytest.mark.contracts
def test_an_import_the_log_refuses_puts_the_files_back(home, tmp_path, monkeypatch):
    folder = home / "cases" / store.new_case()

    def refuse(self, *a, **k):
        raise case.SaveNotRecorded("refused for the test; the save was not recorded")

    monkeypatch.setattr(case.CaseLog, "append", refuse)
    with pytest.raises(case.SaveNotRecorded):
        store.import_file(folder, _statement(tmp_path), "csv")
    assert list((folder / "inputs").iterdir()) == [] and list((folder / "statements").iterdir()) == []


@pytest.mark.contracts
def test_an_import_into_a_broken_log_is_refused(home, tmp_path):
    folder = home / "cases" / store.new_case()
    log = folder / "log.jsonl"
    log.write_bytes(log.read_bytes().replace(b'"demo":false', b'"demo":true '))
    r = runner.invoke(app, ["case", "import", folder.name, str(_statement(tmp_path)), "--format", "csv"])
    assert r.exit_code == 3, r.output
    assert list((folder / "statements").iterdir()) == []


@pytest.mark.contracts
def test_store_writes_only_under_the_case_root(home, tmp_path):
    before = sorted(p.name for p in tmp_path.iterdir())
    cid = store.new_case()
    store.import_file(home / "cases" / cid, _statement(tmp_path), "csv")
    after = sorted(p.name for p in tmp_path.iterdir())
    assert set(after) - set(before) == {"statement.csv", "cma-home"}


# --- review 2: reads under the lock, the log decides an import, keys end where they end ---


@pytest.mark.contracts
def test_overlapping_profile_changes_keep_both(home, monkeypatch):
    """A `cma case profile` that read the profile before another one saved builds its change
    on the profile as it stands once it holds the case, so neither change is lost."""
    import threading

    cid = store.new_case()
    folder = home / "cases" / cid
    real = store.update_profile

    def other_window_saves_first(f, update):
        t = threading.Thread(target=real, args=(f, lambda p: {**p, "directors": ["Jane Doe"]}))
        t.start()
        t.join()
        return real(f, update)

    monkeypatch.setattr(store, "update_profile", other_window_saves_first)
    r = runner.invoke(app, ["case", "profile", cid, "--vat-stagger", "2"])
    assert r.exit_code == 0, r.output
    profile = store.load_profile(folder)
    assert profile["directors"] == ["Jane Doe"] and profile["vat_stagger"] == 2
    events = _events(folder)
    assert [d["fields"] for e, d in events if e == "profile_changed"] == [["directors"], ["vat_stagger"]]
    assert events[-1][1]["profile_sha256"] == store.profile_sha256(profile)


@pytest.mark.contracts
def test_a_refused_profile_change_puts_back_the_profile_the_log_last_recorded(home, monkeypatch):
    folder = home / "cases" / store.new_case()
    store.update_profile(folder, lambda p: {**p, "directors": ["Jane Doe"]})

    def refuse(self, *a, **k):
        raise case.SaveNotRecorded("refused for the test; the save was not recorded")

    monkeypatch.setattr(case.CaseLog, "append", refuse)
    with pytest.raises(case.SaveNotRecorded):
        store.update_profile(folder, lambda p: {**p, "vat_stagger": 2})
    profile = store.load_profile(folder)
    assert profile["directors"] == ["Jane Doe"] and profile["vat_stagger"] is None
    assert _events(folder)[-1][1]["profile_sha256"] == store.profile_sha256(profile)


@pytest.mark.contracts
def test_an_import_whose_entry_never_reached_the_log_is_recorded_when_imported_again(home, tmp_path, monkeypatch):
    """Files an import left without its log entry (a save not confirmed whose entry a repair
    dropped, or a crash between the files and the entry) do not make the file 'already
    imported': importing it again appends the entry (R42, R39)."""
    cid = store.new_case()
    folder = home / "cases" / cid
    src = _statement(tmp_path)

    def not_confirmed(self, *a, **k):
        raise case.SaveNotConfirmed("for the test; whether the save is in the log is not confirmed")

    with monkeypatch.context() as m:
        m.setattr(case.CaseLog, "append", not_confirmed)
        with pytest.raises(case.SaveNotConfirmed):
            store.import_file(folder, src, "csv")
    assert (folder / "statements" / "main.csv").exists() and len(list((folder / "inputs").iterdir())) == 1
    assert [e for e, _ in _events(folder)] == ["case_created"]

    r = runner.invoke(app, ["case", "import", cid, str(src), "--format", "csv"])
    assert r.exit_code == 0 and "imported account main" in " ".join(r.output.split()), r.output
    assert [e for e, _ in _events(folder)] == ["case_created", "import"]
    r = runner.invoke(app, ["case", "import", cid, str(src), "--format", "csv"])
    assert "already imported" in " ".join(r.output.split())
    assert [e for e, _ in _events(folder)] == ["case_created", "import"]
    assert runner.invoke(app, ["case", "check", str(folder)]).exit_code == 0


@pytest.mark.contracts
def test_a_statement_left_without_its_entry_is_replaced_by_the_next_import(home, tmp_path):
    folder = home / "cases" / store.new_case()
    (folder / "statements" / "main.csv").write_text("left over by a crash\n")
    store.import_file(folder, _statement(tmp_path), "csv")
    assert (folder / "statements" / "main.csv").read_text() == STATEMENT
    assert [e for e, _ in _events(folder)] == ["case_created", "import"]


@pytest.mark.contracts
@pytest.mark.parametrize("account", ["main\n", "main\r", " main"])
def test_an_account_key_with_a_trailing_newline_is_refused(home, tmp_path, account):
    folder = home / "cases" / store.new_case()
    with pytest.raises(store.CaseStoreError, match="account key"):
        store.import_file(folder, _statement(tmp_path), "csv", account=account)
    assert list((folder / "statements").iterdir()) == []


@pytest.mark.contracts
def test_a_case_root_the_file_system_refuses_is_a_refusal_not_a_traceback(tmp_path, monkeypatch):
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder")
    monkeypatch.setenv("CMA_HOME", str(blocker / "cma"))  # its parent is a file
    r = runner.invoke(app, ["case", "new"])
    assert r.exit_code == 2, (r.output, r.exception)
    assert "Refused." in " ".join(r.output.split()) and "Traceback" not in r.output
    assert blocker.read_text() == "not a folder"


@pytest.mark.contracts
def test_a_case_id_with_a_trailing_newline_is_refused(home):
    cid = store.new_case()
    with pytest.raises(store.CaseStoreError, match="16 lowercase hex"):
        store.case_folder(cid + "\n")
