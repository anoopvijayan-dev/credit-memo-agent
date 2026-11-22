"""T37: promoting an override to a firm rule from the workspace (R20, R21, R42).

Before anything is saved the workspace shows the lines the rule would match in the open case,
each saying whether the rule would set its category; saving writes the rule's pattern,
direction, category, the typed name, the UTC time and the reason to the firm rules file,
records the promotion in the case log, and every case categorised afterwards applies it.

The workspace tests talk to a real server running in a thread, with the per-launch token."""

import http.client
import json
import threading
from urllib.parse import urlencode

import pytest
import yaml

from cma import case, firm, firm_rules, overrides, promotion, store
from cma.review import promote as promote_page
from cma.review import server as rs

pytestmark = pytest.mark.contracts

STATEMENT = ("id,date,description,amount,balance\n"
             "L1,2026-01-05,FPS CREDIT ACME TRUST,500.00,1500.00\n"
             "L2,2026-01-06,FPS CREDIT ACME-TRUST JAN,300.00,1800.00\n"
             "L3,2026-01-07,HMRC REPAYMENT ACME TRUST,100.00,1900.00\n"
             "L4,2026-01-08,FPS ACME TRUST FEES,-50.00,1850.00\n"
             "L5,2026-01-09,CARD SETTLEMENT STRIPE,200.00,2050.00\n"
             "L6,2026-02-02,FPS CREDIT ACME TRUSTEES <b>X</b>,40.00,2090.00\n")
LATER = ("id,date,description,amount,balance\n"
         "N1,2026-03-05,FPS CREDIT ACME TRUST MAR,700.00,700.00\n"
         "N2,2026-03-06,FPS CREDIT J BLOGGS,20.00,720.00\n")


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    monkeypatch.setenv("CMA_RULESET", "v2")
    return root


def _case(tmp_path, body=STATEMENT, name="main.csv"):
    case_id = store.new_case(label="t37")
    folder = store.case_folder(case_id)
    src = tmp_path / name
    src.write_text(body, encoding="utf-8")
    store.import_file(folder, src, "csv")
    return case_id, folder


def _overridden(tmp_path):
    case_id, folder = _case(tmp_path)
    r = overrides.add_override(folder, "L1", "other_explained_in", reason="trust distribution", name="Pat Jones")
    return case_id, folder, r.seq


def _rules_path():
    return firm.load().firm_rules_path


def _line(folder, line, account="main"):
    figs = overrides.case_figures(folder)
    return next(t for t in figs[account].statement.transactions if t.id == line)


# --- the preview -------------------------------------------------------------------------


def test_the_preview_counts_every_line_the_rule_matches_and_writes_nothing(home, tmp_path):
    _, folder, seq = _overridden(tmp_path)
    before = case.for_case(folder).entries()
    p = promotion.preview(folder, {"seq": seq, "pattern": "ACME TRUST"})
    # The credits naming ACME TRUST, whatever separator the bank prints, whole words only:
    # not the debit (direction in, from the override's credit) nor ACME TRUSTEES.
    assert [m.line.id for m in p.lines] == ["L1", "L2", "L3"]
    assert p.count == 3 and p.applies == 1
    by = {m.line.id: m for m in p.lines}
    assert by["L2"].applies and by["L2"].why is None
    assert not by["L1"].applies and by["L1"].why == promotion.WHY["override"]
    assert not by["L3"].applies and by["L3"].why == promotion.WHY["protected"]  # HMRC (R21)
    assert p.rule == {"id": "acme_trust", "pattern": "ACME TRUST", "direction": "in",
                      "category": "other_explained_in"}
    assert not _rules_path().exists()
    assert case.for_case(folder).entries() == before


def test_the_preview_takes_the_direction_given_and_refuses_a_category_it_cannot_give(home, tmp_path):
    _, folder, seq = _overridden(tmp_path)
    p = promotion.preview(folder, {"seq": seq, "pattern": "ACME TRUST", "direction": "in"})
    assert p.rule["direction"] == "in"
    for direction in ("out", "any"):  # other_explained_in is money in only
        with pytest.raises(store.CaseStoreError, match=f"direction {direction}"):
            promotion.preview(folder, {"seq": seq, "pattern": "ACME TRUST", "direction": direction})
    with pytest.raises(store.CaseStoreError, match="direction must be"):
        promotion.preview(folder, {"seq": seq, "pattern": "ACME TRUST", "direction": "up"})
    with pytest.raises(store.CaseStoreError, match="no override 9"):
        promotion.preview(folder, {"seq": 9, "pattern": "ACME TRUST"})


