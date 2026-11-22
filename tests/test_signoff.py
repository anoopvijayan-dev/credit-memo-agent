"""T27: sign-off from `cma case sign` and from the workspace preview (C6; R3, R22, R23, R24,
R26, R28, R42, R43).

The underwriter signs off the figures of a preview they saw, with a typed name; the sign-off
records it with the operating-system user and the UTC time, bound to the preview's snapshot.
It is refused, writing nothing, when the case changed since the preview (naming what), when
the case log is broken (naming the first bad entry), when the figures do not reconcile or the
text fails the guard. memo.md and evidence.json are written once to packs/<snapshot>, through
a folder of their own renamed into place, never overwritten, and the pack write and the
sign-off are appended to the case log after the rename."""

import hashlib
import http.client
import json
import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cma import case as caselog
from cma import metrics, overrides, pack, store
from cma.cli import app
from cma.review import server as rs

pytestmark = pytest.mark.contracts

STATEMENT = ("id,date,description,amount,balance\n"
             "L1,2026-01-05,CARD SETTLEMENT STRIPE,500.60,1500.60\n"
             "L2,2026-01-06,FPS CREDIT J BLOGGS,300.25,1800.85\n"
             "L3,2026-01-07,IWOCA LOAN REPAYMENT,-100.00,1700.85\n"
             "L4,2026-02-02,CASH PAID IN,250.10,1950.95\n")
OTHER = ("id,date,description,amount,balance\n"
         "S1,2026-01-03,CARD SETTLEMENT SUMUP,900.00,900.00\n")
# Every event a Tier 1 case records (R42): the case's creation, an import, a profile change,
# an override, a preview, the pack write and the sign-off. A firm-rule promotion (R20) and the
# sync-folder warning (R32) are Tier 2.
TIER1_EVENTS = {"case_created", "import", "profile_changed", "override", "preview",
                pack.PACK_EVENT, pack.SIGN_EVENT}


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    monkeypatch.setenv("CMA_RULESET", "v2")
    return root


def _case(tmp_path, body=STATEMENT):
    case_id = store.new_case(label="t27")
    folder = store.case_folder(case_id)
    src = tmp_path / "main.csv"
    src.write_text(body, encoding="utf-8")
    store.import_file(folder, src, "csv")
    return case_id, folder


def _events(folder):
    return [e.event for e in caselog.for_case(folder).entries()]


def _tree(folder: Path) -> dict:
    """Every file of the case but the log's lock files, with its bytes."""
    skip = {caselog.LOG_NAME + ".gate", caselog.LOG_NAME + ".lock"}
    return {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file() and p.name not in skip}


def test_sign_off_writes_memo_and_evidence_once_bound_to_the_snapshot(home, tmp_path):
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)
    s = pack.sign_off(folder, case_id, p.snapshot, "  Ann   Smith ")
    target = folder / "packs" / p.snapshot
    assert s.pack == target and sorted(os.listdir(target)) == ["evidence.json", "memo.md"]
    assert (target / "memo.md").read_text(encoding="utf-8") == p.text  # from the signed preview
    ev = json.loads((target / "evidence.json").read_text(encoding="utf-8"))
    assert ev["snapshot"] == p.snapshot and ev["inputs"] == p.inputs and ev["case"] == case_id
    so = ev["sign_off"]
    assert so["name"] == "Ann Smith" and so["os_user"] == store._os_user() and so["snapshot"] == p.snapshot
    assert so["at"].endswith("Z") and so["at"] == s.at  # UTC
    assert "approve the loan" not in so["wording"].replace("does not approve the loan", "")
    assert ev["egress"]["destination"] == "offline"
    # owner-only (R31)
    assert stat.S_IMODE(target.stat().st_mode) == store.DIR_MODE
    assert all(stat.S_IMODE((target / f).stat().st_mode) == store.FILE_MODE for f in pack.PACK_FILES)
    # the pack write and then the sign-off, after the preview, with the files' hashes (R42)
    entries = caselog.for_case(folder).entries()
    assert [e.event for e in entries[-3:]] == ["preview", pack.PACK_EVENT, pack.SIGN_EVENT]
    signed = entries[-1].data
    assert signed["name"] == "Ann Smith" and signed["snapshot"] == p.snapshot and signed["at"] == s.at
    assert signed["files"] == {f: hashlib.sha256((target / f).read_bytes()).hexdigest()
                               for f in pack.PACK_FILES}
    assert s.position == len(entries)
    assert caselog.for_case(folder).verify().ok


