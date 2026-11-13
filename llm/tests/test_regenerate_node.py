from __future__ import annotations

import json
import uuid

import pytest
from config import Settings
from contracts.models import (
    BranchContext,
    ChunkSource,
    EdgeDraft,
    GenerationRequest,
    JobType,
    NodeDraft,
)
from rag.pinecone_store import VectorChunk
from validation.schema import (
    ValidationFailure,
    resolve_branch_to_records,
    validate_branch_draft,
)
from worker.tasks import generate_mindmap

from tests.conftest import (
    FakeCallbackClient,
    FakeDecisionModel,
    FakeEmbeddingProvider,
    FakeLLMProvider,
    FakeVectorStore,
)


class RecordingVectorStore(FakeVectorStore):
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


def _make_selected_node(label: str = "Cellular Respiration") -> NodeDraft:
    return NodeDraft(
        id=uuid.uuid4(),
        label=label,
        description="Process converting biochemical energy into ATP",
        node_type="default",
    )


def test_validate_branch_draft_valid_resolution():
    selected_node = _make_selected_node()
    payload = {
        "nodes": [
            {"id": "n1", "label": selected_node.label, "description": selected_node.description},
            {"id": "n2", "label": "Glycolysis", "description": "Breakdown of glucose"},
            {"id": "n3", "label": "Krebs Cycle", "description": "Citric acid cycle"},
        ],
        "edges": [
            {"source": "n1", "target": "n2", "label": "phase 1"},
            {"source": "n1", "target": "n3", "label": "phase 2"},
        ],
    }

    draft = validate_branch_draft(
        payload,
        selected_node=selected_node,
        allowed_node_ids={str(selected_node.id)},
        ancestor_labels=["Biology", "Cell Physiology"],
    )
    nodes, edges = resolve_branch_to_records(
        draft,
        selected_node=selected_node,
        allowed_node_ids={str(selected_node.id)},
    )

    assert len(nodes) == 3
    assert len(edges) == 2
    # Requirement 6: Root node ID must strictly match selected_node.id
    assert nodes[0].id == selected_node.id
    # Child nodes receive new unique UUIDs
    assert nodes[1].id != selected_node.id
    assert nodes[2].id != selected_node.id
    assert nodes[1].id != nodes[2].id

    # Edges originate from selected_node.id
    edge_sources = {e.source_node_id for e in edges}
    assert edge_sources == {selected_node.id}


def test_validate_branch_draft_root_id_modification_rejected():
    selected_node = _make_selected_node()
    different_root_id = str(uuid.uuid4())
    payload = {
        "nodes": [
            {"id": different_root_id, "label": "Modified Root", "description": "Attempt to change root ID"},
            {"id": "n2", "label": "Child", "description": "Subtopic"},
        ],
        "edges": [
            {"source": different_root_id, "target": "n2", "label": "sub"},
        ],
    }

    # Requirement 6: Rejection of any change to the branch root ID
    with pytest.raises(ValidationFailure) as exc_info:
        validate_branch_draft(
            payload,
            selected_node=selected_node,
            allowed_node_ids={str(selected_node.id)},
        )
    assert "Branch root ID cannot be modified" in str(exc_info.value)


def test_validate_branch_draft_selected_node_id_on_non_root_rejected():
    selected_node = _make_selected_node()
    payload = {
        "nodes": [
            {"id": "n1", "label": "New Root", "description": "Root"},
            {"id": str(selected_node.id), "label": "Demoted Child", "description": "Selected node demoted"},
        ],
        "edges": [
            {"source": "n1", "target": str(selected_node.id), "label": "sub"},
        ],
    }

    with pytest.raises(ValidationFailure) as exc_info:
        validate_branch_draft(
            payload,
            selected_node=selected_node,
            allowed_node_ids={str(selected_node.id)},
        )
    assert "cannot be assigned to non-root" in str(exc_info.value)


def test_validate_branch_draft_outside_id_rejected():
    selected_node = _make_selected_node()
    outside_unrelated_id = str(uuid.uuid4())
    payload = {
        "nodes": [
            {"id": "n1", "label": selected_node.label, "description": selected_node.description},
            {"id": outside_unrelated_id, "label": "Injected Outside Node", "description": "From another branch"},
        ],
        "edges": [
            {"source": "n1", "target": outside_unrelated_id, "label": "links to"},
        ],
    }

    # Requirement 5: Rejection of IDs outside the allowed branch
    with pytest.raises(ValidationFailure) as exc_info:
        validate_branch_draft(
            payload,
            selected_node=selected_node,
            allowed_node_ids={str(selected_node.id)},
        )
    assert "outside the allowed branch" in str(exc_info.value)


