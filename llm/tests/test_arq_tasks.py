"""
Unit tests for S2-L10: ARQ tasks and callbacks for regenerate_node_task and chat_turn_task.

All tests run fully offline with fake providers (no real external API calls, no database).
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from config import Settings
from contracts.models import (
    ChatMessage,
    ChatTurnRequest,
    EdgeDraft,
    GenerationRequest,
    JobType,
    MapSnapshot,
    NodeDraft,
)
from worker.settings import WorkerSettings
from worker.tasks import chat_turn_task, generate_mindmap, regenerate_node_task

from tests.conftest import (
    FakeCallbackClient,
    FakeDecisionModel,
    FakeEmbeddingProvider,
    FakeLLMProvider,
    FakeVectorStore,
)


class ExplodingLLM(FakeLLMProvider):
    def __init__(self):
        super().__init__([])

    async def generate(self, *args, **kwargs):
        raise RuntimeError("Simulated LLM crash in worker task")


def _make_context(llm=None, callback=None) -> dict:
    return {
        "llm": llm or FakeLLMProvider([]),
        "decision_model": FakeDecisionModel(),
        "embedding_provider": FakeEmbeddingProvider(),
        "vector_store": FakeVectorStore(),
        "tools": {},
        "settings": Settings(),
        "callback": callback or FakeCallbackClient(),
    }


# ---------------------------------------------------------------------------
# 1. regenerate_node_task Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regenerate_node_task_success():
    """Valid node regeneration job runs to completion and triggers complete callback."""
    root_id = uuid4()
    child_id = uuid4()
    selected_node = NodeDraft(id=root_id, label="Photosynthesis", description="Root")
    child_node = NodeDraft(id=child_id, label="Light Reactions", description="Child")
    edge = EdgeDraft(id=uuid4(), source_node_id=root_id, target_node_id=child_id)

    draft_payload = {
        "nodes": [
            {"id": "n1", "label": "Photosynthesis", "description": "Updated root", "node_type": "default"},
            {"id": "n2", "label": "Dark Reactions", "description": "New child", "node_type": "default"},
        ],
        "edges": [
            {"source": "n1", "target": "n2", "label": "includes"},
        ],
    }

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
    ctx = _make_context(
        llm=FakeLLMProvider([json.dumps(draft_payload)]),
        callback=cb,
    )

    result = await regenerate_node_task(ctx, req.model_dump(mode="json"))

    assert result["status"] == "completed"
    assert len(result["nodes"]) == 2
    assert cb.terminal_sent is True
    assert cb.complete_result is not None
    assert cb.complete_result.status == "completed"
    assert cb.fail_result is None


@pytest.mark.asyncio
async def test_regenerate_node_task_missing_selected_node_fails():
    """Missing selected_node fails validation and triggers fail callback."""
    req_dict = {
        "job_id": str(uuid4()),
        "map_id": str(uuid4()),
        "job_type": "regenerate_node",
        "comment": "Redo branch",
        "callback_base_url": "https://api.test",
        "callback_token": "test-token",
    }

    cb = FakeCallbackClient()
    ctx = _make_context(callback=cb)

    result = await regenerate_node_task(ctx, req_dict)

    assert result["status"] == "failed"
    assert cb.terminal_sent is True
    assert cb.fail_result is not None
    assert "selected_node" in cb.fail_result.error.message.lower()


@pytest.mark.asyncio
async def test_regenerate_node_task_llm_failure_triggers_fail_callback():
    """Workflow exception during node regeneration reports terminal failure callback."""
    node_id = uuid4()
    selected_node = NodeDraft(id=node_id, label="Photosynthesis")
    req = GenerationRequest(
        job_id=uuid4(),
        map_id=uuid4(),
        job_type=JobType.REGENERATE_NODE,
        target_node_id=node_id,
        selected_node=selected_node,
        comment="Update",
        branch_nodes=[selected_node],
        callback_base_url="https://api.test",
        callback_token="test-token",
    )

    cb = FakeCallbackClient()
    ctx = _make_context(llm=ExplodingLLM(), callback=cb)

    result = await regenerate_node_task(ctx, req.model_dump(mode="json"))

    assert result["status"] == "failed"
    assert cb.terminal_sent is True
    assert cb.fail_result is not None
    assert cb.fail_result.status == "failed"


@pytest.mark.asyncio
async def test_generate_mindmap_routes_to_node_regeneration():
    """Verifies existing generate_mindmap entry point continues to support JobType.REGENERATE_NODE."""
    node_id = uuid4()
    selected_node = NodeDraft(id=node_id, label="Photosynthesis")
    draft_payload = {
        "nodes": [{"id": str(node_id), "label": "Photosynthesis", "description": "Updated", "node_type": "default"}],
        "edges": [],
    }

    req = GenerationRequest(
        job_id=uuid4(),
        map_id=uuid4(),
        job_type=JobType.REGENERATE_NODE,
        target_node_id=node_id,
        selected_node=selected_node,
        comment="Simplify",
        branch_nodes=[selected_node],
        callback_base_url="https://api.test",
        callback_token="test-token",
    )

    cb = FakeCallbackClient()
    ctx = _make_context(llm=FakeLLMProvider([json.dumps(draft_payload)]), callback=cb)

    result = await generate_mindmap(ctx, req.model_dump(mode="json"))

    assert result["status"] == "completed"
    assert cb.terminal_sent is True
    assert cb.complete_result is not None


# ---------------------------------------------------------------------------
# 2. chat_turn_task Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_turn_task_question_success():
    """Question intent chat turn runs, resolves answer, and calls complete_chat callback."""
    map_id = uuid4()
    job_id = uuid4()
    node_id = uuid4()
    snap = MapSnapshot(
        map_id=map_id,
        nodes=[NodeDraft(id=node_id, label="Root Topic", description="Overview")],
        edges=[],
    )

    req = ChatTurnRequest(
        job_id=job_id,
        map_id=map_id,
        message="What is the root topic?",
        history=[],
        map_snapshot=snap,
        callback_base_url="https://api.test",
        callback_token="test-token",
    )

    cb = FakeCallbackClient()
    answer_text = f"The root topic is Root Topic [node:{node_id}]."
    ctx = _make_context(llm=FakeLLMProvider([answer_text]), callback=cb)

    result = await chat_turn_task(ctx, req.model_dump(mode="json"))

    assert result["intent"] == "question"
    assert cb.terminal_sent is True
    assert cb.chat_complete_result is not None
    assert cb.chat_complete_result.status == "completed"
    assert cb.chat_complete_result.map_id == map_id
    assert answer_text in cb.chat_complete_result.assistant_message


@pytest.mark.asyncio
async def test_chat_turn_task_action_proposal_success():
    """Action proposal intent forwards proposal explanation to complete_chat callback."""
    map_id = uuid4()
    job_id = uuid4()
    node_id = uuid4()
    snap = MapSnapshot(map_id=map_id, nodes=[NodeDraft(id=node_id, label="Topic A")], edges=[])

    proposal_json = json.dumps(
        {
            "action_type": "edit_proposal",
            "target_node_ids": [str(node_id)],
            "parameters": {},
            "explanation": "Add subconcept B under Topic A",
            "needs_clarification": False,
        }
    )

    req = ChatTurnRequest(
        job_id=job_id,
        map_id=map_id,
        message="Add subconcept B under Topic A",
        history=[],
        map_snapshot=snap,
        callback_base_url="https://api.test",
        callback_token="test-token",
    )

    class ProposalDecisionModel(FakeDecisionModel):
        async def classify(self, *args, **kwargs):
            from providers.decision_model import ClassifyResult

            return ClassifyResult(label="edit_proposal", confidence=1.0)

    cb = FakeCallbackClient()
    ctx = _make_context(llm=FakeLLMProvider([proposal_json]), callback=cb)
    ctx["decision_model"] = ProposalDecisionModel()

    result = await chat_turn_task(ctx, req.model_dump(mode="json"))

    assert result["intent"] == "edit_proposal"
    assert cb.terminal_sent is True
    assert cb.chat_complete_result is not None
    assert "Add subconcept B" in cb.chat_complete_result.assistant_message


@pytest.mark.asyncio
async def test_chat_turn_task_preserves_short_term_memory():
    """Verifies that short-term memory (summary & history) is retained and sent back to callback."""
    map_id = uuid4()
    job_id = uuid4()
    snap = MapSnapshot(map_id=map_id, nodes=[], edges=[])

    req = ChatTurnRequest(
        job_id=job_id,
        map_id=map_id,
        message="Explain more",
        history=[ChatMessage(role="user", content="Past question")],
        summary="User is a beginner studying biology.",
        map_snapshot=snap,
        callback_base_url="https://api.test",
        callback_token="test-token",
    )

    cb = FakeCallbackClient()
    ctx = _make_context(llm=FakeLLMProvider(["Here is more detail."]), callback=cb)

    result = await chat_turn_task(ctx, req.model_dump(mode="json"))

    assert result["intent"] == "question"
    assert cb.terminal_sent is True
    assert cb.chat_complete_result is not None
    assert cb.chat_complete_result.summary == "User is a beginner studying biology."


@pytest.mark.asyncio
async def test_chat_turn_task_clarification_sent_to_callback():
    """When clarification is needed, the clarification question is passed to the callback."""
    map_id = uuid4()
    snap = MapSnapshot(map_id=map_id, nodes=[], edges=[])

    proposal_json = json.dumps(
        {
            "action_type": "regen_node",
            "target_node_ids": [],
            "parameters": {},
            "explanation": "",
            "needs_clarification": True,
            "clarification_question": "Which specific branch would you like to redo?",
        }
    )

    class RegenDecisionModel(FakeDecisionModel):
        async def classify(self, *args, **kwargs):
            from providers.decision_model import ClassifyResult

            return ClassifyResult(label="regen_node", confidence=1.0)

    req = ChatTurnRequest(
        map_id=map_id,
        message="Redo branch",
        history=[],
        map_snapshot=snap,
        callback_base_url="https://api.test",
        callback_token="test-token",
    )

    cb = FakeCallbackClient()
    ctx = _make_context(llm=FakeLLMProvider([proposal_json]), callback=cb)
    ctx["decision_model"] = RegenDecisionModel()

    result = await chat_turn_task(ctx, req.model_dump(mode="json"))

    assert result["needs_clarification"] is True
    assert cb.chat_complete_result is not None
    assert "Which specific branch" in cb.chat_complete_result.assistant_message


@pytest.mark.asyncio
async def test_chat_turn_task_failure_triggers_fail_chat(monkeypatch):
    """Unhandled exception in chat turn triggers fail_chat callback."""
    map_id = uuid4()
    snap = MapSnapshot(map_id=map_id, nodes=[], edges=[])
    req = ChatTurnRequest(
        map_id=map_id,
        message="Hello",
        map_snapshot=snap,
        callback_base_url="https://api.test",
        callback_token="test-token",
    )

    async def _exploding_run_chat_turn(deps, request):
        raise RuntimeError("Simulated unhandled chat turn crash")

    monkeypatch.setattr("worker.tasks.run_chat_turn", _exploding_run_chat_turn)

    cb = FakeCallbackClient()
    ctx = _make_context(callback=cb)

    result = await chat_turn_task(ctx, req.model_dump(mode="json"))

    assert "error" in result
    assert cb.terminal_sent is True
    assert cb.chat_fail_result is not None
    assert cb.chat_fail_result.status == "failed"
    assert "crash" in cb.chat_fail_result.error.lower()


# ---------------------------------------------------------------------------
# 3. ARQ Registration & Architecture Tests
# ---------------------------------------------------------------------------


def test_arq_worker_settings_functions_registered():
    """Verifies all required task functions are registered in WorkerSettings."""
    func_names = [f.__name__ for f in WorkerSettings.functions]
    assert "generate_mindmap" in func_names
    assert "regenerate_node_task" in func_names
    assert "chat_turn_task" in func_names


def test_tasks_stateless_no_database_access():
    """Verifies tasks module does not import or use Postgres/asyncpg/database packages."""
    import inspect

    import worker.tasks as tasks_mod

    source = inspect.getsource(tasks_mod)
    assert "asyncpg" not in source
    assert "sqlalchemy" not in source
    assert "connect(" not in source
    assert "PostgresCheckpointer" not in source
