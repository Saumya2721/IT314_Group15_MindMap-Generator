"""
Chat branch elaboration integration for S2-L12 and S3-A3.

Reuses the shared branch expansion engine (orchestration.expansion.expand_branch)
to elaborate/expand a selected node in an existing mind map with scoped RAG
grounding, schema validation, and ActionProposal synthesis.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from contracts.models import (
    ActionProposal,
    ChatIntent,
    CreateEdgeParam,
    CreateNodeParam,
    GraphEditParameters,
    NodeDraft,
)
from orchestration.deps import GenerationDeps
from orchestration.expansion import BranchExpansionResult, expand_branch

logger = logging.getLogger(__name__)


async def elaborate_branch(
    deps: GenerationDeps,
    *,
    map_id: UUID,
    target_node: NodeDraft,
    ancestor_labels: list[str] | None = None,
    instruction: str | None = None,
    branch_context_nodes: list[NodeDraft] | None = None,
    allowed_node_ids: set[str] | None = None,
    max_retries: int | None = None,
    enforce_quality: bool = True,
) -> BranchExpansionResult:
    """Elaborate on a branch for the chat agent using the shared expand_branch function.

    Args:
        deps: Generation dependencies (LLM, vector store, embedding provider, etc.)
        map_id: UUID of the mind map (used as Pinecone namespace).
        target_node: The node to expand / elaborate on.
        ancestor_labels: Optional labels of ancestor nodes for breadcrumb context.
        instruction: Optional user instruction or focus prompt.
        branch_context_nodes: Optional existing subnodes under this branch.
        allowed_node_ids: Optional set of allowed node IDs.
        max_retries: Number of retries on failure (defaults to settings).
        enforce_quality: Whether to enforce minimum subnode expansion and citation validity.

    Returns:
        BranchExpansionResult containing resolved nodes, edges, citations, and status.
    """
    namespace = str(map_id)
    retries = max_retries if max_retries is not None else deps.settings.generation.max_generation_retries
    logger.info("Elaborating branch '%s' for map %s", target_node.label, map_id)
    return await expand_branch(
        deps=deps,
        namespace=namespace,
        selected_node=target_node,
        ancestor_labels=ancestor_labels or [],
        comment=instruction,
        branch_context_nodes=branch_context_nodes,
        allowed_node_ids=allowed_node_ids,
        max_retries=retries,
        report_progress=False,
        enforce_quality=enforce_quality,
    )


def build_elaboration_action_proposal(
    *,
    target_node: NodeDraft,
    expansion_result: BranchExpansionResult,
    instruction: str | None = None,
) -> ActionProposal:
    """Create a structured ActionProposal for chat from a branch expansion result (S3-A3).

    Converts the newly generated subnodes and connecting edges into typed
    GraphEditParameters that can be sent to the frontend for user confirmation.
    """
    subnodes = [n for n in expansion_result.nodes if n.id != target_node.id]
    create_nodes: list[CreateNodeParam | dict[str, Any]] = [
        CreateNodeParam(id=n.id, label=n.label, description=n.description) for n in subnodes
    ]
    create_edges: list[CreateEdgeParam | dict[str, Any]] = [
        CreateEdgeParam(
            id=e.id,
            source_node_id=e.source_node_id,
            target_node_id=e.target_node_id,
            label=e.label,
        )
        for e in expansion_result.edges
    ]
    explanation = (
        f"Elaborated branch '{target_node.label}' by proposing {len(subnodes)} sub-concept(s)."
        if subnodes
        else f"Attempted elaboration for branch '{target_node.label}'."
    )
    return ActionProposal(
        action_type=ChatIntent.EDIT_PROPOSAL,
        target_node_ids=[target_node.id],
        target_node_id=target_node.id,
        parameters=GraphEditParameters(
            create_nodes=create_nodes,
            create_edges=create_edges,
        ),
        explanation=explanation,
        instruction=instruction,
    )
