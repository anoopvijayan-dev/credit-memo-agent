"""MCP server exposing the deterministic layers as tools.

Lets any MCP client (Claude Desktop, an n8n agent node, another LangGraph graph) ask
for metrics or a policy result without ever touching the arithmetic itself.

Run:  python -m cma.mcp_server

Files: tools only read files under CMA_DATA_DIR, which must be set (MCP hosts often
start servers with the working directory at / or ~, so there is no default), and only
.csv statements and .yaml/.yml policies. A refused statement is reported by line
number only; cell values are never echoed to the client.

Descriptions: statement descriptions are text the payer chose ("IGNORE PREVIOUS
INSTRUCTIONS..."), and an MCP client is a model, so no tool ever returns one (R34).
`transactions_for` still accepts include_descriptions, and says the text was withheld.

Offline lock: the server is a way for data to reach a model, so while offline_lock is on
in firm.yaml (the default) it refuses to start, however it is started (main(), or an
embedding script calling `server.run()` or an async runner), and every tool refuses
before reading a file. firm.yaml is read on each start and each call. No ASGI app is handed
out (sse_app() and streamable_http_app() refuse): an ASGI server could open its socket for
a saved app before anything in it ran. The HTTP run methods build their app and serve it in
the same call, after reading the lock.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import categorise, firm, ingest, metrics, policy


class LockedMCPServer(MCPServer):
    """An MCPServer whose every way of starting checks the offline lock first."""

    def run(self, *args, **kwargs):
        firm.require_unlocked("Starting the MCP server")
        return super().run(*args, **kwargs)

    async def run_stdio_async(self, *args, **kwargs):
        firm.require_unlocked("Starting the MCP server")
        return await super().run_stdio_async(*args, **kwargs)

    async def run_sse_async(self, *args, **kwargs):
        firm.require_unlocked("Starting the MCP server")
        token = _BUILDING_FOR_RUN.set(True)
        try:
            return await super().run_sse_async(*args, **kwargs)
        finally:
            _BUILDING_FOR_RUN.reset(token)

    async def run_streamable_http_async(self, *args, **kwargs):
        firm.require_unlocked("Starting the MCP server")
        token = _BUILDING_FOR_RUN.set(True)
        try:
            return await super().run_streamable_http_async(*args, **kwargs)
        finally:
            _BUILDING_FOR_RUN.reset(token)

    # An ASGI app is handed out only while the lock is off, as before this change (R53, T17
    # review 4); under the lock it is refused (R29). The app handed out reads the lock again
    # at start-up and before every request, so one saved while the lock was off refuses
    # everything once the lock is on (T17 review 1). Inside a run method, past its lock
    # check, the app it serves is built as the SDK builds it (T17 review 2).
    def sse_app(self, *args, **kwargs):
        if _BUILDING_FOR_RUN.get():
            # The app a run method serves is lock-checked too: a lock turned on after the
            # run's entry check refuses start-up and every request (T17 review 8).
            return LockCheckedASGI(super().sse_app(*args, **kwargs))
        firm.require_unlocked("Handing out the MCP server's ASGI app")
        return LockCheckedASGI(super().sse_app(*args, **kwargs))

    def streamable_http_app(self, *args, **kwargs):
        if _BUILDING_FOR_RUN.get():
            return LockCheckedASGI(super().streamable_http_app(*args, **kwargs))
        firm.require_unlocked("Handing out the MCP server's ASGI app")
        return LockCheckedASGI(super().streamable_http_app(*args, **kwargs))


class LockCheckedASGI:
    """An MCP ASGI app handed out while the offline lock was off. It reads the lock again at
    start-up and before every request: once the lock is on, start-up fails, an HTTP request
    gets 503 and a websocket is closed, and nothing of the server's runs (R29). Any other
    attribute is the wrapped app's, so an embedder can still mount or inspect it (R53)."""

    def __init__(self, app):
        self.app = app

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            # The lock is read when start-up is asked for, not before, so a delayed start-up
            # event cannot start the server under a lock turned on meanwhile (T17 review 6).
            first = await receive()
            while first["type"] != "lifespan.startup":
                first = await receive()
            problem = _lock_problem("Serving the MCP server")
            if problem is not None:
                await send({"type": "lifespan.startup.failed", "message": problem})
                return
            given = [first]

            async def replay():
                return given.pop() if given else await receive()
            return await self.app(scope, replay, send)
        problem = _lock_problem("Serving the MCP server")
        if problem is None:
            return await self.app(scope, receive, send)
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 503,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
            await send({"type": "http.response.body", "body": problem.encode()})
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1013})


# Set only while one of the run methods above, past its lock check, builds the app it serves.
_BUILDING_FOR_RUN: ContextVar[bool] = ContextVar("cma_mcp_building_for_run", default=False)


class NoASGIApp(firm.OfflineLockError):
    """Kept for callers that import it. Since T17 review 4 an ASGI app is refused only under
    the lock, with the lock's own OfflineLockError, which this is a kind of."""

    def __init__(self) -> None:
        super().__init__("The MCP server does not hand out an ASGI app: an ASGI server could open its "
                         "socket before the offline lock is read. Start it with `cma mcp` (or "
                         "server.run()), which reads the lock first.")


