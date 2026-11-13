"""Optional real-pipeline benchmark runner. Requires configured LLM/Pinecone credentials.

From llm/: uv run python tests/evaluation/live_runner.py --output real_outputs.json
No calls are performed by CI; use manually only with credentials and budget approval.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "mindmap_llm") not in sys.path:
    sys.path.insert(0, str(ROOT / "mindmap_llm"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from callback.client import NullCallbackClient  # noqa: E402
from config import get_settings  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from embeddings.registry import get_embedding_provider  # noqa: E402
from harness import load_benchmarks  # noqa: E402
from orchestration.deps import GenerationDeps  # noqa: E402
from orchestration.graph import run_generation  # noqa: E402
from providers.registry import get_decision_model, get_llm_provider  # noqa: E402
from rag.pinecone_store import PineconeVectorStore  # noqa: E402
from seed.extractors import extract_text_from_pdf  # noqa: E402
from tools.registry import build_tool_registry  # noqa: E402


async def run_cases(*, limit: int | None = None) -> list[dict]:
    load_dotenv(ROOT / ".env")
    settings = get_settings()
    llm = get_llm_provider(settings)
    deps = GenerationDeps(
        llm=llm,
        decision_model=get_decision_model(settings, llm_fallback=llm),
        embedding_provider=get_embedding_provider(settings),
        vector_store=PineconeVectorStore(
            api_key=settings.pinecone.api_key,
            index_name=settings.pinecone.index_name,
            dense_weight=settings.pinecone.dense_weight,
        ),
        tools=build_tool_registry(settings),
        settings=settings,
        callback=NullCallbackClient(),  # type: ignore[arg-type]
    )
    results = []
    cases = load_benchmarks()
    if limit is not None:
        cases = cases[:limit]
    for case in cases:
        seed_text = case["seed_text"]
        if case.get("pdf_fixture"):
            pdf_path = ROOT / case["pdf_fixture"]
            seed_text = extract_text_from_pdf(pdf_path.read_bytes())
        map_id = uuid4()  # unique Pinecone namespace per case
        state = {
            "job_id": uuid4(),
            "map_id": map_id,
            "namespace": str(map_id),
            "seed_text": seed_text,
            "detail_level": case["detail_level"],
            "is_regeneration": False,
            "regeneration_instruction": None,
        }
        start = perf_counter()
        final = await run_generation(
            deps,
            initial_state=state,  # type: ignore[arg-type]
            max_retries=settings.generation.max_generation_retries,
        )
        elapsed = perf_counter() - start
        if final.get("status") != "completed":
            print(f"WARNING: {case['id']} failed: {final.get('error_message')}")
        retrieved = final.get("retrieved_chunks", [])
        results.append(
            {
                "seed_id": case["id"],
                "duration_seconds": round(elapsed, 3),
                "context_count": len(retrieved),
                "nodes": [n.model_dump(mode="json") for n in final.get("nodes", [])],
                "edges": [e.model_dump(mode="json") for e in final.get("edges", [])],
                "generation_status": final.get("status"),
            }
        )
        print(f"{case['id']}: {elapsed:.2f}s ({final.get('status')})")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the real LLM pipeline against evaluation fixtures")
    parser.add_argument("--limit", type=int, help="Run first N benchmark cases")
    parser.add_argument("--output", type=Path, default=Path("real_outputs.json"))
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    results = asyncio.run(run_cases(limit=args.limit))
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"Results: {args.output}")


if __name__ == "__main__":
    main()
