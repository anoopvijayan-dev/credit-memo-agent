"""The case app (`cma app`): every step of a case in the browser, for an analyst who never
opens a terminal. Loopback only, a token per start, a Host and Origin check as the review
workspace has; cases listed with their status; a case made, its details saved, a statement
uploaded and imported, the review workspace opened with a link back, a signed memo shown
with its verification.

Requests are sent raw to an app running in a thread, as a browser would send them: forms
URL-encoded or multipart, with the app's own Origin."""

import http.client
import re
import uuid
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from cma import store
from cma.app import actions, cases, pages
from cma.app import server as app_server
from cma.review import server as rs
from cma.review import signoff

pytestmark = pytest.mark.contracts

# An HSBC download: no header line, newest first, money out negative.
HSBC = (b"03/03/2026,CARD ACQUIRER SETTLEMENT,812.40\r\n"
        b"02/03/2026,DD BRITISH GAS,-120.00\r\n"
        b"01/03/2026,CARD ACQUIRER SETTLEMENT,640.10\r\n")


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    return root


@pytest.fixture
def app(home):
    srv = app_server.start(port=0)
    srv.serve_in_thread()
    yield srv
    srv.close()


def _request(app, method, path, *, body=b"", kind=None, host=None, origin="own", token=True):
    if token:
        path += ("&" if "?" in path else "?") + urlencode({"token": app.token})
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=20)
    try:
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host or f"127.0.0.1:{app.port}")
        if origin == "own":
            conn.putheader("Origin", f"http://127.0.0.1:{app.port}")
        elif origin is not None:
            conn.putheader("Origin", origin)
        if body or method == "POST":
            conn.putheader("Content-Type", kind or app_server.FORM)
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        if body:
            conn.send(body)
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        conn.close()


def _get(app, path, **kw):
    return _request(app, "GET", path, origin=None, **kw)


def _post(app, path, fields, **kw):
    return _request(app, "POST", path, body=urlencode(fields).encode(), **kw)


def _upload(app, fields, filename, data):
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                 "Content-Type: text/csv\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return _request(app, "POST", "/import", body=b"".join(parts), kind=f"multipart/form-data; boundary={boundary}")


def _follow(app, headers):
    """The page a 303 sends the browser to."""
    loc = headers["location"]
    status, _h, body = _get(app, loc, token=False)
    assert status == 200
    return body.decode()


def _new(app, **fields):
    status, headers, _ = _post(app, "/new", {"label": "Case one", **fields})
    assert status == 303, _
    case_id = parse_qs(urlsplit(headers["location"]).query)["id"][0]
    return case_id, headers


# --- the checks every request passes ------------------------------------------------------------


def test_every_request_needs_the_token_its_own_host_and_its_own_origin(app):
    assert _get(app, "/")[0] == 200
    assert _get(app, "/", token=False)[0] == 403
    assert _get(app, "/?token=wrong", token=False)[0] == 403
    assert _get(app, "/", host="localhost:%d" % app.port)[0] == 403  # DNS rebinding reaches nothing
    assert _post(app, "/new", {"label": "x"}, origin="http://evil.example")[0] == 403
    # A browser sends "Origin: null" under a no-referrer policy: refused, so the app must not use one.
    assert _post(app, "/new", {"label": "x"}, origin="null")[0] == 403
    _status, headers, _ = _get(app, "/")
    assert headers["referrer-policy"] == "same-origin"
    assert "script-src" not in headers["content-security-policy"]  # no script at all
    assert cases.case_ids() == []


def test_it_listens_on_loopback_only(home):
    srv = app_server.start(port=0)
    try:
        assert srv.httpd.server_address[0] == "127.0.0.1"
        assert srv.url.startswith(f"http://127.0.0.1:{srv.port}/?token=")
    finally:
        srv.close()


def test_writes_are_post_and_pages_are_get(app):
    assert _get(app, "/new")[0] == 200  # the form
    assert _get(app, "/details")[0] == 405
    assert _get(app, "/import")[0] == 405
    assert _post(app, "/", {})[0] == 405


# --- the list of cases and a new case -----------------------------------------------------------


def test_an_empty_list_says_how_to_start(app):
    body = _get(app, "/")[2].decode()
    assert "Start your first case" in body and "New case" in body


def test_a_new_case_is_made_and_said_once(app):
    case_id, headers = _new(app, business_name="HARBOURSIDE COFFEE LTD", demo="yes")
    profile = store.load_profile(store.case_folder(case_id))
    assert profile["label"] == "Case one" and profile["business_name"] == "HARBOURSIDE COFFEE LTD"
    assert profile["demo"] is True
    page = _follow(app, headers)
    assert "Case made." in page and "Needs statements" in page and "Demonstration case" in page
    assert "Case made." not in _follow(app, headers)  # said once
    listing = _get(app, "/")[2].decode()
    assert "Case one" in listing and "Needs statements" in listing