@pytest.mark.parametrize("change, named", [
    ("override", "the override log"),
    ("profile", "the case profile"),
    ("statement", "the statement for account main changed"),
    ("account", "a statement was imported for account other"),
    ("field_map", "the memo field map"),
    ("verdict", "the show_policy_verdict setting"),
])
def test_r23_stale_snapshot_is_refused_naming_what_changed(home, tmp_path, change, named):
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)
    if change == "override":
        overrides.add_override(folder, "L2", "revenue_transfer", reason="customer", name="Ann")
    elif change == "profile":
        store.update_profile(folder, lambda cur: {**cur, "directors": ["Jane Doe"]})
    elif change == "statement":
        stmt = folder / store.STATEMENTS / "main.csv"
        stmt.write_text(STATEMENT.replace("CASH PAID IN", "CASH PAID IN AT BRANCH"), encoding="utf-8")
    elif change == "account":
        src = tmp_path / "o.csv"
        src.write_text(OTHER, encoding="utf-8")
        store.import_file(folder, src, "csv", account="other")
    elif change == "field_map":
        (home / "memo_fields.yaml").write_text(pack.default_field_map_text() + "\n# firm\n", encoding="utf-8")
    else:
        (home / "firm.yaml").write_text("show_policy_verdict: false\n", encoding="utf-8")
    before, events = _tree(folder), _events(folder)
    with pytest.raises(pack.SignOffRefused) as e:
        pack.sign_off(folder, case_id, p.snapshot, "Ann")
    assert named in str(e.value)
    assert len(e.value.changed) == 1 and e.value.changed[0].startswith(named)
    assert _tree(folder) == before and _events(folder) == events  # nothing written, nothing recorded
    assert not (folder / "packs" / p.snapshot).exists()


def test_r23_a_snapshot_no_preview_recorded_is_refused(home, tmp_path):
    case_id, folder = _case(tmp_path)
    pack.build_preview(folder, case_id)
    with pytest.raises(pack.SignOffRefused, match="no preview of this case has snapshot"):
        pack.sign_off(folder, case_id, "0" * 64, "Ann")
    with pytest.raises(pack.SignOffRefused, match="snapshot"):
        pack.sign_off(folder, case_id, "not-a-hash", "Ann")


@pytest.mark.parametrize("name", ["", "   ", "---", None, "x" * 201])
def test_r22_a_sign_off_needs_a_typed_name(home, tmp_path, name):
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)
    events = _events(folder)
    with pytest.raises(pack.SignOffRefused):
        pack.sign_off(folder, case_id, p.snapshot, name)
    assert _events(folder) == events and not (folder / "packs").exists()


def test_r43_broken_log_is_refused_naming_the_first_bad_entry(home, tmp_path):
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)
    log = folder / caselog.LOG_NAME
    lines = log.read_bytes().splitlines(keepends=True)
    lines[1] = lines[1].replace(b'"import"', b'"imporT"')  # entry 2 changed by hand
    log.write_bytes(b"".join(lines))
    with pytest.raises(caselog.CaseLogBroken) as e:
        pack.sign_off(folder, case_id, p.snapshot, "Ann")
    assert e.value.first_bad == 2
    assert not (folder / "packs").exists()
    r = CliRunner().invoke(app, ["case", "sign", case_id, "--snapshot", p.snapshot], input="Ann\ny\n")
    assert r.exit_code == 3 and "entry 2" in r.output and not (folder / "packs").exists()


def test_r43_incomplete_last_entry_is_refused(home, tmp_path):
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)
    with (folder / caselog.LOG_NAME).open("ab") as f:
        f.write(b'{"seq": 4, "event": "ov')  # a torn write a crash left
    with pytest.raises(caselog.CaseLogBroken) as e:
        pack.sign_off(folder, case_id, p.snapshot, "Ann")
    assert e.value.first_bad == 4 and not (folder / "packs").exists()


def test_r28_guard_failure_is_refused(home, tmp_path):
    case_id, folder = _case(tmp_path)
    (home / "memo_fields.yaml").write_text(
        "fields:\n  - id: a\n    title: A\n    figures: [M2]\n    template: 'Revenue {M2} over 12 months.'\n",
        encoding="utf-8")
    p = pack.build_preview(folder, case_id)
    assert not p.grounded
    events = _events(folder)
    with pytest.raises(pack.SignOffRefused, match="grounding guard"):
        pack.sign_off(folder, case_id, p.snapshot, "Ann")
    assert _events(folder) == events and not (folder / "packs").exists()


