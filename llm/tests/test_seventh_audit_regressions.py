"""Regression tests for 7th audit items (U2 summary memory, U6 PDF multi-line headers/footers,
L11 citation quality gate, L14 3-gram deduplication).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from contracts.models import ChatMessage, ChatTurnRequest, ChunkSource
from orchestration.nodes import _deduplicate_against_seed
from rag.pinecone_store import RetrievedChunkRecord
from rag.retrieval import RetrievedChunk
from seed.extractors import extract_text_from_pdf
from validation.schema import DraftMindMap, run_quality_checks
from validation.schema import _DraftEdge as DraftEdge
from validation.schema import _DraftNode as DraftNode
from worker.tasks import chat_turn_task


def _make_retrieved_chunk(text: str, *, chunk_id: str, score: float = 0.9) -> RetrievedChunk:
    record = RetrievedChunkRecord(
        id=chunk_id,
        chunk_text=text,
        source=ChunkSource.UPLOADED_SEED,
        source_title=None,
        source_url=None,
        score=score,
    )
    return RetrievedChunk(record=record, relevance_score=score)


@pytest.mark.asyncio
async def test_chat_summary_includes_current_exchange():
    """U2 / R4: Verify chat_turn_task passes history + current user message + assistant message
    to summarize_conversation, ensuring the summary represents the complete conversation
    through the new assistant message without losing exchanges.
    """
    map_id = uuid.uuid4()
    thread_id = uuid.uuid4()
    job_id = uuid.uuid4()

    history = [
        ChatMessage(role="user", content="Turn 1 user"),
        ChatMessage(role="assistant", content="Turn 1 assistant"),
        ChatMessage(role="user", content="Turn 2 user"),
        ChatMessage(role="assistant", content="Turn 2 assistant"),
    ]

    req = ChatTurnRequest(
        job_id=job_id,
        map_id=map_id,
        thread_id=thread_id,
        message="Turn 3 user prompt asking for details",
        history=history,
        summary="Initial summary of earlier topics",
    )

    mock_llm = MagicMock()
    mock_callback = MagicMock()
    mock_callback.complete_chat = AsyncMock()
    mock_callback.aclose = AsyncMock()

    ctx = {
        "llm": mock_llm,
        "callback": mock_callback,
        "decision_model": MagicMock(),
        "embedding_provider": MagicMock(),
        "vector_store": MagicMock(),
        "tools": {},
        "settings": MagicMock(),
    }

    # Fake turn execution
    fake_response = MagicMock()
    fake_response.answer = "Turn 3 assistant response with explanations."
    fake_response.action_proposal = None
    fake_response.needs_clarification = False
    fake_response.cited_node_ids = []
    fake_response.source_refs = []
    fake_response.model_dump.return_value = {"status": "ok"}

    captured_messages: list[ChatMessage] = []

    async def fake_summarize(llm, messages, previous_summary):
        captured_messages.extend(messages)
        return "Updated rolling summary covering all 3 turns."

    with (
        patch("worker.tasks.run_chat_turn", AsyncMock(return_value=fake_response)),
        patch("chat.memory.summarize_conversation", side_effect=fake_summarize),
    ):
        await chat_turn_task(ctx, req.model_dump(mode="json"))

    # 4 history + 1 current user + 1 assistant message = 6 messages total
    assert len(captured_messages) == 6
    assert captured_messages[-2].role == "user"
    assert captured_messages[-2].content == "Turn 3 user prompt asking for details"
    assert captured_messages[-1].role == "assistant"
    assert captured_messages[-1].content == "Turn 3 assistant response with explanations."

    # Callback was called with the fresh summary
    mock_callback.complete_chat.assert_called_once()
    assert mock_callback.complete_chat.call_args[1]["summary"] == "Updated rolling summary covering all 3 turns."


def test_pdf_multiline_running_header_and_footer_filtered():
    """U6 / L3: Multi-line headers (e.g. line 1 and line 2) and recurring footers
    appearing across 2+ pages are detected and filtered from content/headings.
    """
    mock_page1 = MagicMock()
    mock_page1.extract_text.return_value = (
        "ADVANCED BIOLOGY TEXTBOOK\n"  # Running header line 1
        "CHAPTER 4: CELLULAR RESPIRATION\n"  # Running header line 2
        "Glycolysis converts glucose into pyruvate.\n"
        "Page 45\n"  # Running footer
        "CONFIDENTIAL DRAFT"  # Running footer line 2
    )

    mock_page2 = MagicMock()
    mock_page2.extract_text.return_value = (
        "ADVANCED BIOLOGY TEXTBOOK\n"  # Running header line 1
        "CHAPTER 4: CELLULAR RESPIRATION\n"  # Running header line 2
        "The Krebs cycle occurs in the mitochondrial matrix.\n"
        "Page 46\n"  # Running footer (page number regex)
        "CONFIDENTIAL DRAFT"  # Running footer line 2
    )

    mock_reader = MagicMock()
    mock_reader.is_encrypted = False
    mock_reader.pages = [mock_page1, mock_page2]
    mock_reader.outline = None

    with patch("pypdf.PdfReader", return_value=mock_reader):
        text = extract_text_from_pdf(b"%PDF-mock")

    # Recurring headers on lines 1 and 2 must NOT be converted to markdown headings '##'
    assert "## ADVANCED BIOLOGY TEXTBOOK" not in text
    assert "## CHAPTER 4: CELLULAR RESPIRATION" not in text

    # Recurring footers must be completely stripped
    assert "CONFIDENTIAL DRAFT" not in text
    assert "Page 45" not in text
    assert "Page 46" not in text

    # Substantive content is preserved
    assert "Glycolysis converts glucose into pyruvate." in text
    assert "The Krebs cycle occurs in the mitochondrial matrix." in text


def test_quality_gate_uncited_context_triggers_failed_pass():
    """L11: When supporting context is provided but no nodes include citation sources ('ctx:<n>'),
    quality gate adds a warning AND sets passed = False, triggering generation retry with feedback.
    """
    draft = DraftMindMap(
        nodes=[
            DraftNode(id="root", label="Photosynthesis", description="Main concept", sources=[]),
            DraftNode(id="child1", label="Light Reactions", description="Thylakoid reactions", sources=[]),
            DraftNode(id="child2", label="Calvin Cycle", description="Stroma carbon fixation", sources=[]),
        ],
        edges=[
            DraftEdge(source="root", target="child1", label="phase 1"),
            DraftEdge(source="root", target="child2", label="phase 2"),
        ],
    )

    supporting_context = [
        _make_retrieved_chunk(
            "Light reactions produce ATP and NADPH in the thylakoid membrane.",
            chunk_id="ctx-1",
        )
    ]

    report = run_quality_checks(
        draft,
        target_node_range=(2, 10),
        target_max_depth=3,
        supporting_context=supporting_context,
    )

    assert report.passed is False
    assert any("Supporting context was provided but no nodes include citation sources" in w for w in report.warnings)


def test_quality_gate_excessive_fan_out_logs_warning_only():
    """User requirement: Fan-out > 8 should log a warning only and NOT fail the pass (passed=True)."""
    # 1 root + 9 direct children = fan-out of 9, plus 1 grandchild so tree depth is 2 (passes depth check)
    nodes = [DraftNode(id="root", label="Root Concept", description="Root description", sources=["ctx:1"])]
    edges = []
    for i in range(1, 10):
        nid = f"child_{i}"
        nodes.append(DraftNode(id=nid, label=f"Child {i}", description=f"Description {i}", sources=["ctx:1"]))
        edges.append(DraftEdge(source="root", target=nid, label=f"rel_{i}"))

    # Add a grandchild to achieve depth = 2
    nodes.append(DraftNode(id="grandchild_1", label="Sub-concept", description="Sub description", sources=["ctx:1"]))
    edges.append(DraftEdge(source="child_1", target="grandchild_1", label="sub_rel"))

    draft = DraftMindMap(nodes=nodes, edges=edges)

    supporting_context = [
        _make_retrieved_chunk(
            "Some supporting context.",
            chunk_id="ctx-1",
        )
    ]

    report = run_quality_checks(
        draft,
        target_node_range=(5, 15),
        target_max_depth=3,
        supporting_context=supporting_context,
    )

    # Fan out warning is present
    assert any("direct branches (max recommended is 8)" in w for w in report.warnings)
    # But passed remains True (warning only)
    assert report.passed is True


def test_deduplication_3gram_preserves_distinct_vocabulary():
    """L14: Using 3-gram shingles ensures that documents sharing technical vocabulary
    (e.g., 'quantum', 'computing', 'algorithm', 'qubit') in different sentences are NOT
    falsely dropped, while true verbatim copies are filtered out.
    """
    seed = (
        "Quantum computing utilizes qubits and superposition to process complex mathematical states. "
        "Quantum algorithms can search unsorted databases quadratically faster than classical computers."
    )

    # Related but distinct document sharing common vocabulary in completely different sentences
    novel_chunk = _make_retrieved_chunk(
        "Error correction in quantum computing requires fault-tolerant logical qubits. "
        "Physical qubits suffer from environmental decoherence and thermal noise.",
        chunk_id="novel-doc",
    )

    # Near verbatim copy of the seed
    duplicate_chunk = _make_retrieved_chunk(
        "Quantum computing utilizes qubits and superposition to process complex mathematical states. "
        "Quantum algorithms can search unsorted databases quickly.",
        chunk_id="dup-doc",
    )

    # With 3-gram shingles (shingle_size=3, threshold=0.7)
    surviving = _deduplicate_against_seed(
        [novel_chunk, duplicate_chunk],
        seed_text=seed,
        threshold=0.7,
        shingle_size=3,
    )

    # Novel document survives!
    surviving_ids = [c.record.id for c in surviving]
    assert "novel-doc" in surviving_ids
    # Duplicate document is filtered out!
    assert "dup-doc" not in surviving_ids
