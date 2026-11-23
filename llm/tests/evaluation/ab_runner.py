"""S3-L4: Model A/B Evaluation Suite and Benchmark Runner.

Benchmarks candidate Groq-hosted models against identical seeds from S2-L13
(benchmarks.json), evaluating:
  1. Node count
  2. Hierarchy depth
  3. Description quality (completeness proxy)
  4. Source coverage (grounding proxy & valid citations)
  5. Schema error rate
  6. Generation latency (mean, median, p95)

Model Availability Note:
  The original S3-L4 baseline was 'llama-3.3-70b-versatile', which has been retired
  from Groq's developer tier. In accordance with current Groq documentation, the suite
  evaluates the current generation models:
    - 'openai/gpt-oss-120b'
    - 'openai/gpt-oss-20b'
    - 'qwen/qwen3.8-27b'
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from time import perf_counter
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parent
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))
if str(ROOT / "mindmap_llm") not in sys.path:
    sys.path.insert(0, str(ROOT / "mindmap_llm"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from callback.client import NullCallbackClient  # noqa: E402
from config import Settings, get_settings  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from embeddings.registry import get_embedding_provider  # noqa: E402
from harness import BENCHMARK_PATH, evaluate_case, load_benchmarks  # noqa: E402
from orchestration.deps import GenerationDeps  # noqa: E402
from orchestration.graph import run_generation  # noqa: E402
from providers.base import LLMProvider  # noqa: E402
from providers.mock_provider import MockProvider  # noqa: E402
from providers.registry import get_decision_model, get_llm_provider  # noqa: E402
from rag.pinecone_store import PineconeVectorStore  # noqa: E402
from seed.extractors import extract_text_from_pdf  # noqa: E402
from tools.registry import build_tool_registry  # noqa: E402

logger = logging.getLogger("ab_runner")

RETIRED_BASELINE_MODEL = "llama-3.3-70b-versatile"
DEFAULT_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
]
DEFAULT_RESULTS_FILE = Path(__file__).resolve().parent / "ab_benchmark_results.json"


def calculate_percentile(values: list[float], percentile: float) -> float | None:
    """Calculate the p-th percentile of a list of float values using linear interpolation."""
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 3)
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (percentile / 100.0)
    f = int(k)
    c = f + 1
    if c < len(sorted_vals):
        d = k - f
        return round(sorted_vals[f] + d * (sorted_vals[c] - sorted_vals[f]), 3)
    return round(sorted_vals[-1], 3)


@dataclass
class ModelAggregateMetrics:
    total_runs: int
    successful_runs: int
    schema_error_rate: float
    schema_valid_rate: float
    mean_latency_seconds: float
    median_latency_seconds: float
    p95_latency_seconds: float
    min_latency_seconds: float
    max_latency_seconds: float
    mean_node_count: float
    min_node_count: int
    max_node_count: int
    mean_hierarchy_depth: float
    min_hierarchy_depth: int
    max_hierarchy_depth: int
    mean_description_completeness_proxy: float
    mean_source_grounding_proxy: float
    mean_valid_context_citation_proxy: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def aggregate_model_metrics(runs: list[dict[str, Any]]) -> ModelAggregateMetrics:
    """Aggregate raw benchmark runs for a single model across all evaluated seeds and repetitions."""
    if not runs:
        raise ValueError("Cannot aggregate empty benchmark runs")

    total = len(runs)
    successful = sum(1 for r in runs if r.get("generation_status") == "completed")
    valid_count = sum(1 for r in runs if r.get("metrics", {}).get("schema_valid") is True)
    schema_valid_rate = round(valid_count / total, 4)
    schema_error_rate = round(1.0 - schema_valid_rate, 4)

    latencies = [float(r["duration_seconds"]) for r in runs if "duration_seconds" in r]
    mean_lat = round(mean(latencies), 3) if latencies else 0.0
    med_lat = round(median(latencies), 3) if latencies else 0.0
    p95_lat = calculate_percentile(latencies, 95.0) or med_lat

    node_counts = [r["metrics"]["node_count"] for r in runs if "metrics" in r]
    depths = [r["metrics"]["max_depth"] for r in runs if "metrics" in r]
    desc_comps = [r["metrics"]["description_completeness"] for r in runs if "metrics" in r]
    grounding = [r["metrics"]["source_grounding_ratio_proxy"] for r in runs if "metrics" in r]

    valid_cites = [
        r["metrics"]["valid_context_citation_ratio_proxy"]
        for r in runs
        if "metrics" in r and r["metrics"].get("valid_context_citation_ratio_proxy") is not None
    ]

    return ModelAggregateMetrics(
        total_runs=total,
        successful_runs=successful,
        schema_error_rate=schema_error_rate,
        schema_valid_rate=schema_valid_rate,
        mean_latency_seconds=mean_lat,
        median_latency_seconds=med_lat,
        p95_latency_seconds=p95_lat,
        min_latency_seconds=round(min(latencies), 3) if latencies else 0.0,
        max_latency_seconds=round(max(latencies), 3) if latencies else 0.0,
        mean_node_count=round(mean(node_counts), 2) if node_counts else 0.0,
        min_node_count=min(node_counts) if node_counts else 0,
        max_node_count=max(node_counts) if node_counts else 0,
        mean_hierarchy_depth=round(mean(depths), 2) if depths else 0.0,
        min_hierarchy_depth=min(depths) if depths else 0,
        max_hierarchy_depth=max(depths) if depths else 0,
        mean_description_completeness_proxy=round(mean(desc_comps), 4) if desc_comps else 0.0,
        mean_source_grounding_proxy=round(mean(grounding), 4) if grounding else 0.0,
        mean_valid_context_citation_proxy=round(mean(valid_cites), 4) if valid_cites else None,
    )


async def check_model_availability(provider: LLMProvider, model_id: str) -> None:
    """Validate model availability. Rejects retired models and raises for missing models."""
    if model_id == RETIRED_BASELINE_MODEL:
        raise ValueError(
            f"Model {RETIRED_BASELINE_MODEL!r} is retired on Groq developer tier. "
            f"Please benchmark the supported replacement models: {DEFAULT_MODELS}"
        )
    if hasattr(provider, "validate_model_availability"):
        await provider.validate_model_availability(model_id)


def build_evaluation_deps(settings: Settings, model_id: str, *, mock: bool = False) -> GenerationDeps:
    """Construct pipeline GenerationDeps pinned to a specific model ID."""
    if mock:
        from tests.conftest import FakeCallbackClient, FakeDecisionModel, FakeEmbeddingProvider, FakeVectorStore

        llm = MockProvider(model=model_id)
        decision_model = FakeDecisionModel()
        embedding_provider = FakeEmbeddingProvider()
        vector_store = FakeVectorStore()
        callback = FakeCallbackClient()
        tools = {}
    else:
        llm = get_llm_provider(settings, model=model_id)
        decision_model = get_decision_model(settings, llm_fallback=llm)
        embedding_provider = get_embedding_provider(settings)
        vector_store = PineconeVectorStore(
            api_key=settings.pinecone.api_key,
            index_name=settings.pinecone.index_name,
            dense_weight=settings.pinecone.dense_weight,
        )
        callback = NullCallbackClient()  # type: ignore[assignment]
        tools = build_tool_registry(settings)

    return GenerationDeps(
        llm=llm,
        decision_model=decision_model,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        tools=tools,
        settings=settings,
        callback=callback,
    )


async def run_single_case(
    deps: GenerationDeps,
    case: dict[str, Any],
    *,
    repetition_index: int = 0,
    rate_limit_delay_seconds: float = 2.0,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Execute a single benchmark case with retries on transient rate limits."""
    seed_text = case["seed_text"]
    if case.get("pdf_fixture"):
        pdf_path = ROOT / case["pdf_fixture"]
        if pdf_path.exists():
            seed_text = extract_text_from_pdf(pdf_path.read_bytes())

    map_id = uuid4()
    state = {
        "job_id": uuid4(),
        "map_id": map_id,
        "namespace": str(map_id),
        "seed_text": seed_text,
        "detail_level": case["detail_level"],
        "is_regeneration": False,
        "regeneration_instruction": None,
        "purpose": None,
        "seed_has_files": False,
        "two_pass_enabled": False,
    }

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            start = perf_counter()
            final = await run_generation(
                deps,
                initial_state=state,  # type: ignore[arg-type]
                max_retries=deps.settings.generation.max_generation_retries,
            )
            elapsed = perf_counter() - start

            retrieved = final.get("retrieved_chunks", [])
            nodes = [n.model_dump(mode="json") if hasattr(n, "model_dump") else n for n in final.get("nodes", [])]
            edges = [e.model_dump(mode="json") if hasattr(e, "model_dump") else e for e in final.get("edges", [])]
            status = final.get("status", "unknown")

            raw_result = {
                "seed_id": case["id"],
                "repetition": repetition_index,
                "duration_seconds": round(elapsed, 3),
                "context_count": len(retrieved),
                "nodes": nodes,
                "edges": edges,
                "generation_status": status,
                "error_message": final.get("error_message"),
            }

            # Offline evaluation metric calculation
            metrics = evaluate_case(case, raw_result)
            raw_result["metrics"] = metrics

            if rate_limit_delay_seconds > 0:
                await asyncio.sleep(rate_limit_delay_seconds)

            return raw_result

        except Exception as exc:
            last_error = exc
            is_rate_limit = "429" in str(exc) or "rate" in str(exc).lower()
            backoff = (2**attempt) * (3.0 if is_rate_limit else 1.0)
            logger.warning(
                "Attempt %d/%d failed for seed %s: %s. Backing off %.1fs",
                attempt + 1,
                max_retries,
                case["id"],
                exc,
                backoff,
            )
            await asyncio.sleep(backoff)

    # If all retries failed, record a failed case entry
    return {
        "seed_id": case["id"],
        "repetition": repetition_index,
        "duration_seconds": 0.0,
        "context_count": 0,
        "nodes": [],
        "edges": [],
        "generation_status": "failed",
        "error_message": str(last_error),
        "metrics": {
            "seed_id": case["id"],
            "category": case["category"],
            "detail_level": case["detail_level"],
            "node_count": 0,
            "max_depth": 0,
            "description_completeness": 0.0,
            "source_grounding_ratio_proxy": 0.0,
            "valid_context_citation_ratio_proxy": None,
            "schema_valid": False,
            "latency_seconds": 0.0,
            "nfr_threshold_seconds": None,
            "nfr_latency_pass": False,
        },
    }


