"""Offline metrics for real generation outputs; no provider credentials required.

Usage (from llm/):
 python tests/evaluation/harness.py --results path/to/results.json --output eval_report.json
 python tests/evaluation/harness.py --results path/to/results.json --baseline previous_report.json

Results JSON: [{"seed_id":"short_01", "duration_seconds":3.2,
                "nodes":[{"id":"n1","label":"...","description":"...","sources":["ctx:1"]}],
                "edges":[], "context_count":1}, ...]
Only evaluate actual generation runs when their real outputs and timings are supplied.
"""

from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path
from statistics import mean
from typing import Any

BENCHMARK_PATH = Path(__file__).with_name("benchmarks.json")


def load_benchmarks(path: Path = BENCHMARK_PATH) -> list[dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    ids = [case["id"] for case in cases]
    if not 10 <= len(ids) <= 15 or len(ids) != len(set(ids)):
        raise ValueError("Expected 10-15 unique benchmark IDs")
    return cases


def graph_depth(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> int:
    """Longest shortest directed path from roots, with finite behavior on cycles."""
    ids = {str(n["id"]) for n in nodes}
    incoming = {node_id: 0 for node_id in ids}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for edge in edges:
        source = str(edge.get("source_node_id", edge.get("source", "")))
        target = str(edge.get("target_node_id", edge.get("target", "")))
        if source in ids and target in ids:
            outgoing[source].append(target)
            incoming[target] += 1
    roots = [node_id for node_id in sorted(ids) if incoming[node_id] == 0]
    if not roots:
        return 0
    distances = {node_id: 0 for node_id in roots}
    queue = deque(roots)
    while queue:
        source = queue.popleft()
        for target in outgoing[source]:
            if target not in distances:
                distances[target] = distances[source] + 1
                queue.append(target)
    return max(distances.values(), default=0)


def schema_valid(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> bool:
    """Validate graph shape with existing LLM draft validator, using stable ID mapping."""
    try:
        from validation.schema import validate_draft

        id_to_str = {str(node["id"]): str(node["id"]) for node in nodes}
        payload = {
            "nodes": [
                {
                    "id": str(n["id"]),
                    "label": n.get("label", ""),
                    "description": n.get("description"),
                    "sources": n.get("sources", []),
                }
                for n in nodes
            ],
            "edges": [
                {
                    "source": id_to_str[str(e.get("source_node_id", e.get("source")))],
                    "target": id_to_str[str(e.get("target_node_id", e.get("target")))],
                    "label": e.get("label"),
                }
                for e in edges
            ],
        }
        validate_draft(payload)
        return True
    except (ValueError, KeyError, TypeError, ImportError):
        return False


def evaluate_case(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    nodes = result.get("nodes", [])
    edges = result.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("nodes and edges must be lists")
    duration = float(result["duration_seconds"])
    if duration < 0:
        raise ValueError("duration_seconds must be nonnegative")
    count = len(nodes)
    described = sum(bool(str(n.get("description") or "").strip()) for n in nodes)
    # Proxy, not proof of factual grounding: counts nodes with a declared source.
    sourced = sum(bool(n.get("sources")) for n in nodes)
    context_count = result.get("context_count")
    known_context_count = isinstance(context_count, int) and context_count >= 0
    supported = sum(
        bool(n.get("sources"))
        and known_context_count
        and all(
            (
                isinstance(src, str)
                and src.startswith("ctx:")
                and src[4:].isdigit()
                and 1 <= int(src[4:]) <= context_count
            )
            for src in n["sources"]
        )
        for n in nodes
    )
    threshold = 180.0 if case["detail_level"] == "detailed" else 30.0
    # NFR thresholds apply specifically to ~10k word inputs; smaller inputs are diagnostic only.
    eligible = 9000 <= int(case.get("word_count", 0)) <= 11000
    return {
        "seed_id": case["id"],
        "category": case["category"],
        "detail_level": case["detail_level"],
        "node_count": count,
        "max_depth": graph_depth(nodes, edges),
        "description_completeness": described / count if count else 0.0,
        "source_grounding_ratio_proxy": sourced / count if count else 0.0,
        "valid_context_citation_ratio_proxy": supported / count if count and known_context_count else None,
        "schema_valid": schema_valid(nodes, edges),
        "latency_seconds": duration,
        "nfr_threshold_seconds": threshold if eligible else None,
        "nfr_latency_pass": duration < threshold if eligible else None,
    }


def evaluate(benchmarks: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {case["id"]: case for case in benchmarks}
    if len({r["seed_id"] for r in results}) != len(results):
        raise ValueError("Duplicate seed_id in results")
    unknown = {r["seed_id"] for r in results} - by_id.keys()
    if unknown:
        raise ValueError(f"Unknown seed IDs: {sorted(unknown)}")
    rows = [evaluate_case(by_id[r["seed_id"]], r) for r in results]
    measured = [r for r in rows if r["nfr_latency_pass"] is not None]
    quick = [r for r in measured if r["detail_level"] != "detailed"]
    detailed = [r for r in measured if r["detail_level"] == "detailed"]
    return {
        "evaluated_count": len(rows),
        "benchmark_count": len(benchmarks),
        "summary": {
            "mean_node_count": mean(r["node_count"] for r in rows) if rows else None,
            "mean_description_completeness": mean(r["description_completeness"] for r in rows) if rows else None,
            "mean_grounding_proxy": mean(r["source_grounding_ratio_proxy"] for r in rows) if rows else None,
            "schema_valid_rate": mean(float(r["schema_valid"]) for r in rows) if rows else None,
            "nfr01_quick_p95_pass": (sum(r["nfr_latency_pass"] for r in quick) / len(quick) >= 0.95) if quick else None,
            "nfr02_detailed_all_pass": all(r["nfr_latency_pass"] for r in detailed) if detailed else None,
            "nfr_eligible_quick_count": len(quick),
            "nfr_eligible_detailed_count": len(detailed),
        },
        "cases": rows,
    }


def compare_reports(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, float | None]:
    keys = ("mean_node_count", "mean_description_completeness", "mean_grounding_proxy", "schema_valid_rate")
    before = baseline["summary"]
    after = current["summary"]
    return {
        key: (after[key] - before[key]) if after.get(key) is not None and before.get(key) is not None else None
        for key in keys
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate saved mind-map generation outputs")
    parser.add_argument("--results", type=Path, required=True, help="JSON array of real generation results and timings")
    parser.add_argument("--benchmarks", type=Path, default=BENCHMARK_PATH)
    parser.add_argument("--baseline", type=Path, help="Previous evaluation report for improvement comparison")
    parser.add_argument("--output", type=Path, default=Path("evaluation_report.json"))
    args = parser.parse_args()
    benchmarks = load_benchmarks(args.benchmarks)
    results = json.loads(args.results.read_text(encoding="utf-8"))
    report = evaluate(benchmarks, results)
    if args.baseline:
        report["delta_vs_baseline"] = compare_reports(json.loads(args.baseline.read_text(encoding="utf-8")), report)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Evaluated {report['evaluated_count']}/{report['benchmark_count']} seeds -> {args.output}")


if __name__ == "__main__":
    main()
