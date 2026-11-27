"""The review workspace's server (C5, R33): loopback only, a new random token per launch,
a loopback Host check, bundled assets only, a strict content security policy with no
inline script, no CORS, the next free port, and `cma review` printing the URL with its
token.

Each test sends raw requests to a server running in a thread, so it can set any Host,
Origin or path the browser would never send."""

import http.client
import os
import re
import socket
import subprocess
import sys
import time
from importlib import resources
from pathlib import Path

import pytest

from cma import store
from cma.review import server as rs

pytestmark = pytest.mark.contracts


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    return root


@pytest.fixture
def ws(home):
    case_id = store.new_case(label="r33")
    srv = rs.start(store.case_folder(case_id), case_id, port=0)
    srv.serve_in_thread()
    yield srv
    srv.close()


def _get(srv, path, *, host=None, headers=None, method="GET"):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    try:
        conn.putrequest(method, path, skip_host=True)
        if host is not False:
            conn.putheader("Host", host if host is not None else f"127.0.0.1:{srv.port}")
        for k, v in (headers or {}).items():
            conn.putheader(k, v)
        conn.endheaders()
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        conn.close()


def _auth(srv):
    return {"X-CMA-Token": srv.token}


# --- the token ---------------------------------------------------------------------------


def test_requests_without_the_token_are_refused(ws):
    for path in ("/", "/static/app.js", "/static/app.css", "/api/case", "/nowhere"):
        status, _, body = _get(ws, path)
        assert status == 403, path
        assert ws.token.encode() not in body
        assert b"r33" not in body and ws.case_id.encode() not in body


def test_requests_with_a_wrong_token_are_refused(ws):
    wrong = ws.token[:-1] + ("A" if ws.token[-1] != "A" else "B")
    for path in (f"/?token={wrong}", "/?token=", f"/api/case?token={ws.token[:10]}"):
        assert _get(ws, path)[0] == 403, path
    assert _get(ws, "/api/case", headers={"X-CMA-Token": wrong})[0] == 403
    assert _get(ws, "/api/case", headers={"X-CMA-Token": ""})[0] == 403


def test_the_token_in_the_query_or_the_header_is_accepted(ws):
    status, headers, body = _get(ws, f"/?token={ws.token}")
    assert status == 200 and headers["content-type"].startswith("text/html")
    status, headers, body = _get(ws, "/api/case", headers=_auth(ws))
    assert status == 200 and headers["content-type"].startswith("application/json")
    assert ws.case_id.encode() in body


def test_each_launch_issues_a_new_random_token(home):
    case_id = store.new_case()
    folder = store.case_folder(case_id)
    tokens = set()
    for _ in range(3):
        srv = rs.start(folder, case_id, port=0)
        tokens.add(srv.token)
        assert re.fullmatch(r"[A-Za-z0-9_-]{43,}", srv.token)  # 32 random bytes or more
        srv.close()
    assert len(tokens) == 3


# --- the Host header ---------------------------------------------------------------------


@pytest.mark.parametrize("host", [
    "evil.example", "evil.example:{port}", "127.0.0.1", "127.0.0.1:{other}", "localhost:{port}",
    "127.0.0.2:{port}", "[::1]:{port}", "0.0.0.0:{port}", "127.0.0.1:{port}.evil.example",
    "127.0.0.1:{port}, evil.example", "",
])
def test_a_bad_host_is_refused_even_with_the_token(ws, host):
    host = host.format(port=ws.port, other=ws.port + 1)
    for path in (f"/?token={ws.token}", "/api/case", "/static/app.js"):
        status, _, body = _get(ws, path, host=host, headers=_auth(ws))
        assert status == 403, (host, path)
        assert ws.case_id.encode() not in body


def test_a_missing_or_repeated_host_is_refused(ws):
    assert _get(ws, "/api/case", host=False, headers=_auth(ws))[0] == 403
    two = {"X-CMA-Token": ws.token, "Host": f"127.0.0.1:{ws.port}"}
    assert _get(ws, "/api/case", headers=two)[0] == 403


def test_an_http_1_0_request_with_no_host_is_refused(ws):
    with socket.create_connection(("127.0.0.1", ws.port), timeout=5) as s:
        s.sendall(f"GET /api/case?token={ws.token} HTTP/1.0\r\n\r\n".encode())
        reply = b""
        while chunk := s.recv(4096):
            reply += chunk
    assert reply.split(b"\r\n", 1)[0].split()[1] == b"403"


