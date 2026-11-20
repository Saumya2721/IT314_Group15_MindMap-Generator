"""
Push S2-L13 evaluation benchmark seeds to a named LangSmith dataset.

This script is idempotent — running it multiple times will not create
duplicate dataset entries (it checks for the dataset first).

Usage (from llm/):
    python tests/evaluation/langsmith_dataset.py
    python tests/evaluation/langsmith_dataset.py --dataset-name mindweave-eval-seeds

Requirements:
    LANGSMITH_API_KEY must be set in environment.
    LANGSMITH_ENDPOINT should point to the APAC endpoint if applicable.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

BENCHMARKS_PATH = Path(__file__).with_name("benchmarks.json")
DEFAULT_DATASET_NAME = "mindweave-eval-seeds"
DEFAULT_DATASET_DESCRIPTION = (
    "MindWeave S2-L13 quality-gate evaluation seeds. "
    "Each example is a text seed with expected detail_level and category metadata. "
    "Used for LangSmith quality-gate tracking across sprints."
)


def push_dataset(dataset_name: str) -> None:
    api_key = os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")
    endpoint = os.environ.get(
        "LANGSMITH_ENDPOINT",
        os.environ.get("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com"),
    )
    if not api_key:
        raise ValueError(
            "LANGSMITH_API_KEY is not set. Export it before running this script:\n  $env:LANGSMITH_API_KEY='ls-...'"
        )

    try:
        from langsmith import Client
    except ImportError as exc:
        raise RuntimeError("langsmith package not installed. Run: pip install langsmith") from exc

    client = Client(api_url=endpoint, api_key=api_key)

    if not BENCHMARKS_PATH.exists():
        raise FileNotFoundError(f"Benchmarks file not found at {BENCHMARKS_PATH}")

    cases = json.loads(BENCHMARKS_PATH.read_text(encoding="utf-8"))
    logger.info("Loaded %d benchmark seeds from %s", len(cases), BENCHMARKS_PATH)

    # Create or fetch existing dataset
    datasets = list(client.list_datasets(dataset_name=dataset_name))
    if datasets:
        dataset = datasets[0]
        logger.info(
            "Dataset %r already exists (id=%s) — upserting examples",
            dataset_name,
            dataset.id,
        )
    else:
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description=DEFAULT_DATASET_DESCRIPTION,
        )
        logger.info("Created dataset %r (id=%s)", dataset_name, dataset.id)

    # Fetch existing example IDs to avoid duplicates
    existing_examples = {
        ex.metadata.get("seed_id") for ex in client.list_examples(dataset_id=dataset.id) if ex.metadata
    }
    logger.info("%d existing examples found in dataset", len(existing_examples))

    new_count = 0
    for case in cases:
        if case["id"] in existing_examples:
            logger.debug("Skipping existing seed %r", case["id"])
            continue

        client.create_example(
            inputs={
                "seed_text": case["seed_text"],
                "detail_level": case["detail_level"],
            },
            outputs={
                "category": case["category"],
                "source_format": case.get("source_format", "text"),
            },
            metadata={
                "seed_id": case["id"],
                "title": case["title"],
                "word_count": case.get("word_count", 0),
                "detail_level": case["detail_level"],
                "category": case["category"],
            },
            dataset_id=dataset.id,
        )
        new_count += 1
        logger.info("Added seed %r (%s)", case["id"], case["title"])

    logger.info(
        "Done — %d new examples added, %d already existed. Dataset: %s/datasets/%s",
        new_count,
        len(existing_examples),
        endpoint.rstrip("/"),
        dataset.id,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Push evaluation seeds to LangSmith dataset")
    parser.add_argument(
        "--dataset-name",
        default=DEFAULT_DATASET_NAME,
        help=f"LangSmith dataset name (default: {DEFAULT_DATASET_NAME})",
    )
    args = parser.parse_args()
    push_dataset(args.dataset_name)


if __name__ == "__main__":
    main()
