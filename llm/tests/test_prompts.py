from __future__ import annotations

import pytest
from contracts.models import ChunkSource
from orchestration.policy import classify_seed_mode, plan_research
from orchestration.prompts import (
    JSONExtractionError,
    build_generation_system_prompt,
    build_generation_user_prompt,
    extract_json_object,
    size_targets,
)
from rag.pinecone_store import RetrievedChunkRecord
from rag.retrieval import RetrievedChunk

# ---------------------------------------------------------------------------
# Helper — build a minimal RetrievedChunk for prompt tests.
# section_path is set as a dynamic attribute to mirror the forward-compatible
# getattr guard used in build_generation_user_prompt (S2-L4 not yet merged).
# ---------------------------------------------------------------------------


def _make_chunk(
    text: str,
    *,
    section_path: str | None = None,
    url: str | None = None,
) -> RetrievedChunk:
    record = RetrievedChunkRecord(
        id="test-id",
        chunk_text=text,
        source=ChunkSource.UPLOADED_SEED,
        source_title=None,
        source_url=url,
        score=1.0,
    )
    if section_path is not None:
        record.section_path = section_path  # type: ignore[attr-defined]
    return RetrievedChunk(record=record, relevance_score=1.0)


def test_plain_json_parses():
    text = '{"nodes": [], "edges": []}'
    assert extract_json_object(text) == {"nodes": [], "edges": []}


def test_markdown_fenced_json_parses():
    text = '```json\n{"nodes": [], "edges": []}\n```'
    assert extract_json_object(text) == {"nodes": [], "edges": []}


def test_json_with_surrounding_prose_extracts():
    text = 'Here is the mind map:\n{"nodes": [], "edges": []}\nHope that helps!'
    assert extract_json_object(text) == {"nodes": [], "edges": []}


def test_none_text_raises():
    with pytest.raises(JSONExtractionError):
        extract_json_object(None)


def test_no_json_object_raises():
    with pytest.raises(JSONExtractionError):
        extract_json_object("I couldn't generate a mind map for that.")


def test_malformed_json_raises():
    with pytest.raises(JSONExtractionError):
        extract_json_object('{"nodes": [}')


def test_size_targets_overview_and_scaling():
    targets = size_targets("overview", seed_tokens=100, mode="topic", max_output_tokens=8000)
    assert targets.level == "overview"
    assert targets.min_nodes == 10
    assert targets.max_nodes == 18
    assert targets.min_depth == 2

    # In document mode with large seed, scale should increase nodes
    large_targets = size_targets("balanced", seed_tokens=8000, mode="document", max_output_tokens=8000)
    assert large_targets.max_nodes > 35


def test_build_generation_system_prompt():
    targets = size_targets("balanced", seed_tokens=200, mode="document", max_output_tokens=8000)
    prompt = build_generation_system_prompt(targets, mode="document")
    assert "balanced level" in prompt
    assert f"{targets.min_nodes} to {targets.max_nodes} nodes" in prompt
    assert "Mode DOCUMENT" in prompt
    assert '"sources": ["seed"]' in prompt


def test_build_generation_user_prompt():
    user_prompt = build_generation_user_prompt(
        seed_text="Machine learning concepts",
        detail_level="overview",
        target_node_range=(10, 18),
        target_max_depth=2,
        supporting_context=[
            _make_chunk("Context item 1"),
            _make_chunk("Context item 2"),
        ],
        regeneration_instruction="Focus on neural nets",
        previous_validation_errors=["Need at least 10 nodes"],
        mode="topic",
    )
    assert "Detail level: overview" in user_prompt
    assert "Mode: topic" in user_prompt
    assert "Target: 10–18 nodes, max 2 levels deep." in user_prompt
    assert "Topic and ideas:" in user_prompt
    assert "Focus on neural nets" in user_prompt
    assert "Context item 1" in user_prompt
    assert "Need at least 10 nodes" in user_prompt


def test_breadcrumb_rendering_with_section_path():
    """When section_path and source_url exist, breadcrumb header and structural
    guidance instruction must appear in the prompt."""
    chunk = _make_chunk(
        "Detailed content about classification.",
        section_path="Machine Learning > Supervised Learning > Classification",
        url="https://example.com/ml",
    )
    prompt = build_generation_user_prompt(
        seed_text="ML overview",
        detail_level="balanced",
        target_node_range=(20, 35),
        target_max_depth=4,
        supporting_context=[chunk],
        regeneration_instruction=None,
        previous_validation_errors=None,
    )
    assert "Section: Machine Learning > Supervised Learning > Classification" in prompt
    assert "Source: https://example.com/ml" in prompt
    assert "structural guidance" in prompt
    assert "Top-level heading" in prompt


def test_breadcrumb_rendering_without_section_path():
    """When no chunk has section_path, structural guidance block is omitted
    and context renders as a plain numbered block."""
    chunk = _make_chunk("Plain context text.")
    prompt = build_generation_user_prompt(
        seed_text="Topic",
        detail_level="balanced",
        target_node_range=(20, 35),
        target_max_depth=4,
        supporting_context=[chunk],
        regeneration_instruction=None,
        previous_validation_errors=None,
    )
    assert "structural guidance" not in prompt
    assert "Top-level heading" not in prompt
    assert "Plain context text." in prompt
    assert "[1]" in prompt


def test_no_supporting_context_renders_none_retrieved():
    """Empty supporting_context list renders the 'none retrieved' fallback."""
    prompt = build_generation_user_prompt(
        seed_text="Topic",
        detail_level="overview",
        target_node_range=(10, 18),
        target_max_depth=2,
        supporting_context=[],
        regeneration_instruction=None,
        previous_validation_errors=None,
    )
    assert "Supporting context: none retrieved." in prompt
    assert "structural guidance" not in prompt


def test_policy_classification_and_plan():
    short_seed = "Photosynthesis process"
    long_seed = "word " * 300

    assert classify_seed_mode(short_seed, has_files=False) == "topic"
    assert classify_seed_mode(long_seed, has_files=False) == "document"
    assert classify_seed_mode(short_seed, has_files=True) == "document"

    plan = plan_research(
        seed_text=short_seed,
        has_files=False,
        available_tools={"web_search", "arxiv_search"},
        purpose="research",
    )
    assert plan.mode == "topic"
    assert plan.web_search is True
    assert plan.arxiv_search is True
