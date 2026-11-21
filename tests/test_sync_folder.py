"""R32: warn before creating a case inside a folder a known cloud-sync client uploads
(OneDrive, Dropbox, Google Drive, iCloud Drive), and record the warning in the case log.

Path fixtures per client, as each client lays its folder out on Windows, macOS and Linux.
"""

from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath

import pytest
from typer.testing import CliRunner

from cma import case, store
from cma.cli import app

runner = CliRunner()

# (client, path of a case root inside its synced folder, the synced folder itself)
CLIENT_PATHS = [
    ("OneDrive", "/Users/ann/OneDrive/cases", "/Users/ann/OneDrive"),
    ("OneDrive", "/Users/ann/OneDrive - Contoso Ltd/Underwriting/cma", "/Users/ann/OneDrive - Contoso Ltd"),
    ("OneDrive", "/Users/ann/Library/CloudStorage/OneDrive-Personal/cma",
     "/Users/ann/Library/CloudStorage/OneDrive-Personal"),
    ("OneDrive", "/Users/ann/Library/CloudStorage/OneDrive-ContosoLtd/x", "/Users/ann/Library/CloudStorage/OneDrive-ContosoLtd"),
    ("Dropbox", "/home/ann/Dropbox/work/cma", "/home/ann/Dropbox"),
    ("Dropbox", "/Users/ann/Dropbox (Contoso)/cma", "/Users/ann/Dropbox (Contoso)"),
    ("Dropbox", "/Users/ann/Library/CloudStorage/Dropbox/cma", "/Users/ann/Library/CloudStorage/Dropbox"),
    ("Dropbox", "/Users/ann/Library/CloudStorage/Dropbox-Contoso/cma", "/Users/ann/Library/CloudStorage/Dropbox-Contoso"),
    ("Google Drive", "/Users/ann/Google Drive/cma", "/Users/ann/Google Drive"),
    ("Google Drive", "/Users/ann/Library/CloudStorage/GoogleDrive-ann@example.com/My Drive/cma",
     "/Users/ann/Library/CloudStorage/GoogleDrive-ann@example.com"),
    ("Google Drive", "/Volumes/GoogleDrive/My Drive/cma", "/Volumes/GoogleDrive"),
    ("iCloud Drive", "/Users/ann/Library/Mobile Documents/com~apple~CloudDocs/cma",
     "/Users/ann/Library/Mobile Documents"),
    ("iCloud Drive", "/Users/ann/Library/Mobile Documents/iCloud~com~apple~Pages/Documents/cma",
     "/Users/ann/Library/Mobile Documents"),
    ("iCloud Drive", "/Users/ann/iCloudDrive/cma", "/Users/ann/iCloudDrive"),
]

WINDOWS_PATHS = [
    ("OneDrive", r"C:\Users\ann\OneDrive\cma", r"C:\Users\ann\OneDrive"),
    ("OneDrive", r"C:\Users\ann\OneDrive - Contoso Ltd\cma", r"C:\Users\ann\OneDrive - Contoso Ltd"),
    ("Dropbox", r"C:\Users\ann\Dropbox\cma", r"C:\Users\ann\Dropbox"),
    ("Google Drive", r"G:\My Drive\cma", r"G:\My Drive"),
    ("iCloud Drive", r"C:\Users\ann\iCloudDrive\cma", r"C:\Users\ann\iCloudDrive"),
]

NOT_SYNCED = [
    "/home/ann/private/cma",
    "/Users/ann/Documents/cases",
    "/Users/ann/Dropboxes/cma",          # a name that only begins like a client's
    "/Users/ann/my-onedrive-notes/cma",  # a name that only holds a client's
    "/Users/ann/Library/Mobile Documents Backup/cma",
]


@pytest.mark.contracts
@pytest.mark.parametrize("client, path, folder", CLIENT_PATHS)
def test_each_client_folder_is_recognised(client, path, folder, tmp_path):
    found = store.sync_folder(Path(path), home=tmp_path, env={})
    assert found is not None
    assert (found.client, found.folder) == (client, Path(folder))


@pytest.mark.contracts
@pytest.mark.parametrize("client, path, folder", WINDOWS_PATHS)
def test_each_client_folder_is_recognised_on_windows_paths(client, path, folder, tmp_path):
    found = store.sync_folder(PureWindowsPath(path), home=tmp_path, env={})
    assert found is not None
    assert (found.client, str(found.folder)) == (client, folder)


@pytest.mark.contracts
@pytest.mark.parametrize("path", NOT_SYNCED)
def test_an_ordinary_folder_is_not_taken_for_a_sync_folder(path, tmp_path):
    assert store.sync_folder(Path(path), home=tmp_path, env={}) is None


@pytest.mark.contracts
@pytest.mark.parametrize("var", ["OneDrive", "OneDriveCommercial", "OneDriveConsumer"])
def test_onedrive_folder_named_by_its_environment_variable(var, tmp_path):
    synced = tmp_path / "Work Files"  # renamed: only the client's variable says it is OneDrive
    found = store.sync_folder(synced / "cma", home=tmp_path, env={var: str(synced)})
    assert found is not None and (found.client, found.folder) == ("OneDrive", synced)


