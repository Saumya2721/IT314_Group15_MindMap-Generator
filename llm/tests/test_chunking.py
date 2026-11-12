from __future__ import annotations

from rag.ingestion import chunk_text


def test_short_text_single_chunk():
    text = "Photosynthesis is the process by which plants convert light into energy."
    chunks = chunk_text(text, chunk_size=1200)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_paragraphs_grouped_up_to_chunk_size():
    paragraphs = [f"Paragraph {i} " + ("x" * 100) for i in range(20)]
    text = "\n\n".join(paragraphs)
    chunks = chunk_text(text, chunk_size=500, overlap=50)
    assert len(chunks) > 1
    assert all(len(c) <= 600 for c in chunks)  # allow small slack for paragraph joins


def test_huge_single_paragraph_hard_split_with_overlap():
    text = "y" * 5000
    chunks = chunk_text(text, chunk_size=1000, overlap=100)
    assert len(chunks) >= 5
    # reconstructing should cover the whole original text (overlap means some duplication)
    assert sum(len(c) for c in chunks) >= len(text)


def test_empty_text_returns_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  ") == []
