"""MCP server exposing the deterministic layers as tools.

Lets any MCP client (Claude Desktop, an n8n agent node, another LangGraph graph) ask
for metrics or a policy result without ever touching the arithmetic itself.

Run:  python -m cma.mcp_server

Files: tools only read files under CMA_DATA_DIR, which must be set (MCP hosts often
start servers with the working directory at / or ~, so there is no default), and only
.csv statements and .yaml/.yml policies. A refused statement is reported by line
number only; cell values are never echoed to the client.

Descriptions: statement descriptions are text the payer chose ("IGNORE PREVIOUS
INSTRUCTIONS..."). `transactions_for` leaves them out unless the caller asks, and then
returns them marked as untrusted data.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import categorise, ingest, metrics, policy

server = MCPServer("credit-memo-agent")


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
    p = safe_path(path, (".csv",))
    try:
        return ingest.load_csv(p)
    except ingest.StatementError as e:
        # A problem with the whole statement (money in or out reaching the limit) is said in
        # full, ahead of the line labels, never cut off by them (R2, T04 review 12); a line's
        # text stays local, only its label is given.
        # Every line whose amount or balance is at the limit is named too, never cut off by
        # the ten other labels (T04 review 13).
        problems = e.problems
        whole = [x for x in problems if not x.startswith("line ")]

        def labels(marker: str | None) -> list[str]:
            got = {x.split(":", 1)[0] for x in problems if x.startswith("line ")
                   and (marker in x if marker else not (metrics.AT_LIMIT in x or ingest.TOO_LONG in x))}
            return sorted(got, key=lambda label: int(label.split()[1]))

        # A field too long to read is named as that, never as an amount at the limit: what it
        # holds is not known (T04 review 21). Both are named in full.
        limit, long_ = labels(metrics.AT_LIMIT), labels(ingest.TOO_LONG)
        other = [x for x in labels(None) if x not in limit and x not in long_]
        parts = whole + ([f"amount or balance {metrics.AT_LIMIT} on {', '.join(limit)}"] if limit else [])
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
    """Categorise a statement and compute the cited metrics table (M1..M26), money-in buckets
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
    return policy.evaluate(metrics.compute(s, vat_stagger=vat_stagger), p).model_dump()


@server.tool()
def transactions_for(path: str, ids: list[str], include_descriptions: bool = False) -> list[dict]:
    """Return the statement lines behind a metric's evidence ids, so a reviewer can check a
    figure. Descriptions are payer-written text; they are only included when asked for,
    and then under `untrusted_description`."""
    by_id = _statement(path).by_id()
    out = []
    for i in ids:
        if i not in by_id:
            continue
        t = by_id[i]
        row = {"id": t.id, "date": t.date.isoformat(), "amount": t.amount, "balance": t.balance,
               "category": t.category.value if t.category else None}
        if include_descriptions:
            row["untrusted_description"] = t.description
        out.append(row)
    return out


def main() -> None:
    data_dir()  # refuse to start without CMA_DATA_DIR
    server.run()


if __name__ == "__main__":
    main()
