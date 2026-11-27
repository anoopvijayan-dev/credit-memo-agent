"""The review workspace's server (C5, R33).

`cma review CASE` starts one of these for one case. It:

- listens on the loopback interface only (127.0.0.1), never on another address;
- issues a new random session token at each launch, and refuses every request that does
  not carry it, in the `token` query parameter or the X-CMA-Token header;
- refuses every request whose Host header is not exactly 127.0.0.1 and its own port, so a
  page whose name was rebound to 127.0.0.1 (DNS rebinding) reaches nothing;
- refuses a request whose Origin is not its own, and sends no CORS header at all;
- serves only the assets bundled in the package (an allow-list built at start-up, matched
  against the request path exactly), under a strict content security policy with no
  inline script, no inline style and nothing loaded from anywhere but itself;
- takes the next free port from the one it is given, and prints its URL with the token.

It is one process that serves requests on threads and never forks (R42's scope). It
renders what the figures layer computed; it computes no money. Its request log is off, so the token never reaches a log.
Refusals say only that the request was refused, never what the case holds.

Every page and data path is GET only. The override form and the stale overrides' forms (R17,
R19; edit.py), the preview and sign-off forms (R22, R26; signoff.py) and the firm-rule
promotion form (R20; promote.py) are the only writes: POST to /override, /stale, /preview,
/sign and /promote from the page, or to /api/override, /api/stale, /api/preview, /api/sign
and /api/promote for data. The comparison entry (R51; compare.py) is posted the same way, to
/compare or /api/compare, and writes nothing. Every POST carries the token and the workspace's own Origin,
a body of at most MAX_BODY bytes with its Content-Length (never chunked), and a form or JSON
content type.
"""

from __future__ import annotations

import errno
import hmac
import json
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlsplit

LOOPBACK = "127.0.0.1"
DEFAULT_PORT = 8765
PORT_TRIES = 50  # ports tried from the one given, before giving up
TOKEN_BYTES = 32
TOKEN_HEADER = "X-CMA-Token"
TOKEN_MARK = "__CMA_TOKEN__"  # where the page names its token, filled in when it is served
SUMMARY_MARK = "__CMA_SUMMARY__"  # where the page holds the case summary, rendered per request

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
       "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    # The token is in the URL, so no other site is ever told it. Not "no-referrer": under it a
    # browser sends "Origin: null" with a form's POST, and the origin check refuses every save.
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}
TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
INDEX = "index.html"
REFUSED = b"Refused: this workspace answers only the URL it printed when it started.\n"
WRITES = {"/override": ("override", "page"), "/stale": ("stale", "page"),
          "/api/override": ("override", "data"), "/api/stale": ("stale", "data"),
          "/preview": ("preview", "page"), "/sign": ("sign", "page"),
          "/api/preview": ("preview", "data"), "/api/sign": ("sign", "data"),
          "/promote": ("promote", "page"), "/api/promote": ("promote", "data"),
          "/compare": ("compare", "page"), "/api/compare": ("compare", "data")}
PACK_WRITES = frozenset({"preview", "sign"})
PAGE_WRITES = PACK_WRITES | {"promote", "compare"}  # answered with a page of their own, never a redirect
MAX_BODY = 16 * 1024  # bytes: a form or JSON body is far smaller
FORM = "application/x-www-form-urlencoded"


class NoFreePort(OSError):
    """No loopback port was free in the range tried."""


def _bundled() -> tuple[bytes, dict[str, tuple[bytes, str]]]:
    """The page template and every other bundled asset, by the exact path it is served at.
    Read once, from the package, so nothing outside it can ever be served."""
    static = resources.files("cma.review") / "static"
    page = (static / INDEX).read_bytes()
    assets = {}
    for entry in static.iterdir():
        suffix = Path(entry.name).suffix
        if entry.is_file() and entry.name != INDEX and suffix in TYPES:
            assets[f"/static/{entry.name}"] = (entry.read_bytes(), TYPES[suffix])
    return page, assets


