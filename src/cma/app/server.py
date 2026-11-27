"""The case app's server (`cma app`).

It keeps the review workspace's rules (review/server.py): it listens on 127.0.0.1 only, on
the next free port from the one given; it issues a new random token at each start and
refuses any request without it; it refuses a request whose Host is not 127.0.0.1 and its own
port, or whose Origin is not its own; it serves only the stylesheet bundled with it, under a
content security policy with no script at all; its request log is off.

Pages are GET; the four writes are POST: /new (make a case), /details (its profile),
/import (one statement, uploaded as a form file) and /review (open the review workspace for
the case, then go to it). After a write the browser is sent back to the case page, with what
happened said once at the top.

Opening the review starts the case's review workspace on its own port with its own token,
once per case while the app runs; every page of it links back to the case here.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import threading
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlsplit

from .. import store
from ..review import server as review_server
from . import actions, cases, pages

DEFAULT_PORT = 8750
WORKSPACE_PORT = 8766  # the first port tried for a case's review workspace
CSP = ("default-src 'none'; style-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self' "
       "http://127.0.0.1:*; frame-ancestors 'none'")
HEADERS = {**review_server.SECURITY_HEADERS, "Content-Security-Policy": CSP}
FORM = "application/x-www-form-urlencoded"
MULTIPART = "multipart/form-data"
MAX_FORM = 64 * 1024
MAX_MULTIPART = actions.MAX_UPLOAD + 256 * 1024
REFUSED = b"Refused: this page answers only the address the app opened when it started.\n"
SNAPSHOT = re.compile(r"[0-9a-f]{64}")
WRITES = frozenset({"/new", "/details", "/import", "/review"})  # POST
POST_ONLY = WRITES - {"/new"}  # /new is also the new-case form, by GET


def _css() -> bytes:
    return (resources.files("cma.app") / "static" / "app.css").read_bytes()


class _Handler(BaseHTTPRequestHandler):
    server_version = "cma-app"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 60

    def log_message(self, format, *args):  # the request line holds the token
        return

    def do_GET(self):
        self._handle()

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = do_GET

    # --- answering ----------------------------------------------------------------------------

    def _send(self, status: int, body: bytes, kind: str = "text/html; charset=utf-8",
              headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for key, value in (headers or HEADERS).items():
            self.send_header(key, value)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        for key, value in HEADERS.items():
            self.send_header(key, value)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # --- the checks every request passes -----------------------------------------------------

    def _host_ok(self) -> bool:
        hosts = self.headers.get_all("Host") or []
        return len(hosts) == 1 and hosts[0] == f"{review_server.LOOPBACK}:{self.server.server_port}"

    def _origin_ok(self) -> bool:
        origins = self.headers.get_all("Origin") or []
        return all(o == f"http://{review_server.LOOPBACK}:{self.server.server_port}" for o in origins)

    def _token_ok(self, query: str) -> bool:
        given = parse_qs(query, keep_blank_values=True).get("token", [])
        if len(given) != 1:
            return False
        return hmac.compare_digest(given[0].encode("utf-8", "surrogateescape"), self.server.app.token.encode())

    def _handle(self) -> None:
        if self.command != "GET" or self.headers.get("Content-Length") or self.headers.get("Transfer-Encoding"):
            self.close_connection = True
        target = self.path
        parts = urlsplit(target) if target.startswith("/") and not target.startswith("//") else None
        if not (parts and self._host_ok() and self._origin_ok() and self._token_ok(parts.query)):
            self._send(403, REFUSED, "text/plain; charset=utf-8")
            return
        path = parts.path
        query = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items() if len(v) == 1}
        write = self.command == "POST" and path in WRITES
        if not write and (self.command not in ("GET", "HEAD") or path in POST_ONLY):
            self.send_response(405)
            for key, value in HEADERS.items():
                self.send_header(key, value)
            self.send_header("Allow", "POST" if path in POST_ONLY else ("GET, POST" if path in WRITES else "GET"))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        app = self.server.app
        try:
            if write:
                self._write(path)
            elif path == "/static/app.css":
                self._send(200, app.css, "text/css; charset=utf-8")
            elif path == "/":
                notice, error = app.flash(query.get("flash"))
                self._send(200, pages.cases_page(app.token, cases.load_all(), notice=notice, error=error))
            elif path == "/new":
                self._send(200, pages.new_page(app.token))
            elif path == "/case":
                view = cases.load(query.get("id", ""))
                notice, error = app.flash(query.get("flash"))
                self._send(200, pages.case_page(app.token, view, notice=notice, error=error))
            elif path in ("/pack", "/pack/record"):
                self._pack(path, query)
            else:
                self._send(404, pages.message_page(app.token, "Not found", "There is no such page."))
        except store.CaseStoreError:
            self._send(404, pages.message_page(app.token, "No such case",
                                               "That case is not under this computer's case folder."))
        except Exception:  # a failure no refusal foresees is answered, naming nothing the case holds
            self._send(500, pages.message_page(app.token, "Something went wrong",
                                               "The page could not be shown. Nothing was changed."))

    # --- writes -------------------------------------------------------------------------------

    def _body(self, limit: int) -> bytes | None:
        self.close_connection = True
        if self.headers.get("Transfer-Encoding"):
            self._send(411, b"Send the body with its Content-Length.\n", "text/plain; charset=utf-8")
            return None
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1 or not lengths[0].isdigit():
            self._send(411, b"Send the body with its Content-Length.\n", "text/plain; charset=utf-8")
            return None
        n = int(lengths[0])
        if n > limit:
            self._send(413, pages.message_page(self.server.app.token, "Too large",
                                               "The file is too large to be a statement (more than 25 MB)."))
            return None
        return self.rfile.read(n)

    def _fields(self) -> tuple[dict[str, str], tuple[str, bytes] | None] | None:
        """The form's text fields and, for an upload, its one file (name, bytes)."""
        kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if kind == FORM:
            raw = self._body(MAX_FORM)
            if raw is None:
                return None
            try:
                pairs = parse_qsl(raw.decode("utf-8"), keep_blank_values=True)
            except UnicodeDecodeError:
                pairs = []
            return dict(pairs), None
        if kind == MULTIPART:
            raw = self._body(MAX_MULTIPART)
            if raw is None:
                return None
            head = f"Content-Type: {self.headers.get('Content-Type')}\r\n\r\n".encode("latin-1", "replace")
            message = BytesParser(policy=HTTP).parsebytes(head + raw)
            fields: dict[str, str] = {}
            upload: tuple[str, bytes] | None = None
            for part in message.iter_parts() if message.is_multipart() else []:
                name = part.get_param("name", header="content-disposition")
                payload = part.get_payload(decode=True) or b""
                filename = part.get_filename()
                if filename is not None:
                    if upload is None and name == "file":
                        upload = (filename, payload)
                elif isinstance(name, str):
                    fields[name] = payload.decode("utf-8", "replace")
            return fields, upload
        self._send(415, b"Send a form.\n", "text/plain; charset=utf-8")
        return None

    def _write(self, path: str) -> None:
        app = self.server.app
        got = self._fields()
        if got is None:
            return
        fields, upload = got
        case_id = fields.get("id", "")
        try:
            if path == "/new":
                done, warning = actions.new_case(fields)
                self._redirect(app.case_url(done.case_id, notice=done.message, error=warning or ""))
            elif path == "/details":
                done = actions.save_details(case_id, fields)
                self._redirect(app.case_url(case_id, notice=done.message, anchor="details"))
            elif path == "/import":
                name, data = upload if upload else ("", b"")
                done = actions.import_statement(case_id, fields, name, data)
                self._redirect(app.case_url(case_id, notice=done.message, anchor="statements"))
            elif path == "/review":
                cases.load(case_id)  # refuses an unknown case before anything starts
                self._redirect(app.open_review(case_id))
        except Exception as e:
            text = actions.explain(e)
            if path == "/new":
                self._send(400, pages.new_page(app.token, error=text, fields=fields))
            elif isinstance(e, store.CaseStoreError) and not cases.case_exists(case_id):
                self._send(404, pages.message_page(app.token, "No such case",
                                                   "That case is not under this computer's case folder."))
            else:
                anchor = {"/details": "details", "/import": "statements"}.get(path, "review")
                self._redirect(app.case_url(case_id, error=text, anchor=anchor))

    # --- signed packs ------------------------------------------------------------------------

    def _pack(self, path: str, query: dict[str, str]) -> None:
        app = self.server.app
        view = cases.load(query.get("id", ""))
        snapshot = query.get("snapshot", "")
        folder = cases.pack_folder(view, snapshot) if SNAPSHOT.fullmatch(snapshot) else None
        if folder is None:
            self._send(404, pages.message_page(app.token, "No such memo", "This case has no such signed memo."))
            return
        if path == "/pack/record":
            from .. import record

            # The record is a page of its own, with its own inline style and no script.
            body = (folder / "record.html").read_bytes()
            self._send(200, body, headers={**review_server.SECURITY_HEADERS, "Content-Security-Policy": record.CSP})
            return
        pack = next(p for p in view.packs if p["snapshot"] == snapshot)
        memo = (folder / "memo.md").read_text(encoding="utf-8")
        self._send(200, pages.pack_page(app.token, view, pack, memo, _verified(folder, view.folder), str(folder)))