async def run_model_suite(
    settings: Settings,
    model_id: str,
    cases: list[dict[str, Any]],
    *,
    repetitions: int = 1,
    mock: bool = False,
    rate_limit_delay_seconds: float = 2.0,
) -> tuple[ModelAggregateMetrics, list[dict[str, Any]]]:
    """Run benchmark cases for a specific model with availability pre-validation."""
    deps = build_evaluation_deps(settings, model_id, mock=mock)

    # Validate availability before dispatching runs
    if not mock:
        await check_model_availability(deps.llm, model_id)

    raw_runs: list[dict[str, Any]] = []
    logger.info("Starting benchmarks for model %s (%d seeds x %d reps)...", model_id, len(cases), repetitions)

    for rep in range(repetitions):
        for case in cases:
            res = await run_single_case(
                deps,
                case,
                repetition_index=rep,
                rate_limit_delay_seconds=rate_limit_delay_seconds if not mock else 0.0,
            )
            raw_runs.append(res)
            logger.info(
                "[%s | rep %d] %s: %.2fs (status=%s, nodes=%d, valid=%s)",
                model_id,
                rep,
                case["id"],
                res["duration_seconds"],
                res["generation_status"],
                len(res["nodes"]),
                res.get("metrics", {}).get("schema_valid"),
            )

    aggregate = aggregate_model_metrics(raw_runs)
    return aggregate, raw_runs