# --- saving ------------------------------------------------------------------------------


def test_promoting_saves_the_rule_logs_it_and_later_cases_apply_it(home, tmp_path):
    _, folder, seq = _overridden(tmp_path)
    position = len(case.for_case(folder).entries())
    p = promotion.preview(folder, {"seq": seq, "pattern": "ACME TRUST"})
    r = promotion.promote(folder, {"seq": seq, "pattern": "ACME TRUST", "name": "Pat Jones",
                                   "reason": "trust distributions are not trading"},
                          shown=p.count, expected=position)
    saved = yaml.safe_load(_rules_path().read_text(encoding="utf-8"))["rules"]
    assert len(saved) == 1
    rec = saved[0]
    assert {k: rec[k] for k in ("id", "pattern", "direction", "category", "by", "reason")} == {
        "id": "acme_trust", "pattern": "ACME TRUST", "direction": "in", "category": "other_explained_in",
        "by": "Pat Jones", "reason": "trust distributions are not trading"}
    assert str(rec["at"]).endswith("Z") or "+00:00" in str(rec["at"])
    loaded = firm_rules.for_case()
    assert [x.id for x in loaded.rules] == ["acme_trust"]
    # the promotion is in the case log, with neither the pattern nor the reason
    last = case.for_case(folder).entries()[-1]
    assert last.event == promotion.EVENT and r.position == last.seq == position + 1
    assert last.data["rule"] == "acme_trust" and last.data["from_override"] == seq
    assert last.data["matched"] == 3 and last.data["applies"] == 1 and last.data["name"] == "Pat Jones"
    assert last.data["firm_rules_sha256"] == loaded.sha256
    assert "ACME" not in json.dumps(last.data) and "trading" not in json.dumps(last.data)
    case.for_case(folder).require_intact()
    # the open case, categorised again, applies it where the preview said it would
    assert _line(folder, "L2").rule_id == "firm:acme_trust"
    assert _line(folder, "L3").rule_id != "firm:acme_trust"
    # a case categorised afterwards applies it too
    _, later = _case(tmp_path, LATER, "later.csv")
    n1 = _line(later, "N1")
    assert n1.category.value == "other_explained_in" and n1.rule_id == "firm:acme_trust"
    assert _line(later, "N2").rule_id != "firm:acme_trust"


def test_a_second_promotion_keeps_the_first_rule_and_takes_a_new_id(home, tmp_path):
    _, folder, seq = _overridden(tmp_path)
    for reason in ("first", "second"):
        n = len(case.for_case(folder).entries())
        p = promotion.preview(folder, {"seq": seq, "pattern": "ACME TRUST"})
        promotion.promote(folder, {"seq": seq, "pattern": "ACME TRUST", "name": "Pat",
                                   "reason": reason}, shown=p.count, expected=n)
    ids = [r.id for r in firm_rules.for_case().rules]
    assert ids == ["acme_trust", "acme_trust_2"]


def test_a_promotion_whose_preview_no_longer_counts_the_lines_shown_saves_nothing(home, tmp_path):
    _, folder, seq = _overridden(tmp_path)
    n = len(case.for_case(folder).entries())
    with pytest.raises(promotion.PreviewChanged, match="showed 2 matching lines and the rule now matches 3"):
        promotion.promote(folder, {"seq": seq, "pattern": "ACME TRUST", "name": "Pat", "reason": "r"},
                          shown=2, expected=n)
    assert not _rules_path().exists()
    assert len(case.for_case(folder).entries()) == n


def test_a_promotion_from_a_stale_position_is_a_conflict_and_saves_nothing(home, tmp_path):
    _, folder, seq = _overridden(tmp_path)
    n = len(case.for_case(folder).entries())
    with pytest.raises(case.LogConflict):
        promotion.promote(folder, {"seq": seq, "pattern": "ACME TRUST", "name": "Pat", "reason": "r"},
                          shown=3, expected=n - 1)
    assert not _rules_path().exists()


def test_a_promotion_the_case_log_does_not_record_puts_the_rules_file_back(home, tmp_path, monkeypatch):
    _, folder, seq = _overridden(tmp_path)
    n = len(case.for_case(folder).entries())

    def refuse(self, event, data, *, expected):
        raise case.LogConflict(expected, expected + 1)

    monkeypatch.setattr(case.CaseLog, "append", refuse)
    with pytest.raises(case.LogConflict):
        promotion.promote(folder, {"seq": seq, "pattern": "ACME TRUST", "name": "Pat", "reason": "r"},
                          shown=3, expected=n)
    assert not _rules_path().exists()


