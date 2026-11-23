"""Unit tests for S3-L4 Model A/B Evaluation Suite and benchmark runner."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

LLM_ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = LLM_ROOT / "tests" / "evaluation"
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))
if str(LLM_ROOT / "mindmap_llm") not in sys.path:
    sys.path.insert(0, str(LLM_ROOT / "mindmap_llm"))
if str(LLM_ROOT) not in sys.path:
    sys.path.insert(0, str(LLM_ROOT))

import pytest
from ab_runner import (
    RETIRED_BASELINE_MODEL,
    aggregate_model_metrics,
    calculate_percentile,
    check_model_availability,
    generate_markdown_comparison_table,
    push_results_to_langsmith,
    run_ab_benchmark,
)
from config import Settings
from orchestration.deps import GenerationDeps
from orchestration.nodes import make_nodes
from providers.mock_provider import MockProvider

from tests.conftest import FakeCallbackClient, FakeDecisionModel, FakeEmbeddingProvider, FakeVectorStore


def test_calculate_percentile_various_cases() -> None:
    assert calculate_percentile([], 95.0) is None
    assert calculate_percentile([4.2], 95.0) == 4.2
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    p50 = calculate_percentile(values, 50.0)
    p95 = calculate_percentile(values, 95.0)
    assert p50 == 3.0
    assert p95 == 4.8


def test_aggregate_model_metrics_all_six_metrics() -> None:
    sample_runs = [
        {
            "seed_id": "seed_01",
            "duration_seconds": 2.0,
            "generation_status": "completed",
            "metrics": {
                "node_count": 10,
                "max_depth": 3,
                "description_completeness": 0.9,
                "source_grounding_ratio_proxy": 0.8,
                "valid_context_citation_ratio_proxy": 0.8,
                "schema_valid": True,
            },
        },
        {
            "seed_id": "seed_02",
            "duration_seconds": 4.0,
            "generation_status": "completed",
            "metrics": {
                "node_count": 20,
                "max_depth": 5,
                "description_completeness": 0.7,
                "source_grounding_ratio_proxy": 0.6,
                "valid_context_citation_ratio_proxy": 0.6,
                "schema_valid": False,
            },
        },
    ]

    metrics = aggregate_model_metrics(sample_runs)

    assert metrics.total_runs == 2
    assert metrics.successful_runs == 2
    assert metrics.schema_valid_rate == 0.5
    assert metrics.schema_error_rate == 0.5
    assert metrics.mean_latency_seconds == 3.0
    assert metrics.median_latency_seconds == 3.0
    assert metrics.min_latency_seconds == 2.0
    assert metrics.max_latency_seconds == 4.0
    assert metrics.mean_node_count == 15.0
    assert metrics.min_node_count == 10
    assert metrics.max_node_count == 20
    assert metrics.mean_hierarchy_depth == 4.0
    assert metrics.min_hierarchy_depth == 3
    assert metrics.max_hierarchy_depth == 5
    assert metrics.mean_description_completeness_proxy == 0.8
    assert metrics.mean_source_grounding_proxy == 0.7
    assert metrics.mean_valid_context_citation_proxy == 0.7


def test_retired_baseline_model_rejected() -> None:
    async def _run() -> None:
        mock_provider = MagicMock()
        with pytest.raises(ValueError, match="is retired on Groq developer tier"):
            await check_model_availability(mock_provider, RETIRED_BASELINE_MODEL)

    asyncio.run(_run())


def test_missing_model_raises_clear_error() -> None:
    async def _run() -> None:
        mock_provider = MagicMock()
        mock_provider.validate_model_availability = AsyncMock(
            side_effect=ValueError("Model 'unknown-model' is not available on Groq")
        )
        with pytest.raises(ValueError, match="is not available on Groq"):
            await check_model_availability(mock_provider, "unknown-model")

    asyncio.run(_run())


def test_structured_output_integration_with_nodes() -> None:
    async def _run() -> None:
        mock_llm = MockProvider(model="mock-structured")
        mock_llm.supports_structured_output = True
        settings = Settings()
        deps = GenerationDeps(
            llm=mock_llm,
            decision_model=FakeDecisionModel(),
            embedding_provider=FakeEmbeddingProvider(),
            vector_store=FakeVectorStore(),
            tools={},
            settings=settings,
            callback=FakeCallbackClient(),
        )
        nodes = make_nodes(deps)
        state = {
            "job_id": "test-job",
            "map_id": "test-map",
            "namespace": "test-ns",
            "seed_text": "Photosynthesis is the process by which green plants transform light energy.",
            "detail_level": "balanced",
            "resolved_detail_level": "balanced",
            "is_regeneration": False,
            "two_pass_enabled": False,
            "seed_has_files": False,
            "purpose": None,
        }
        res = await nodes["generate"](state)
        assert res.get("draft_payload") is not None
        assert mock_llm.last_response_format == {"type": "json_object"}

        # Now test that provider without structured output support does not receive response_format
        mock_llm.reset()
        mock_llm.supports_structured_output = False
        res2 = await nodes["generate"](state)
        assert res2.get("draft_payload") is not None
        assert mock_llm.last_response_format is None

    asyncio.run(_run())


def test_markdown_comparison_table_formatting() -> None:
    results_mock = {
        "models": {
            "openai/gpt-oss-120b": {
                "aggregate": {
                    "mean_latency_seconds": 2.45,
                    "median_latency_seconds": 2.30,
                    "p95_latency_seconds": 3.10,
                    "schema_error_rate": 0.0,
                    "mean_node_count": 14.2,
                    "min_node_count": 8,
                    "max_node_count": 22,
                    "mean_hierarchy_depth": 3.5,
                    "max_hierarchy_depth": 5,
                    "mean_description_completeness_proxy": 0.98,
                    "mean_source_grounding_proxy": 0.95,
                    "mean_valid_context_citation_proxy": 0.90,
                    "successful_runs": 12,
                    "total_runs": 12,
                }
            },
            "openai/gpt-oss-20b": {
                "aggregate": {
                    "mean_latency_seconds": 0.85,
                    "median_latency_seconds": 0.80,
                    "p95_latency_seconds": 1.10,
                    "schema_error_rate": 0.0,
                    "mean_node_count": 12.0,
                    "min_node_count": 6,
                    "max_node_count": 18,
                    "mean_hierarchy_depth": 3.0,
                    "max_hierarchy_depth": 4,
                    "mean_description_completeness_proxy": 0.95,
                    "mean_source_grounding_proxy": 0.92,
                    "mean_valid_context_citation_proxy": 0.88,
                    "successful_runs": 12,
                    "total_runs": 12,
                }
            },
        }
    }

    table = generate_markdown_comparison_table(results_mock)
    assert "| Metric | openai/gpt-oss-120b | openai/gpt-oss-20b |" in table
    assert "Mean Latency (s)" in table
    assert "2.45s" in table
    assert "0.85s" in table
    assert "Schema Error Rate" in table
    assert "0.0%" in table
    assert "Description Completeness (Proxy)" in table


def test_missing_langsmith_credentials_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run() -> None:
        monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
        monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
        res = await push_results_to_langsmith({"models": {}})
        assert res is None

    asyncio.run(_run())


def test_ab_runner_dry_run_mock_offline(tmp_path: Path) -> None:
    async def _run() -> None:
        out_file = tmp_path / "mock_ab_results.json"
        results = await run_ab_benchmark(
            models=["openai/gpt-oss-120b", "openai/gpt-oss-20b"],
            limit=2,
            repetitions=1,
            output_path=out_file,
            mock=True,
            no_langsmith=True,
        )

        assert "metadata" in results
        assert results["metadata"]["mode"] == "mock"
        assert results["metadata"]["notes"]["original_baseline"] == RETIRED_BASELINE_MODEL
        assert "models" in results
        assert "openai/gpt-oss-120b" in results["models"]
        assert "openai/gpt-oss-20b" in results["models"]

        mod1 = results["models"]["openai/gpt-oss-120b"]
        assert mod1["aggregate"]["total_runs"] == 2
        assert len(mod1["runs"]) == 2
        assert mod1["aggregate"]["schema_valid_rate"] == 1.0

        # Verify output JSON file exists and does not contain API keys
        assert out_file.exists()
        content = out_file.read_text(encoding="utf-8")
        assert "gsk_" not in content
        assert "pcsk_" not in content
        assert "api_key" not in content.lower() or '"api_key": null' in content.lower()

    asyncio.run(_run())