def test_a_new_case_form_that_is_refused_keeps_what_was_typed(app):
    status, _h, body = _post(app, "/new", {"label": "x" * 201, "business_name": "KEEP ME"})
    assert status == 400
    assert "too long" in body.decode() and "KEEP ME" in body.decode()
    assert cases.case_ids() == []


def test_case_text_is_escaped(app):
    _case_id, headers = _new(app, label="<script>alert(1)</script>")
    page = _follow(app, headers)
    assert "<script>alert(1)" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert(1)" not in _get(app, "/")[2].decode()


def test_an_unknown_case_is_not_found(app):
    assert _get(app, "/case?id=0123456789abcdef")[0] == 404
    assert _get(app, "/case?id=../../etc")[0] == 404
    status, _h, _b = _post(app, "/details", {"id": "0123456789abcdef", "label": "x"})
    assert status == 404


# --- details -------------------------------------------------------------------------------------


def test_details_are_saved_through_the_profile(app):
    case_id, _ = _new(app)
    status, headers, _ = _post(app, "/details", {
        "id": case_id, "label": "Renamed", "business_name": "HARBOURSIDE COFFEE LTD",
        "directors": "AMARA OKAFOR\r\n\r\nJO BLOGGS\r\n", "shareholders": "", "own_accounts": "HARBOURSIDE RESERVE",
        "vat_stagger": "3", "proposed_monthly_repayment": "£2,500.00"})
    assert status == 303 and "step=details" in headers["location"]
    p = store.load_profile(store.case_folder(case_id))
    assert p["label"] == "Renamed" and p["directors"] == ["AMARA OKAFOR", "JO BLOGGS"]
    assert p["own_accounts"] == ["HARBOURSIDE RESERVE"] and p["vat_stagger"] == 3
    assert p["proposed_monthly_repayment"] == "2500.00"
    assert "Details saved." in _follow(app, headers)


@pytest.mark.parametrize(("field", "value", "said"), [
    ("vat_stagger", "4", "VAT stagger group is 1, 2 or 3"),
    ("proposed_monthly_repayment", "1500,50", "a comma may only separate thousands"),
    ("proposed_monthly_repayment", "lots", "should be an amount"),
    ("directors", "AMARA OKAFOR\nAMARA OKAFOR", "names someone twice"),
])
def test_details_that_are_refused_change_nothing_and_say_why(app, field, value, said):
    case_id, _ = _new(app)
    before = store.load_profile(store.case_folder(case_id))
    status, headers, _ = _post(app, "/details", {"id": case_id, field: value})
    assert status == 303
    page = _follow(app, headers)
    assert said in page and 'class="banner bad"' in page
    assert store.load_profile(store.case_folder(case_id)) == before


# --- statements ------------------------------------------------------------------------------


def test_an_hsbc_download_is_imported_from_the_page(app, home):
    case_id, _ = _new(app)
    status, headers, _ = _upload(app, {"id": case_id, "bank": "hsbc", "account": "Current",
                                       "opening_balance": "£1,000.00"}, "statement.csv", HSBC)
    assert status == 303
    page = _follow(app, headers)
    assert "Imported account current: 3 transactions from 2026-03-01 to 2026-03-03." in page
    view = cases.load(case_id)
    assert view.status == cases.READY and view.statements[0]["account"] == "current"
    assert list((home / actions.UPLOADS).iterdir()) == []  # the upload is not kept outside the case
    # The same file again changes nothing.
    status, headers, _ = _upload(app, {"id": case_id, "bank": "hsbc", "account": "current",
                                       "opening_balance": "1000"}, "statement.csv", HSBC)
    assert "already imported" in _follow(app, headers)


def test_an_import_that_is_refused_says_why_in_words(app):
    case_id, _ = _new(app)
    _status, headers, _ = _upload(app, {"id": case_id, "bank": "hsbc", "account": "current"}, "hsbc.csv", HSBC)
    page = _follow(app, headers)
    assert "opening balance" in page and "--opening-balance" not in page
    assert cases.load(case_id).status == cases.NEEDS_STATEMENTS
    for fields, said in [({"bank": ""}, "Choose the bank"), ({"bank": "hsbc", "account": "a b/c"}, "short name"),
                         ({"bank": "hsbc", "account": "x", "opening_balance": "abc"}, "should be an amount")]:
        _status, headers, _ = _upload(app, {"id": case_id, **fields}, "hsbc.csv", HSBC)
        assert said in _follow(app, headers), said


