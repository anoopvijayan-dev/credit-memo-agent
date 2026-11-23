"""Offline evaluation across every archetype x profile.

Reports, per case: policy outcome, grounding rate of the drafted memo, redrafts
needed, and every metric that disagrees with the generator's ground truth (M2, M3,
M5-M13, M15, M16), including the inputs to the fail rules (M9, M10). Run with the fake model for a deterministic
baseline, or with a real provider to measure it.

Then, per archetype x profile with every opt-in synthetic feature (R47, P17: real acquirers,
director funding, cash, lenders and a second account the case profile names), it checks under
the v2 rule table and the generated case profile, on both accounts: every line's category,
total money in (M17), every money-in bucket with its lines (M2, M19-M25), the cash within
trading receipts (M18), every indicator with its lines, and the metrics above.

Exit code: 1 if any metric, category, bucket or indicator disagrees with truth, or if the fake model's memo is not
grounded (the fake writes from the table, so an ungrounded fake memo is a guard bug).
A real model's grounding is measured and reported, not enforced.

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

from cma import categorise, indicators, ingest, metrics, synth
from cma.categorise import ProfileNames, active_ruleset, load_ruleset
from cma.graph import build
from cma.llm import get_llm
from cma.models import Bucket


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
            state = graph.invoke({"statement_path": str(csv_path), "vat_stagger": truth.vat_stagger}, cfg)
            graph.invoke(Command(resume={"approved": False, "reviewer": "eval", "notes": "evaluation run"}), cfg)
            m = {x.id: x.value for x in state["metrics"]}
            mismatches = [mid for mid, expected in truth_metrics(truth).items() if not same(m[mid], expected)]
            results.append(
                {
                    "case": f"{archetype}/{profile}",
                    "policy": state["policy"].overall.value,
                    "grounded": state["guard"].grounded,
                    "grounding_rate": round(state["guard"].grounding_rate, 3),
                    "numbers_checked": state["guard"].numbers_checked,
                    "unmatched": [f"{f.number_text}: {f.reason}" for f in state["guard"].unmatched],
                    "redrafts": state.get("redrafts", 0),
                    "truth_mismatches": mismatches,
                    "seconds": round(time.perf_counter() - t0, 2),
                }
            )

    (out_dir / f"{args.llm}.json").write_text(json.dumps(results, indent=2))
    print(f"{'case':22} {'policy':7} {'grounded':9} {'rate':6} {'redrafts':8} {'truth':14} {'s':>5}")
    for r in results:
        truth_col = "ok" if not r["truth_mismatches"] else ",".join(r["truth_mismatches"])
        print(
            f"{r['case']:22} {r['policy']:7} {r['grounded']!s:9} {r['grounding_rate']:<6} "
            f"{r['redrafts']:<8} {truth_col:14} {r['seconds']:>5}"
        )
    grounded = sum(r["grounded"] for r in results)
    print(f"\n{grounded}/{len(results)} memos fully grounded; model = {args.llm}; ruleset = {active_ruleset().id}")
    truth_ok = all(not r["truth_mismatches"] for r in results)
    fake_ok = args.llm != "fake" or grounded == len(results)

    featured = []
    for archetype in synth.ARCHETYPES:
        for profile in synth.PROFILES:
            rows, truth = synth.generate(archetype, profile, end=date(2026, 9, 30), features=synth.FEATURES)
            stem = f"{archetype}_{profile}_features"
            csv_path, _ = synth.write(rows, truth, work, stem=stem)
            mismatches = feature_mismatches(truth, csv_path)
            featured.append({"case": f"{archetype}/{profile}+features", "lines": len(rows),
                             "indicators": len(truth.indicators), "truth_mismatches": mismatches})
    (out_dir / f"{args.llm}.features.json").write_text(json.dumps(featured, indent=2))
    print(f"\n{'case (v2, case profile)':32} {'lines':>5} {'indicators':>10} truth")
    for r in featured:
        print(f"{r['case']:32} {r['lines']:>5} {r['indicators']:>10} "
              f"{'ok' if not r['truth_mismatches'] else ', '.join(r['truth_mismatches'][:6])}")
    features_ok = all(not r["truth_mismatches"] for r in featured)
    print(f"{sum(not r['truth_mismatches'] for r in featured)}/{len(featured)} featured cases match truth "
          f"(features: {', '.join(synth.FEATURES)})")
    return 0 if truth_ok and fake_ok and features_ok else 1


def feature_mismatches(truth, csv_path: Path) -> list[str]:
    """What disagrees with the truth of a statement generated with features, on the main
    account and on every further account the generator wrote beside it (P17). Categorised with
    the v2 table, whatever CMA_RULESET says: feature truth is v2's, with the case profile."""
    v2 = load_ruleset("v2")
    profile = ProfileNames(tuple(truth.case_profile["directors"]), tuple(truth.case_profile["own_accounts"]))
    accounts = [("main", csv_path, truth.__dict__)]
    accounts += [(a["key"], csv_path.with_name(f"{csv_path.stem}_{a['key']}.csv"), a) for a in truth.accounts]
    out: list[str] = []
    for key, path, t in accounts:
        s = categorise.categorise(ingest.load_csv(path), v2, profile=profile)
        out += [f"{key}:category:{x.id}" for x in s.transactions if x.category.value != t["category_by_id"][x.id]]
        m = {x.id: x for x in metrics.compute(s, vat_stagger=synth.VAT_STAGGER)}
        if not same_pence(m["M17"].value, t["total_money_in"]):
            out.append(f"{key}:M17")
        for bucket, (mid, _, _) in [*metrics.BUCKET_METRICS.items(), (Bucket.TRADING_RECEIPTS, ("M2", "", ""))]:
            want = t["buckets"][bucket.value]
            if not same_pence(m[mid].value, want["amount"]) or sorted(m[mid].evidence) != sorted(want["ids"]):
                out.append(f"{key}:{mid}")
        cash = t["cash_deposits"]
        if not same_pence(m["M18"].value, cash["amount"]) or sorted(m["M18"].evidence) != sorted(cash["ids"]):
            out.append(f"{key}:M18")
        got = [(i.kind, i.evidence) for i in indicators.raise_indicators(s)]
        if got != [(i["kind"], i["lines"]) for i in t["indicators"]]:
            out.append(f"{key}:indicators")
        if key == "main":
            out += [f"main:{mid}" for mid, want in truth_metrics(truth).items() if not same(m[mid].value, want)]
    return out


def truth_metrics(truth) -> dict[str, float | None]:
    """The figures the generator knows, keyed by the metric that must reproduce them."""
    return {
        "M2": truth.total_revenue,
        "M3": truth.average_monthly_revenue,
        "M5": truth.lowest_balance,
        "M6": truth.days_overdrawn,
        "M7": truth.returned_dd_count,
        "M8": truth.total_debt_service,
        "M9": truth.net_operating,
        "M10": truth.debt_cover,
        "M11": truth.total_owner_drawings,
        "M12": truth.total_hmrc_vat,
        "M13": truth.late_vat_count,
        "M15": truth.top_customer_share,
        "M16": truth.unclassified_share,
    }


def same(got: float | None, expected: float | None) -> bool:
    if got is None or expected is None:
        return got is None and expected is None
    return abs(got - expected) < 0.01


def same_pence(got: float | None, expected: float | None) -> bool:
    """Money compared to the penny: a 1p difference is a mismatch. abs(a - b) < 0.01 in binary
    floating point accepts some 1p differences (Antigravity re-review of T10)."""
    if got is None or expected is None:
        return got is None and expected is None
    return round(got * 100) == round(expected * 100)


if __name__ == "__main__":
    raise SystemExit(main())