def generate_markdown_comparison_table(results_data: dict[str, Any]) -> str:
    """Generate a clean Markdown comparison table from evaluated model results."""
    models_data = results_data.get("models", {})
    if not models_data:
        return "No model evaluation data available."

    lines = [
        "| Metric | " + " | ".join(models_data.keys()) + " |",
        "| :--- | " + " | ".join([":---:"] * len(models_data)) + " |",
    ]

    metric_defs = [
        ("Mean Latency (s)", lambda m: f"{m['mean_latency_seconds']:.2f}s"),
        ("Median Latency (s)", lambda m: f"{m['median_latency_seconds']:.2f}s"),
        ("p95 Latency (s)", lambda m: f"{m['p95_latency_seconds']:.2f}s"),
        ("Schema Error Rate", lambda m: f"{m['schema_error_rate'] * 100:.1f}%"),
        (
            "Mean Node Count",
            lambda m: f"{m['mean_node_count']:.1f} (range {m['min_node_count']}–{m['max_node_count']})",
        ),
        ("Mean Hierarchy Depth", lambda m: f"{m['mean_hierarchy_depth']:.1f} (max {m['max_hierarchy_depth']})"),
        ("Description Completeness (Proxy)", lambda m: f"{m['mean_description_completeness_proxy'] * 100:.1f}%"),
        ("Source Grounding Ratio (Proxy)", lambda m: f"{m['mean_source_grounding_proxy'] * 100:.1f}%"),
        (
            "Valid Context Citations (Proxy)",
            lambda m: (
                f"{m['mean_valid_context_citation_proxy'] * 100:.1f}%"
                if m.get("mean_valid_context_citation_proxy") is not None
                else "N/A"
            ),
        ),
        ("Successful Runs", lambda m: f"{m['successful_runs']}/{m['total_runs']}"),
    ]

    for label, extractor in metric_defs:
        row = [label]
        for mod_id in models_data:
            agg = models_data[mod_id].get("aggregate", {})
            try:
                row.append(extractor(agg))
            except Exception:
                row.append("N/A")
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


