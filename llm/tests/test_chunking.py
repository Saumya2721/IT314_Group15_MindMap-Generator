from __future__ import annotations

import re

from rag.chunking import (
    DocumentChunk,
    StructureAwareChunker,
    TextChunk,
    chunk_text,
)


def test_text_chunk_inherits_str_and_exposes_metadata():
    chunk = TextChunk("Artificial Intelligence", breadcrumb="CS > AI")
    assert isinstance(chunk, str)
    assert chunk == "Artificial Intelligence"
    assert chunk.breadcrumb == "CS > AI"
    assert chunk.section_path == "CS > AI"

    # Direct section_path specification
    chunk2 = TextChunk("Machine Learning", section_path="CS > ML")
    assert chunk2.breadcrumb == "CS > ML"
    assert chunk2.section_path == "CS > ML"

    # Default None
    chunk3 = TextChunk("Plain text")
    assert chunk3.breadcrumb is None
    assert chunk3.section_path is None


def test_empty_and_whitespace_text_returns_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n\t  \n  ") == []


def test_short_text_single_chunk():
    text = "Photosynthesis is the process by which plants convert light into energy."
    chunks = chunk_text(text, chunk_size=1200)
    assert len(chunks) == 1
    assert chunks[0] == text
    assert chunks[0].breadcrumb is None


def test_plain_text_without_headings_boundary_preservation():
    sentences = [
        "First sentence discussing machine learning fundamentals.",
        "Second sentence covering gradient descent optimization.",
        "Third sentence analyzing loss function convergence.",
        "Fourth sentence evaluating model validation scores.",
        "Fifth sentence summarizing hyperparameter tuning results.",
    ]
    text = " ".join(sentences)
    chunks = chunk_text(text, chunk_size=120, overlap=20)
    assert len(chunks) > 1
    for c in chunks:
        assert c.breadcrumb is None
        # Assert sentence/word boundary: chunks shouldn't end or start mid-word
        stripped = c.strip()
        first_word = stripped.split()[0]
        last_word = stripped.split()[-1].rstrip(".")
        assert any(first_word in s for s in sentences)
        assert any(last_word in s for s in sentences)


def test_nested_heading_breadcrumb_inheritance():
    text = (
        "# Deep Learning\n"
        "Introduction to deep neural networks.\n\n"
        "## Architectures\n"
        "Overview of network architectures.\n\n"
        "### Convolutional Networks\n"
        "CNNs are used extensively for visual processing.\n\n"
        "### Recurrent Networks\n"
        "RNNs process sequential time-series data."
    )
    chunks = chunk_text(text, chunk_size=500)
    assert len(chunks) == 4

    assert chunks[0] == "Introduction to deep neural networks."
    assert chunks[0].breadcrumb == "Deep Learning"

    assert chunks[1] == "Overview of network architectures."
    assert chunks[1].breadcrumb == "Deep Learning > Architectures"

    assert chunks[2] == "CNNs are used extensively for visual processing."
    assert chunks[2].breadcrumb == "Deep Learning > Architectures > Convolutional Networks"

    # Sibling heading reset: Recurrent Networks replaces Convolutional Networks
    assert chunks[3] == "RNNs process sequential time-series data."
    assert chunks[3].breadcrumb == "Deep Learning > Architectures > Recurrent Networks"


def test_sibling_heading_breadcrumb_reset_across_multiple_levels():
    text = (
        "# Biology\n"
        "Foundational life sciences.\n\n"
        "## Genetics\n"
        "DNA and heredity.\n\n"
        "### DNA Structure\n"
        "Double helix with base pairs.\n\n"
        "## Ecology\n"
        "Ecosystem interactions.\n\n"
        "# Chemistry\n"
        "Study of matter and reactions."
    )
    chunks = chunk_text(text, chunk_size=500)
    breadcrumbs = [c.breadcrumb for c in chunks]

    assert breadcrumbs == [
        "Biology",
        "Biology > Genetics",
        "Biology > Genetics > DNA Structure",
        "Biology > Ecology",
        "Chemistry",
    ]


def test_sentence_and_word_boundary_preservation():
    words = ["word" + str(i) for i in range(100)]
    text = " ".join(words)
    chunks = chunk_text(text, chunk_size=150, overlap=30)
    assert len(chunks) > 1

    # Verify no cut words across any chunk
    word_pattern = re.compile(r"^word\d+$")
    for chunk in chunks:
        chunk_words = chunk.split()
        for w in chunk_words:
            assert word_pattern.match(w), f"Word '{w}' was sliced in chunk: '{chunk}'"