def test_validate_branch_draft_ancestor_node_rejected():
    selected_node = _make_selected_node()
    payload = {
        "nodes": [
            {"id": "n1", "label": selected_node.label, "description": selected_node.description},
            {"id": "n2", "label": "Cell Physiology", "description": "This is an ancestor label!"},
        ],
        "edges": [
            {"source": "n1", "target": "n2", "label": "links to"},
        ],
    }

    # Requirement 2: Ancestors are read-only and must not be generated/modified as nodes
    with pytest.raises(ValidationFailure) as exc_info:
        validate_branch_draft(
            payload,
            selected_node=selected_node,
            allowed_node_ids={str(selected_node.id)},
            ancestor_labels=["Biology", "Cell Physiology"],
        )
    assert "ancestors are read-only" in str(exc_info.value)


def test_resolve_branch_preserves_allowed_existing_descendant_id():
    selected_node = _make_selected_node()
    existing_child_id = uuid.uuid4()
    payload = {
        "nodes": [
            {"id": "n1", "label": selected_node.label, "description": selected_node.description},
            {"id": str(existing_child_id), "label": "Preserved Child", "description": "Existing node kept"},
            {"id": "n3", "label": "New Child", "description": "Brand new node"},
        ],
        "edges": [
            {"source": "n1", "target": str(existing_child_id), "label": "sub1"},
            {"source": "n1", "target": "n3", "label": "sub2"},
        ],
    }

    draft = validate_branch_draft(
        payload,
        selected_node=selected_node,
        allowed_node_ids={str(selected_node.id), str(existing_child_id)},
    )
    nodes, edges = resolve_branch_to_records(
        draft,
        selected_node=selected_node,
        allowed_node_ids={str(selected_node.id), str(existing_child_id)},
    )

    resolved_ids = {n.id for n in nodes}
    assert selected_node.id in resolved_ids
    assert existing_child_id in resolved_ids
    # n3 received a new UUID
    new_child = [n for n in nodes if n.id not in (selected_node.id, existing_child_id)][0]
    assert isinstance(new_child.id, uuid.UUID)


@pytest.mark.asyncio
async def test_regenerate_node_end_to_end_success():
    map_id = uuid.uuid4()
    job_id = uuid.uuid4()
    selected_node = _make_selected_node()
    ancestor_labels = ["Science", "Biology", "Metabolism"]

    existing_branch_child = NodeDraft(
        id=uuid.uuid4(),
        label="Old Subtopic",
        description="Will be regenerated",
    )

    branch_payload = {
        "nodes": [
            {"id": "n1", "label": selected_node.label, "description": "Refined respiration overview"},
            {"id": "n2", "label": "Electron Transport Chain", "description": "Generates proton gradient"},
            {"id": "n3", "label": "ATP Synthase Mechanism", "description": "Produces ATP by chemiosmosis"},
        ],
        "edges": [
            {"source": "n1", "target": "n2", "label": "involves"},
            {"source": "n2", "target": "n3", "label": "drives"},
        ],
    }

    req = GenerationRequest(
        job_id=job_id,
        map_id=map_id,
        job_type=JobType.REGENERATE_NODE,
        selected_node=selected_node,
        comment="Expand on ATP generation mechanisms",
        ancestor_labels=ancestor_labels,
        branch_context=BranchContext(
            nodes=[selected_node, existing_branch_child],
            edges=[
                EdgeDraft(id=uuid.uuid4(), source_node_id=selected_node.id, target_node_id=existing_branch_child.id)
            ],
        ),
        callback_base_url="https://api.test",
        callback_token="test-token",
    )
    payload = req.model_dump(mode="json")

    vector_store = RecordingVectorStore()
    await vector_store.upsert_chunks(
        namespace=str(map_id),
        chunks=[
            VectorChunk(
                id="chunk-1",
                chunk_text="ATP synthase uses proton flux to phosphorylate ADP.",
                embedding=[0.1] * 1024,
                source=ChunkSource.UPLOADED_SEED,
            )
        ],
    )

    callback = FakeCallbackClient()
    ctx = {
        "llm": FakeLLMProvider([json.dumps(branch_payload)]),
        "decision_model": FakeDecisionModel(),
        "embedding_provider": FakeEmbeddingProvider(),
        "vector_store": vector_store,
        "tools": {},
        "settings": Settings(),
        "callback": callback,
    }

    result = await generate_mindmap(ctx, payload)

    assert result["status"] == "completed"
    assert callback.terminal_sent
    assert callback.complete_result is not None
    assert callback.complete_result.status == "completed"

    # Progress ticks verified
    assert 40 in callback.progress_calls
    assert 70 in callback.progress_calls
    assert 85 in callback.progress_calls
    assert 100 in callback.progress_calls

    # Requirement 3 & 4: Output covers only the selected branch
    nodes = result["nodes"]
    assert len(nodes) == 3
    # Requirement 6: Root ID preserved
    assert nodes[0]["id"] == str(selected_node.id)

    # Requirement 2: Ancestors must NOT be in returned nodes
    returned_labels = {n["label"] for n in nodes}
    for ancestor in ancestor_labels:
        assert ancestor not in returned_labels

    # Requirement 7: Scoped retrieval uses breadcrumb path
    assert len(vector_store.queries) >= 1
    query_text = vector_store.queries[0]["query_text"]
    assert "Science > Biology > Metabolism > Cellular Respiration" in query_text
    assert "Expand on ATP generation mechanisms" in query_text