def _two_cases(tmp_path):
    """Two cases under one firm, each with an override to promote (A: ACME TRUST, B: J BLOGGS)."""
    _, a, a_seq = _overridden(tmp_path)
    _, b = _case(tmp_path, LATER, "later.csv")
    b_seq = overrides.add_override(b, "N2", "other_explained_in", reason="gift", name="Sam").seq
    return a, a_seq, b, b_seq


def _promote_in_thread(folder, seq, pattern, done, errors, name=None, digest=True):
    def run():
        try:
            n = len(case.for_case(folder).entries())
            p = promotion.preview(folder, {"seq": seq, "pattern": pattern})
            promotion.promote(folder, {"seq": seq, "pattern": pattern, "name": "Pat", "reason": "r"},
                              shown=p.count, expected=n, seen=p.digest if digest else None)
        except BaseException as e:  # handed back to the test
            errors.append(e)
        finally:
            done.set()

    t = threading.Thread(target=run, daemon=True, name=name)
    t.start()
    return t


def test_two_cases_promoting_at_once_take_turns_and_keep_both_rules(home, tmp_path, monkeypatch):
    """verify-268: the firm rules file is the firm's; a promotion paused between reading it and
    writing it holds it, so another case's promotion waits, and neither rule is lost."""
    a, a_seq, b, b_seq = _two_cases(tmp_path)
    paused, go = threading.Event(), threading.Event()
    real = store._restorer

    def pausing(path, *args, **kwargs):
        undo = real(path, *args, **kwargs)
        if threading.current_thread().name == "A":
            paused.set()
            go.wait(10)
        return undo

    monkeypatch.setattr(store, "_restorer", pausing)
    errors: list = []
    a_done, b_done = threading.Event(), threading.Event()
    ta = _promote_in_thread(a, a_seq, "ACME TRUST", a_done, errors, name="A")
    assert paused.wait(10)
    # B confirms by its count alone: with its digest, which names the rules file it was shown,
    # it would be refused as stale once A's rule is in, and still lose nothing.
    tb = _promote_in_thread(b, b_seq, "J BLOGGS", b_done, errors, digest=False)
    assert not b_done.wait(0.5)  # B waits for A, which has read the file and not yet written it
    go.set()
    ta.join(10)
    tb.join(10)
    assert not errors
    assert sorted(r.id for r in firm_rules.for_case().rules) == ["acme_trust", "j_bloggs"]
    sha = firm_rules.for_case().sha256
    a_last, b_last = case.for_case(a).entries()[-1], case.for_case(b).entries()[-1]
    assert a_last.event == b_last.event == promotion.EVENT
    assert b_last.data["firm_rules_sha256"] == sha  # B recorded the file that holds both rules


def test_a_promotion_put_back_never_removes_another_cases_recorded_rule(home, tmp_path, monkeypatch):
    """verify-268: A writes its rule and its case log then refuses it; B, promoting meanwhile,
    waits until A has put the file back, so B's recorded rule stays."""
    a, a_seq, b, b_seq = _two_cases(tmp_path)
    paused, go = threading.Event(), threading.Event()
    real = case.CaseLog.append

    def append(self, event, data, *, expected):
        if event == promotion.EVENT and threading.current_thread().name == "A":
            paused.set()
            go.wait(10)
            raise case.LogConflict(expected, expected + 1)
        return real(self, event, data, expected=expected)

    monkeypatch.setattr(case.CaseLog, "append", append)
    errors: list = []
    a_done, b_done = threading.Event(), threading.Event()
    ta = _promote_in_thread(a, a_seq, "ACME TRUST", a_done, errors, name="A")
    assert paused.wait(10)
    tb = _promote_in_thread(b, b_seq, "J BLOGGS", b_done, errors, digest=False)
    assert not b_done.wait(0.5)
    go.set()
    ta.join(10)
    tb.join(10)
    assert [type(e) for e in errors] == [case.LogConflict]  # A's, refused by its log
    assert [r.id for r in firm_rules.for_case().rules] == ["j_bloggs"]
    assert case.for_case(b).entries()[-1].data["rule"] == "j_bloggs"
    assert case.for_case(a).entries()[-1].event != promotion.EVENT