def test_an_uploaded_file_name_cannot_leave_its_folder(app, home):
    case_id, _ = _new(app)
    _status, headers, _ = _upload(app, {"id": case_id, "bank": "hsbc", "account": "current",
                                       "opening_balance": "0"}, "..\\..\\..\\evil.csv", HSBC)
    assert "Imported account current" in _follow(app, headers)
    assert not (home.parent / "evil.csv").exists() and not (home / "evil.csv").exists()
    assert actions._safe_name("../../x/../../y.csv") == "y.csv"
    assert actions._safe_name("..") == "statement"


# --- review, sign-off and the signed memo ----------------------------------------------------


def _imported_case(app):
    case_id, _ = _new(app)
    _upload(app, {"id": case_id, "bank": "hsbc", "account": "current", "opening_balance": "1000"}, "s.csv", HSBC)
    return case_id


def test_opening_the_review_starts_the_cases_workspace_with_a_link_back(app):
    case_id = _imported_case(app)
    status, headers, _ = _post(app, "/review", {"id": case_id})
    assert status == 303
    loc = urlsplit(headers["location"])
    assert loc.hostname == "127.0.0.1" and loc.port != app.port
    ws = app._workspaces[case_id]
    assert headers["location"] == ws.url
    page = _request_ws(ws, "/").decode()
    assert 'class="app-nav"' in page and f"127.0.0.1:{app.port}/case?id={case_id}" in page.replace("&amp;", "&")
    # Asking again reuses the same workspace.
    assert _post(app, "/review", {"id": case_id})[1]["location"] == ws.url


def _request_ws(ws, path):
    conn = http.client.HTTPConnection("127.0.0.1", ws.port, timeout=20)
    try:
        conn.putrequest("GET", f"{path}?token={ws.token}", skip_host=True)
        conn.putheader("Host", f"127.0.0.1:{ws.port}")
        conn.endheaders()
        return conn.getresponse().read()
    finally:
        conn.close()


def _sign(case_id):
    folder = store.case_folder(case_id)
    status, reply = signoff.answer(folder, case_id, "preview", {})
    assert status == 200, reply
    status, reply = signoff.answer(folder, case_id, "sign", {"snapshot": reply["snapshot"], "name": "A. Analyst"})
    assert status == 200, reply
    return cases.load(case_id).packs[0]["snapshot"]


def test_a_signed_memo_is_shown_verified_with_its_record(app):
    case_id = _imported_case(app)
    snapshot = _sign(case_id)
    view = cases.load(case_id)
    assert view.status == cases.SIGNED
    case_page = _get(app, f"/case?id={case_id}")[2].decode()
    assert "Memo signed by A. Analyst" in case_page
    status, _h, body = _get(app, f"/pack?id={case_id}&snapshot={snapshot}")
    page = body.decode()
    assert status == 200 and "<strong>Verified.</strong>" in page
    assert "<h3>At a glance</h3>" in page and "## At a glance" not in page
    status, headers, body = _get(app, f"/pack/record?id={case_id}&snapshot={snapshot}")
    assert status == 200 and body.startswith(b"<!doctype html>")
    assert "style-src 'unsafe-inline'" in headers["content-security-policy"]
    assert "script" not in headers["content-security-policy"]
    assert _get(app, f"/pack?id={case_id}&snapshot={'0' * 64}")[0] == 404
    assert _get(app, f"/pack?id={case_id}&snapshot=../../case.yaml")[0] == 404


def test_a_change_after_signing_is_shown(app):
    case_id = _imported_case(app)
    _sign(case_id)
    _post(app, "/details", {"id": case_id, "label": "Case one", "business_name": "NEW NAME LTD"})
    view = cases.load(case_id)
    assert view.status == cases.CHANGED
    assert "changed after it was last signed" in _get(app, f"/case?id={case_id}")[2].decode()


def test_a_tampered_pack_is_shown_not_verified(app):
    case_id = _imported_case(app)
    snapshot = _sign(case_id)
    statement = store.case_folder(case_id) / "statements" / "current.csv"
    statement.chmod(0o600)
    statement.write_bytes(statement.read_bytes().replace(b"812.40", b"912.40"))
    page = _get(app, f"/pack?id={case_id}&snapshot={snapshot}")[2].decode()
    assert "<strong>Not verified.</strong>" in page


def test_a_case_with_a_broken_log_is_listed_as_needing_attention(app):
    case_id, _ = _new(app)
    log = store.case_folder(case_id) / "log.jsonl"
    log.chmod(0o600)
    log.write_bytes(log.read_bytes().replace(b"case_created", b"case_createX"))
    assert "Needs attention" in _get(app, "/")[2].decode()
    page = _get(app, f"/case?id={case_id}")[2].decode()
    assert "broken at entry 1" in page and "Import statement" not in page