def test_overlap_deduplication():
    # Long text with multiple sentences
    sentences = [f"Section item {i} contains important domain details." for i in range(25)]
    text = " ".join(sentences)
    chunks = chunk_text(text, chunk_size=200, overlap=50)

    assert len(chunks) > 1
    # Verify adjacent chunks have overlap but are not identical
    for i in range(len(chunks) - 1):
        assert chunks[i] != chunks[i + 1]
        # Overlap check: words from the end of chunk i appear in chunk i + 1
        words_curr = chunks[i].split()
        words_next = chunks[i + 1].split()
        overlap_words = set(words_curr[-3:]).intersection(set(words_next[:5]))
        assert len(overlap_words) > 0, "Expected overlap between consecutive chunks"


def test_huge_single_paragraph_hard_split_with_overlap():
    text = "y" * 5000
    chunks = chunk_text(text, chunk_size=1000, overlap=100)
    assert len(chunks) >= 5
    assert sum(len(c) for c in chunks) >= len(text)
    assert all(len(c) <= 1000 for c in chunks)


def test_very_long_line_without_newlines():
    sentences = [f"This is sentence number {i} in a single unbroken long line." for i in range(30)]
    text = " ".join(sentences)
    chunks = chunk_text(text, chunk_size=250, overlap=40)
    assert len(chunks) > 1
    # Check that chunks cleanly split without slicing words
    for c in chunks:
        assert not c.startswith(" ")
        assert not c.endswith(" ")
        for word in c.split():
            assert not word.startswith(".")
            assert len(word) > 0


def test_headings_only_without_body():
    text = "# Root\n## Branch\n### Leaf"
    chunks = chunk_text(text, chunk_size=500)
    assert len(chunks) == 1
    assert chunks[0] == "Leaf"
    assert chunks[0].breadcrumb == "Root > Branch > Leaf"


def test_structure_aware_chunker_numbered_headings_and_root_breadcrumb():
    text = (
        "1. Chapter 1\n"
        "Introductory content for Chapter 1.\n\n"
        "1.1 Section A\n"
        "Detailed explanation of Section A.\n"
        "- Bullet item 1\n"
        "- Bullet item 2\n"
        "  - Sub-bullet 2.1"
    )
    chunker = StructureAwareChunker(root_title="Root")
    chunks = chunker.chunk(text)

    assert len(chunks) == 2
    assert chunks[0] == "Introductory content for Chapter 1."
    assert chunks[0].breadcrumb == "Root > Chapter 1"
    assert chunks[0].breadcrumb_path == "Root > Chapter 1"
    assert chunks[0].breadcrumbs == ["Root", "Chapter 1"]

    assert "Detailed explanation of Section A." in chunks[1]
    assert "- Bullet item 1" in chunks[1]
    assert chunks[1].breadcrumb == "Root > Chapter 1 > Section A"
    assert chunks[1].breadcrumb_path == "Root > Chapter 1 > Section A"
    assert chunks[1].breadcrumbs == ["Root", "Chapter 1", "Section A"]


def test_structure_aware_chunker_numbered_sibling_reset():
    text = (
        "1. Chapter 1\n"
        "Chapter 1 content.\n\n"
        "1.1 Section A\n"
        "Section A content.\n\n"
        "1.2 Section B\n"
        "Section B content.\n\n"
        "2. Chapter 2\n"
        "Chapter 2 content."
    )
    chunker = StructureAwareChunker(root_title="Root")
    chunks = chunker.chunk(text)

    assert len(chunks) == 4
    assert chunks[0].breadcrumb == "Root > Chapter 1"
    assert chunks[1].breadcrumb == "Root > Chapter 1 > Section A"
    assert chunks[2].breadcrumb == "Root > Chapter 1 > Section B"
    assert chunks[3].breadcrumb == "Root > Chapter 2"
    assert chunks[3].breadcrumbs == ["Root", "Chapter 2"]


