"""
Manual end-to-end run with no ARQ, no backend — for local testing only.
Builds GenerationDeps directly (mirroring worker/settings.py::on_startup)
and calls orchestration.graph.run_generation, printing the result.
Needs real Pinecone + embedding + LLM credentials; nothing here is mocked.

Usage:
    cp .env.example .env   # fill in real values
    python scripts/run_local.py generate <map_id> --topic "..." --ideas "..."
    python scripts/run_local.py regenerate <map_id> --instruction "..."
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from callback.client import NullCallbackClient  # noqa: E402
from config import get_settings  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from embeddings.registry import get_embedding_provider  # noqa: E402
from orchestration.deps import GenerationDeps  # noqa: E402
from orchestration.graph import run_generation  # noqa: E402
from orchestration.nodes import reconstruct_seed_from_namespace  # noqa: E402
from orchestration.state import GenerationState  # noqa: E402
from providers.registry import get_decision_model, get_llm_provider  # noqa: E402
from rag.pinecone_store import PineconeVectorStore  # noqa: E402
from seed.normalizer import SeedSubmission, normalize_seed  # noqa: E402
from tools.registry import build_tool_registry  # noqa: E402

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate")
    gen.add_argument("map_id")
    gen.add_argument("--topic", default=None)
    gen.add_argument("--ideas", default=None)
    gen.add_argument("--detail-level", default=None, choices=["overview", "balanced", "detailed"])

    regen = sub.add_parser("regenerate")
    regen.add_argument("map_id")
    regen.add_argument("--instruction", required=True)
    regen.add_argument("--detail-level", default=None, choices=["overview", "balanced", "detailed"])

    args = parser.parse_args()
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
        callback=NullCallbackClient(),
    )

    map_id = uuid.UUID(args.map_id)
    namespace = str(map_id)
    job_id = uuid.uuid4()

    initial_state: GenerationState
    if args.command == "generate":
        normalized = await normalize_seed(
            submission=SeedSubmission(topic=args.topic, ideas=args.ideas, files=[]),
            decision_model=deps.decision_model,
        )
        initial_state = {
            "job_id": job_id,
            "map_id": map_id,
            "namespace": namespace,
            "seed_text": normalized.combined_text,
            "detail_level": args.detail_level,
            "regeneration_instruction": None,
            "is_regeneration": False,
        }
    else:
        seed_text = await reconstruct_seed_from_namespace(deps, namespace=namespace)
        initial_state = {
            "job_id": job_id,
            "map_id": map_id,
            "namespace": namespace,
            "seed_text": seed_text,
            "detail_level": args.detail_level,
            "regeneration_instruction": args.instruction,
            "is_regeneration": True,
        }

    final_state = await run_generation(
        deps,
        initial_state=initial_state,
        max_retries=settings.generation.max_generation_retries,
    )
    print(f"status={final_state.get('status')}")
    print(f"nodes={len(final_state.get('nodes', []))} edges={len(final_state.get('edges', []))}")
    if final_state.get("status") != "completed":
        print(f"error={final_state.get('error_message')}")


if __name__ == "__main__":
    asyncio.run(main())
