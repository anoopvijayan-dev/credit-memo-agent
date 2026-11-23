"""T22: the workspace's override form and stale overrides (C5; R17, R18, R19, R52).

The underwriter changes a line's category in the workspace with a reason and their typed name;
the form offers only the categories valid for the line's direction and carries the case log
position the page was built at, so a save made from a stale page is a conflict that asks for a
reload. A stale override (R19) is listed until the underwriter confirms or drops it. Each
override, confirmation and drop is appended to the case log, and the figures, indicators and
policy check come back recomputed, within 2 seconds at 10,000 lines (R52).

Each test talks to a real server running in a thread, with the per-launch token."""

import http.client
import json
import random
import re
import time
from datetime import date, timedelta
from html.parser import HTMLParser
from urllib.parse import urlencode

import pytest

from cma import case, store
from cma.models import CREDIT_BUCKET, Category
from cma.overrides import valid_categories
from cma.review import server as rs

pytestmark = pytest.mark.contracts

SMALL = ("id,date,description,amount,balance\n"
         "L1,2026-01-05,CARD SETTLEMENT STRIPE,500.00,1500.00\n"
         "L2,2026-01-06,FPS CREDIT J BLOGGS,300.25,1800.25\n"
         "L3,2026-01-07,DD COUNCIL,-100.00,1700.25\n"
         "L4,2026-02-02,CASH PAID IN,250.10,1950.35\n")


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    monkeypatch.setenv("CMA_RULESET", "v2")
    return root


def _case(tmp_path, body=SMALL):
    case_id = store.new_case(label="t22")
    folder = store.case_folder(case_id)
    src = tmp_path / "main.csv"
    src.write_text(body, encoding="utf-8")
    store.import_file(folder, src, "csv")
    return case_id, folder


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


def _request(srv, method, path, body=None, *, kind="application/json", headers=None, token=True):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=30)
    try:
        h = {"Host": f"127.0.0.1:{srv.port}"}
        if token:
            h["X-CMA-Token"] = srv.token
        data = None
        if body is not None:
            data = json.dumps(body).encode() if kind == "application/json" else body.encode()
            h["Content-Type"] = kind
        h.update(headers or {})
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        conn.close()


def _post(srv, path, body, **kw):
    status, headers, raw = _request(srv, "POST", path, body, **kw)
    ctype = headers.get("content-type", "")
    return status, (json.loads(raw) if ctype.startswith("application/json") else raw)


def _summary(srv) -> dict:
    status, _, raw = _request(srv, "GET", "/api/case")
    assert status == 200
    return json.loads(raw)


def _figure(summary, fid, account="main"):
    a = next(x for x in summary["accounts"] if x["account"] == account)
    return next(f for f in a["figures"] if f["id"] == fid)


def _entries(folder):
    return [e.event for e in case.for_case(folder).entries()]


def _override(srv, position, **extra):
    body = {"account": "main", "line": "L2", "category": "director_funding", "reason": "director's loan",
            "name": "Pat Jones", "expected": position, **extra}
    return _post(srv, "/api/override", body)


# --- an override saved from the workspace ------------------------------------------------