PAIRED_A = ("id,date,description,amount,balance\n"
            "A1,2026-01-05,CARD SETTLEMENT STRIPE,500.00,1500.00\n"
            "A2,2026-01-06,FPS TO 12345678,-100.00,1400.00\n"
            "A3,2026-01-20,CARD SETTLEMENT STRIPE,100.00,1500.00\n")
PAIRED_B = ("id,date,description,amount,balance\n"
            "B1,2026-01-05,CARD SETTLEMENT STRIPE,50.00,50.00\n"
            "B2,2026-01-07,FPS CREDIT ACME TRUST,100.00,150.00\n"
            "B3,2026-01-09,FPS CREDIT ACME TRUST REF,30.00,180.00\n"
            "B4,2026-01-25,CARD SETTLEMENT STRIPE,10.00,190.00\n")


def test_the_preview_reads_the_whole_case_so_a_pairing_the_rule_unmakes_is_shown(home, tmp_path):
    """verify-268: a rule giving returned_dd takes a paired credit out of its pairing; the preview
    says the rule sets that credit, and lists its debit partner, which the rule does not match,
    as changing too; and the case, once the rule is saved, is what the preview said."""
    case_id = store.new_case(label="t37")
    folder = store.case_folder(case_id)
    for account, body in (("a", PAIRED_A), ("b", PAIRED_B)):
        src = tmp_path / f"{account}.csv"
        src.write_text(body, encoding="utf-8")
        store.import_file(folder, src, "csv", account=account)
    assert _line(folder, "B2", "b").rule_id.startswith("pairing:")
    seq = overrides.add_override(folder, "B3", "returned_dd", reason="bounced", name="Pat", account="b").seq
    p = promotion.preview(folder, {"seq": seq, "pattern": "ACME TRUST", "direction": "in"})
    by = {m.line.id: m for m in p.lines}
    assert by["B2"].applies and by["B2"].why is None
    assert [(a, t.id) for a, t, _now, _then in p.changed] == [("a", "A2")]
    n = len(case.for_case(folder).entries())
    promotion.promote(folder, {"seq": seq, "pattern": "ACME TRUST", "direction": "in", "name": "Pat",
                               "reason": "r"}, shown=p.count, expected=n, seen=p.digest)
    assert _line(folder, "B2", "b").rule_id == "firm:acme_trust"
    _a, _t, now, then = p.changed[0]
    assert now == "own_account_transfer" != then
    assert _line(folder, "A2", "a").category.value == then


# --- from the workspace ------------------------------------------------------------------


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


def _seen(folder, seq, pattern="ACME TRUST"):
    """The digest the preview page would carry for this rule (Preview.digest)."""
    return promotion.preview(folder, {"seq": seq, "pattern": pattern, "direction": "in", "id": "acme_trust"}).digest


def _request(srv, method, path, body=None, kind="application/json"):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=30)
    try:
        h = {"Host": f"127.0.0.1:{srv.port}"}
        if "token=" not in path:
            h["X-CMA-Token"] = srv.token
        data = None
        if body is not None:
            data = json.dumps(body).encode() if kind == "application/json" else urlencode(body).encode()
            h["Content-Type"] = kind
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def test_the_workspace_previews_then_saves_a_promotion(home, tmp_path, serve):
    case_id, folder, seq = _overridden(tmp_path)
    srv = serve(case_id)
    status, raw = _request(srv, "GET", "/api/case")
    summary = json.loads(raw)
    assert summary["promotable"] == [{"seq": seq, "account": "main", "line": "L1",
                                      "category": "other_explained_in", "direction": "in"}]
    status, raw = _request(srv, "POST", "/api/promote", {"seq": seq, "pattern": "ACME TRUST"})
    assert status == 200
    shown = json.loads(raw)
    assert shown["saved"] is False and shown["preview"]["count"] == 3
    assert [x["id"] for x in shown["preview"]["lines"]] == ["L1", "L2", "L3"]
    assert not _rules_path().exists()
    status, raw = _request(srv, "POST", "/api/promote", {
        "seq": seq, "pattern": "ACME TRUST", "direction": "in", "id": shown["preview"]["rule"]["id"],
        "shown": shown["preview"]["count"], "seen": shown["preview"]["digest"], "expected": shown["position"],
        "confirm": "1",
        "name": "Pat Jones", "reason": "trust distributions"})
    assert status == 200, raw
    done = json.loads(raw)
    assert done["saved"] is True and done["rule"]["id"] == "acme_trust" and done["matched"] == 3
    assert [r.id for r in firm_rules.for_case().rules] == ["acme_trust"]
    assert case.for_case(folder).entries()[-1].event == promotion.EVENT