class _Handler(BaseHTTPRequestHandler):
    server_version = "cma-review"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    server: _HTTPServer
    # A connection that sends nothing for this long is closed, so an idle or half-sent
    # request never holds a thread for good.
    timeout = 30

    def log_message(self, format, *args):  # the request line holds the token
        return

    # Every method goes through the same checks; only GET is served.
    def do_GET(self):
        self._handle()

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = do_GET

    def _send(self, status: int, body: bytes, kind: str = "text/plain; charset=utf-8") -> None:
        self.send_response(status)
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        if self.command == "HEAD":
            self.end_headers()
            return
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self) -> bool:
        hosts = self.headers.get_all("Host") or []
        return len(hosts) == 1 and hosts[0] == f"{LOOPBACK}:{self.server.server_port}"

    def _origin_ok(self) -> bool:
        origins = self.headers.get_all("Origin") or []
        return all(o == f"http://{LOOPBACK}:{self.server.server_port}" for o in origins)

    def _token_ok(self, query: str) -> bool:
        given = parse_qs(query, keep_blank_values=True).get("token", [])
        given += self.headers.get_all(TOKEN_HEADER) or []
        if len(given) != 1:
            return False
        want = self.server.token.encode()
        return hmac.compare_digest(given[0].encode("utf-8", "surrogateescape"), want)

    def _handle(self) -> None:
        # A request that carries a body, or that is not a GET, ends its connection: a body is
        # read only by a write path (_body, at most MAX_BODY bytes), and an unread one is
        # never parsed as a further request.
        if (self.command != "GET" or self.headers.get("Content-Length")
                or self.headers.get("Transfer-Encoding")):
            self.close_connection = True
        # Only a path is served: an absolute URL or //host names somewhere else.
        target = self.path
        parts = urlsplit(target) if target.startswith("/") and not target.startswith("//") else None
        if not (self._host_ok() and self._origin_ok() and self._token_ok(parts.query if parts else "")):
            self._send(403, REFUSED)
            return
        path = parts.path if parts else None
        allowed = "POST" if path in WRITES else "GET"
        if self.command != allowed:
            self.send_response(405)
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.send_header("Allow", allowed)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path in WRITES:
            self._write(*WRITES[path])
            return
        if path in ("/lines", "/api/lines"):
            self._lines(path, parts.query)
        elif path in ("/", "/api/case"):
            # The summary is computed per request; a failure no refusal foresees is answered,
            # naming nothing the case holds, rather than dropping the connection unanswered.
            try:
                if path == "/":
                    body, kind = self.server.page_html(), "text/html; charset=utf-8"
                else:
                    body, kind = self.server.case_json(), "application/json"
            except Exception:
                self._send(500, b"The case could not be shown.\n")
                return
            self._send(200, body, kind)
        elif path in self.server.assets:
            body, kind = self.server.assets[path]
            self._send(200, body, kind)
        else:
            self._send(404, b"Not found.\n")


    def _body(self) -> dict | None:
        """The request's fields, or None once a refusal has been sent. The connection is
        closed after every write, so nothing past the body is ever read as a request."""
        self.close_connection = True
        if self.headers.get("Transfer-Encoding"):
            self._send(411, b"Send the body with its Content-Length.\n")
            return None
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1 or not lengths[0].isdigit():
            self._send(411, b"Send the body with its Content-Length.\n")
            return None
        n = int(lengths[0])
        if n > MAX_BODY:
            self._send(413, b"The request body is too large.\n")
            return None
        kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        raw = self.rfile.read(n)
        try:
            text = raw.decode("utf-8")
            if kind == "application/json":
                fields = json.loads(text)
                if not isinstance(fields, dict):
                    raise ValueError
            elif kind == FORM:
                pairs = parse_qsl(text, keep_blank_values=True, strict_parsing=bool(text))
                fields = dict(pairs)
                if len(fields) != len(pairs):
                    raise ValueError
            else:
                self._send(415, b"Send a form or JSON body.\n")
                return None
        except ValueError:  # UnicodeDecodeError and JSONDecodeError among them
            self._send(400, b"The request body could not be read.\n")
            return None
        return fields

    def _write(self, kind: str, shape: str) -> None:
        """Save one override or resolve one stale override (edit.answer). Data paths answer
        JSON; page paths go back to the page the form was on once saved (303), or show why
        nothing was saved, with a link to reload the case."""
        from . import edit

        fields = self._body()
        if fields is None:
            return
        try:
            status, reply = self.server.save(kind, fields, shape == "data")
        except Exception:  # a comparison saves nothing, so its failure says so (R51)
            status, reply = 500, {"error": "The figure could not be compared." if kind == "compare"
                                  else "The change could not be saved."}
        if shape == "data":
            self._send(status, json.dumps(reply, ensure_ascii=False).encode("utf-8"), "application/json")
        elif kind in PAGE_WRITES:
            self._send(status, self.server.pack_html(kind, status, reply, fields), "text/html; charset=utf-8")
        elif status == 200:
            self.send_response(303)
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.send_header("Location", edit.back_url(fields.get("back"), self.server.token))
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._send(status, self.server.outcome_html(status, reply), "text/html; charset=utf-8")

    def _lines(self, path: str, query: str) -> None:
        """The lines behind one figure or monthly cell (R16): as data at /api/lines, as the
        page at /lines. A request that names no figure or cell of the case is answered 400,
        naming only what was asked."""
        from .drilldown import DrillError

        q = parse_qs(query, keep_blank_values=True)
        args = {}
        for key in ("account", "figure", "month", "cell"):
            given = q.get(key, [])
            if len(given) > 1:
                self._send(400, f"Give {key} once.\n".encode())
                return
            args[key] = given[0] if given else None
        try:
            if path == "/lines":
                body, kind = self.server.lines_html(**args), "text/html; charset=utf-8"
            else:
                body, kind = self.server.lines_json(**args), "application/json"
        except DrillError as e:
            self._send(400, f"{e}.\n".encode())
            return
        except Exception:
            self._send(500, b"The lines could not be shown.\n")
            return
        self._send(200, body, kind)


