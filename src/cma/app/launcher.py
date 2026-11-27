"""The Windows download's entry point (CreditMemo.exe), and the same for anyone who runs
`python -m cma.app.launcher`. It starts the case app and opens it in the browser.

Cases are kept under CMA_HOME when it is set; otherwise, for the packaged app, in the
user's own local application data folder (on Windows %LOCALAPPDATA%\\Credit Memo), which is
private to the user and not a folder cloud-sync clients upload. `--self-test` starts the app
on a free port with a throwaway case folder, makes the demo case through the page as a
browser would, checks its pages, and exits: the release build runs it on the built .exe."""

from __future__ import annotations

import http.client
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

APP_FOLDER = "Credit Memo"


def default_home() -> Path:
    """Where the packaged app keeps cases when CMA_HOME is not set."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_FOLDER


def _request(port: int, token: str, method: str, path: str, body: bytes = b"") -> tuple[int, dict, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    try:
        sep = "&" if "?" in path else "?"
        conn.putrequest(method, f"{path}{sep}{urlencode({'token': token})}", skip_host=True)
        conn.putheader("Host", f"127.0.0.1:{port}")
        if method == "POST":
            conn.putheader("Origin", f"http://127.0.0.1:{port}")
            conn.putheader("Content-Type", "application/x-www-form-urlencoded")
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        if body:
            conn.send(body)
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        conn.close()


def self_test() -> int:
    """Start the app with a throwaway case folder, make the demo case and read its pages."""
    from .server import start

    before = os.environ.get("CMA_HOME")
    with tempfile.TemporaryDirectory(prefix="cma-selftest-") as home:
        os.environ["CMA_HOME"] = str(Path(home) / "cases")
        try:
            checks = _self_checks(start)
        finally:
            if before is None:
                os.environ.pop("CMA_HOME", None)
            else:
                os.environ["CMA_HOME"] = before
    for name, ok in checks:
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    return 0 if all(ok for _n, ok in checks) else 1


def _self_checks(start) -> list[tuple[str, bool]]:
    """Start the app, make the demo case through the page and read its pages."""
    app = start(port=0)
    app.serve_in_thread()
    try:
        checks = []
        status, _h, body = _request(app.port, app.token, "GET", "/")
        checks.append(("case list", status == 200 and b"Try with demo data" in body))
        status, _h, body = _request(app.port, app.token, "GET", "/static/theme.css")
        checks.append(("stylesheet", status == 200 and b"--brand" in body))
        status, headers, _b = _request(app.port, app.token, "POST", "/demo")
        location = headers.get("location", "")
        case_id = parse_qs(urlsplit(location).query).get("id", [""])[0]
        checks.append(("demo case made", status == 303 and bool(case_id)))
        status, _h, body = _request(app.port, app.token, "GET", f"/case?id={case_id}")
        checks.append(("key figures", status == 200 and b'id="headline"' in body and b"All accounts" in body))
        status, _h, body = _request(app.port, app.token, "GET", f"/case?id={case_id}&step=review")
        checks.append(("policy check", status == 200 and b"illustrative" in body.lower()))
        status, headers, _b = _request(app.port, app.token, "POST", "/review", urlencode({"id": case_id}).encode())
        checks.append(("review opens", status == 303 and headers.get("location", "").startswith("http://127.0.0.1:")))
    finally:
        app.close()
    return checks


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in args:
        return self_test()
    if not os.environ.get("CMA_HOME") and getattr(sys, "frozen", False):
        os.environ["CMA_HOME"] = str(default_home())
    port = 8750
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    browser = "--no-browser" not in args

    from .. import firm, store
    from ..review import server as review_server
    from .server import start

    try:
        store.check_not_in_git(Path(os.environ.get("CMA_HOME") or firm.case_root()))
        app = start(port=port)
    except (store.CaseStoreError, review_server.NoFreePort) as e:
        print(f"The case app could not start: {e}")
        if getattr(sys, "frozen", False):
            input("Press Enter to close this window.")
        return 2
    print("Credit Memo is running on this computer only.")
    print(f"Cases are kept in {os.environ.get('CMA_HOME') or firm.case_root()}")
    print("If the browser does not open, paste this address into it:")
    print(app.url)
    print("Keep this window open while you work. Close it to stop Credit Memo.")
    if browser:
        import webbrowser
        from contextlib import suppress

        with suppress(Exception):  # no browser is fine; the address is printed
            webbrowser.open(app.url)
    try:
        app.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
