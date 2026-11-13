from __future__ import annotations

import re
from uuid import UUID

from contracts.models import ChunkSource
from embeddings.base import EmbeddingProvider
from rag.pinecone_store import PineconeVectorStore, VectorChunk

DEFAULT_CHUNK_SIZE_CHARS = 1200
DEFAULT_CHUNK_OVERLAP_CHARS = 150


class TextChunk(str):
    """A string chunk that preserves structural metadata (breadcrumbs / section_path).

    Inheriting from str ensures full backward compatibility with any code expecting
    a plain str (e.g., len(), ==, JSON serialization, embeddings).
    """

    breadcrumb: str | None
    section_path: str | None
    heading_level: int | None
    page_number: int | None
    char_length: int

    def __new__(
        cls,
        content: str,
        breadcrumb: str | None = None,
        section_path: str | None = None,
        heading_level: int | None = None,
        page_number: int | None = None,
    ) -> TextChunk:
        obj = super().__new__(cls, content)
        path = breadcrumb if breadcrumb is not None else section_path
        obj.breadcrumb = path
        obj.section_path = path
        obj.heading_level = heading_level
        obj.page_number = page_number
        obj.char_length = len(content)
        return obj


def _split_into_boundary_chunks(
    text: str,
    breadcrumb: str | None,
    chunk_size: int,
    overlap: int,
    heading_level: int | None = None,
    page_number: int | None = None,
) -> list[TextChunk]:
    """Split text while preserving sentence and word boundaries (no cut words)."""
    text = text.strip()
    if not text:
        return []

    if len(text) <= chunk_size:
        return [TextChunk(text, breadcrumb=breadcrumb, heading_level=heading_level, page_number=page_number)]

    chunks: list[TextChunk] = []
    start = 0
    total_len = len(text)
    last_end = 0

    while start < total_len:
        # If remaining text fits in chunk_size, finalize if it adds new content
        if total_len - start <= chunk_size:
            final_slice = text[start:].strip()
            if final_slice and total_len > last_end:
                chunks.append(TextChunk(final_slice, breadcrumb=breadcrumb, heading_level=heading_level, page_number=page_number))
            break

        target_end = min(start + chunk_size, total_len)
        window = text[start:target_end]

        # Prioritize boundaries:
        # 1. Paragraph break (\n\n) in the second half of window
        split_pos = -1
        para_idx = window.rfind("\n\n")
        if para_idx >= chunk_size // 2:
            split_pos = para_idx + 2
        else:
            # 2. Sentence boundary in the second half of window
            sentence_matches = list(re.finditer(r"[.!?](?:\s+|\n|$)", window))
            if sentence_matches:
                last_m = sentence_matches[-1]
                if last_m.end() >= chunk_size // 3:
                    split_pos = last_m.end()

        # 3. Word boundary (whitespace) anywhere in window
        if split_pos == -1:
            space_idx = window.rfind(" ")
            newline_idx = window.rfind("\n")
            best_space = max(space_idx, newline_idx)
            if best_space > 0:
                split_pos = best_space + 1

        # 4. Fallback for continuous unbroken string (e.g. 'y' * 5000)
        if split_pos == -1:
            split_pos = chunk_size

        chunk_end = start + split_pos
        chunk_content = text[start:chunk_end].strip()
        if chunk_content and chunk_end > last_end:
            chunks.append(TextChunk(chunk_content, breadcrumb=breadcrumb, heading_level=heading_level, page_number=page_number))
            last_end = chunk_end

        if chunk_end >= total_len:
            break

        # Calculate next start with overlap aligned to a word boundary
        if overlap > 0 and split_pos > overlap:
            raw_next_start = chunk_end - overlap
            next_space = text.find(" ", raw_next_start, chunk_end)
            if next_space != -1 and next_space > start:
                start = next_space + 1
            else:
                start = max(start + 1, raw_next_start)
        else:
            start = chunk_end

    return chunks


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP_CHARS,
) -> list[TextChunk]:
    """Structure-aware chunker with breadcrumb inheritance and word/sentence boundary preservation.

    - Respects Markdown heading hierarchies (# H1, ## H2, ### H3) and passes inherited breadcrumb metadata.
    - Preserves sentence and word boundaries without slicing words in half.
    - Employs overlap deduplication to avoid redundant identical slices.
    - Handles empty, plain text, and unbroken long-string edge cases.
    """
    if not text or not text.strip():
        return []

    lines = text.split("\n")
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$")

    sections: list[tuple[str | None, int | None, int | None, str]] = []
    heading_stack: list[tuple[int, str]] = []
    current_lines: list[str] = []
    current_page: int | None = None
    page_pattern = re.compile(r"^--- Page (\d+) ---$")

    def current_breadcrumb() -> str | None:
        if not heading_stack:
            return None
        return " > ".join(title for _, title in heading_stack)

    for line in lines:
        page_match = page_pattern.match(line.strip())
        if page_match:
            if current_lines:
                content = "\n".join(current_lines).strip()
                if content:
                    sections.append((current_breadcrumb(), heading_stack[-1][0] if heading_stack else None, current_page, content))
                current_lines = []
            current_page = int(page_match.group(1))
            continue
        match = heading_pattern.match(line.strip())
        if match:
            # New heading found: save accumulated content for previous section
            if current_lines:
                content = "\n".join(current_lines).strip()
                if content:
                    sections.append((current_breadcrumb(), heading_stack[-1][0] if heading_stack else None, current_page, content))
                current_lines = []

            hashes, title = match.groups()
            level = len(hashes)
            title = title.strip()

            # Update heading hierarchy stack
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
        else:
            current_lines.append(line)

    # Save final section
    if current_lines:
        content = "\n".join(current_lines).strip()
        if content:
            sections.append((current_breadcrumb(), heading_stack[-1][0] if heading_stack else None, current_page, content))

    # If document had headings but no trailing body text
    if not sections and heading_stack:
        title = heading_stack[-1][1]
        return [TextChunk(title, breadcrumb=current_breadcrumb(), heading_level=heading_stack[-1][0])]

    all_chunks: list[TextChunk] = []
    for breadcrumb, heading_level, page_number, sec_text in sections:
        sec_chunks = _split_into_boundary_chunks(
            sec_text,
            breadcrumb=breadcrumb,
            chunk_size=chunk_size,
            overlap=overlap,
            heading_level=heading_level,
            page_number=page_number,
        )
        all_chunks.extend(sec_chunks)

    return all_chunks


