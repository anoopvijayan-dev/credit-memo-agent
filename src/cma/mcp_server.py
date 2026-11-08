"""MCP server exposing the deterministic layers as tools.

Lets any MCP client (Claude Desktop, an n8n agent node, another LangGraph graph) ask
for metrics or a policy result without ever touching the arithmetic itself.

Run:  python -m cma.mcp_server

Files: tools only read files under CMA_DATA_DIR (default: the directory the server was
started in), and only .csv statements and .yaml/.yml policies. A client cannot point
the server at ~/.ssh or anything else on the machine.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import categorise, ingest, metrics, policy

server = MCPServer("credit-memo-agent")


def data_dir() -> Path:
    return Path(os.environ.get("CMA_DATA_DIR") or Path.cwd()).resolve()


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
    return ingest.load_csv(safe_path(path, (".csv",)))


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
    """Categorise a statement and compute the cited metrics table (M1..M16).
    vat_stagger is the HMRC stagger group (1, 2 or 3); without it M13 is n/a."""
    s = categorise.categorise(_statement(path))
    return [m.model_dump() for m in metrics.compute(s, vat_stagger=vat_stagger)]


@server.tool()
def check_policy(path: str, policy_path: str | None = None, vat_stagger: int | None = None) -> dict:
    """Run the lending policy against a statement and return pass/refer/fail per rule."""
    s = categorise.categorise(_statement(path))
    p = policy.load_policy(safe_path(policy_path, (".yaml", ".yml"))) if policy_path else None
    return policy.evaluate(metrics.compute(s, vat_stagger=vat_stagger), p).model_dump()


@server.tool()
def transactions_for(path: str, ids: list[str]) -> list[dict]:
    """Return the statement lines behind a metric's evidence ids, so a reviewer can check a figure."""
    by_id = _statement(path).by_id()
    return [by_id[i].model_dump(mode="json") for i in ids if i in by_id]


if __name__ == "__main__":
    server.run()
