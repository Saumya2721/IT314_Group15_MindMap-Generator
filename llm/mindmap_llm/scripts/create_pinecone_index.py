"""
Run ONCE before the worker's first real use, and again any time
EMBEDDING_MODEL changes to a different native dimension. Pinecone
indexes are fixed-dimension for their lifetime — there is no "resize";
to switch embedding models you create a new index (and re-ingest).

Usage:
    python scripts/create_pinecone_index.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import get_settings  # noqa: E402
from dotenv import load_dotenv  # noqa: E402


def main() -> None:
    load_dotenv()
    settings = get_settings()

    try:
        from pinecone import Pinecone, ServerlessSpec
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pinecone package not installed. `pip install pinecone`.") from exc

    pc = Pinecone(api_key=settings.pinecone.api_key)
    existing = {idx["name"] for idx in pc.list_indexes()}

    if settings.pinecone.index_name in existing:
        print(f"Index {settings.pinecone.index_name!r} already exists — not recreating it.")
        print(
            "If EMBEDDING_MODEL changed dimension, delete the old index first "
            "(this will lose all stored chunks)."
        )
        return

    pc.create_index(
        name=settings.pinecone.index_name,
        dimension=settings.embedding.dimension,
        metric="dotproduct",  # required for hybrid dense+sparse queries (see rag/pinecone_store.py)
        spec=ServerlessSpec(cloud="aws", region="us-east-1"),
    )
    print(
        f"Created index {settings.pinecone.index_name!r} "
        f"(dimension={settings.embedding.dimension}, "
        f"metric=dotproduct, model={settings.embedding.model})"
    )


if __name__ == "__main__":
    main()