# --- the memo page -----------------------------------------------------------------------------


def test_the_memo_is_shown_as_a_document_and_escaped():
    html = pages.memo_html("# Title\n\n> Demo <b>\n\n## At a glance\n\nLine one\nline two.\n\n- R1: pass\n- R2 <x>\n\n"
                           "_For the underwriter: left empty._\n")
    assert "Title" not in html
    assert '<p class="memo-note">Demo &lt;b&gt;</p>' in html
    assert "<h3>At a glance</h3><p>Line one line two.</p>" in html
    assert "<ul><li>R1: pass</li><li>R2 &lt;x&gt;</li></ul>" in html
    assert '<p class="memo-empty">For the underwriter: left empty.</p>' in html


def test_cma_app_command_is_listed():
    from typer.testing import CliRunner

    from cma.cli import app as cli

    raw = CliRunner().invoke(cli, ["app", "--help"], env={"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"}).output
    out = " ".join(re.sub(r"\x1b\[[0-9;]*m", "", raw).split())  # Windows consoles get colour codes
    assert "case app" in out and "--port" in out
    assert re.search(r"127\.0\.0\.1", out)
    assert rs.LOOPBACK == "127.0.0.1"


# --- the redesign: filters, search, steps, key figures ------------------------------------------


def test_the_list_filters_by_status_and_searches_by_name(app):
    _new(app, label="Alpha Bakery")
    signed = _imported_case(app)
    _sign(signed)
    page = _get(app, "/")[2].decode()
    assert "Alpha Bakery" in page and "Case one" in page
    assert 'class="stat-n">1</span><span class="stat-label">Need statements' in page
    only_signed = _get(app, "/?status=signed")[2].decode()
    assert "Case one" in only_signed and "Alpha Bakery" not in only_signed
    found = _get(app, "/?q=bakery")[2].decode()
    assert "Alpha Bakery" in found and "Case one" not in found
    assert "No case matches" in _get(app, "/?q=nothing-like-this")[2].decode()
    assert "Alpha Bakery" in _get(app, "/?status=nonsense")[2].decode()  # an unknown filter shows all


def test_a_case_opens_on_the_step_to_do_next(app):
    case_id, _ = _new(app)
    assert 'aria-current="step"' in (page := _get(app, f"/case?id={case_id}")[2].decode())
    assert 'id="d-label"' in page  # details first, before any statement
    _upload(app, {"id": case_id, "bank": "hsbc", "account": "current", "opening_balance": "1000"}, "s.csv", HSBC)
    assert "Open the review" in _get(app, f"/case?id={case_id}")[2].decode()  # details are optional
    _sign(case_id)
    assert "Memo signed by" in _get(app, f"/case?id={case_id}")[2].decode()
    for step, marker in (("details", 'id="d-label"'), ("statements", 'name="bank"'), ("review", "Open the review"),
                         ("memo", "Memo signed by")):
        assert marker in _get(app, f"/case?id={case_id}&step={step}")[2].decode(), step
    assert 'id="d-label"' not in _get(app, f"/case?id={case_id}&step=../etc")[2].decode()


def test_a_case_with_figures_shows_its_key_figures_and_monthly_chart(app):
    case_id, _ = _new(app)
    two_months = b"02/04/2026,CARD ACQUIRER SETTLEMENT,900.00\r\n" + HSBC  # March and April
    _upload(app, {"id": case_id, "bank": "hsbc", "account": "current", "opening_balance": "1000"}, "s.csv", two_months)
    page = _get(app, f"/case?id={case_id}")[2].decode()
    assert 'id="headline"' in page and "Average monthly revenue" in page and '<span class="id">M3</span>' in page
    assert '<svg class="chart"' in page and "Trading receipts" in page
    # The policy outcome is said only inside its labelled panel (P20), here on the review step.
    tiles = page.split('id="headline"', 1)[1].split("</section>", 1)[0]
    assert not re.search(r"\b(pass|refer|fail)\b", tiles, re.I)
    assert "illustrative" in _get(app, f"/case?id={case_id}&step=review")[2].decode().lower()
    empty_id, _ = _new(app)
    assert 'id="headline"' not in _get(app, f"/case?id={empty_id}")[2].decode()


def test_every_page_loads_the_shared_design_system(app):
    status, headers, body = _get(app, "/static/theme.css")
    assert status == 200 and headers["content-type"].startswith("text/css")
    from importlib import resources

    assert body == (resources.files("cma.review") / "static" / "theme.css").read_bytes()
    assert "/static/theme.css?token=" in _get(app, "/")[2].decode()
    assert "/static/theme.css?token=" in (resources.files("cma.review") / "static" / "index.html").read_text()