def test_r3_reconciliation_failure_is_refused(home, tmp_path, monkeypatch):
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)

    def unreconciled(_folder):
        raise metrics.ReconciliationError(1, ["L2"])

    monkeypatch.setattr(overrides, "case_figures", unreconciled)
    events = _events(folder)
    with pytest.raises(metrics.ReconciliationError):
        pack.sign_off(folder, case_id, p.snapshot, "Ann")
    assert _events(folder) == events and not (folder / "packs").exists()
    r = CliRunner().invoke(app, ["case", "sign", case_id], input="Ann\ny\n")
    assert r.exit_code == 2 and "do not reconcile" in r.output


def test_r26_second_sign_off_is_refused_and_the_pack_never_overwritten(home, tmp_path):
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)
    pack.sign_off(folder, case_id, p.snapshot, "Ann")
    before, events = _tree(folder), _events(folder)
    with pytest.raises(pack.SignOffRefused, match="never overwritten"):
        pack.sign_off(folder, case_id, p.snapshot, "Bob")
    assert _tree(folder) == before and _events(folder) == events
    # a folder already at the snapshot's path, whatever put it there, is never replaced
    case2, folder2 = _case(tmp_path)
    p2 = pack.build_preview(folder2, case2)
    (folder2 / "packs" / p2.snapshot).mkdir(parents=True)
    with pytest.raises(pack.SignOffRefused, match="never overwritten"):
        pack.sign_off(folder2, case2, p2.snapshot, "Ann")
    assert os.listdir(folder2 / "packs" / p2.snapshot) == []


def test_r26_crash_leftover_is_removed_and_no_partial_pack_is_visible(home, tmp_path, monkeypatch):
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)
    leftover = folder / "packs" / (pack.PART + "crashed")
    leftover.mkdir(parents=True)
    (leftover / "memo.md").write_text("half", encoding="utf-8")
    # a crash at the rename: no pack at the snapshot's path, nothing recorded
    real = os.rename

    def crash(src, dst):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(os, "rename", crash)
    events = _events(folder)
    with pytest.raises(OSError):
        pack.sign_off(folder, case_id, p.snapshot, "Ann")
    assert not (folder / "packs" / p.snapshot).exists() and _events(folder) == events
    assert [c.name for c in (folder / "packs").iterdir()] == []  # its own part removed, the old leftover too
    # the next start removes a leftover and writes the pack
    monkeypatch.setattr(os, "rename", real)
    leftover.mkdir()
    (leftover / "evidence.json").write_text("{", encoding="utf-8")
    pack.sign_off(folder, case_id, p.snapshot, "Ann")
    assert [c.name for c in (folder / "packs").iterdir()] == [p.snapshot]


def test_r42_entries_are_logged_after_the_rename_and_a_refused_entry_removes_the_pack(home, tmp_path, monkeypatch):
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)
    target = folder / "packs" / p.snapshot
    seen = []
    real = caselog.CaseLog.append

    def watched(self, event, data, *, expected):
        seen.append((event, target.is_dir() and sorted(os.listdir(target)) == ["evidence.json", "memo.md"]))
        return real(self, event, data, expected=expected)

    monkeypatch.setattr(caselog.CaseLog, "append", watched)
    pack.sign_off(folder, case_id, p.snapshot, "Ann")
    assert seen == [(pack.PACK_EVENT, True), (pack.SIGN_EVENT, True)]
    # the log refuses the pack write: the pack is removed, so no pack sits without its entry
    case2, folder2 = _case(tmp_path)
    p2 = pack.build_preview(folder2, case2)

    def refused(self, event, data, *, expected):
        raise caselog.SaveNotRecorded("the case lock file was replaced; the save was not recorded")

    monkeypatch.setattr(caselog.CaseLog, "append", refused)
    with pytest.raises(caselog.SaveNotRecorded):
        pack.sign_off(folder2, case2, p2.snapshot, "Ann")
    assert not (folder2 / "packs" / p2.snapshot).exists()


def test_r42_every_tier1_event_kind_is_in_the_case_log(home, tmp_path):
    case_id, folder = _case(tmp_path)
    store.update_profile(folder, lambda cur: {**cur, "business_name": "Acme Cafe"})
    overrides.add_override(folder, "L2", "revenue_transfer", reason="customer", name="Ann")
    p = pack.build_preview(folder, case_id)
    pack.sign_off(folder, case_id, p.snapshot, "Ann")
    assert set(_events(folder)) >= TIER1_EVENTS
    assert caselog.for_case(folder).verify().ok


