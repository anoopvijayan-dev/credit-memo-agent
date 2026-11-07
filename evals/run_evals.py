"""Offline evaluation across every archetype x profile.

Reports, per case: policy outcome, grounding rate of the drafted memo, redrafts
needed, and whether the metrics agreed with ground truth. Run with the fake model
for a deterministic baseline, or with a real provider to measure it.

    python evals/run_evals.py --llm fake
    CMA_LLM=anthropic python evals/run_evals.py --llm anthropic
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langgraph.types import Command

from cma import synth
from cma.graph import build
from cma.llm import get_llm


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", default="fake")
    ap.add_argument("--out", default="evals/results")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "statements"
    graph = build(get_llm(args.llm))

    results = []
    for archetype in synth.ARCHETYPES:
        for profile in synth.PROFILES:
            rows, truth = synth.generate(archetype, profile, end=date(2026, 9, 30))
            csv_path, _ = synth.write(rows, truth, work, stem=f"{archetype}_{profile}")
            cfg = {"configurable": {"thread_id": f"{archetype}-{profile}"}}
            t0 = time.perf_counter()
            state = graph.invoke({"statement_path": str(csv_path)}, cfg)
            graph.invoke(Command(resume={"approved": state["guard"].grounded, "reviewer": "eval"}), cfg)
            m = {x.id: x for x in state["metrics"]}
            results.append(
                {
                    "case": f"{archetype}/{profile}",
                    "policy": state["policy"].overall.value,
                    "grounded": state["guard"].grounded,
                    "grounding_rate": round(state["guard"].grounding_rate, 3),
                    "numbers_checked": state["guard"].numbers_checked,
                    "redrafts": state.get("redrafts", 0),
                    "revenue_matches_truth": abs(m["M2"].value - truth.total_revenue) < 0.01,
                    "seconds": round(time.perf_counter() - t0, 2),
                }
            )

    (out_dir / f"{args.llm}.json").write_text(json.dumps(results, indent=2))
    print(f"{'case':22} {'policy':7} {'grounded':9} {'rate':6} {'redrafts':8} {'truth':6} {'s':>5}")
    for r in results:
        print(
            f"{r['case']:22} {r['policy']:7} {r['grounded']!s:9} {r['grounding_rate']:<6} "
            f"{r['redrafts']:<8} {r['revenue_matches_truth']!s:6} {r['seconds']:>5}"
        )
    grounded = sum(r["grounded"] for r in results)
    print(f"\n{grounded}/{len(results)} memos fully grounded; model = {args.llm}")
    return 0 if all(r["revenue_matches_truth"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