def _lock_problem(what: str) -> str | None:
    try:
        firm.require_unlocked(what)
    except (firm.OfflineLockError, firm.FirmConfigError) as e:
        return str(e)
    return None


server = LockedMCPServer("credit-memo-agent")


def data_dir() -> Path:
    raw = os.environ.get("CMA_DATA_DIR")
    if not raw:
        raise ValueError("CMA_DATA_DIR is not set; set it to the folder of statements this server may read")
    return Path(raw).resolve()


def safe_path(raw: str, suffixes: tuple[str, ...]) -> Path:
    """Resolve `raw` inside the data directory, or refuse."""
    root = data_dir()
    p = Path(raw)
    p = (p if p.is_absolute() else root / p).resolve()
    if not p.is_relative_to(root):
        raise ValueError(f"{raw}: outside the data directory {root} (set CMA_DATA_DIR)")
    if p.suffix.lower() not in suffixes:
        raise ValueError(f"{raw}: only {', '.join(suffixes)} files can be read")
    if not p.is_file():
        raise ValueError(f"{raw}: no such file")
    return p


def _statement(path: str):
    # Every tool starts here, so none reads a file while the lock is on.
    firm.require_unlocked("An MCP tool call")
    p = safe_path(path, (".csv",))
    try:
        return ingest.load_csv(p)
    except ingest.StatementError as e:
        # Problems are grouped by the kind the loader recorded, never by their text, which can
        # quote a cell (T04 review 22). Money in or out at the limit is said in full; another
        # problem with the whole file is named by its label; lines at the limit, and lines
        # with a field too long to read (whose content is not known), are each named in full,
        # ahead of up to ten other line labels. No cell value is given (R2, T04 reviews 12-22).
        problems, kinds = e.problems, e.kinds

        def labels(kind: str) -> list[str]:
            got = {x.split(":", 1)[0] for x, k in zip(problems, kinds, strict=True) if k == kind}
            return sorted(got, key=lambda label: int(label.split()[1]))

        flows = [x for x, k in zip(problems, kinds, strict=True) if k == "flow"]
        files = [x.split(":", 1)[0].split(" (", 1)[0] for x, k in zip(problems, kinds, strict=True) if k == "file"]
        limit, long_ = labels("limit"), labels("too_long")
        other = [x for x in labels("line") if x not in limit and x not in long_]
        parts = flows + files + ([f"amount or balance {metrics.AT_LIMIT} on {', '.join(limit)}"] if limit else [])
        parts += [f"{ingest.TOO_LONG} on {', '.join(long_)}"] if long_ else []
        if other:
            parts.append(f"other problems on {', '.join(other[:10])}{' and more' if len(other) > 10 else ''}")
        raise ValueError(f"{path}: statement refused, {len(problems)} problem(s): {'; '.join(parts)}; "
                         "run `cma run` locally to see them") from None


@server.tool()
def statement_summary(path: str) -> dict:
    """Load a statement CSV and return account name, period and transaction count."""
    s = _statement(path)
    return {
        "account_name": s.account_name,
        "start": s.start.isoformat(),
        "end": s.end.isoformat(),
        "transactions": len(s.transactions),
    }


@server.tool()
def compute_metrics(path: str, vat_stagger: int | None = None) -> list[dict]:
    """Categorise a statement and compute the cited metrics table (M1..M28), money-in buckets
    included. A statement whose buckets do not reconcile is refused (ValueError), naming the
    difference in pence and the line ids.
    vat_stagger is the HMRC stagger group (1, 2 or 3); without it M13 is n/a."""
    s = categorise.categorise(_statement(path))
    return [m.model_dump() for m in metrics.compute(s, vat_stagger=vat_stagger)]


@server.tool()
def check_policy(path: str, policy_path: str | None = None, vat_stagger: int | None = None) -> dict:
    """Run the lending policy against a statement and return pass/refer/fail per rule."""
    s = categorise.categorise(_statement(path))
    p = policy.load_policy(safe_path(policy_path, (".yaml", ".yml"))) if policy_path else None
    return policy.evaluate(metrics.compute(s, vat_stagger=vat_stagger), p).evidence_dump()


@server.tool()
def transactions_for(path: str, ids: list[str], include_descriptions: bool = False) -> list[dict]:
    """Return the statement lines behind a metric's evidence ids, so a reviewer can check a
    figure. Descriptions are payer-written text and are never returned, even when
    include_descriptions is true; each row says so with description_withheld."""
    by_id = _statement(path).by_id()
    out = []
    for i in ids:
        if i not in by_id:
            continue
        t = by_id[i]
        row = {"id": t.id, "date": t.date.isoformat(), "amount": t.amount, "balance": t.balance,
               "category": t.category.value if t.category else None, "description_withheld": True}
        out.append(row)
    return out


def main() -> None:
    try:
        firm.load().require_offline_off("Starting the MCP server")
    except (firm.OfflineLockError, firm.FirmConfigError) as e:
        raise SystemExit(str(e)) from None
    data_dir()  # refuse to start without CMA_DATA_DIR
    server.run()


if __name__ == "__main__":
    main()