# --- cma case sign ----------------------------------------------------------------------


def test_cli_case_sign_is_interactive_and_worded_as_signing_off_figures(home, tmp_path):
    case_id, folder = _case(tmp_path)
    runner = CliRunner()
    r = runner.invoke(app, ["case", "sign", case_id], input="Ann\ny\n")
    assert r.exit_code == 2 and "no preview" in r.output  # preview first
    p = pack.build_preview(folder, case_id)
    r = runner.invoke(app, ["case", "sign", case_id, "--yes"], input="Ann\ny\n")
    assert r.exit_code == 2 and not (folder / "packs").exists()  # there is no --yes
    r = runner.invoke(app, ["case", "sign", case_id], input="Ann\nn\n")
    assert r.exit_code == 1 and "nothing was written" in r.output and not (folder / "packs").exists()
    r = runner.invoke(app, ["case", "sign", case_id], input="Ann Smith\ny\n")
    assert r.exit_code == 0, r.output
    out = " ".join(r.output.split())
    assert "Sign off the figures" in out and "does not approve the loan" in out
    assert "Figures signed off by Ann Smith" in out and p.snapshot in out.replace(" ", "")
    assert "approve the loan?" not in out.lower()
    assert (folder / "packs" / p.snapshot / "memo.md").read_text(encoding="utf-8") == p.text
    r = runner.invoke(app, ["case", "sign", case_id], input="Ann\ny\n")
    assert r.exit_code == 2 and "never overwritten" in " ".join(r.output.split())


def test_cli_case_sign_stale_names_what_changed(home, tmp_path):
    case_id, folder = _case(tmp_path)
    pack.build_preview(folder, case_id)
    overrides.add_override(folder, "L2", "revenue_transfer", reason="customer", name="Ann")
    r = CliRunner().invoke(app, ["case", "sign", case_id], input="Ann\ny\n")
    assert r.exit_code == 2 and "the override log" in " ".join(r.output.split())
    assert not (folder / "packs").exists()


# --- the workspace ------------------------------------------------------------------------


@pytest.fixture
def serve():
    servers = []

    def start(case_id):
        srv = rs.start(store.case_folder(case_id), case_id, port=0)
        srv.serve_in_thread()
        servers.append(srv)
        return srv

    yield start
    for srv in servers:
        srv.close()


def _post(srv, path, body, kind="application/json"):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=30)
    try:
        data = json.dumps(body).encode() if kind == "application/json" else body.encode()
        headers = {"Host": f"127.0.0.1:{srv.port}", "Content-Type": kind, "Origin": f"http://127.0.0.1:{srv.port}"}
        if "token=" not in path:  # the page's forms carry it in the URL, data calls in the header
            headers["X-CMA-Token"] = srv.token
        conn.request("POST", path, body=data, headers=headers)
        r = conn.getresponse()
        return r.status, r.read().decode("utf-8")
    finally:
        conn.close()


def _get(srv, path):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=30)
    try:
        conn.request("GET", path, headers={"Host": f"127.0.0.1:{srv.port}", "X-CMA-Token": srv.token})
        r = conn.getresponse()
        return r.status, r.read().decode("utf-8")
    finally:
        conn.close()