def _verified(pack: Path, case_folder: Path) -> tuple[bool, str]:
    """Whether the pack's figures recompute from the case's own inputs (cma verify)."""
    from .. import verify

    try:
        record = json.loads((pack / "evidence.json").read_text(encoding="utf-8"))
        result = verify.verify(record, case_folder=case_folder)
    except verify.VerifyRefused as e:
        return False, f"The record could not be checked: {e}."
    except Exception as e:
        return False, f"The record could not be checked ({type(e).__name__})."
    if result.mismatches:
        return False, (f"{len(result.mismatches)} figure(s) no longer match the case's statements: "
                       + "; ".join(m.where for m in result.mismatches[:5]) + ".")
    n = sum(result.checked.values())
    return True, f"All {n} figures and cells recomputed from the case's statements and matched; nothing changed."


class CaseApp:
    """The app: bound on the loopback interface, not yet serving. Build it with start()."""

    def __init__(self, *, port: int = DEFAULT_PORT):
        self.token = secrets.token_urlsafe(review_server.TOKEN_BYTES)
        self.css = _css()
        self.httpd = review_server._bind(port, _Handler)
        self.httpd.app = self
        self.port = self.httpd.server_port
        self._flashes: dict[str, tuple[str, str]] = {}
        self._workspaces: dict[str, review_server.ReviewServer] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{review_server.LOOPBACK}:{self.port}/?token={self.token}"

    def case_url(self, case_id: str, *, notice: str = "", error: str = "", anchor: str = "") -> str:
        params = {"id": case_id}
        if notice or error:
            key = secrets.token_hex(8)
            with self._lock:
                self._flashes[key] = (notice, error)
            params["flash"] = key
        return pages.url(self.token, "/case", **params) + (f"#{anchor}" if anchor else "")

    def flash(self, key: str | None) -> tuple[str, str]:
        """What a write said, shown once."""
        with self._lock:
            return self._flashes.pop(key, ("", "")) if key else ("", "")

    def open_review(self, case_id: str) -> str:
        """The URL of the case's review workspace, started the first time it is asked for."""
        with self._lock:
            ws = self._workspaces.get(case_id)
            if ws is None:
                home = f"http://{review_server.LOOPBACK}:{self.port}{pages.url(self.token, '/case', id=case_id)}"
                ws = review_server.start(store.case_folder(case_id), case_id, port=WORKSPACE_PORT, home_url=home)
                ws.serve_in_thread()
                self._workspaces[case_id] = ws
            return ws.url

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def serve_in_thread(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        with self._lock:
            for ws in self._workspaces.values():
                ws.close()
            self._workspaces.clear()
        if self._thread is not None:
            self.httpd.shutdown()
            self._thread.join()
        self.httpd.server_close()


def start(*, port: int = DEFAULT_PORT) -> CaseApp:
    return CaseApp(port=port)