def test_the_page_shows_the_promote_form_and_the_preview_escaped(home, tmp_path, serve):
    case_id, folder, seq = _overridden(tmp_path)
    srv = serve(case_id)
    status, raw = _request(srv, "GET", f"/?token={srv.token}")
    assert status == 200, raw
    assert b'id="promote-overrides"' in raw and b'class="promote"' in raw
    status, raw = _request(srv, "POST", f"/promote?token={srv.token}",
                           {"seq": seq, "pattern": "ACME TRUST", "direction": "in"}, kind=rs.FORM)
    assert status == 200
    assert b'id="promote-preview"' in raw and b'class="promote-confirm"' in raw
    assert b'name="shown" value="3"' in raw
    assert f'name="seen" value="{_seen(folder, seq)}"'.encode() in raw
    # a malformed request is refused naming nothing of the case
    status, raw = _request(srv, "POST", f"/promote?token={srv.token}", {"seq": seq, "pattern": "ACME", "amount": "1"},
                           kind=rs.FORM)
    assert status == 400 and b'id="promote-refused"' in raw and b"amount" in raw
    assert not _rules_path().exists()


def test_the_preview_page_escapes_case_text(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    r = overrides.add_override(folder, "L6", "other_explained_in", reason="r", name="Pat")
    srv = serve(case_id)
    status, raw = _request(srv, "POST", f"/promote?token={srv.token}",
                           {"seq": r.seq, "pattern": "ACME TRUSTEES"}, kind=rs.FORM)
    assert status == 200
    assert b"<b>X</b>" not in raw and b"&lt;b&gt;X&lt;/b&gt;" in raw


def test_confirming_the_page_preview_saves_the_rule_and_a_stale_page_is_refused(home, tmp_path, serve):
    case_id, folder, seq = _overridden(tmp_path)
    srv = serve(case_id)
    n = len(case.for_case(folder).entries())
    form = {"seq": seq, "pattern": "ACME TRUST", "direction": "in", "id": "acme_trust", "shown": 3,
            "seen": _seen(folder, seq), "confirm": "1", "name": "Pat Jones", "reason": "trust distributions"}
    status, raw = _request(srv, "POST", f"/promote?token={srv.token}", {**form, "expected": n - 1}, kind=rs.FORM)
    assert status == 409 and b'id="promote-refused"' in raw and not _rules_path().exists()
    status, raw = _request(srv, "POST", f"/promote?token={srv.token}", {**form, "expected": n}, kind=rs.FORM)
    assert status == 200 and b'id="promoted"' in raw
    assert [r.id for r in firm_rules.for_case().rules] == ["acme_trust"]


# --- hardening (T37) ---------------------------------------------------------------------


def _request_raw(srv, method, path, body, headers):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=30)
    try:
        data = json.dumps(body).encode()
        conn.request(method, path, body=data, headers={"Content-Type": "application/json", **headers})
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def test_a_promotion_without_the_token_or_from_another_origin_is_refused(home, tmp_path, serve):
    case_id, folder, seq = _overridden(tmp_path)
    srv = serve(case_id)
    n = len(case.for_case(folder).entries())
    body = {"seq": seq, "pattern": "ACME TRUST", "direction": "in", "id": "acme_trust", "shown": 3,
            "expected": n, "confirm": "1", "name": "Pat", "reason": "r"}
    host = {"Host": f"127.0.0.1:{srv.port}"}
    status, _ = _request_raw(srv, "POST", "/api/promote", body, host)
    assert status == 403
    status, _ = _request_raw(srv, "POST", "/api/promote", body, {**host, "X-CMA-Token": "wrong"})
    assert status == 403
    status, _ = _request_raw(srv, "POST", "/api/promote", body,
                             {**host, "X-CMA-Token": srv.token, "Origin": "http://evil.example"})
    assert status == 403
    status, _ = _request_raw(srv, "GET", f"/api/promote?token={srv.token}", body, host)
    assert status == 405
    assert not _rules_path().exists()
    assert len(case.for_case(folder).entries()) == n