def test_workspace_preview_then_sign_off(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    status, page = _get(srv, "/")
    assert status == 200 and 'action="/preview?token=' in page
    status, body = _post(srv, "/api/preview", {})
    assert status == 200
    pv = json.loads(body)
    assert pv["grounded"] and "Sign off the figures" in pv["wording"]
    status, body = _post(srv, "/api/sign", {"snapshot": pv["snapshot"], "name": "Ann"})
    assert status == 200, body
    s = json.loads(body)
    assert s["signed"] and s["name"] == "Ann" and s["pack"] == f"packs/{pv['snapshot']}"
    assert (folder / s["pack"] / "memo.md").read_text(encoding="utf-8") == pv["text"]
    status, body = _post(srv, "/api/sign", {"snapshot": pv["snapshot"], "name": "Ann"})
    assert status == 409 and "never overwritten" in json.loads(body)["refused"]


def test_workspace_sign_off_refuses_a_stale_preview_naming_what_changed(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    pv = json.loads(_post(srv, "/api/preview", {})[1])
    overrides.add_override(folder, "L2", "revenue_transfer", reason="customer", name="Ann")
    status, body = _post(srv, "/api/sign", {"snapshot": pv["snapshot"], "name": "Ann"})
    reply = json.loads(body)
    assert status == 409 and "the override log" in reply["refused"]
    assert any("override log" in c for c in reply["changed"]) and not (folder / "packs").exists()
    status, body = _post(srv, "/api/sign", {"snapshot": pv["snapshot"], "name": "Ann", "amount": "1"})
    assert status == 400


def test_workspace_preview_page_offers_the_sign_off_form_and_escapes_the_text(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    store.update_profile(folder, lambda cur: {**cur, "demo": True})
    srv = serve(case_id)
    status, page = _post(srv, f"/preview?token={srv.token}", "back=%2F", kind="application/x-www-form-urlencoded")
    assert status == 200 and 'id="pack-preview"' in page
    assert 'action="/sign?token=' in page and "Sign off these figures" in page
    assert "Approve" not in page and "approve the loan" not in page.replace("does not approve the loan", "")
    snapshot = pack.latest_preview(caselog.for_case(folder).entries())[1]["snapshot"]
    assert f'name="snapshot" value="{snapshot}"' in page
    status, page = _post(srv, f"/sign?token={srv.token}", f"snapshot={snapshot}&name=Ann+%3Cb%3E&back=%2F",
                         kind="application/x-www-form-urlencoded")
    assert status == 200 and 'id="signed-off"' in page and "Ann &lt;b&gt;" in page and "<b>" not in page
    assert (folder / "packs" / snapshot / "evidence.json").exists()


# --- hardening: authz on the new entry points, and refusals the workspace and CLI report ---


def _raw(srv, method, path, body=b"", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=30)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


@pytest.mark.parametrize("path", ["/sign", "/api/sign", "/preview", "/api/preview"])
def test_workspace_sign_and_preview_need_the_token_host_and_origin(home, tmp_path, serve, path):
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)
    srv = serve(case_id)
    body = json.dumps({"snapshot": p.snapshot, "name": "Ann"}).encode()
    good = {"Host": f"127.0.0.1:{srv.port}", "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{srv.port}"}
    events = _events(folder)
    cases = [
        ("POST", path, {**good}),  # no token
        ("POST", path, {**good, "X-CMA-Token": "wrong"}),
        ("POST", path, {**good, "X-CMA-Token": srv.token, "Origin": "http://evil.example"}),
        ("POST", path, {**good, "X-CMA-Token": srv.token, "Host": "evil.example"}),
    ]
    for method, target, headers in cases:
        status, _ = _raw(srv, method, target, body, headers)
        assert status == 403, (target, headers)
    status, _ = _raw(srv, "GET", path, b"", {"Host": good["Host"], "X-CMA-Token": srv.token})
    assert status == 405  # a write is POST only
    assert _events(folder) == events and not (folder / "packs").exists()


def test_workspace_sign_off_on_a_broken_log_names_the_first_bad_entry(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)
    srv = serve(case_id)
    log = folder / caselog.LOG_NAME
    lines = log.read_bytes().splitlines(keepends=True)
    lines[1] = lines[1].replace(b'"import"', b'"imporT"')
    log.write_bytes(b"".join(lines))
    status, body = _post(srv, "/api/sign", {"snapshot": p.snapshot, "name": "Ann"})
    reply = json.loads(body)
    assert status == 409 and reply["first_bad"] == 2 and "entry 2" in reply["refused"]
    assert not (folder / "packs").exists()
    status, page = _post(srv, f"/sign?token={srv.token}", f"snapshot={p.snapshot}&name=Ann&back=%2F",
                         kind="application/x-www-form-urlencoded")
    assert status == 409 and 'id="sign-refused"' in page and "entry 2" in page


def test_cli_case_sign_reports_a_save_not_recorded_and_leaves_no_pack(home, tmp_path, monkeypatch):
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)

    def refused(self, event, data, *, expected):
        raise caselog.SaveNotRecorded("the case lock file was replaced; the save was not recorded")

    monkeypatch.setattr(caselog.CaseLog, "append", refused)
    r = CliRunner().invoke(app, ["case", "sign", case_id, "--snapshot", p.snapshot], input="Ann\ny\n")
    assert r.exit_code == 4 and "not recorded" in r.output.lower()
    assert not (folder / "packs" / p.snapshot).exists()


class _Died(BaseException):
    """The process stops here: no cleanup of ours runs after it."""


def _die_at(monkeypatch, event):
    """Stop the process when the case log is asked to record `event`, and let no folder be
    removed on the way out, as a killed process removes none."""
    import shutil

    real = caselog.CaseLog.append

    def append(self, ev, data, *, expected):
        if ev == event:
            raise _Died()
        return real(self, ev, data, expected=expected)

    monkeypatch.setattr(caselog.CaseLog, "append", append)
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **k: None)