@pytest.mark.contracts
def test_dropbox_folder_named_by_its_info_file(tmp_path):
    home = tmp_path / "home"
    synced = tmp_path / "Team Files"  # a Dropbox folder moved and renamed
    (home / ".dropbox").mkdir(parents=True)
    (home / ".dropbox" / "info.json").write_text(json.dumps({"business": {"path": str(synced)}}))
    found = store.sync_folder(synced / "cma", home=home, env={})
    assert found is not None and (found.client, found.folder) == ("Dropbox", synced)


@pytest.mark.contracts
def test_a_malformed_dropbox_info_file_is_ignored(tmp_path):
    (tmp_path / ".dropbox").mkdir()
    (tmp_path / ".dropbox" / "info.json").write_text("{not json")
    assert store.sync_folder(tmp_path / "cma", home=tmp_path, env={}) is None


@pytest.mark.contracts
def test_a_symlink_into_a_synced_folder_is_followed(tmp_path):
    synced = tmp_path / "Dropbox" / "cma"
    synced.mkdir(parents=True)
    link = tmp_path / "plain"
    link.symlink_to(synced, target_is_directory=True)
    found = store.sync_folder(link, home=tmp_path, env={})
    assert found is not None and found.client == "Dropbox"


# --- creating a case -------------------------------------------------------------------


def _isolate(monkeypatch, tmp_path):
    """No real client variable or info file of the machine running the tests counts."""
    for var in store.ONEDRIVE_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "test-home"))


@pytest.mark.contracts
@pytest.mark.parametrize("client, folder_name", [
    ("OneDrive", "OneDrive - Contoso"), ("Dropbox", "Dropbox"),
    ("Google Drive", "Google Drive"), ("iCloud Drive", "iCloudDrive"),
])
def test_case_new_in_a_synced_folder_warns_first_and_logs_the_warning(tmp_path, monkeypatch, client, folder_name):
    _isolate(monkeypatch, tmp_path)
    root = tmp_path / folder_name / "cma"
    monkeypatch.setenv("CMA_HOME", str(root))
    r = runner.invoke(app, ["case", "new", "--label", "Cafe"])
    assert r.exit_code == 0, r.output
    cid = r.stdout.strip()  # stdout carries the id alone; the warning goes to stderr
    assert store.CASE_ID.fullmatch(cid)
    assert "Warning" in r.stderr and client in r.stderr and str(root) in r.stderr.replace("\n", "")
    entries = case.for_case(root / "cases" / cid).entries()
    assert [e.event for e in entries] == ["case_created", "sync_folder_warning"]
    data = entries[1].data
    assert data["client"] == client and data["sync_folder"] == str(tmp_path / folder_name)
    assert data["case_root"] == str(root) and client in data["warning"]
    assert runner.invoke(app, ["case", "check", str(root / "cases" / cid)]).exit_code == 0


@pytest.mark.contracts
def test_the_warning_is_given_before_the_case_is_made(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    root = tmp_path / "OneDrive" / "cma"
    seen: list[list[Path]] = []

    def warn(message):
        assert "OneDrive" in message
        seen.append(list((root / "cases").iterdir()))  # no case folder yet when warned

    cid = store.new_case(root=root, warn=warn)
    assert seen == [[]]
    assert [e.event for e in case.for_case(root / "cases" / cid).entries()][-1] == "sync_folder_warning"


@pytest.mark.contracts
def test_case_new_outside_a_synced_folder_neither_warns_nor_logs_one(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    root = tmp_path / "local" / "cma"
    monkeypatch.setenv("CMA_HOME", str(root))
    r = runner.invoke(app, ["case", "new"])
    assert r.exit_code == 0, r.output
    assert r.stderr == ""
    entries = case.for_case(root / "cases" / r.stdout.strip()).entries()
    assert [e.event for e in entries] == ["case_created"]


@pytest.mark.contracts
def test_library_caller_without_a_callback_still_gets_a_python_warning(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    with pytest.warns(store.SyncFolderWarning, match="Dropbox"):
        store.new_case(root=tmp_path / "Dropbox" / "cma")


@pytest.mark.contracts
def test_a_failure_recording_the_warning_leaves_no_case_behind(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    root = tmp_path / "Dropbox" / "cma"
    real_append = case.CaseLog.append

    def append(self, event, *args, **kwargs):
        if event == "sync_folder_warning":
            raise OSError("disk full")
        return real_append(self, event, *args, **kwargs)

    monkeypatch.setattr(case.CaseLog, "append", append)
    with pytest.raises(OSError, match="disk full"):
        store.new_case(root=root, warn=lambda m: None)
    assert list((root / "cases").iterdir()) == []  # the case is removed, not left without its warning


@pytest.mark.contracts
def test_the_logged_warning_holds_no_case_profile(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    root = tmp_path / "OneDrive" / "cma"
    cid = store.new_case(root=root, label="Jane Doe Cafe", warn=lambda m: None)
    entry = case.for_case(root / "cases" / cid).entries()[-1]
    assert set(entry.data) == {"client", "sync_folder", "case_root", "warning"}
    assert "Jane Doe Cafe" not in json.dumps(entry.data)