def test_resending_the_same_confirmed_promotion_saves_one_rule(home, tmp_path, serve):
    case_id, folder, seq = _overridden(tmp_path)
    srv = serve(case_id)
    n = len(case.for_case(folder).entries())
    form = {"seq": seq, "pattern": "ACME TRUST", "direction": "in", "id": "acme_trust", "shown": 3,
            "seen": _seen(folder, seq), "expected": n, "confirm": "1", "name": "Pat Jones",
            "reason": "trust distributions"}
    status, _ = _request(srv, "POST", "/api/promote", form)
    assert status == 200
    status, raw = _request(srv, "POST", "/api/promote", form)  # a double submit, or a retry
    assert status in (400, 409), raw
    assert [r.id for r in firm_rules.for_case().rules] == ["acme_trust"]
    assert [e.event for e in case.for_case(folder).entries()].count(promotion.EVENT) == 1


def test_the_firm_rules_file_is_written_readable_by_its_owner_only(home, tmp_path):
    import os
    import stat

    _, folder, seq = _overridden(tmp_path)
    n = len(case.for_case(folder).entries())
    promotion.promote(folder, {"seq": seq, "pattern": "ACME TRUST", "name": "Pat", "reason": "r"},
                      shown=3, expected=n)
    mode = stat.S_IMODE(os.stat(_rules_path()).st_mode)
    assert mode & 0o077 == 0, oct(mode)


def test_a_rules_file_changed_since_the_preview_is_read_as_it_now_stands(home, tmp_path):
    """Another case promoted a rule after this preview: saving builds on the file as it is,
    keeps the other rule and takes an id of its own."""
    _, folder, seq = _overridden(tmp_path)
    n = len(case.for_case(folder).entries())
    promotion.preview(folder, {"seq": seq, "pattern": "ACME TRUST"})
    path = _rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"rules": [{
        "id": "acme_trust", "pattern": "SOMEONE ELSE", "direction": "in", "category": "other_explained_in",
        "by": "Sam", "at": "2026-11-20T10:00:00Z", "reason": "other case"}]}), encoding="utf-8")
    r = promotion.promote(folder, {"seq": seq, "pattern": "ACME TRUST", "name": "Pat", "reason": "r"},
                          shown=3, expected=n)
    assert r.rule["id"] == "acme_trust_2"
    assert [x.id for x in firm_rules.for_case().rules] == ["acme_trust", "acme_trust_2"]