# --- loopback only, and the port -------------------------------------------------------


def test_listens_on_the_loopback_interface_only(ws):
    assert ws.address == "127.0.0.1"
    assert ws.httpd.socket.getsockname()[0] == "127.0.0.1"
    assert ws.url == f"http://127.0.0.1:{ws.port}/?token={ws.token}"


def test_refuses_to_bind_anything_but_loopback():
    with pytest.raises(ValueError):
        rs.ReviewServer(Path("."), "0" * 16, host="0.0.0.0")


def test_takes_the_next_free_port(home):
    case_id = store.new_case()
    folder = store.case_folder(case_id)
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen()
    start = taken.getsockname()[1]
    try:
        srv = rs.start(folder, case_id, port=start)
        try:
            assert srv.port != start and start < srv.port <= start + rs.PORT_TRIES
        finally:
            srv.close()
    finally:
        taken.close()


def test_says_so_when_no_port_is_free(home, monkeypatch):
    case_id = store.new_case()
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen()
    monkeypatch.setattr(rs, "PORT_TRIES", 1)
    try:
        with pytest.raises(rs.NoFreePort):
            rs.start(store.case_folder(case_id), case_id, port=taken.getsockname()[1])
    finally:
        taken.close()


# --- the content security policy and CORS ------------------------------------------------


def test_every_response_carries_a_strict_csp(ws):
    for path, headers in ((f"/?token={ws.token}", {}), ("/static/app.js", _auth(ws)),
                          ("/api/case", _auth(ws)), ("/api/case", {}), ("/nope", _auth(ws))):
        _, h, _ = _get(ws, path, headers=headers)
        csp = h["content-security-policy"]
        assert "default-src 'none'" in csp
        assert "script-src 'self'" in csp
        assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
        assert "frame-ancestors 'none'" in csp
        assert h["x-content-type-options"] == "nosniff"
        # same-origin, not no-referrer: under no-referrer a browser sends "Origin: null" with a
        # form's POST, and every save from the page would be refused (seen in Chrome).
        assert h["referrer-policy"] == "same-origin"
        assert h["cache-control"] == "no-store"


def test_the_page_has_no_inline_script_or_style(ws):
    _, _, body = _get(ws, f"/?token={ws.token}")
    html = body.decode()
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S | re.I)
    assert scripts, "the page loads its script"
    for attrs, inner in scripts:
        assert "src=" in attrs and inner.strip() == ""
    assert not re.search(r"\son[a-z]+\s*=", html, re.I)
    assert not re.search(r"\sstyle\s*=", html, re.I)
    assert "<style" not in html.lower()
    assert "javascript:" not in html.lower()


def test_no_cors_headers_are_ever_sent(ws):
    origin = {"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"}
    for method in ("OPTIONS", "GET", "POST"):
        _, h, _ = _get(ws, "/api/case", method=method, headers={**_auth(ws), **origin})
        assert not [k for k in h if k.startswith("access-control-")], method


def test_a_cross_origin_request_is_refused(ws):
    for origin in ("http://evil.example", f"http://localhost:{ws.port}", "null"):
        status, _, body = _get(ws, "/api/case", headers={**_auth(ws), "Origin": origin})
        assert status == 403, origin
        assert ws.case_id.encode() not in body
    same = {**_auth(ws), "Origin": f"http://127.0.0.1:{ws.port}"}
    assert _get(ws, "/api/case", headers=same)[0] == 200