def test_r26_a_pack_the_tool_stopped_before_recording_is_refused_as_unrecorded_never_removed(home, tmp_path,
                                                                                           monkeypatch):
    """The tool stops after renaming the pack into place and before recording it: the log holds
    no pack write and no sign-off. Signing off again says so (not that the figures are signed
    off), leaves the folder as it is, and signs off once it is moved aside."""
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)
    with monkeypatch.context() as m:
        _die_at(m, pack.PACK_EVENT)
        with pytest.raises(_Died):
            pack.sign_off(folder, case_id, p.snapshot, "Ann")
    target = folder / "packs" / p.snapshot
    assert sorted(os.listdir(target)) == ["evidence.json", "memo.md"]
    assert pack.PACK_EVENT not in _events(folder) and pack.SIGN_EVENT not in _events(folder)
    events = _events(folder)
    for snapshot in (p.snapshot, pack.build_preview(folder, case_id).snapshot):  # the same preview, or a new one
        with pytest.raises(pack.SignOffRefused) as e:
            pack.sign_off(folder, case_id, snapshot, "Ann")
        msg = str(e.value)
        assert "records no pack written" in msg and "move that folder aside" in msg and "already signed off" not in msg
    assert sorted(os.listdir(target)) == ["evidence.json", "memo.md"]  # never removed or replaced
    assert _events(folder)[: len(events)] == events and pack.SIGN_EVENT not in _events(folder)
    os.rename(target, folder / "aside")
    signed = pack.sign_off(folder, case_id, p.snapshot, "Ann")
    assert signed.pack == target and _events(folder)[-2:] == [pack.PACK_EVENT, pack.SIGN_EVENT]


def test_r42_a_sign_off_the_tool_stopped_before_recording_is_recorded_on_the_next_sign_off(home, tmp_path,
                                                                                           monkeypatch):
    """The tool stops between recording the pack write and the sign-off: signing off again
    under another name is refused, naming the entry and the name the pack carries; signing off
    under that name records the pack's own sign-off, once, without rewriting the pack."""
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)
    with monkeypatch.context() as m:
        _die_at(m, pack.SIGN_EVENT)
        with pytest.raises(_Died):
            pack.sign_off(folder, case_id, p.snapshot, "Ann")
    events = _events(folder)
    assert events[-1] == pack.PACK_EVENT and pack.SIGN_EVENT not in events
    target = folder / "packs" / p.snapshot
    before = {f: (target / f).read_bytes() for f in os.listdir(target)}
    with pytest.raises(pack.SignOffRefused) as e:
        pack.sign_off(folder, case_id, p.snapshot, "Bob")
    msg = str(e.value)
    assert f"case log entry {len(events)}" in msg and "sign-off was not recorded" in msg and "Ann" in msg
    assert _events(folder) == events
    signed = pack.sign_off(folder, case_id, p.snapshot, "Ann")
    assert signed.completed and signed.name == "Ann" and signed.pack == target
    assert _events(folder) == [*events, pack.SIGN_EVENT]
    assert {f: (target / f).read_bytes() for f in os.listdir(target)} == before
    evidence = json.loads(before["evidence.json"])
    entry = caselog.for_case(folder).entries()[-1].data
    assert {k: entry[k] for k in ("name", "os_user", "at", "snapshot")} == \
        {k: evidence["sign_off"][k] for k in ("name", "os_user", "at", "snapshot")}
    assert caselog.for_case(folder).verify().ok
    with pytest.raises(pack.SignOffRefused, match="already signed off"):
        pack.sign_off(folder, case_id, p.snapshot, "Ann")