def test_an_override_is_appended_to_the_case_log_and_the_figures_come_back_recomputed(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    before = _summary(srv)
    m25 = _figure(before, "M25")["value"]
    status, reply = _override(srv, before["position"])
    assert status == 200, reply
    assert reply["recorded"] is True and reply["seq"] == 1
    assert (reply["line"], reply["old"], reply["new"]) == ("L2", "other", "director_funding")
    assert _entries(folder)[-1] == "override"
    data = case.for_case(folder).entries()[-1].data
    assert (data["line"], data["new"], data["name"]) == ("L2", "director_funding", "Pat Jones")
    after = reply["case"]
    assert after["position"] == before["position"] + 1 == reply["position"]
    assert _figure(after, "M20")["value"] == pytest.approx(300.25) and _figure(after, "M20")["overridden"]
    assert _figure(after, "M25")["value"] == pytest.approx(m25 - 300.25)
    a = after["accounts"][0]
    assert "indicators" in a and a["policy_check"] is not None  # recomputed with the figures
    assert after == _summary(srv)  # what the workspace shows next is what the reply showed


def test_the_same_category_records_nothing(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    pos = _summary(srv)["position"]
    status, reply = _override(srv, pos, category="other")
    assert status == 200 and reply["recorded"] is False and reply["position"] == pos
    assert len(_entries(folder)) == pos


# --- refusals (R18) -----------------------------------------------------------------------


def _refused(srv, folder, body, *, status=400, match):
    pos = len(_entries(folder))
    got, reply = _post(srv, "/api/override", body)
    assert got == status, reply
    assert re.search(match, reply["refused"]), reply
    assert len(_entries(folder)) == pos  # nothing appended
    assert not (folder / "overrides.jsonl").exists() or (folder / "overrides.jsonl").read_bytes() == b""


@pytest.mark.parametrize("line,category", [("L2", "supplier"), ("L3", "revenue_transfer"), ("L3", "director_funding")])
def test_a_category_not_valid_for_the_lines_direction_is_refused(home, tmp_path, serve, line, category):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    pos = _summary(srv)["position"]
    body = {"line": line, "category": category, "reason": "r", "name": "Pat", "expected": pos}
    _refused(srv, folder, body, match="not valid for a (credit|debit)")


@pytest.mark.parametrize("field,value", [("amount", "1.00"), ("date", "2026-01-01"), ("balance", "0.00"),
                                         ("description", "X"), ("add_line", "L9"), ("remove_line", "L2")])
def test_anything_but_a_category_is_refused_naming_it(home, tmp_path, serve, field, value):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    pos = _summary(srv)["position"]
    body = {"line": "L2", "category": "director_funding", "reason": "r", "name": "Pat", "expected": pos,
            field: value}
    _refused(srv, folder, body, match=f"category only.*{field}")


@pytest.mark.parametrize("missing,match", [("reason", "reason"), ("name", "name"), ("expected", "position"),
                                           ("line", "line"), ("category", "category")])
def test_an_override_without_its_reason_name_position_line_or_category_is_refused(home, tmp_path, serve,
                                                                                  missing, match):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    body = {"line": "L2", "category": "director_funding", "reason": "r", "name": "Pat",
            "expected": _summary(srv)["position"]}
    del body[missing]
    _refused(srv, folder, body, match=match)


def test_an_unknown_line_or_category_is_refused(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    pos = _summary(srv)["position"]
    _refused(srv, folder, {"line": "L99", "category": "other", "reason": "r", "name": "P", "expected": pos},
             match="no line L99")
    _refused(srv, folder, {"line": "L2", "category": "money", "reason": "r", "name": "P", "expected": pos},
             match="not a category")


def test_a_malformed_request_body_is_refused(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    n = len(_entries(folder))
    for raw, kind in (("{not json", "application/json"), ("[1, 2]", "application/json")):
        status, _reply = _post(srv, "/api/override", raw, kind="text/x-raw", headers={"Content-Type": kind})
        assert status == 400, raw
    status, _ = _post(srv, "/api/override", "x=1", kind="text/plain")
    assert status == 415
    assert len(_entries(folder)) == n


# --- conflicts ----------------------------------------------------------------------------


def test_a_save_from_a_stale_page_is_a_conflict_that_asks_for_a_reload(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    stale_pos = _summary(srv)["position"]
    assert _override(srv, stale_pos)[0] == 200  # another tab saves first
    n = len(_entries(folder))
    status, reply = _override(srv, stale_pos, line="L4", category="other_explained_in")
    assert status == 409, reply
    assert reply["reload"] is True and reply["position"] == n and reply["expected"] == stale_pos
    assert "reload" in reply["conflict"]
    assert len(_entries(folder)) == n


def test_a_save_while_another_writer_holds_the_case_is_refused(home, tmp_path, serve, monkeypatch):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    pos = _summary(srv)["position"]
    monkeypatch.setattr(case, "WRITER_WAIT", 0.1, raising=False)
    log = case.for_case(folder)
    import threading

    held, release = threading.Event(), threading.Event()

    def hold():
        with log.writer():
            held.set()
            release.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(5)
    try:
        status, reply = _override(srv, pos)
    finally:
        release.set()
        t.join()
    assert status == 409 and "another window" in reply["locked"]
    assert len(_entries(folder)) == pos


# --- stale overrides (R19) ---------------------------------------------------------------


def _replace_statement(folder, body):
    p = folder / "statements" / "main.csv"
    p.chmod(0o600)
    p.write_text(body)


def test_a_stale_override_is_listed_and_confirmed_from_the_workspace(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    assert _override(srv, _summary(srv)["position"])[0] == 200
    _replace_statement(folder, SMALL.replace("J BLOGGS", "J BLOGGS LTD"))
    s = _summary(srv)
    assert s["stale"] == [{"seq": 1, "account": "main", "line": "L2", "new": "director_funding",
                           "why": "statement changed", "can_confirm": True}]
    assert not _figure(s, "M20")["overridden"] and _figure(s, "M20")["value"] == 0
    status, reply = _post(srv, "/api/stale", {"seq": 1, "action": "confirm", "name": "Pat",
                                              "expected": s["position"]})
    assert status == 200, reply
    assert _entries(folder)[-1] == "override_confirmed"
    assert reply["case"]["stale"] == [] and _figure(reply["case"], "M20")["value"] == pytest.approx(300.25)
    assert reply["position"] == s["position"] + 1


def test_a_stale_override_on_a_missing_line_can_only_be_dropped(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    assert _override(srv, _summary(srv)["position"], line="L4", category="revenue_transfer")[0] == 200
    _replace_statement(folder, "\n".join(SMALL.splitlines()[:4]) + "\n")
    s = _summary(srv)
    assert [(x["seq"], x["why"], x["can_confirm"]) for x in s["stale"]] == [(1, "line not in the case", False)]
    n = len(_entries(folder))
    status, reply = _post(srv, "/api/stale", {"seq": 1, "action": "confirm", "name": "Pat", "expected": n})
    assert status == 400 and "dropped" in reply["refused"] and len(_entries(folder)) == n
    status, reply = _post(srv, "/api/stale", {"seq": 1, "action": "drop", "name": "Pat", "expected": n})
    assert status == 200 and reply["case"]["stale"] == []
    assert _entries(folder)[-1] == "override_dropped"


def test_resolving_an_override_that_is_not_stale_or_from_a_stale_page_is_refused(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    pos = _summary(srv)["position"]
    assert _override(srv, pos)[0] == 200
    n = len(_entries(folder))
    status, reply = _post(srv, "/api/stale", {"seq": 1, "action": "drop", "name": "Pat", "expected": n})
    assert status == 400 and "not stale" in reply["refused"]
    _replace_statement(folder, SMALL.replace("J BLOGGS", "J BLOGGS LTD"))
    status, reply = _post(srv, "/api/stale", {"seq": 1, "action": "drop", "name": "Pat", "expected": pos})
    assert status == 409 and reply["reload"] is True
    for bad in ({"seq": 1, "action": "keep", "name": "Pat", "expected": n},
                {"seq": "one", "action": "drop", "name": "Pat", "expected": n},
                {"seq": 1, "action": "drop", "expected": n},
                {"seq": 1, "action": "drop", "name": "Pat"}):
        assert _post(srv, "/api/stale", bad)[0] == 400, bad
    assert len(_entries(folder)) == n


# --- the page's forms ---------------------------------------------------------------------


class _Forms(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms, self._form, self._select = [], None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self._form = {"action": a.get("action"), "method": a.get("method"), "fields": {}, "options": [],
                          "required": set()}
            self.forms.append(self._form)
        elif self._form is not None and tag in ("input", "select", "textarea"):
            self._form["fields"][a.get("name")] = a.get("value")
            if "required" in a:
                self._form["required"].add(a.get("name"))
            if tag == "select":
                self._select = a.get("name")
        elif self._form is not None and tag == "option" and self._select:
            self._form["options"].append(a.get("value"))

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None
        elif tag == "select":
            self._select = None


def _forms(html: bytes):
    p = _Forms()
    p.feed(html.decode())
    return p.forms


def test_the_form_offers_only_the_categories_valid_for_each_lines_direction(home, tmp_path, serve):
    case_id, _folder = _case(tmp_path)
    srv = serve(case_id)
    pos = _summary(srv)["position"]
    status, _, html = _request(srv, "GET", "/lines?" + urlencode({"token": srv.token, "figure": "M17"}), token=False)
    assert status == 200
    forms = [f for f in _forms(html) if f["fields"].get("line")]
    credits = {f["fields"]["line"]: f for f in forms}
    assert set(credits) == {"L1", "L2", "L4"}
    for f in forms:
        assert f["method"] == "post" and f["action"].startswith("/override?")
        assert f["options"] == [c.value for c in Category if c in CREDIT_BUCKET]
        assert f["fields"]["expected"] == str(pos)
        assert {"reason", "name", "category"} <= f["required"]
    status, _, html = _request(srv, "GET", "/lines?" + urlencode({"token": srv.token, "month": "2026-01",
                                                                  "cell": "money_out"}), token=False)
    (debit,) = [f for f in _forms(html) if f["fields"].get("line")]
    from cma.models import Transaction

    t = Transaction(id="x", date=date(2026, 1, 1), description="x", amount=-1, balance=0)
    assert debit["options"] == [c.value for c in Category if c in valid_categories(t)]
    assert "director_funding" not in debit["options"] and "supplier" in debit["options"]


def test_the_judgement_queue_carries_the_form(home, tmp_path, serve):
    case_id, _folder = _case(tmp_path)
    srv = serve(case_id)
    _status, _, html = _request(srv, "GET", "/?token=" + srv.token, token=False)
    queue = html.decode().split('id="judgement-queue"')[1].split("</section>")[0]
    lines = {f["fields"]["line"] for f in _forms(queue.encode())}
    assert "L2" in lines  # the unexplained credit, listed first, can be decided where it is listed


def test_a_form_post_saves_and_returns_to_the_page_it_came_from(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    pos = _summary(srv)["position"]
    back = "/lines?" + urlencode({"token": srv.token, "figure": "M17"})
    form = urlencode({"account": "main", "line": "L2", "category": "director_funding", "reason": "loan",
                      "name": "Pat", "expected": pos, "back": "/lines?figure=M17&account=main"})
    status, headers, _ = _request(srv, "POST", "/override?token=" + srv.token, form,
                                  kind="application/x-www-form-urlencoded", token=False)
    assert status == 303
    assert headers["location"].startswith("/lines?") and f"token={srv.token}" in headers["location"]
    assert _entries(folder)[-1] == "override"
    status, _, html = _request(srv, "GET", back, token=False)
    assert "rests on an override" in html.decode()


def test_a_form_post_from_a_stale_page_shows_the_conflict_and_a_reload_link(home, tmp_path, serve):
    case_id, _folder = _case(tmp_path)
    srv = serve(case_id)
    pos = _summary(srv)["position"]
    assert _override(srv, pos)[0] == 200
    form = urlencode({"line": "L4", "category": "other_explained_in", "reason": "r", "name": "Pat",
                      "expected": pos, "back": "https://evil.example/"})
    status, _headers, html = _request(srv, "POST", "/override?token=" + srv.token, form,
                                     kind="application/x-www-form-urlencoded", token=False)
    assert status == 409
    page = html.decode()
    assert "Reload" in page and f'href="/?token={srv.token}"' in page and "evil.example" not in page
    assert _forms(html) is not None


def test_the_page_lists_stale_overrides_with_confirm_and_drop_forms(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    assert _override(srv, _summary(srv)["position"])[0] == 200
    _replace_statement(folder, SMALL.replace("J BLOGGS", "J BLOGGS LTD"))
    pos = _summary(srv)["position"]
    _status, _, html = _request(srv, "GET", "/?token=" + srv.token, token=False)
    page = html.decode()
    assert 'id="stale-overrides"' in page
    section = page.split('id="stale-overrides"')[1].split("</section>")[0]
    forms = _forms(section.encode())
    assert sorted(f["fields"]["action"] for f in forms) == ["confirm", "drop"]
    assert all(f["action"].startswith("/stale?") and f["fields"]["expected"] == str(pos) and
               f["fields"]["seq"] == "1" and "name" in f["required"] for f in forms)


# --- the write endpoints keep the workspace's guards --------------------------------------


def test_the_write_endpoints_need_the_token_and_the_same_origin(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    n = len(_entries(folder))
    body = {"line": "L2", "category": "director_funding", "reason": "r", "name": "P", "expected": n}
    assert _request(srv, "POST", "/api/override", body, token=False)[0] == 403
    assert _request(srv, "POST", "/api/override", body, headers={"Origin": "http://evil.example"})[0] == 403
    assert _request(srv, "POST", "/api/override", body, headers={"Host": "localhost"})[0] == 403
    assert _request(srv, "GET", "/api/override")[0] == 405
    assert _request(srv, "POST", "/api/case", body)[0] == 405
    big = {**body, "reason": "x" * (rs.MAX_BODY + 1)}
    assert _request(srv, "POST", "/api/override", big)[0] == 413
    assert len(_entries(folder)) == n


def test_a_chunked_body_is_refused(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    n = len(_entries(folder))
    import socket

    raw = (f"POST /api/override?token={srv.token} HTTP/1.1\r\nHost: 127.0.0.1:{srv.port}\r\n"
           "Content-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n\r\n").encode()
    with socket.create_connection(("127.0.0.1", srv.port), timeout=5) as s:
        s.sendall(raw)
        reply = b""
        while chunk := s.recv(4096):
            reply += chunk
    assert reply.startswith(b"HTTP/1.1 411") and reply.count(b"HTTP/1.1 ") == 1
    assert len(_entries(folder)) == n


# --- R52: recomputed within 2 seconds at 10,000 lines -------------------------------------


DESCRIPTIONS = ["CARD SETTLEMENT STRIPE", "FPS CREDIT J BLOGGS", "DD COUNCIL", "CASH PAID IN", "IWOCA LTD",
                "TESCO STORES", "HMRC VAT", "TO SAVINGS POT", "PAYPAL REFUND", "BET365", "SUMUP PAYOUT",
                "FPS CREDIT ACME LTD INV 42", "CASH WITHDRAWAL", "UNPAID DD IWOCA"]


def _big(n=10_000) -> str:
    rng = random.Random(22)
    pence, rows, start = 100_000_00, ["id,date,description,amount,balance"], date(2025, 1, 1)
    for i in range(n):
        amount = rng.randint(-50_000, 60_000) or 1
        pence += amount
        rows.append(f"L{i + 1},{start + timedelta(days=i * 365 // n)},{rng.choice(DESCRIPTIONS)},"
                    f"{amount / 100:.2f},{pence / 100:.2f}")
    return "\n".join(rows) + "\n"


def test_r52_an_override_on_a_10000_line_case_comes_back_recomputed_within_2_seconds(home, tmp_path, serve):
    case_id, _folder = _case(tmp_path, _big())
    srv = serve(case_id)
    first = _summary(srv)  # the workspace is open on the case
    assert first["state"] == "shown" and len(first["accounts"]) == 1
    credit = next(q["line"] for q in first["queue"] if not q["line"]["amount"].startswith("-"))
    timings = []
    position = first["position"]
    for category in ("director_funding", "other_explained_in", "revenue_transfer"):
        start = time.perf_counter()
        status, reply = _post(srv, "/api/override", {"line": credit["id"], "category": category,
                                                     "reason": "checked", "name": "Pat", "expected": position})
        timings.append(time.perf_counter() - start)
        assert status == 200 and reply["recorded"], reply
        position = reply["position"]
        c = reply["case"]
        assert c["state"] == "shown"
        assert c["accounts"][0]["indicators"] is not None and c["accounts"][0]["policy_check"] is not None
        assert any(f["overridden"] for f in c["accounts"][0]["figures"])
    assert max(timings) < 2.0, timings


def test_r52_a_form_save_on_a_10000_line_case_shows_the_recomputed_page_within_2_seconds(home, tmp_path, serve):
    case_id, _folder = _case(tmp_path, _big())
    srv = serve(case_id)
    first = _summary(srv)
    credit = next(q["line"] for q in first["queue"] if not q["line"]["amount"].startswith("-"))
    form = urlencode({"account": "main", "line": credit["id"], "category": "director_funding", "reason": "checked",
                      "name": "Pat", "expected": first["position"], "back": "/"})
    start = time.perf_counter()
    status, headers, _ = _request(srv, "POST", "/override?token=" + srv.token, form,
                                  kind="application/x-www-form-urlencoded", token=False)
    assert status == 303
    status, _, html = _request(srv, "GET", headers["location"], token=False)
    elapsed = time.perf_counter() - start
    assert status == 200 and "rests on an override" in html.decode()
    assert 'class="policy-check"' in html.decode() and 'id="judgement-queue"' in html.decode()
    assert elapsed < 2.0, elapsed


# --- hardening: the redirect stays in the workspace, and the kept steps give what a fresh run gives


@pytest.mark.parametrize("back", ["//evil.example/", "https://evil.example/", "/static/app.js", "/api/case",
                                  "/\\evil.example", None, 7, "/lines?next=https://evil.example"])
def test_a_form_never_sends_the_browser_outside_the_workspace_pages(back):
    from cma.review.edit import back_url

    url = back_url(back, "tok")
    assert url.startswith(("/?", "/lines?")) and "evil" not in url and "next=" not in url
    assert "token=tok" in url


def test_categorise_with_kept_classes_gives_what_classifying_every_line_gives(home, tmp_path):
    from cma.categorise import LineOverride, active_ruleset, categorise, classify_lines
    from cma.ingest import load_csv

    p = tmp_path / "big.csv"
    p.write_text(_big(2_000))
    rs_ = active_ruleset()
    rng = random.Random(7)
    base = load_csv(p, account_name="main")
    classes = classify_lines(base, rs_)
    for _ in range(5):
        picked = rng.sample(base.transactions, 20)
        ov = {t.id: LineOverride(sorted(valid_categories(t), key=lambda c: c.value)[0], f"o:{t.id}") for t in picked}
        a = categorise(load_csv(p, account_name="main"), rs_, overrides=ov)
        b = categorise(load_csv(p, account_name="main"), rs_, overrides=ov, unoverridden=classes)
        assert [(t.category, t.rule_id, t.reverses) for t in a.transactions] == \
               [(t.category, t.rule_id, t.reverses) for t in b.transactions]
    with pytest.raises(ValueError):
        categorise(load_csv(p, account_name="main"), rs_, unoverridden=classes[:-1])


def test_the_kept_figures_follow_the_rule_table(home, tmp_path, monkeypatch):
    from cma import overrides

    _case_id, folder = _case(tmp_path)
    v2 = overrides.case_figures(folder)["main"].statement.by_id()["L4"].category
    monkeypatch.setenv("CMA_RULESET", "v1")
    v1 = overrides.case_figures(folder)["main"].statement.by_id()["L4"].category
    assert v2 == Category.CASH_DEPOSIT and v1 != v2  # no categories kept from the other table


def _clear_kept():
    from cma import overrides

    for memo in (overrides._PARSED, overrides._BARE, overrides._CLASSES):
        memo._items.clear()


def _marks(folder):
    from cma import overrides

    return {a: (sorted(f.marked), sorted(f.marked_cells), [(m.id, m.value, m.evidence) for m in f.metrics])
            for a, f in overrides.case_figures(folder).items()}


def test_marks_with_the_kept_figures_are_what_a_fresh_process_gives(home, tmp_path):
    """The figures kept from the case's first, override-free computation mark each override's
    figures exactly as a computation with nothing kept does, override after override (R17, R52)."""
    from cma import overrides

    _case_id, folder = _case(tmp_path, _big(2_000))
    _clear_kept()
    base = overrides.case_figures(folder)["main"]  # the page opens: the override-free figures are kept
    assert not base.marked
    rng = random.Random(22)
    credits = [t for t in base.statement.transactions if t.amount > 0]
    position = len(case.for_case(folder).entries())
    for t in rng.sample(credits, 4):
        new = next(c for c in sorted(valid_categories(t), key=lambda c: c.value) if c != t.category)
        overrides.add_override(folder, t.id, new.value, reason="checked", name="Pat", expected=position)
        position = len(case.for_case(folder).entries())
        kept = _marks(folder)
        _clear_kept()
        assert kept == _marks(folder)
        assert kept["main"][0]  # something now rests on an override


def test_a_form_save_does_not_recompute_the_case_before_sending_the_browser_back(home, tmp_path, serve,
                                                                                 monkeypatch):
    from cma.review import summary

    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    position = _summary(srv)["position"]
    calls = []
    real = summary.case_summary
    monkeypatch.setattr(summary, "case_summary", lambda *a, **k: calls.append(a) or real(*a, **k))
    form = urlencode({"account": "main", "line": "L2", "category": "director_funding", "reason": "loan",
                      "name": "Pat", "expected": position, "back": "/"})
    status, _, _ = _request(srv, "POST", "/override?token=" + srv.token, form,
                            kind="application/x-www-form-urlencoded", token=False)
    assert status == 303 and calls == []  # the page it goes back to is built once, on the GET
    assert _entries(folder)[-1] == "override"
    status, reply = _override(srv, position + 1, line="L1", category="director_funding")
    assert status == 200 and reply["case"]["state"] == "shown" and len(calls) == 1  # data still recomputes