def _raw(srv, data: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", srv.port), timeout=5) as s:
        s.sendall(data)
        reply = b""
        while chunk := s.recv(4096):
            reply += chunk
    return reply


def test_an_unread_body_is_never_parsed_as_a_second_request(ws):
    inner = f"GET /api/case?token={ws.token} HTTP/1.1\r\nHost: 127.0.0.1:{ws.port}\r\n\r\n".encode()
    for method in ("POST", "GET"):
        outer = (f"{method} /?token={ws.token} HTTP/1.1\r\nHost: 127.0.0.1:{ws.port}\r\n"
                 f"Content-Length: {len(inner)}\r\n\r\n").encode()
        reply = _raw(ws, outer + inner)  # the server closes the connection after one answer
        assert reply.count(b"HTTP/1.1 ") == 1, method
        assert ws.case_id.encode() not in reply, method


def test_an_idle_connection_is_closed(ws, monkeypatch):
    monkeypatch.setattr(rs._Handler, "timeout", 0.5)
    with socket.create_connection(("127.0.0.1", ws.port), timeout=5) as s:
        s.sendall(b"GET / HTTP/1.1\r\n")  # never finished
        start = time.monotonic()
        assert s.recv(4096) == b""
        assert time.monotonic() - start < 4


def test_methods_other_than_get_are_not_served(ws):
    for method in ("POST", "PUT", "DELETE", "OPTIONS"):
        assert _get(ws, "/api/case", method=method, headers=_auth(ws))[0] == 405


# --- bundled assets only -----------------------------------------------------------------


def test_serves_the_bundled_assets_byte_for_byte(ws):
    static = resources.files("cma.review") / "static"
    for name, kind in (("app.js", "text/javascript"), ("app.css", "text/css")):
        status, h, body = _get(ws, f"/static/{name}", headers=_auth(ws))
        assert status == 200 and h["content-type"].startswith(kind)
        assert body == (static / name).read_bytes()


@pytest.mark.parametrize("path", [
    "/static/../server.py", "/static/%2e%2e/server.py", "/static/..%2fserver.py",
    "/static/index.html", "/static/", "/static", "/static/app.js/", "/static//app.js",
    "/etc/passwd", "/../../etc/passwd", "/static/APP.JS", "/server.py", "/favicon.ico",
    "/static/app.js%00", "http://evil.example/static/app.js",
])
def test_nothing_but_the_bundled_assets_is_served(ws, path):
    status, _, body = _get(ws, path, headers=_auth(ws))
    assert status == 404, path
    assert b"import" not in body


def test_the_bundled_assets_reference_nothing_outside_the_package(ws):
    static = resources.files("cma.review") / "static"
    for entry in static.iterdir():
        text = entry.read_text("utf-8")
        assert not re.search(r"(https?:)?//[a-z0-9.-]+\.[a-z]", text, re.I), entry.name
        assert "@import" not in text


def test_the_request_log_never_holds_the_token(ws, capfd):
    _get(ws, f"/?token={ws.token}")
    _get(ws, f"/?token={ws.token}x")
    out, err = capfd.readouterr()
    assert ws.token not in out + err


# --- `cma review` ------------------------------------------------------------------------


def test_cma_review_prints_the_url_with_its_token_and_serves_it(home, tmp_path):
    case_id = store.new_case()
    env = {**os.environ, "CMA_HOME": str(home), "PYTHONUNBUFFERED": "1",
           "PYTHONPATH": str(Path(rs.__file__).resolve().parents[2])}
    proc = subprocess.Popen([sys.executable, "-m", "cma.cli", "review", case_id, "--no-browser"],
                            cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    try:
        url = None
        deadline = time.monotonic() + 30
        lines = []
        while url is None and time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line)
            m = re.search(r"http://127\.0\.0\.1:(\d+)/\?token=([A-Za-z0-9_-]+)", line)
            if m:
                url = m
        assert url, "".join(lines)
        port, token = int(url.group(1)), url.group(2)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", f"/?token={token}")
        assert conn.getresponse().status == 200
        conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/case")
        assert conn.getresponse().status == 403
        conn.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_cma_review_refuses_an_unknown_case(home):
    from typer.testing import CliRunner

    from cma.cli import app

    result = CliRunner().invoke(app, ["review", "0123456789abcdef", "--no-browser"])
    assert result.exit_code == 2
    assert "no case" in result.output


def test_the_port_is_claimed_alone_where_the_platform_allows_it(monkeypatch):
    """R33: on Windows a socket bound without SO_EXCLUSIVEADDRUSE can be bound again by another
    program, which would take requests meant for the workspace; the server claims its port
    alone wherever the platform has the option."""
    import socket as socketmod

    from cma.review import server

    calls = []

    class Sock:
        def setsockopt(self, *args):
            calls.append(args)

    monkeypatch.setattr(socketmod, "SO_EXCLUSIVEADDRUSE", 0x7FFF_FFFB, raising=False)
    server._claim_port_alone(Sock())
    assert calls == [(socketmod.SOL_SOCKET, 0x7FFF_FFFB, 1)]
    monkeypatch.delattr(socketmod, "SO_EXCLUSIVEADDRUSE")
    calls.clear()
    server._claim_port_alone(Sock())
    assert calls == []
    # Here (no such option), a server still binds and serves as before.
    srv = server._bind(0)
    try:
        assert srv.server_address[0] == server.LOOPBACK
    finally:
        srv.server_close()