class _HTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False  # never share a port another listener holds
    daemon_threads = True

    def server_bind(self) -> None:
        # On Windows a port bound without SO_EXCLUSIVEADDRUSE can be bound again by another
        # program on this machine, which would then take requests meant for the workspace,
        # its token among them. Elsewhere a second listener needs both sockets to agree.
        _claim_port_alone(self.socket)
        super().server_bind()


def _claim_port_alone(sock) -> None:
    option = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
    if option is not None:  # Windows only
        sock.setsockopt(socket.SOL_SOCKET, option, 1)


class ReviewServer:
    """One case's workspace server. Build it with start(); it is bound but not yet serving."""

    def __init__(self, folder: Path, case_id: str, *, host: str = LOOPBACK, port: int = DEFAULT_PORT):
        if host != LOOPBACK:
            raise ValueError(f"the review workspace listens on {LOOPBACK} only")
        self.folder = Path(folder)
        self.case_id = case_id
        self.address = LOOPBACK
        self.token = secrets.token_urlsafe(TOKEN_BYTES)
        template, assets = _bundled()
        self.httpd = _bind(port)
        self.port = self.httpd.server_port
        self.httpd.token = self.token
        self._page = template.replace(TOKEN_MARK.encode(), self.token.encode())
        self.httpd.assets = assets
        self.httpd.case_json = self._case_json
        self.httpd.page_html = self._page_html
        self.httpd.lines_json = self._lines_json
        self.httpd.lines_html = self._lines_html
        self.httpd.save = self._save
        self.httpd.outcome_html = self._outcome_html
        self.httpd.pack_html = self._pack_html
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{LOOPBACK}:{self.port}/?token={self.token}"

    def _shell(self, html: str) -> bytes:
        """The page with `html` as its main element, a demo case's banner first on every page (R48)."""
        from . import summary

        html = summary.demo_banner(summary.is_demo(self.folder)) + html
        return self._page.replace(SUMMARY_MARK.encode(), html.encode("utf-8"))

    def _case_json(self) -> bytes:
        """The case summary as data (R3): computed afresh, through the figures layer."""
        from . import summary

        return json.dumps(summary.case_summary(self.folder, self.case_id), ensure_ascii=False).encode("utf-8")

    def _page_html(self) -> bytes:
        """The page with the case summary rendered into it; every case text escaped (R34)."""
        from . import compare, summary

        s = summary.case_summary(self.folder, self.case_id)
        html = summary.render(s, token=self.token) + compare.form_html(s, self.token)  # R51
        return self._shell(html)

    def _lines_json(self, **target) -> bytes:
        """The lines behind one figure or monthly cell, as data (R16)."""
        from . import drilldown

        d = drilldown.lines_behind(self.folder, self.case_id, **target)
        return json.dumps(d, ensure_ascii=False).encode("utf-8")

    def _lines_html(self, **target) -> bytes:
        """The page with the lines behind one figure or monthly cell; every case text escaped (R34)."""
        from . import drilldown

        html = drilldown.render(drilldown.lines_behind(self.folder, self.case_id, **target), self.token)
        return self._shell(html)

    def _save(self, kind: str, fields: dict, recompute: bool = True) -> tuple[int, dict]:
        """One write from the workspace: its status and reply, the recomputed case included for
        a data path (R17, R52); a page form is sent back to the page, which is built afresh."""
        from . import compare, edit, promote, signoff

        if kind == "compare":  # nothing is saved: the figures layer compares (R51)
            return compare.answer(self.folder, self.case_id, fields)
        if kind == "promote":
            return promote.answer(self.folder, self.case_id, fields)
        if kind in PACK_WRITES:
            return signoff.answer(self.folder, self.case_id, kind, fields)
        return edit.answer(self.folder, self.case_id, kind, fields, recompute=recompute)

    def _pack_html(self, kind: str, status: int, reply: dict, fields: dict | None = None) -> bytes:
        from . import compare, promote, signoff

        if kind == "compare":
            html = compare.html(status, reply, self.token)
        elif kind == "promote":
            html = promote.html(status, reply, self.token, fields or {})
        else:
            html = signoff.html(kind, status, reply, self.token)
        return self._shell(html)

    def _outcome_html(self, status: int, reply: dict) -> bytes:
        from . import edit

        return self._shell(edit.outcome_html(status, reply, self.token))

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def serve_in_thread(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._thread is not None:
            self.httpd.shutdown()
            self._thread.join()
        self.httpd.server_close()


def _in_use(e: OSError) -> bool:
    # EACCES: Windows reports a port held exclusively by another process this way.
    return e.errno in (errno.EADDRINUSE, errno.EACCES) or getattr(e, "winerror", None) in (10048, 10013)


def _bind(port: int) -> _HTTPServer:
    """A server bound on the loopback interface at `port` or the next free port after it;
    port 0 lets the operating system choose."""
    tries = 1 if port == 0 else PORT_TRIES
    for candidate in range(port, port + tries):
        if candidate > 65535:
            break
        try:
            return _HTTPServer((LOOPBACK, candidate), _Handler)
        except OSError as e:
            if not _in_use(e):
                raise
    raise NoFreePort(errno.EADDRINUSE, f"no free loopback port from {port} to {port + tries - 1}")


def start(folder: Path, case_id: str, *, port: int = DEFAULT_PORT) -> ReviewServer:
    """Bind the workspace for a case, with a new token; call serve_forever() to serve."""
    return ReviewServer(folder, case_id, port=port)