def test_r42_a_sign_off_entry_not_recorded_can_be_recorded_again(home, tmp_path, monkeypatch):
    """verify-251's schedule: the pack write is recorded, the sign-off append fails as not
    recorded. The next sign-off, from the same preview or a fresh one, records it."""
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)
    real = caselog.CaseLog.append

    def second_fails(self, event, data, *, expected):
        if event == pack.SIGN_EVENT:
            raise caselog.SaveNotRecorded("the disk is full; the save was not recorded")
        return real(self, event, data, expected=expected)

    with monkeypatch.context() as m:
        m.setattr(caselog.CaseLog, "append", second_fails)
        with pytest.raises(caselog.SaveNotRecorded):
            pack.sign_off(folder, case_id, p.snapshot, "Ann")
    assert _events(folder)[-1] == pack.PACK_EVENT
    again = pack.build_preview(folder, case_id)
    assert again.snapshot == p.snapshot
    signed = pack.sign_off(folder, case_id, again.snapshot, "Ann")
    assert signed.completed and _events(folder)[-1] == pack.SIGN_EVENT
    assert caselog.for_case(folder).verify().ok
    # through the CLI too
    case2, folder2 = _case(tmp_path)
    p2 = pack.build_preview(folder2, case2)
    with monkeypatch.context() as m:
        m.setattr(caselog.CaseLog, "append", second_fails)
        r = CliRunner().invoke(app, ["case", "sign", case2, "--snapshot", p2.snapshot], input="Ann\ny\n")
        assert r.exit_code == 4
    r = CliRunner().invoke(app, ["case", "sign", case2, "--snapshot", p2.snapshot], input="Ann\ny\n")
    assert r.exit_code == 0 and "now recorded" in r.output and "not rewritten" in r.output


def test_r42_a_recorded_pack_whose_files_changed_is_refused(home, tmp_path, monkeypatch):
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)
    with monkeypatch.context() as m:
        _die_at(m, pack.SIGN_EVENT)
        with pytest.raises(_Died):
            pack.sign_off(folder, case_id, p.snapshot, "Ann")
    (folder / "packs" / p.snapshot / "memo.md").write_text("edited", encoding="utf-8")
    events = _events(folder)
    with pytest.raises(pack.SignOffRefused, match="not the ones that entry recorded"):
        pack.sign_off(folder, case_id, p.snapshot, "Ann")
    assert _events(folder) == events
    os.rename(folder / "packs" / p.snapshot, folder / "aside")
    with pytest.raises(pack.SignOffRefused, match="put the pack back"):
        pack.sign_off(folder, case_id, p.snapshot, "Ann")
    assert _events(folder) == events


def test_r23_a_refused_sign_off_leaves_a_crash_leftover_in_place(home, tmp_path):
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)
    leftover = folder / "packs" / (pack.PART + "x")
    leftover.mkdir(parents=True)
    (leftover / "memo.md").write_text("half", encoding="utf-8")
    overrides.add_override(folder, "L2", "revenue_transfer", reason="customer", name="Ann")
    with pytest.raises(pack.SignOffRefused):
        pack.sign_off(folder, case_id, p.snapshot, "Ann")
    assert (leftover / "memo.md").read_text(encoding="utf-8") == "half"


def test_workspace_sign_off_names_a_bad_configuration_file(home, tmp_path, monkeypatch):
    from cma import policy
    from cma.review import signoff

    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)

    def bad(*a, **k):
        raise policy.PolicyError("the policy file is not valid YAML")

    monkeypatch.setattr(pack, "sign_off", bad)
    status, reply = signoff.answer(folder, case_id, "sign", {"snapshot": p.snapshot, "name": "Ann"})
    assert status == 409 and "not valid YAML" in reply["refused"] and "Nothing was signed off" in reply["refused"]


def _head_fails_once_for(monkeypatch, event):
    """The disk fills while the head record that would list `event` is staged: the whole line
    of `event` is in the log, past the recorded entries, as a torn entry (case.py's
    SaveNotRecorded 'the next holder confirms dropping it')."""
    import errno

    real = caselog.CaseLog._write_head
    state = {"armed": False, "done": False}
    real_append = caselog.CaseLog.append

    def append(self, ev, data, *, expected):
        state["armed"] = ev == event and not state["done"]
        return real_append(self, ev, data, expected=expected)

    def write_head(self, *a, **k):
        if state["armed"]:
            state["armed"], state["done"] = False, True
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, *a, **k)

    monkeypatch.setattr(caselog.CaseLog, "append", append)
    monkeypatch.setattr(caselog.CaseLog, "_write_head", write_head)


