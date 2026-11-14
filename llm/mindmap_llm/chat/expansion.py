"""
Chat branch elaboration integration for S2-L12.

Reuses the shared branch expansion engine (orchestration.expansion.expand_branch)
to elaborate/expand a selected node in an existing mind map with scoped RAG
grounding and validation.
"""

from __future__ import annotations

import logging
from uuid import UUID

from contracts.models import NodeDraft
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

    Returns:
        BranchExpansionResult containing resolved nodes, edges, citations, and status.
    """
    namespace = str(map_id)
    logger.info("Elaborating branch '%s' for map %s", target_node.label, map_id)
    return await expand_branch(
        deps=deps,
        namespace=namespace,
        selected_node=target_node,
        ancestor_labels=ancestor_labels or [],
        comment=instruction,
        branch_context_nodes=branch_context_nodes,
        allowed_node_ids=allowed_node_ids,
        max_retries=deps.settings.generation.max_generation_retries,
    )
