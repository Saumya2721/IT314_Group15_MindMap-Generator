"""Unit tests for the offline evaluation harness (not real LLM benchmarks)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parent / "evaluation" / "harness.py"
SPEC = importlib.util.spec_from_file_location("evaluation_harness", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def sample_graph() -> dict:
    return {
        "seed_id": "short_01",
        "duration_seconds": 1.25,
        "context_count": 1,
        "nodes": [
            {"id": "root", "label": "Root", "description": "Intro", "sources": ["ctx:1"]},
            {"id": "child", "label": "Child", "description": "", "sources": []},
        ],
        "edges": [{"source": "root", "target": "child"}],
    }


def test_dataset_has_12_diverse_unique_seeds():
    cases = harness.load_benchmarks()
    assert len(cases) == 12
    assert len({c["id"] for c in cases}) == 12
    assert {c["category"] for c in cases} == {"short_topic", "long_document", "technical_pdf", "multi_section"}


def test_depth_and_metrics():
    case = next(c for c in harness.load_benchmarks() if c["id"] == "short_01")
    metrics = harness.evaluate_case(case, sample_graph())
    assert metrics["node_count"] == 2
    assert metrics["max_depth"] == 1
    assert metrics["description_completeness"] == 0.5
    assert metrics["source_grounding_ratio_proxy"] == 0.5
    assert metrics["valid_context_citation_ratio_proxy"] == 0.5
    assert metrics["schema_valid"] is True
    assert metrics["nfr_latency_pass"] is None  # Not a 10k-word input


def test_invalid_graph_is_rejected_by_schema_validator():
    result = sample_graph()
    result["edges"] = [{"source": "missing", "target": "child"}]
    metrics = harness.evaluate_case(harness.load_benchmarks()[0], result)
    assert metrics["schema_valid"] is False


def test_no_source_context_does_not_claim_valid_grounding():
    result = sample_graph()
    result.pop("context_count")
    metrics = harness.evaluate_case(harness.load_benchmarks()[0], result)
    assert metrics["valid_context_citation_ratio_proxy"] is None


def test_nfr_threshold_uses_10k_word_inputs_only():
    case = {**harness.load_benchmarks()[0], "word_count": 10000}
    assert harness.evaluate_case(case, sample_graph())["nfr_latency_pass"] is True
    case["detail_level"] = "detailed"
    result = {**sample_graph(), "duration_seconds": 100.0}
    assert harness.evaluate_case(case, result)["nfr_threshold_seconds"] == 180.0
    assert harness.evaluate_case(case, result)["nfr_latency_pass"] is True


def test_comparison_deltas_and_duplicate_guard():
    cases = harness.load_benchmarks()
    before = harness.evaluate(cases, [sample_graph()])
    new = sample_graph()
    new["nodes"][1]["description"] = "Filled in"
    after = harness.evaluate(cases, [new])
    assert harness.compare_reports(before, after)["mean_description_completeness"] == 0.5
    with pytest.raises(ValueError, match="Duplicate"):
        harness.evaluate(cases, [new, new])


def test_bad_duration_rejected():
    bad = {**sample_graph(), "duration_seconds": -1}
    with pytest.raises(ValueError, match="nonnegative"):
        harness.evaluate_case(harness.load_benchmarks()[0], bad)