class StructureAwareChunker:
    """Reusable entry point for Markdown-aware chunking used by both ingestion paths."""

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE_CHARS,
        overlap: int = DEFAULT_CHUNK_OVERLAP_CHARS,
    ) -> None:
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, text: str) -> list[TextChunk]:
        return chunk_text(text, chunk_size=self.chunk_size, overlap=self.overlap)


async def ingest_seed_text(
    *,
    vector_store: PineconeVectorStore,
    namespace: str,
    generation_job_id: UUID,
    seed_text: str,
    embedding_provider: EmbeddingProvider,
) -> int:
    """Chunks + embeds the normalized seed text and upserts it into
    Pinecone under the map's namespace, source=uploaded_seed."""
    chunks = StructureAwareChunker().chunk(seed_text)
    if not chunks:
        return 0
    vectors = await embedding_provider.embed([str(c) for c in chunks])
    records = [
        VectorChunk(
            chunk_text=chunk,
            embedding=vector,
            embedding_model=embedding_provider.model_name,
            source=ChunkSource.UPLOADED_SEED,
            generation_job_id=generation_job_id,
            section_path=chunk.section_path,
            heading_level=chunk.heading_level,
            page_number=chunk.page_number,
            char_length=chunk.char_length,
            chunk_index=index,
        )
        for index, (chunk, vector) in enumerate(zip(chunks, vectors))
    ]
    return await vector_store.upsert_chunks(namespace=namespace, chunks=records)


async def ingest_tool_result(
    *,
    vector_store: PineconeVectorStore,
    namespace: str,
    generation_job_id: UUID,
    text: str,
    source: ChunkSource,
    source_title: str | None,
    source_url: str,
    embedding_provider: EmbeddingProvider,
) -> int:
    """Same as ingest_seed_text but for web_search/arxiv tool output,
    where source_url is always populated (mirrors the old
    chk_embeddings_source_url constraint, now enforced by convention
    rather than a DB CHECK)."""
    chunks = StructureAwareChunker().chunk(text)
    if not chunks:
        return 0
    vectors = await embedding_provider.embed([str(c) for c in chunks])
    records = [
        VectorChunk(
            chunk_text=chunk,
            embedding=vector,
            embedding_model=embedding_provider.model_name,
            source=source,
            generation_job_id=generation_job_id,
            source_title=source_title,
            source_url=source_url,
            section_path=chunk.section_path,
            heading_level=chunk.heading_level,
            page_number=chunk.page_number,
            char_length=chunk.char_length,
            chunk_index=index,
        )
        for index, (chunk, vector) in enumerate(zip(chunks, vectors))
    ]
    return await vector_store.upsert_chunks(namespace=namespace, chunks=records)
