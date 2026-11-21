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
renders; it computes no money. Its request log is off, so the token never reaches a log.
Refusals say only that the request was refused, never what the case holds.
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
from urllib.parse import parse_qs, urlsplit

LOOPBACK = "127.0.0.1"
DEFAULT_PORT = 8765
PORT_TRIES = 50  # ports tried from the one given, before giving up
TOKEN_BYTES = 32
TOKEN_HEADER = "X-CMA-Token"
TOKEN_MARK = "__CMA_TOKEN__"  # where the page names its token, filled in when it is served

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
       "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",  # the token is in the URL; no page is told it
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}
TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
INDEX = "index.html"
REFUSED = b"Refused: this workspace answers only the URL it printed when it started.\n"


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
        # No request body is ever read, so a request that carries one, or that is not a GET,
        # ends its connection: its unread body is never parsed as a further request.
        if (self.command != "GET" or self.headers.get("Content-Length")
                or self.headers.get("Transfer-Encoding")):
            self.close_connection = True
        # Only a path is served: an absolute URL or //host names somewhere else.
        target = self.path
        parts = urlsplit(target) if target.startswith("/") and not target.startswith("//") else None
        if not (self._host_ok() and self._origin_ok() and self._token_ok(parts.query if parts else "")):
            self._send(403, REFUSED)
            return
        if self.command != "GET":
            self.send_response(405)
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.send_header("Allow", "GET")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        path = parts.path if parts else None
        if path == "/":
            self._send(200, self.server.page, "text/html; charset=utf-8")
        elif path == "/api/case":
            self._send(200, self.server.case_json(), "application/json")
        elif path in self.server.assets:
            body, kind = self.server.assets[path]
            self._send(200, body, kind)
        else:
            self._send(404, b"Not found.\n")


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
        self.httpd.page = template.replace(TOKEN_MARK.encode(), self.token.encode())
        self.httpd.assets = assets
        self.httpd.case_json = self._case_json
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{LOOPBACK}:{self.port}/?token={self.token}"

    def _case_json(self) -> bytes:
        from .. import store

        try:
            label = store.load_profile(self.folder).get("label")
        except (OSError, store.CaseStoreError):
            label = None
        return json.dumps({"case": self.case_id, "label": label}).encode()

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