def test_structure_aware_chunker_mixed_markdown_and_numbered():
    text = (
        "# System Architecture\n"
        "High level system design.\n\n"
        "1. Ingestion Pipeline\n"
        "Parses input documents.\n\n"
        "1.1 Normalizer\n"
        "Normalizes file formats to text.\n\n"
        "2. Retrieval Pipeline\n"
        "Performs hybrid dense and sparse search."
    )
    chunker = StructureAwareChunker()
    chunks = chunker.chunk(text)

    breadcrumbs = [c.breadcrumb for c in chunks]
    assert breadcrumbs == [
        "System Architecture",
        "System Architecture > Ingestion Pipeline",
        "System Architecture > Ingestion Pipeline > Normalizer",
        "System Architecture > Retrieval Pipeline",
    ]


def test_structure_aware_chunker_preserve_numbered_prefix():
    text = "1. Overview\nProject overview.\n\n1.1 Goals\nProject goals."
    chunker = StructureAwareChunker(preserve_numbered_prefix=True)
    chunks = chunker.chunk(text)

    assert chunks[0].breadcrumb == "1. Overview"
    assert chunks[1].breadcrumb == "1. Overview > 1.1 Goals"


def test_structure_aware_chunker_track_bullets():
    text = "# Topics\n- Machine Learning\n  - Supervised\n    - Linear Models\n"
    chunker = StructureAwareChunker(track_bullets=True)
    chunks = chunker.chunk(text)

    assert len(chunks) >= 3
    paths = [c.breadcrumb for c in chunks]
    assert any("Topics > Machine Learning" in p for p in paths)
    assert any("Supervised" in p for p in paths)
    assert any("Linear Models" in p for p in paths)


def test_document_chunk_model_compatibility():
    chunk = DocumentChunk(
        "Content body",
        breadcrumbs=["Root", "Chapter 1", "Section A"],
        metadata={"heading_level": 2},
    )
    assert isinstance(chunk, str)
    assert chunk == "Content body"
    assert chunk.breadcrumb == "Root > Chapter 1 > Section A"
    assert chunk.breadcrumb_path == "Root > Chapter 1 > Section A"
    assert chunk.breadcrumbs == ["Root", "Chapter 1", "Section A"]
    assert chunk.section_path == "Root > Chapter 1 > Section A"
    assert chunk.metadata["heading_level"] == 2


def test_recursive_fallback_splitting_large_section():
    # Long text with multiple paragraphs exceeding chunk_size=1500
    paragraphs = [
        f"Paragraph {i}: This is detailed content discussing distributed neural networks and system architecture. " * 4
        for i in range(15)
    ]
    text = "\n\n".join(paragraphs)
    chunker = StructureAwareChunker(chunk_size=1500, overlap=200)
    chunks = chunker.chunk(text)

    assert len(chunks) > 1
    assert all(len(c) <= 1500 for c in chunks)
    for i in range(len(chunks) - 1):
        assert chunks[i] != chunks[i + 1]


def test_recursive_fallback_unbreakable_string_emergency_split():
    # Continuous unbroken sequence without spaces
    unbreakable = "x" * 3500
    chunker = StructureAwareChunker(chunk_size=1500, overlap=200)
    chunks = chunker.chunk(unbreakable)

    assert len(chunks) >= 3
    assert all(len(c) <= 1500 for c in chunks)
    assert sum(len(c) for c in chunks) >= len(unbreakable)


def test_plain_unstructured_text_fallback_to_root():
    plain_text = (
        "This is an unstructured document without any markdown headings or numbered sections. "
        "It should cleanly fall back to a [Root] breadcrumb path."
    )
    chunker = StructureAwareChunker(chunk_size=1500, overlap=200)
    chunks = chunker.chunk(plain_text)

    assert len(chunks) == 1
    assert chunks[0].breadcrumb == "Root"
    assert chunks[0].breadcrumb_path == "Root"
    assert chunks[0].breadcrumbs == ["Root"]


def test_plain_unstructured_text_explicit_disable_fallback():
    plain_text = "Plain text without structure and fallback root disabled."
    chunker = StructureAwareChunker(fallback_root=None, chunk_size=1500)
    chunks = chunker.chunk(plain_text)

    assert len(chunks) == 1
    assert chunks[0].breadcrumb is None
    assert chunks[0].breadcrumbs == []


def test_overlap_never_slices_words():
    words = [f"token{i}" for i in range(500)]
    text = " ".join(words)
    chunker = StructureAwareChunker(chunk_size=500, overlap=100)
    chunks = chunker.chunk(text)

    assert len(chunks) > 1
    token_pattern = re.compile(r"^token\d+$")
    for chunk in chunks:
        for w in chunk.split():
            assert token_pattern.match(w), f"Word '{w}' was cut mid-word!"
