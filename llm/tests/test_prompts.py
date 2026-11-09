from __future__ import annotations

import pytest
from orchestration.policy import classify_seed_mode, plan_research
from orchestration.prompts import (
    JSONExtractionError,
    build_generation_system_prompt,
    build_generation_user_prompt,
    extract_json_object,
    size_targets,
)


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
        supporting_context=["Context item 1", "Context item 2"],
        regeneration_instruction="Focus on neural nets",
        previous_validation_errors=["Need at least 10 nodes"],
        mode="topic",
    )
    assert "Detail level: overview" in user_prompt
    assert "Mode: topic" in user_prompt
    assert "Topic and ideas:" in user_prompt
    assert "Focus on neural nets" in user_prompt
    assert "[1] Context item 1" in user_prompt
    assert "Need at least 10 nodes" in user_prompt


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
