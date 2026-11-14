"""
Unit and integration tests for S2-L12: Two-Pass Generation.

Covers:
  1. Pass 1 produces exactly one root.
  2. Pass 1 produces between 4 and 8 top-level branches.
  3. Branch outline respects heading hierarchy.
  4. Every branch receives its own independent retrieval operation.
  5. Branches are expanded concurrently.
  6. Configured concurrency limit (semaphore) is strictly respected.
  7. Branch expansion failure is caught and handled safely.
  8. Expanded branches are merged correctly into a complete graph.
  9. Duplicate node IDs are detected or avoided via namespacing.
  10. Final merged graph is validated with the existing validator and quality gate.
  11. Shared branch expansion works with S2-L7 node regeneration.
  12. Chat elaboration reuses the shared expansion functionality.
  13. Existing generation tests continue to pass.
  14. Existing regeneration and chat tests continue to pass.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from chat.expansion import elaborate_branch
from config import Settings
from contracts.models import NodeDraft
from orchestration.deps import GenerationDeps
from orchestration.expansion import (
    execute_two_pass_generation,
    expand_branch,
    expand_branches_parallel,
    generate_outline,
    merge_expanded_branches,
    validate_outline_draft,
)
from orchestration.graph import run_generation
from orchestration.prompts import extract_headings_from_text
from validation.schema import ValidationFailure, _DraftNode, validate_draft

from tests.conftest import (
    FakeCallbackClient,
    FakeDecisionModel,
    FakeEmbeddingProvider,
    FakeLLMProvider,
    FakeVectorStore,
)


class RecordingVectorStore(FakeVectorStore):
    """Vector store recording all queries for inspection."""

    def __init__(self):
        super().__init__()
        self.queries: list[dict] = []

    async def query(
        self, *, namespace: str, query_text: str, query_embedding: list[float], top_k: int, source_filter=None
    ):
        self.queries.append({"namespace": namespace, "query_text": query_text, "top_k": top_k})
        return await super().query(
            namespace=namespace,
            query_text=query_text,
            query_embedding=query_embedding,
            top_k=top_k,
            source_filter=source_filter,
        )


def _make_outline_payload(n_branches: int = 5) -> dict:
    nodes = [{"id": "root", "label": "Machine Learning", "description": "Core ML overview", "node_type": "default"}]
    edges = []
    for i in range(1, n_branches + 1):
        b_id = f"b{i}"
        nodes.append(
            {"id": b_id, "label": f"Branch {i}", "description": f"Details about branch {i}", "node_type": "default"}
        )
        edges.append({"source": "root", "target": b_id, "label": f"part {i}"})
    return {"nodes": nodes, "edges": edges}


def _make_branch_subgraph_payload(branch_label: str) -> dict:
    return {
        "nodes": [
            {"id": "n1", "label": branch_label, "description": f"{branch_label} root"},
            {"id": "n2", "label": f"{branch_label} Concept A", "description": "Subtopic A"},
            {"id": "n3", "label": f"{branch_label} Concept B", "description": "Subtopic B"},
        ],
        "edges": [
            {"source": "n1", "target": "n2", "label": "includes"},
            {"source": "n1", "target": "n3", "label": "includes"},
        ],
    }


# ---------------------------------------------------------------------------
# Requirement 1 & 2: Pass 1 single root and 4-8 branches
# ---------------------------------------------------------------------------


def test_validate_outline_draft_valid_ranges():
    # 4 to 8 branches are valid
    for k in (4, 5, 6, 7, 8):
        payload = _make_outline_payload(k)
        draft, root_node, branches = validate_outline_draft(payload, min_branches=4, max_branches=8)
        assert root_node.id == "root"
        assert len(branches) == k


def test_validate_outline_draft_rejects_fewer_than_4_branches():
    payload = _make_outline_payload(3)
    with pytest.raises(ValidationFailure, match="must have between 4 and 8 top-level branches"):
        validate_outline_draft(payload, min_branches=4, max_branches=8)


def test_validate_outline_draft_rejects_more_than_8_branches():
    payload = _make_outline_payload(9)
    with pytest.raises(ValidationFailure, match="must have between 4 and 8 top-level branches"):
        validate_outline_draft(payload, min_branches=4, max_branches=8)


def test_validate_outline_draft_rejects_multiple_roots():
    payload = _make_outline_payload(4)
    # Add a disconnected second root
    payload["nodes"].append({"id": "extra_root", "label": "Extra Root", "description": "Bad"})
    with pytest.raises(ValidationFailure, match="(exactly 1 root node|DR-02 requires exactly one central node)"):
        validate_outline_draft(payload)


def test_validate_outline_draft_rejects_depth_greater_than_1():
    payload = _make_outline_payload(4)
    # Add a child under b1 (depth 2)
    payload["nodes"].append({"id": "sub_child", "label": "Deep Child", "description": "Too deep"})
    payload["edges"].append({"source": "b1", "target": "sub_child", "label": "sub"})
    with pytest.raises(ValidationFailure, match="should not have deeper sub-nodes in outline pass"):
        validate_outline_draft(payload)


# ---------------------------------------------------------------------------
# Requirement 3: Heading hierarchy extraction
# ---------------------------------------------------------------------------


def test_heading_hierarchy_extraction():
    text = (
        "# Artificial Intelligence\n\n"
        "Introduction text here...\n\n"
        "## Search & Planning\n\n"
        "Content about search...\n\n"
        "## Knowledge Representation\n\n"
        "Content about ontology...\n\n"
        "## Machine Learning\n\n"
        "Content about learning...\n\n"
        "## Natural Language Processing\n\n"
        "NLP content...\n\n"
        "## Computer Vision\n\n"
        "Vision content..."
    )
    headings = extract_headings_from_text(text)
    assert len(headings) == 6
    assert headings[0] == "# Artificial Intelligence"
    assert headings[1] == "## Search & Planning"
    assert headings[5] == "## Computer Vision"


@pytest.mark.asyncio
async def test_generate_outline_with_headings():
    outline_payload = _make_outline_payload(5)
    deps = GenerationDeps(
        llm=FakeLLMProvider([json.dumps(outline_payload)]),
        decision_model=FakeDecisionModel(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=FakeVectorStore(),
        tools={},
        settings=Settings(),
        callback=FakeCallbackClient(),
    )
    seed = "# Biology\n## Genetics\n## Ecology\n## Evolution\n## Physiology\n## Cell Biology"
    draft, root, branches = await generate_outline(deps, seed_text=seed, min_branches=4, max_branches=8)
    assert root.label == "Machine Learning"
    assert len(branches) == 5


# ---------------------------------------------------------------------------
# Requirement 4: Independent retrieval per branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_branch_receives_independent_retrieval():
    vector_store = RecordingVectorStore()
    branches = [
        NodeDraft(id=uuid.uuid4(), label=f"Branch {i}", description=f"Description for branch {i}") for i in range(1, 5)
    ]
    root = NodeDraft(id=uuid.uuid4(), label="Root Topic", description="Root Description")

    subgraph_payloads = [json.dumps(_make_branch_subgraph_payload(b.label)) for b in branches]
    deps = GenerationDeps(
        llm=FakeLLMProvider(subgraph_payloads),
        decision_model=FakeDecisionModel(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=vector_store,
        tools={},
        settings=Settings(),
        callback=FakeCallbackClient(),
    )

    results = await expand_branches_parallel(
        deps,
        namespace="map-123",
        root_node=root,
        branch_nodes=branches,
        concurrency_limit=2,
    )

    assert len(results) == 4
    for r in results:
        assert r.status == "completed"

    # Verify 4 distinct retrieval queries were made
    assert len(vector_store.queries) == 4
    queries = [q["query_text"] for q in vector_store.queries]
    for i in range(1, 5):
        assert any(f"Branch {i}" in q for q in queries)
        assert any("Root Topic > Branch" in q for q in queries)


# ---------------------------------------------------------------------------
# Requirement 5 & 6: Concurrency and Semaphore Limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrency_limit_is_strictly_respected():
    concurrency_limit = 2
    max_simultaneous = 0
    current_active = 0
    lock = asyncio.Lock()

    class ConcurrencyTrackingLLM(FakeLLMProvider):
        async def generate(self, *args, **kwargs):
            nonlocal current_active, max_simultaneous
            async with lock:
                current_active += 1
                if current_active > max_simultaneous:
                    max_simultaneous = current_active

            # Simulate network latency
            await asyncio.sleep(0.05)

            async with lock:
                current_active -= 1

            return await super().generate(*args, **kwargs)

    branches = [NodeDraft(id=uuid.uuid4(), label=f"Branch {i}") for i in range(1, 7)]
    root = NodeDraft(id=uuid.uuid4(), label="Root")
    subgraph_payloads = [json.dumps(_make_branch_subgraph_payload(b.label)) for b in branches]

    deps = GenerationDeps(
        llm=ConcurrencyTrackingLLM(subgraph_payloads),
        decision_model=FakeDecisionModel(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=FakeVectorStore(),
        tools={},
        settings=Settings(),
        callback=FakeCallbackClient(),
    )

    results = await expand_branches_parallel(
        deps,
        namespace="map-test",
        root_node=root,
        branch_nodes=branches,
        concurrency_limit=concurrency_limit,
    )

    assert len(results) == 6
    # Verify that concurrency never exceeded the configured limit
    assert max_simultaneous <= concurrency_limit
    assert max_simultaneous >= 2


# ---------------------------------------------------------------------------
# Requirement 7: Branch expansion failure handled correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_branch_expansion_failure_handled_without_corrupt_graph():
    branches = [NodeDraft(id=uuid.uuid4(), label=f"Branch {i}") for i in range(1, 5)]
    root = NodeDraft(id=uuid.uuid4(), label="Root")

    # Branch 2 receives invalid JSON
    subgraph_payloads = [
        json.dumps(_make_branch_subgraph_payload("Branch 1")),
        "NOT VALID JSON",  # Branch 2 fails
        json.dumps(_make_branch_subgraph_payload("Branch 3")),
        json.dumps(_make_branch_subgraph_payload("Branch 4")),
    ]

    deps = GenerationDeps(
        llm=FakeLLMProvider(subgraph_payloads),
        decision_model=FakeDecisionModel(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=FakeVectorStore(),
        tools={},
        settings=Settings(),
        callback=FakeCallbackClient(),
    )

    results = await expand_branches_parallel(
        deps,
        namespace="map-fail",
        root_node=root,
        branch_nodes=branches,
        max_retries=0,
    )

    assert results[0].status == "completed"
    assert results[1].status == "failed"
    assert "Branch 2" in results[1].selected_node.label

    # Merging should catch the failed branch and raise ValidationFailure
    with pytest.raises(ValidationFailure, match="Failed expanding branches: Branch 'Branch 2'"):
        merge_expanded_branches(
            outline_root=root,
            branch_results=results,
        )


# ---------------------------------------------------------------------------
# Requirement 8, 9, 10: Merge, ID collision avoidance, and full validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_avoids_id_collisions_and_validates():
    root = _DraftNode(id="root", label="Operating Systems", description="Root OS")
    branches = [NodeDraft(id=uuid.uuid4(), label=f"OS Branch {i}") for i in range(1, 5)]
    subgraph_payloads = [json.dumps(_make_branch_subgraph_payload(b.label)) for b in branches]

    deps = GenerationDeps(
        llm=FakeLLMProvider(subgraph_payloads),
        decision_model=FakeDecisionModel(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=FakeVectorStore(),
        tools={},
        settings=Settings(),
        callback=FakeCallbackClient(),
    )

    results = await expand_branches_parallel(
        deps,
        namespace="map-merge",
        root_node=root,
        branch_nodes=branches,
    )

    merged_draft, nodes, edges, chunks, warnings = merge_expanded_branches(
        outline_root=root,
        branch_results=results,
        target_node_range=(5, 30),
    )

    # 1 root + 4 branches * 3 nodes each = 13 total nodes
    assert len(nodes) == 13
    assert len(edges) == 12  # 4 root edges + 4 branches * 2 internal edges = 12

    # DR-03: All UUIDs must be unique
    node_ids = {n.id for n in nodes}
    assert len(node_ids) == len(nodes)

    # Edge integrity: all edges point to real node IDs
    for e in edges:
        assert e.source_node_id in node_ids
        assert e.target_node_id in node_ids

    # Validate that draft passes standard validator
    assert validate_draft(merged_draft.model_dump()) is not None


# ---------------------------------------------------------------------------
# Requirement 11: Shared expand_branch works with S2-L7 regeneration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shared_expand_branch_with_s2_l7_node_regeneration():
    selected_node = NodeDraft(id=uuid.uuid4(), label="Photosynthesis", description="Original")
    payload = _make_branch_subgraph_payload(selected_node.label)

    deps = GenerationDeps(
        llm=FakeLLMProvider([json.dumps(payload)]),
        decision_model=FakeDecisionModel(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=FakeVectorStore(),
        tools={},
        settings=Settings(),
        callback=FakeCallbackClient(),
    )

    res = await expand_branch(
        deps,
        namespace="map-regen",
        selected_node=selected_node,
        ancestor_labels=["Biology", "Plants"],
        breadcrumb_path="Biology > Plants > Photosynthesis",
        comment="Add details about light reactions",
        allowed_node_ids={str(selected_node.id)},
    )

    assert res.status == "completed"
    assert len(res.nodes) == 3
    # Requirement: Branch root ID strictly matches selected_node.id
    assert res.nodes[0].id == selected_node.id
    # Descendants receive fresh unique IDs
    assert res.nodes[1].id != selected_node.id
    assert res.nodes[2].id != selected_node.id


# ---------------------------------------------------------------------------
# Requirement 12: Chat elaboration reuses shared expand_branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_elaboration_reuses_shared_expansion():
    map_id = uuid.uuid4()
    target_node = NodeDraft(id=uuid.uuid4(), label="Quantum Computing", description="Qubits and algorithms")
    subgraph_payload = _make_branch_subgraph_payload(target_node.label)

    deps = GenerationDeps(
        llm=FakeLLMProvider([json.dumps(subgraph_payload)]),
        decision_model=FakeDecisionModel(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=FakeVectorStore(),
        tools={},
        settings=Settings(),
        callback=FakeCallbackClient(),
    )

    result = await elaborate_branch(
        deps,
        map_id=map_id,
        target_node=target_node,
        ancestor_labels=["Physics", "Computation"],
        instruction="Elaborate on Shor's and Grover's algorithms",
    )

    assert result.status == "completed"
    assert len(result.nodes) == 3
    assert result.nodes[0].id == target_node.id
    assert len(result.edges) == 2


# ---------------------------------------------------------------------------
# Full Two-Pass Generation Execution & LangGraph Integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_two_pass_generation_flow():
    outline_payload = _make_outline_payload(4)
    subgraph_payloads = [json.dumps(_make_branch_subgraph_payload(f"Branch {i}")) for i in range(1, 5)]

    # 1 outline call + 4 branch expansion calls
    all_responses = [json.dumps(outline_payload), *subgraph_payloads]

    deps = GenerationDeps(
        llm=FakeLLMProvider(all_responses),
        decision_model=FakeDecisionModel(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=FakeVectorStore(),
        tools={},
        settings=Settings(),
        callback=FakeCallbackClient(),
    )

    nodes, edges, chunks, warnings = await execute_two_pass_generation(
        deps,
        namespace="map-twopass",
        seed_text="# Topic\n## Section 1\n## Section 2\n## Section 3\n## Section 4",
        min_branches=4,
        max_branches=8,
    )

    assert len(nodes) == 13
    assert len(edges) == 12


@pytest.mark.asyncio
async def test_two_pass_generation_in_run_generation_graph():
    outline_payload = _make_outline_payload(4)
    subgraph_payloads = [json.dumps(_make_branch_subgraph_payload(f"Branch {i}")) for i in range(1, 5)]
    all_responses = [json.dumps(outline_payload), *subgraph_payloads]

    callback = FakeCallbackClient()
    deps = GenerationDeps(
        llm=FakeLLMProvider(all_responses),
        decision_model=FakeDecisionModel(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=FakeVectorStore(),
        tools={},
        settings=Settings(),
        callback=callback,
    )

    initial_state = {
        "job_id": uuid.uuid4(),
        "map_id": uuid.uuid4(),
        "namespace": "map-graph-twopass",
        "seed_text": "Sample topic with details",
        "detail_level": "balanced",
        "is_regeneration": False,
        "two_pass_enabled": True,  # activate two-pass mode in graph
    }

    final_state = await run_generation(deps, initial_state=initial_state, max_retries=1)

    assert final_state["status"] == "completed"
    assert len(final_state["nodes"]) == 13
    assert len(final_state["edges"]) == 12
    assert 70 in callback.progress_calls
    assert 85 in callback.progress_calls
