"""
Unit and integration test suite for S3-A3: Shared Branch Expansion Logic.

Verifies:
  1. Shared `expand_branch` function in `orchestration/`:
     - Dynamic prompt building via ContextBudgetManager
     - Schema parsing & boundary validation
     - Content quality checks (subnode count, citation validity, descriptions)
     - Source tagging (resolving ctx:N to canonical title/URL)
     - Retry loop recovery on malformed JSON or validation errors
     - Progress milestone reporting (40, 70, 85)
  2. The three invocation paths reuse `expand_branch`:
     - Path 1: Two-pass full generation branch expander
     - Path 2: Node regeneration task (regenerate_node_task / _run_regenerate_node)
     - Path 3: Chat elaboration (elaborate_branch / build_elaboration_action_proposal)
  3. All three invocation paths produce IDENTICAL output structures given equivalent inputs.
  4. Failure handling (malformed LLM, schema violation, quality check failure) across all paths.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from chat.expansion import build_elaboration_action_proposal, elaborate_branch
from config import Settings
from contracts.models import (
    ChatIntent,
    ChunkSource,
    EdgeDraft,
    GenerationRequest,
    JobType,
    NodeDraft,
)
from orchestration.deps import GenerationDeps
from orchestration.expansion import (
    BranchExpansionResult,
    expand_branch,
    expand_branches_parallel,
)
from orchestration.graph import run_regenerate_node
from rag.pinecone_store import RetrievedChunkRecord
from rag.retrieval import RetrievedChunk
from worker.tasks import regenerate_node_task

from tests.conftest import (
    FakeCallbackClient,
    FakeDecisionModel,
    FakeEmbeddingProvider,
    FakeLLMProvider,
    FakeVectorStore,
)


def _make_sample_chunk(
    idx: int = 1, text: str = "Quantum algorithms use superposition and entanglement."
) -> RetrievedChunk:
    record = RetrievedChunkRecord(
        id=f"chunk-{idx}",
        chunk_text=text,
        source=ChunkSource.UPLOADED_SEED,
        source_title=f"Quantum Computing Principles {idx}",
        source_url=f"https://example.org/quantum-{idx}",
        score=0.95,
    )
    return RetrievedChunk(record=record, relevance_score=0.95)


def _make_valid_branch_payload(root_label: str = "Quantum Computing") -> dict[str, Any]:
    return {
        "nodes": [
            {"id": "n1", "label": root_label, "description": "Core concepts and models"},
            {
                "id": "n2",
                "label": "Superposition & Entanglement",
                "description": "Fundamental physical resources powering quantum speedups",
                "sources": ["ctx:1"],
            },
            {
                "id": "n3",
                "label": "Quantum Algorithms",
                "description": "Algorithms outperforming classical equivalents such as Shor and Grover",
                "sources": ["ctx:1"],
            },
        ],
        "edges": [
            {"source": "n1", "target": "n2", "label": "relies on"},
            {"source": "n1", "target": "n3", "label": "enables"},
        ],
    }


def _make_deps(responses: list[str], vector_store: Any | None = None, callback: Any | None = None) -> GenerationDeps:
    return GenerationDeps(
        llm=FakeLLMProvider(responses),
        decision_model=FakeDecisionModel(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=vector_store or FakeVectorStore(),
        tools={},
        settings=Settings(),
        callback=callback or FakeCallbackClient(),
    )


# ---------------------------------------------------------------------------
# 1. Core expand_branch Tests (Prompt, Schema, Quality, Source Tagging)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_branch_success_with_source_tagging():
    """Verify prompt building, LLM parsing, schema validation, and citation tagging."""
    selected = NodeDraft(id=uuid4(), label="Quantum Computing", description="Initial description")
    payload = _make_valid_branch_payload(selected.label)
    chunk = _make_sample_chunk(1)

    deps = _make_deps([json.dumps(payload)])

    res = await expand_branch(
        deps,
        namespace="map-test-1",
        selected_node=selected,
        ancestor_labels=["Physics", "Computer Science"],
        existing_chunks=[chunk],
    )

    assert res.status == "completed"
    assert len(res.nodes) == 3
    assert len(res.edges) == 2

    # Root node preserves original ID
    assert res.nodes[0].id == selected.id
    assert res.nodes[0].label == selected.label

    # Child nodes receive fresh UUIDs
    assert res.nodes[1].id != selected.id
    assert res.nodes[2].id != selected.id

    # Source tagging: ctx:1 is resolved to Title (URL)
    expected_source = f"{chunk.record.source_title} ({chunk.record.source_url})"
    assert expected_source in res.nodes[1].sources
    assert expected_source in res.nodes[2].sources


@pytest.mark.asyncio
async def test_expand_branch_malformed_llm_json_recovers_on_retry():
    """Malformed LLM response on attempt 1 triggers retry and recovers on attempt 2."""
    selected = NodeDraft(id=uuid4(), label="Quantum Computing")
    payload = _make_valid_branch_payload(selected.label)

    # Attempt 1: unparseable garbage; Attempt 2: valid JSON
    llm = FakeLLMProvider(["Not JSON at all! {broken", json.dumps(payload)])
    deps = GenerationDeps(
        llm=llm,
        decision_model=FakeDecisionModel(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=FakeVectorStore(),
        tools={},
        settings=Settings(),
        callback=FakeCallbackClient(),
    )

    res = await expand_branch(
        deps,
        namespace="map-retry-1",
        selected_node=selected,
        ancestor_labels=[],
        max_retries=1,
    )

    assert res.status == "completed"
    assert len(res.nodes) == 3
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_expand_branch_malformed_llm_json_exhausts_retries():
    """Persistently unparseable response reports failure status with extraction errors."""
    selected = NodeDraft(id=uuid4(), label="Quantum Computing")
    deps = _make_deps(["Broken JSON 1", "Broken JSON 2"])

    res = await expand_branch(
        deps,
        namespace="map-fail-1",
        selected_node=selected,
        ancestor_labels=[],
        max_retries=1,
    )

    assert res.status == "failed"
    assert len(res.nodes) == 0
    assert any("Extraction error" in e for e in res.errors)


@pytest.mark.asyncio
async def test_expand_branch_schema_validation_failure_root_id_modified():
    """Modifying the branch root ID fails schema validation."""
    selected = NodeDraft(id=uuid4(), label="Quantum Computing")
    other_root_id = str(uuid4())
    bad_payload = {
        "nodes": [
            {"id": other_root_id, "label": selected.label},  # Modified root ID!
            {"id": "n2", "label": "Child Concept"},
        ],
        "edges": [{"source": other_root_id, "target": "n2", "label": "leads to"}],
    }

    deps = _make_deps([json.dumps(bad_payload)])

    res = await expand_branch(
        deps,
        namespace="map-schema-fail",
        selected_node=selected,
        ancestor_labels=[],
        max_retries=0,
    )

    assert res.status == "failed"
    assert any("Branch root ID cannot be modified" in e or "outside the allowed branch" in e for e in res.errors)


@pytest.mark.asyncio
async def test_expand_branch_schema_validation_failure_ancestor_included():
    """Including a read-only ancestor node in the branch output fails validation."""
    selected = NodeDraft(id=uuid4(), label="Quantum Computing")
    bad_payload = {
        "nodes": [
            {"id": "n1", "label": selected.label},
            {"id": "n2", "label": "Physics"},  # Read-only ancestor!
        ],
        "edges": [{"source": "n1", "target": "n2", "label": "is part of"}],
    }

    deps = _make_deps([json.dumps(bad_payload)])

    res = await expand_branch(
        deps,
        namespace="map-ancestor-fail",
        selected_node=selected,
        ancestor_labels=["Physics"],
        max_retries=0,
    )

    assert res.status == "failed"
    assert any("ancestors are read-only" in e for e in res.errors)


@pytest.mark.asyncio
async def test_expand_branch_quality_check_min_subnodes_failure():
    """Branch that generates zero subnodes (only the root) fails quality checks."""
    selected = NodeDraft(id=uuid4(), label="Quantum Computing")
    zero_subnodes_payload = {
        "nodes": [
            {"id": "n1", "label": selected.label, "description": "Only root, no expansion"},
        ],
        "edges": [],
    }

    deps = _make_deps([json.dumps(zero_subnodes_payload)])

    res = await expand_branch(
        deps,
        namespace="map-quality-zero",
        selected_node=selected,
        ancestor_labels=[],
        max_retries=0,
        min_subnodes=1,
        enforce_quality=True,
    )

    assert res.status == "failed"
    assert any("minimum required is 1" in e for e in res.errors)


@pytest.mark.asyncio
async def test_expand_branch_quality_check_invalid_citation_failure():
    """Branch referencing out-of-bounds context index (e.g. ctx:99) fails quality check."""
    selected = NodeDraft(id=uuid4(), label="Quantum Computing")
    bad_citation_payload = {
        "nodes": [
            {"id": "n1", "label": selected.label},
            {"id": "n2", "label": "Subtopic", "sources": ["ctx:99"]},  # Only 1 context passage exists!
        ],
        "edges": [{"source": "n1", "target": "n2", "label": "has"}],
    }

    chunk = _make_sample_chunk(1)
    deps = _make_deps([json.dumps(bad_citation_payload)])

    res = await expand_branch(
        deps,
        namespace="map-quality-citation",
        selected_node=selected,
        ancestor_labels=[],
        existing_chunks=[chunk],  # Only 1 chunk exists
        max_retries=0,
        enforce_quality=True,
    )

    assert res.status == "failed"
    assert any("invalid citation reference 'ctx:99'" in e for e in res.errors)


@pytest.mark.asyncio
async def test_expand_branch_progress_reporting():
    """When report_progress=True, callback.progress is notified at 40, 70, 85."""
    selected = NodeDraft(id=uuid4(), label="Quantum Computing")
    payload = _make_valid_branch_payload(selected.label)

    cb = FakeCallbackClient()
    deps = _make_deps([json.dumps(payload)], callback=cb)

    res = await expand_branch(
        deps,
        namespace="map-progress",
        selected_node=selected,
        ancestor_labels=["Physics"],
        report_progress=True,
    )

    assert res.status == "completed"
    assert 40 in cb.progress_calls
    assert 70 in cb.progress_calls
    assert 85 in cb.progress_calls


# ---------------------------------------------------------------------------
# 2. Identical Output Structure Verification Across All Three Paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_three_paths_produce_identical_output_structures():
    """Verify that Full Gen Pass 2, Node Regeneration, and Chat Elaboration
    produce IDENTICAL output structures given equivalent inputs.
    """
    map_id = uuid4()
    selected_node = NodeDraft(
        id=uuid4(),
        label="Quantum Computing",
        description="Qubits and algorithmic acceleration",
    )
    ancestors = ["Physics", "Computer Science"]
    subgraph_payload = _make_valid_branch_payload(selected_node.label)
    chunk = _make_sample_chunk(1)

    # Path 1: Two-Pass Full Generation Branch Expander
    deps1 = _make_deps([json.dumps(subgraph_payload)])
    res1 = await expand_branch(
        deps1,
        namespace=str(map_id),
        selected_node=selected_node,
        ancestor_labels=ancestors,
        breadcrumb_path="Physics > Computer Science > Quantum Computing",
        existing_chunks=[chunk],
        report_progress=False,
    )

    # Path 2: Node Regeneration (run_regenerate_node)
    deps2 = _make_deps([json.dumps(subgraph_payload)])
    regen_state = await run_regenerate_node(
        deps2,
        initial_state={
            "job_id": uuid4(),
            "map_id": map_id,
            "namespace": str(map_id),
            "selected_node": selected_node,
            "ancestor_labels": ancestors,
            "breadcrumb_path": "Physics > Computer Science > Quantum Computing",
            "retrieved_chunks": [chunk],
            "is_regeneration": True,
        },
        max_retries=1,
    )
    res2 = regen_state["branch_result"]

    # Path 3: Chat Elaboration (elaborate_branch)
    deps3 = _make_deps([json.dumps(subgraph_payload)])
    res3 = await elaborate_branch(
        deps3,
        map_id=map_id,
        target_node=selected_node,
        ancestor_labels=ancestors,
    )

    # 1. Status identical
    assert res1.status == "completed"
    assert res2.status == "completed"
    assert res3.status == "completed"

    # 2. Node count and labels identical
    assert len(res1.nodes) == len(res2.nodes) == len(res3.nodes) == 3
    assert [n.label for n in res1.nodes] == [n.label for n in res2.nodes] == [n.label for n in res3.nodes]

    # 3. Branch root ID strictly preserved on all three
    assert res1.nodes[0].id == selected_node.id
    assert res2.nodes[0].id == selected_node.id
    assert res3.nodes[0].id == selected_node.id

    # 4. Edge count identical
    assert len(res1.edges) == len(res2.edges) == len(res3.edges) == 2

    # 5. Relationship labels identical
    assert [e.label for e in res1.edges] == [e.label for e in res2.edges] == [e.label for e in res3.edges]

    # 6. Quality warnings structure identical
    assert res1.quality_warnings == res2.quality_warnings == res3.quality_warnings


@pytest.mark.asyncio
async def test_all_three_paths_produce_identical_failure_structure_on_malformed_llm():
    """Verify that all three paths fail with identical error structures on malformed LLM text."""
    map_id = uuid4()
    selected_node = NodeDraft(id=uuid4(), label="Photosynthesis")
    garbage_output = ["Totally unparseable response without braces", "Still broken"]

    # Path 1
    deps1 = _make_deps(list(garbage_output))
    res1 = await expand_branch(
        deps1, namespace=str(map_id), selected_node=selected_node, ancestor_labels=[], max_retries=1
    )

    # Path 2
    deps2 = _make_deps(list(garbage_output))
    regen_state = await run_regenerate_node(
        deps2,
        initial_state={"namespace": str(map_id), "selected_node": selected_node, "ancestor_labels": []},
        max_retries=1,
    )
    res2 = regen_state["branch_result"]

    # Path 3
    deps3 = _make_deps(list(garbage_output))
    res3 = await elaborate_branch(deps3, map_id=map_id, target_node=selected_node, max_retries=1)

    assert res1.status == res2.status == res3.status == "failed"
    assert len(res1.nodes) == len(res2.nodes) == len(res3.nodes) == 0
    assert any("Extraction error" in e for e in res1.errors)
    assert any("Extraction error" in e for e in res2.errors)
    assert any("Extraction error" in e for e in res3.errors)


@pytest.mark.asyncio
async def test_all_three_paths_produce_identical_failure_structure_on_schema_violation():
    """Verify that all three paths reject branch root modification with identical failure status."""
    map_id = uuid4()
    selected_node = NodeDraft(id=uuid4(), label="Metabolism")
    other_root_id = str(uuid4())
    modified_root_payload = json.dumps(
        {
            "nodes": [
                {"id": other_root_id, "label": selected_node.label},  # Modified root ID
                {"id": "n2", "label": "Child"},
            ],
            "edges": [{"source": other_root_id, "target": "n2", "label": "leads to"}],
        }
    )

    # Path 1
    deps1 = _make_deps([modified_root_payload])
    res1 = await expand_branch(
        deps1, namespace=str(map_id), selected_node=selected_node, ancestor_labels=[], max_retries=0
    )

    # Path 2
    deps2 = _make_deps([modified_root_payload])
    regen_state = await run_regenerate_node(
        deps2,
        initial_state={"namespace": str(map_id), "selected_node": selected_node, "ancestor_labels": []},
        max_retries=0,
    )
    res2 = regen_state["branch_result"]

    # Path 3
    deps3 = _make_deps([modified_root_payload])
    res3 = await elaborate_branch(deps3, map_id=map_id, target_node=selected_node, max_retries=0)

    assert res1.status == res2.status == res3.status == "failed"
    assert any("Branch root ID cannot be modified" in e or "outside the allowed branch" in e for e in res1.errors)
    assert any("Branch root ID cannot be modified" in e or "outside the allowed branch" in e for e in res2.errors)
    assert any("Branch root ID cannot be modified" in e or "outside the allowed branch" in e for e in res3.errors)


# ---------------------------------------------------------------------------
# 3. Chat ActionProposal Synthesis Test
# ---------------------------------------------------------------------------


def test_build_elaboration_action_proposal():
    """Verify converting BranchExpansionResult to an ActionProposal for chat UI."""
    target_node = NodeDraft(id=uuid4(), label="Artificial Intelligence")
    child_id = uuid4()
    child_node = NodeDraft(
        id=child_id, label="Machine Learning", description="Data-driven learning", sources=["Source A"]
    )
    edge = EdgeDraft(id=uuid4(), source_node_id=target_node.id, target_node_id=child_id, label="subfield")

    expansion_res = BranchExpansionResult(
        selected_node=target_node,
        nodes=[target_node, child_node],
        edges=[edge],
        status="completed",
    )

    proposal = build_elaboration_action_proposal(
        target_node=target_node,
        expansion_result=expansion_res,
        instruction="Elaborate on machine learning",
    )

    assert proposal.action_type == ChatIntent.EDIT_PROPOSAL
    assert proposal.target_node_ids == [target_node.id]
    assert proposal.target_node_id == target_node.id
    assert "Elaborated branch 'Artificial Intelligence'" in proposal.explanation

    # Subnodes mapped to create_nodes parameter
    created_nodes = proposal.parameters.create_nodes
    assert len(created_nodes) == 1
    node_label = getattr(created_nodes[0], "label", None) or (
        created_nodes[0]["label"] if isinstance(created_nodes[0], dict) else ""
    )
    assert node_label == "Machine Learning"

    # Connecting edges mapped to create_edges parameter
    created_edges = proposal.parameters.create_edges
    assert len(created_edges) == 1
    edge_label = getattr(created_edges[0], "label", None) or (
        created_edges[0]["label"] if isinstance(created_edges[0], dict) else ""
    )
    assert edge_label == "subfield"


# ---------------------------------------------------------------------------
# 4. Two-Pass Parallel Expander Integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_pass_parallel_expander_uses_shared_function():
    """Verify expand_branches_parallel invokes expand_branch across multiple branches."""
    root = NodeDraft(id=uuid4(), label="Science")
    b1 = NodeDraft(id=uuid4(), label="Physics")
    b2 = NodeDraft(id=uuid4(), label="Chemistry")

    p1 = _make_valid_branch_payload("Physics")
    p2 = _make_valid_branch_payload("Chemistry")

    deps = _make_deps([json.dumps(p1), json.dumps(p2)])

    results = await expand_branches_parallel(
        deps,
        namespace="map-parallel-shared",
        root_node=root,
        branch_nodes=[b1, b2],
        concurrency_limit=2,
    )

    assert len(results) == 2
    assert all(r.status == "completed" for r in results)
    assert results[0].selected_node.id == b1.id
    assert results[1].selected_node.id == b2.id
    assert len(results[0].nodes) == 3
    assert len(results[1].nodes) == 3


# ---------------------------------------------------------------------------
# 5. Node Regeneration Task (regenerate_node_task) Integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regenerate_node_task_uses_shared_expansion():
    """Verify that regenerate_node_task integrates with the unified expand_branch engine."""
    root_id = uuid4()
    child_id = uuid4()
    selected_node = NodeDraft(id=root_id, label="Photosynthesis", description="Root")
    child_node = NodeDraft(id=child_id, label="Light Reactions", description="Child")
    edge = EdgeDraft(id=uuid4(), source_node_id=root_id, target_node_id=child_id)

    payload = _make_valid_branch_payload(selected_node.label)

    req = GenerationRequest(
        job_id=uuid4(),
        map_id=uuid4(),
        job_type=JobType.REGENERATE_NODE,
        target_node_id=root_id,
        selected_node=selected_node,
        comment="Update reactions",
        ancestor_labels=["Biology"],
        branch_nodes=[selected_node, child_node],
        branch_edges=[edge],
        callback_base_url="https://api.test",
        callback_token="test-token",
    )

    cb = FakeCallbackClient()
    ctx = {
        "llm": FakeLLMProvider([json.dumps(payload)]),
        "decision_model": FakeDecisionModel(),
        "embedding_provider": FakeEmbeddingProvider(),
        "vector_store": FakeVectorStore(),
        "tools": {},
        "settings": Settings(),
        "callback": cb,
    }

    result = await regenerate_node_task(ctx, req.model_dump(mode="json"))

    assert result["status"] == "completed"
    assert len(result["nodes"]) == 3
    assert cb.terminal_sent is True
    assert cb.complete_result is not None
    assert cb.complete_result.status == "completed"

    # Verifies progress milestones were hit
    assert 40 in cb.progress_calls
    assert 70 in cb.progress_calls
    assert 85 in cb.progress_calls
    assert 100 in cb.progress_calls
