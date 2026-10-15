from __future__ import annotations

import pytest

from orchestration.prompts import JSONExtractionError, extract_json_object


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