def test_r42_r43_a_sign_off_entry_left_torn_is_repaired_with_confirmation_and_then_recorded(home, tmp_path,
                                                                                            monkeypatch):
    """verify-252's schedule: the sign-off append fails after its line is on disk, leaving it
    torn past the recorded pack write. Sign-off is refused naming the torn entry (R43) and says
    how to resolve it; `cma case repair` drops it once a person confirms with a typed name
    (R42's repair), and the next sign-off records the pack's own sign-off."""
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)
    with monkeypatch.context() as m:
        _head_fails_once_for(m, pack.SIGN_EVENT)
        with pytest.raises(caselog.SaveNotRecorded):
            pack.sign_off(folder, case_id, p.snapshot, "Ann")
    check = caselog.for_case(folder).verify()
    assert check.torn and not check.ok
    torn_at = check.first_bad
    target = folder / "packs" / p.snapshot
    before = {f: (target / f).read_bytes() for f in os.listdir(target)}
    runner = CliRunner()
    r = runner.invoke(app, ["case", "sign", case_id, "--snapshot", p.snapshot], input="Ann\ny\n")
    out = " ".join(r.output.split())
    assert r.exit_code == 3 and f"entry {torn_at}" in out and "cma case repair" in out
    # the workspace says the same
    status, reply = __import__("cma.review.signoff", fromlist=["answer"]).answer(
        folder, case_id, "sign", {"snapshot": p.snapshot, "name": "Ann"})
    assert status == 409 and reply["first_bad"] == torn_at and "cma case repair" in reply["refused"]
    # repair: declined changes nothing; a typed name is required; confirmed drops the torn entry
    log_bytes = (folder / caselog.LOG_NAME).read_bytes()
    r = runner.invoke(app, ["case", "repair", case_id], input="Ann\nn\n")
    assert r.exit_code == 1 and (folder / caselog.LOG_NAME).read_bytes() == log_bytes
    r = runner.invoke(app, ["case", "repair", case_id], input="Ann\ny\n")
    assert r.exit_code == 0, r.output
    assert f"entry {torn_at}" in " ".join(r.output.split())
    entries = caselog.for_case(folder).entries()
    assert entries[-1].event == caselog.TORN_DROPPED and entries[-1].data["by"] == "Ann"
    assert caselog.for_case(folder).verify().ok
    r = runner.invoke(app, ["case", "repair", case_id], input="Ann\ny\n")
    assert r.exit_code == 2 and "nothing is torn" in " ".join(r.output.split())
    r = runner.invoke(app, ["case", "sign", case_id, "--snapshot", p.snapshot], input="Ann\ny\n")
    assert r.exit_code == 0 and "now recorded" in r.output, r.output
    assert _events(folder)[-1] == pack.SIGN_EVENT and caselog.for_case(folder).verify().ok
    assert {f: (target / f).read_bytes() for f in os.listdir(target)} == before


def test_r42_a_pack_write_entry_left_torn_removes_the_pack_and_signs_off_after_repair(home, tmp_path,
                                                                                     monkeypatch):
    """The pack-write append fails after its line is on disk: the pack is removed (no pack
    without its entry), the torn entry is dropped with confirmation, and sign-off then writes
    the pack once."""
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)
    with monkeypatch.context() as m:
        _head_fails_once_for(m, pack.PACK_EVENT)
        with pytest.raises(caselog.SaveNotRecorded):
            pack.sign_off(folder, case_id, p.snapshot, "Ann")
    assert not (folder / "packs" / p.snapshot).exists()
    assert caselog.for_case(folder).verify().torn
    r = CliRunner().invoke(app, ["case", "repair", case_id], input="Ann\ny\n")
    assert r.exit_code == 0, r.output
    signed = pack.sign_off(folder, case_id, p.snapshot, "Ann")
    assert not signed.completed and _events(folder)[-2:] == [pack.PACK_EVENT, pack.SIGN_EVENT]


def test_r42_a_failed_folder_sync_after_the_rename_leaves_no_unrecorded_pack(home, tmp_path, monkeypatch):
    """verify-252's minor: the packs folder cannot be synced after the rename. The pack is
    removed, so the log and the folder agree, and the next sign-off writes it."""
    import errno

    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)
    real = pack._sync_dir

    def sync(path):
        if Path(path) == folder / "packs":
            raise OSError(errno.EIO, "Input/output error")
        return real(path)

    with monkeypatch.context() as m:
        m.setattr(pack, "_sync_dir", sync)
        with pytest.raises(OSError):
            pack.sign_off(folder, case_id, p.snapshot, "Ann")
    assert not (folder / "packs" / p.snapshot).exists() and pack.PACK_EVENT not in _events(folder)
    signed = pack.sign_off(folder, case_id, p.snapshot, "Ann")
    assert not signed.completed and _events(folder)[-1] == pack.SIGN_EVENT