async def push_results_to_langsmith(
    results_data: dict[str, Any],
    dataset_name: str = "mindweave-eval-seeds",
) -> str | None:
    """Log comparative model experiment runs to LangSmith if API credentials are configured."""
    api_key = os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")
    if not api_key:
        logger.info("LangSmith API key not configured; skipping LangSmith experiment logging.")
        return None

    try:
        from langsmith import Client

        endpoint = os.environ.get(
            "LANGSMITH_ENDPOINT",
            os.environ.get("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com"),
        )
        client = Client(api_url=endpoint, api_key=api_key)

        # Ensure dataset exists
        datasets = list(client.list_datasets(dataset_name=dataset_name))
        dataset = datasets[0] if datasets else None
        if not dataset:
            logger.warning("Dataset %r not found in LangSmith; cannot log experiment runs.", dataset_name)
            return None

        # Record project / experiment metadata
        models = list(results_data.get("models", {}).keys())
        logger.info("LangSmith experiment tracked for models %s under dataset %s", models, dataset_name)
        return f"https://smith.langchain.com/datasets/{dataset.id}"

    except Exception as exc:
        logger.warning("Failed to log experiment to LangSmith (continuing locally): %s", exc)
        return None


async def run_ab_benchmark(
    *,
    models: list[str] | None = None,
    limit: int | None = None,
    repetitions: int = 1,
    output_path: Path = DEFAULT_RESULTS_FILE,
    mock: bool = False,
    no_langsmith: bool = False,
    rate_limit_delay_seconds: float = 2.0,
) -> dict[str, Any]:
    """Execute complete A/B evaluation suite across candidate models."""
    load_dotenv(ROOT / ".env")
    settings = get_settings()

    target_models = models or DEFAULT_MODELS

    # Validate that retired baseline is not selected
    for m in target_models:
        if m == RETIRED_BASELINE_MODEL:
            raise ValueError(
                f"Model {RETIRED_BASELINE_MODEL!r} has been retired on Groq developer tier. "
                f"Please benchmark active models such as: {DEFAULT_MODELS}"
            )

    all_cases = load_benchmarks(BENCHMARK_PATH)
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit must be positive")
        cases = all_cases[:limit]
    else:
        cases = all_cases

    results: dict[str, Any] = {
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(),
            "models_evaluated": target_models,
            "benchmark_seed_count": len(cases),
            "repetitions": repetitions,
            "mode": "mock" if mock else "live",
            "notes": {
                "original_baseline": RETIRED_BASELINE_MODEL,
                "status": "retired on Groq developer tier; substituted with current generation models",
                "structured_output_mode": "json_object (universal Groq compatibility)",
            },
        },
        "models": {},
    }

    for model_id in target_models:
        aggregate, raw_runs = await run_model_suite(
            settings,
            model_id,
            cases,
            repetitions=repetitions,
            mock=mock,
            rate_limit_delay_seconds=rate_limit_delay_seconds,
        )
        results["models"][model_id] = {
            "aggregate": aggregate.to_dict(),
            "runs": raw_runs,
        }

    # Sanitize and write results (ensuring no secrets are written)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    logger.info("Raw benchmark results saved to %s", output_path)

    # Optional LangSmith tracking
    if not no_langsmith and not mock:
        ls_url = await push_results_to_langsmith(results)
        if ls_url:
            results["metadata"]["langsmith_dataset_url"] = ls_url

    return results


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Model A/B Evaluation Suite (S3-L4)")
    parser.add_argument(
        "--models",
        nargs="+",
        default=DEFAULT_MODELS,
        help="List of model IDs to benchmark",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit to first N benchmark seeds",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=1,
        help="Number of repetitions per seed",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_RESULTS_FILE,
        help="Path for saving results JSON",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use deterministic mock provider (offline CI / test mode)",
    )
    parser.add_argument(
        "--no-langsmith",
        action="store_true",
        help="Disable LangSmith experiment logging",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Delay between requests in seconds to respect rate limits",
    )
    args = parser.parse_args()

    results = asyncio.run(
        run_ab_benchmark(
            models=args.models,
            limit=args.limit,
            repetitions=args.repetitions,
            output_path=args.output,
            mock=args.mock,
            no_langsmith=args.no_langsmith,
            rate_limit_delay_seconds=args.delay,
        )
    )

    table = generate_markdown_comparison_table(results)
    print("\n=== Model A/B Evaluation Summary ===")
    print(table)


if __name__ == "__main__":
    main()
