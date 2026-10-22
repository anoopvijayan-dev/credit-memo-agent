"""MCP server exposing the deterministic layers as tools.

Lets any MCP client (Claude Desktop, an n8n agent node, another LangGraph graph) ask
for metrics or a policy result without ever touching the arithmetic itself.

Run:  python -m cma.mcp_server
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import categorise, ingest, metrics, policy

server = MCPServer("credit-memo-agent")


@server.tool()
def statement_summary(path: str) -> dict:
    """Load a statement CSV and return account name, period and transaction count."""
    s = ingest.load_csv(Path(path))
    return {
        "account_name": s.account_name,
        "start": s.start.isoformat(),
        "end": s.end.isoformat(),
        "transactions": len(s.transactions),
    }


@server.tool()
def compute_metrics(path: str) -> list[dict]:
    """Categorise a statement and compute the cited metrics table (M1..M15)."""
    s = categorise.categorise(ingest.load_csv(Path(path)))
    return [m.model_dump() for m in metrics.compute(s)]


@server.tool()
def check_policy(path: str, policy_path: str | None = None) -> dict:
    """Run the lending policy against a statement and return pass/refer/fail per rule."""
    s = categorise.categorise(ingest.load_csv(Path(path)))
    p = policy.load_policy(Path(policy_path)) if policy_path else None
    return policy.evaluate(metrics.compute(s), p).model_dump()


@server.tool()
def transactions_for(path: str, ids: list[str]) -> list[dict]:
    """Return the statement lines behind a metric's evidence ids, so a reviewer can check a figure."""
    s = ingest.load_csv(Path(path))
    by_id = s.by_id()
    return [by_id[i].model_dump(mode="json") for i in ids if i in by_id]


if __name__ == "__main__":
    server.run()
