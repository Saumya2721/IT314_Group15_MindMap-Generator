"""Tests for backend MindMapGenerator, layout computation, and health routes."""
import pytest
from fastapi.testclient import TestClient

import main
from providers.base import LLMProvider, LLMResponse
from services.generator import MindMapGenerator, compute_layout


class MockSuccessLLM(LLMProvider):
    @property
    def name(self) -> str:
        return "mock-success"

    async def generate(self, messages, **kwargs) -> LLMResponse:
        content = """{
            "nodes": [
                {"id": "root", "label": "Artificial Intelligence", "node_type": "root"},
                {"id": "n1", "label": "Deep Learning"},
                {"id": "n2", "label": "NLP"}
            ],
            "edges": [
                {"source": "root", "target": "n1", "label": "subset"},
                {"source": "root", "target": "n2", "label": "subset"}
            ]
        }"""
        return LLMResponse(text=content, tool_calls=[])


class MockRetryThenSuccessLLM(LLMProvider):
    def __init__(self):
        self.call_count = 0

    @property
    def name(self) -> str:
        return "mock-retry"

    async def generate(self, messages, **kwargs) -> LLMResponse:
        self.call_count += 1
        if self.call_count == 1:
            # Bad JSON on first try
            return LLMResponse(text="not json", tool_calls=[])
        # Valid JSON on second try
        return LLMResponse(
            text='{"nodes": [{"id": "root", "label": "Retry Success", "node_type": "root"}], "edges": []}',
            tool_calls=[],
        )


def test_health_check_endpoint():
    client = TestClient(main.app)
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "Mind Map Generator API"


def test_root_endpoint():
    client = TestClient(main.app)
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Welcome to Mind Map Generator API"}


@pytest.mark.asyncio
async def test_mindmap_generator_success():
    gen = MindMapGenerator(MockSuccessLLM())
    result = await gen.generate(topic="Artificial Intelligence")

    assert "nodes" in result
    assert "edges" in result
    assert len(result["nodes"]) == 3
    assert len(result["edges"]) == 2

    # Check root node and layout coordinates
    root = result["nodes"][0]
    assert root["id"] == "root"
    assert root["node_type"] == "root"
    assert root["pos_x"] == 450.0
    assert root["pos_y"] == 300.0

    # Children should have radiating positions
    child1 = result["nodes"][1]
    child2 = result["nodes"][2]
    assert (child1["pos_x"], child1["pos_y"]) != (root["pos_x"], root["pos_y"])
    assert (child2["pos_x"], child2["pos_y"]) != (child1["pos_x"], child1["pos_y"])


@pytest.mark.asyncio
async def test_mindmap_generator_retry():
    gen = MindMapGenerator(MockRetryThenSuccessLLM(), max_retries=2)
    result = await gen.generate(topic="Test Retry")
    assert len(result["nodes"]) == 1
    assert result["nodes"][0]["label"] == "Retry Success"


def test_compute_layout_custom_positions_preserved():
    nodes = [
        {"id": "n1", "label": "A", "pos_x": 100.0, "pos_y": 200.0},
        {"id": "n2", "label": "B", "pos_x": 300.0, "pos_y": 400.0},
    ]
    edges = [{"source": "n1", "target": "n2"}]
    compute_layout(nodes, edges)
    # Positions should not be overwritten if already set
    assert nodes[0]["pos_x"] == 100.0
    assert nodes[0]["pos_y"] == 200.0