@pytest.mark.asyncio
async def test_regenerate_node_validation_retry_loop_recovers():
    map_id = uuid.uuid4()
    job_id = uuid.uuid4()
    selected_node = _make_selected_node()
    ancestor_labels = ["Science", "Biology"]

    # First attempt: invalid draft (violates DR-02 by having 2 roots)
    bad_draft = {
        "nodes": [
            {"id": "n1", "label": selected_node.label, "description": "Root 1"},
            {"id": "n2", "label": "Disconnected Root", "description": "Root 2"},
        ],
        "edges": [],  # no edges -> 2 roots, fails validation
    }
    # Second attempt: valid draft fixing the error
    good_draft = {
        "nodes": [
            {"id": "n1", "label": selected_node.label, "description": "Root 1"},
            {"id": "n2", "label": "Connected Subtopic", "description": "Valid child"},
        ],
        "edges": [
            {"source": "n1", "target": "n2", "label": "has part"},
        ],
    }

    req = GenerationRequest(
        job_id=job_id,
        map_id=map_id,
        job_type=JobType.REGENERATE_NODE,
        selected_node=selected_node,
        comment="Add subtopics",
        ancestor_labels=ancestor_labels,
        callback_base_url="https://api.test",
        callback_token="test-token",
    )
    payload = req.model_dump(mode="json")

    llm = FakeLLMProvider([json.dumps(bad_draft), json.dumps(good_draft)])
    callback = FakeCallbackClient()
    ctx = {
        "llm": llm,
        "decision_model": FakeDecisionModel(),
        "embedding_provider": FakeEmbeddingProvider(),
        "vector_store": FakeVectorStore(),
        "tools": {},
        "settings": Settings(),
        "callback": callback,
    }

    result = await generate_mindmap(ctx, payload)

    assert result["status"] == "completed"
    assert callback.complete_result is not None
    assert len(result["nodes"]) == 2
    # Check that LLM was called twice (retry loop worked)
    assert len(llm.calls) == 2
    # Check that retry prompt received previous validation error feedback
    retry_prompt = llm.calls[1][0].content
    assert "Your previous attempt was rejected" in retry_prompt


@pytest.mark.asyncio
async def test_regenerate_node_missing_selected_node_fails():
    map_id = uuid.uuid4()
    job_id = uuid.uuid4()
    req = GenerationRequest(
        job_id=job_id,
        map_id=map_id,
        job_type=JobType.REGENERATE_NODE,
        selected_node=None,  # Missing selected_node!
        comment="Add subtopics",
        callback_base_url="https://api.test",
        callback_token="test-token",
    )
    payload = req.model_dump(mode="json")

    callback = FakeCallbackClient()
    ctx = {
        "llm": FakeLLMProvider([]),
        "decision_model": FakeDecisionModel(),
        "embedding_provider": FakeEmbeddingProvider(),
        "vector_store": FakeVectorStore(),
        "tools": {},
        "settings": Settings(),
        "callback": callback,
    }

    result = await generate_mindmap(ctx, payload)

    assert result["status"] == "failed"
    assert callback.terminal_sent
    assert callback.fail_result is not None
    assert "must include selected_node" in callback.fail_result.error.message