def test_a_malformed_firm_rules_file_refuses_the_preview_and_is_left_as_it_was(home, tmp_path):
    _, folder, seq = _overridden(tmp_path)
    path = _rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("rules: [ {id: x}\n", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(firm_rules.FirmRulesError):
        promotion.preview(folder, {"seq": seq, "pattern": "ACME TRUST"})
    n = len(case.for_case(folder).entries())
    with pytest.raises(firm_rules.FirmRulesError):
        promotion.promote(folder, {"seq": seq, "pattern": "ACME TRUST", "name": "Pat", "reason": "r"},
                          shown=3, expected=n)
    assert path.read_bytes() == before and len(case.for_case(folder).entries()) == n


@pytest.mark.parametrize("pattern", ["ACME (TRUST", "ACME.*", "ACME|STRIPE", "(?i)acme"])
def test_a_pattern_is_a_name_never_a_regular_expression(home, tmp_path, pattern):
    _, folder, seq = _overridden(tmp_path)
    p = promotion.preview(folder, {"seq": seq, "pattern": pattern})
    # STRIPE (L5) never matches through an alternation, nor every line through .*
    assert "L5" not in [m.line.id for m in p.lines]
    assert p.count < 6


# --- the digest of what was shown, and the firm's own file (T37 pass) ----------------------


def test_a_preview_with_the_same_count_but_another_outcome_saves_nothing(home, tmp_path):
    # Another case's promotion adds an earlier firm rule for the same counterparty between the
    # preview and its confirmation: the rule still matches 3 lines, but now sets none of them.
    _, folder, seq = _overridden(tmp_path)
    fields = {"seq": seq, "pattern": "ACME TRUST", "direction": "in", "id": "acme_trust"}
    p = promotion.preview(folder, fields)
    assert p.count == 3 and p.applies == 1
    _rules_path().parent.mkdir(parents=True, exist_ok=True)
    _rules_path().write_text("rules:\n  - id: earlier\n    pattern: ACME TRUST\n    direction: in\n"
                             "    category: revenue_transfer\n    by: Sam\n    at: '2026-11-01T09:00:00Z'\n"
                             "    reason: another case\n", encoding="utf-8")
    again = promotion.preview(folder, fields)
    assert again.count == p.count and again.digest != p.digest
    n = len(case.for_case(folder).entries())
    with pytest.raises(promotion.PreviewChanged, match="changed since the preview"):
        promotion.promote(folder, {**fields, "name": "Pat", "reason": "r"}, shown=p.count, expected=n,
                          seen=p.digest)
    assert [r.id for r in firm_rules.for_case().rules] == ["earlier"]
    assert len(case.for_case(folder).entries()) == n


def test_a_confirmation_without_the_preview_digest_is_refused(home, tmp_path, serve):
    case_id, folder, seq = _overridden(tmp_path)
    srv = serve(case_id)
    n = len(case.for_case(folder).entries())
    status, raw = _request(srv, "POST", "/api/promote", {
        "seq": seq, "pattern": "ACME TRUST", "direction": "in", "id": "acme_trust", "shown": 3, "expected": n,
        "confirm": "1", "name": "Pat", "reason": "r"})
    assert status == 400 and b"seen" in raw
    assert not _rules_path().exists() and len(case.for_case(folder).entries()) == n


def test_promoting_keeps_the_firms_comments_and_layout(home, tmp_path):
    _, folder, seq = _overridden(tmp_path)
    own = ("# Firm rules, kept by the credit team. Do not remove the header.\n"
           "rules:\n"
           "  # Rent collected for the landlord, not trading.\n"
           "  - id: landlord\n"
           "    pattern: HOLLY ESTATES\n"
           "    direction: in\n"
           "    category: other_explained_in\n"
           "    by: Sam\n"
           "    at: '2026-11-01T09:00:00Z'\n"
           "    reason: rent passed through\n")
    _rules_path().parent.mkdir(parents=True, exist_ok=True)
    _rules_path().write_text(own, encoding="utf-8")
    fields = {"seq": seq, "pattern": "ACME TRUST", "direction": "in"}
    p = promotion.preview(folder, fields)
    promotion.promote(folder, {**fields, "name": "Pat", "reason": "trust"}, shown=p.count,
                      expected=len(case.for_case(folder).entries()), seen=p.digest)
    text = _rules_path().read_text(encoding="utf-8")
    assert text.startswith(own)
    assert [r.id for r in firm_rules.for_case().rules] == ["landlord", "acme_trust"]


def test_a_firm_rules_file_in_another_layout_is_written_afresh_and_still_reads(home, tmp_path):
    _, folder, seq = _overridden(tmp_path)
    _rules_path().parent.mkdir(parents=True, exist_ok=True)
    _rules_path().write_text("{rules: [{id: landlord, pattern: HOLLY ESTATES, direction: in, category: "
                             "other_explained_in, by: Sam, at: '2026-11-01T09:00:00Z', reason: rent}]}\n",
                             encoding="utf-8")
    fields = {"seq": seq, "pattern": "ACME TRUST", "direction": "in"}
    p = promotion.preview(folder, fields)
    promotion.promote(folder, {**fields, "name": "Pat", "reason": "trust"}, shown=p.count,
                      expected=len(case.for_case(folder).entries()), seen=p.digest)
    rules = firm_rules.for_case()
    assert [r.id for r in rules.rules] == ["landlord", "acme_trust"]
    assert rules.records[0]["reason"] == "rent" and rules.records[1]["by"] == "Pat"


def test_the_preview_page_lists_the_lines_the_rule_changes_without_matching(home, tmp_path):
    case_id = store.new_case(label="t37")
    folder = store.case_folder(case_id)
    for account, body in (("a", PAIRED_A.replace("FPS TO 12345678", "FPS TO <b>9</b>")), ("b", PAIRED_B)):
        src = tmp_path / f"{account}.csv"
        src.write_text(body, encoding="utf-8")
        store.import_file(folder, src, "csv", account=account)
    seq = overrides.add_override(folder, "B3", "returned_dd", reason="bounced", name="Pat", account="b").seq
    status, reply = promote_page.answer(folder, case_id, {"seq": seq, "pattern": "ACME TRUST", "direction": "in"})
    assert status == 200 and [x["id"] for x in reply["preview"]["changed"]] == ["A2"]
    page = promote_page.html(status, reply, "tok", {})
    assert 'class="lines changed"' in page and "FPS TO &lt;b&gt;9&lt;/b&gt;" in page and "<b>9</b>" not in page


# --- the firm rules file's lock across processes (hardening, verify-268) -------------------


def test_the_firm_rules_lock_keeps_out_another_process_while_a_promotion_holds_it(home, tmp_path):
    """Two `cma review` processes for two cases share the firm rules file: while one holds its
    lock, another process cannot take it (flock, not a per-process lock)."""
    import subprocess
    import sys

    path = _rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    probe = ("import fcntl, os, sys\n"
             "fd = os.open(sys.argv[1], os.O_RDWR)\n"
             "try:\n    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n    print('took')\n"
             "except OSError:\n    print('held')\n")
    lock = path.with_name(path.name + ".lock")
    with promotion._firm_rules_held(path):
        held = subprocess.run([sys.executable, "-c", probe, str(lock)], capture_output=True, text=True, timeout=60)
    free = subprocess.run([sys.executable, "-c", probe, str(lock)], capture_output=True, text=True, timeout=60)
    assert held.stdout.strip() == "held" and free.stdout.strip() == "took"


def test_a_promotion_that_cannot_take_the_firm_rules_lock_saves_nothing(home, tmp_path, monkeypatch):
    _, folder, seq = _overridden(tmp_path)
    n = len(case.for_case(folder).entries())
    monkeypatch.setattr(promotion, "LOCK_WAIT", 0.2)
    path = _rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with promotion._firm_rules_held(path), \
            pytest.raises(store.CaseStoreError, match="another case is saving a firm rule"):
        promotion.promote(folder, {"seq": seq, "pattern": "ACME TRUST", "name": "Pat", "reason": "r"},
                          shown=3, expected=n)
    assert not path.exists() and len(case.for_case(folder).entries()) == n


# --- two cases promoting at once (T37 pass, verify-268's scenario) -------------------------


OTHER = ("id,date,description,amount,balance\n"
         "B1,2026-01-05,FPS CREDIT HOLLY ESTATES,800.00,1800.00\n"
         "B2,2026-01-06,FPS CREDIT HOLLY ESTATES FEB,800.00,2600.00\n")


def _two_overridden_cases(tmp_path):
    _, a, seq_a = _overridden(tmp_path)
    _, b = _case(tmp_path, OTHER, name="other.csv")
    seq_b = overrides.add_override(b, "B1", "other_explained_in", reason="rent passed through", name="Sam").seq
    return (a, seq_a, "ACME TRUST"), (b, seq_b, "HOLLY ESTATES")


@pytest.mark.parametrize("round_", range(6))
def test_two_cases_promoting_at_once_both_keep_their_rule(home, tmp_path, round_):
    import threading

    cases = _two_overridden_cases(tmp_path)
    _rules_path().parent.mkdir(parents=True, exist_ok=True)
    start, results = threading.Barrier(2), {}

    def run(folder, seq, pattern):
        n = len(case.for_case(folder).entries())
        p = promotion.preview(folder, {"seq": seq, "pattern": pattern})
        start.wait()
        try:
            results[pattern] = promotion.promote(folder, {"seq": seq, "pattern": pattern, "name": "Pat",
                                                          "reason": "r"}, shown=p.count, expected=n)
        except Exception as e:  # the test reports whatever either thread met
            results[pattern] = e

    threads = [threading.Thread(target=run, args=c) for c in cases]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert all(isinstance(r, promotion.Promoted) for r in results.values()), results
    assert sorted(r.id for r in firm_rules.for_case().rules) == ["acme_trust", "holly_estates"]
    for folder, _, _ in cases:
        assert [e.event for e in case.for_case(folder).entries()].count(promotion.EVENT) == 1


def test_a_case_confirming_a_preview_another_cases_promotion_overtook_is_refused_and_loses_nothing(home, tmp_path):
    # The confirmed preview read the rules file before the other case's rule was saved, so its
    # digest no longer holds: it is refused, the other case's rule stays, and nothing is logged.
    (a, seq_a, pat_a), (b, seq_b, pat_b) = _two_overridden_cases(tmp_path)
    _rules_path().parent.mkdir(parents=True, exist_ok=True)
    pa = promotion.preview(a, {"seq": seq_a, "pattern": pat_a})
    pb = promotion.preview(b, {"seq": seq_b, "pattern": pat_b})
    na, nb = len(case.for_case(a).entries()), len(case.for_case(b).entries())
    promotion.promote(a, {"seq": seq_a, "pattern": pat_a, "name": "Pat", "reason": "r"}, shown=pa.count,
                      expected=na, seen=pa.digest)
    with pytest.raises(promotion.PreviewChanged):
        promotion.promote(b, {"seq": seq_b, "pattern": pat_b, "name": "Sam", "reason": "r"}, shown=pb.count,
                          expected=nb, seen=pb.digest)
    assert [r.id for r in firm_rules.for_case().rules] == ["acme_trust"]
    assert len(case.for_case(b).entries()) == nb
