"""
Shared branch expansion, parallel execution, and graph merging for S2-L12 Two-Pass Generation.

This module provides reusable primitives shared by:
  1. Full mind map generation (Pass 2 parallel branch expansion).
  2. S2-L7 node regeneration (regenerating a single branch with breadcrumbs).
  3. Chat "elaborate" functionality (expanding a branch on user request).

Architectural constraints:
  - Worker remains stateless and database-free.
  - Concurrency is strictly bounded by an asyncio.Semaphore to respect Groq rate limits.
  - Independent retrieval is performed per branch with breadcrumb grounding.
  - Existing schema validation (validate_draft, validate_branch_draft, run_quality_checks)
    and wire-contract models (NodeDraft, EdgeDraft) are strictly preserved.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from uuid import uuid4

from contracts.models import EdgeDraft, NodeDraft
from orchestration.deps import GenerationDeps
from orchestration.prompts import (
    JSONExtractionError,
    build_outline_system_prompt,
    build_outline_user_prompt,
    build_regenerate_node_system_prompt,
    build_regenerate_node_user_prompt,
    extract_headings_from_text,
    extract_json_object,
)
from providers.base import Message
from rag.retrieval import RetrievedChunk, rerank_context, retrieve_context
from validation.schema import (
    DraftMindMap,
    ValidationFailure,
    _DraftEdge,
    _DraftNode,
    resolve_branch_to_records,
    resolve_to_records,
    run_quality_checks,
    validate_branch_draft,
    validate_draft,
)

logger = logging.getLogger(__name__)


@dataclass
class BranchExpansionResult:
    """The result of expanding a single branch."""

    selected_node: NodeDraft
    nodes: list[NodeDraft] = field(default_factory=list)
    edges: list[EdgeDraft] = field(default_factory=list)
    draft: DraftMindMap | None = None
    retrieved_chunks: list[RetrievedChunk] = field(default_factory=list)
    status: str = "completed"  # "completed" | "failed"
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. Reusable Branch Expansion Function (Used by Full Gen, S2-L7, Chat)
# ---------------------------------------------------------------------------


async def expand_branch(
    deps: GenerationDeps,
    *,
    namespace: str,
    selected_node: NodeDraft,
    ancestor_labels: list[str],
    breadcrumb_path: str | None = None,
    comment: str | None = None,
    branch_context_nodes: list[NodeDraft] | None = None,
    allowed_node_ids: set[str] | None = None,
    max_retries: int = 2,
    existing_chunks: list[RetrievedChunk] | None = None,
    previous_validation_errors: list[str] | None = None,
) -> BranchExpansionResult:
    """Expand a single branch independently.

    Shared by:
      - Full mind map Pass 2 (expanding each outline branch).
      - S2-L7 node regeneration.
      - Chat branch elaboration.
    """
    breadcrumb = breadcrumb_path or " > ".join([*ancestor_labels, selected_node.label])
    scoped_query = f"{breadcrumb}: {comment or selected_node.description or selected_node.label}"

    # Stage 1: Branch-specific Retrieval
    if existing_chunks is not None:
        chunks = existing_chunks
    else:
        candidates = await retrieve_context(
            vector_store=deps.vector_store,
            namespace=namespace,
            query=scoped_query,
            embedding_provider=deps.embedding_provider,
            top_k=deps.settings.generation.rag_top_k,
        )
        chunks = await rerank_context(
            decision_model=deps.decision_model,
            query=scoped_query,
            candidates=candidates,
            keep_top_k=deps.settings.generation.rag_rerank_top_k,
        )

    # Stage 2: LLM Generation with Retry Loop
    system_prompt = build_regenerate_node_system_prompt()
    validation_errors: list[str] = list(previous_validation_errors or [])
    draft: DraftMindMap | None = None

    for _attempt in range(max_retries + 1):
        prompt = build_regenerate_node_user_prompt(
            selected_node_label=selected_node.label,
            selected_node_description=selected_node.description,
            comment=comment,
            ancestor_labels=ancestor_labels,
            breadcrumb_path=breadcrumb,
            branch_context_nodes=branch_context_nodes,
            supporting_context=chunks,
            previous_validation_errors=validation_errors or None,
        )

        try:
            response = await deps.llm.generate(
                [Message(role="user", content=prompt)],
                system=system_prompt,
                max_tokens=deps.settings.llm.max_output_tokens,
                temperature=0.3,
            )
            payload = extract_json_object(response.text or "")
        except (JSONExtractionError, Exception) as exc:
            validation_errors = [f"Extraction error: {exc}"]
            continue

        effective_allowed = allowed_node_ids
        if effective_allowed is None:
            effective_allowed = {str(selected_node.id), *(str(n.id) for n in (branch_context_nodes or []))}

        try:
            draft = validate_branch_draft(
                payload,
                selected_node=selected_node,
                allowed_node_ids=effective_allowed,
                ancestor_labels=ancestor_labels,
            )
            validation_errors = []
            break
        except ValidationFailure as exc:
            validation_errors = exc.errors

    if draft is None or validation_errors:
        return BranchExpansionResult(
            selected_node=selected_node,
            retrieved_chunks=chunks,
            status="failed",
            errors=validation_errors or ["Failed to generate valid branch draft after retries"],
        )

    effective_allowed = allowed_node_ids or {str(selected_node.id)}
    nodes, edges = resolve_branch_to_records(
        draft,
        selected_node=selected_node,
        allowed_node_ids=effective_allowed,
        supporting_context=chunks,
    )

    return BranchExpansionResult(
        selected_node=selected_node,
        nodes=nodes,
        edges=edges,
        draft=draft,
        retrieved_chunks=chunks,
        status="completed",
        errors=[],
    )


# ---------------------------------------------------------------------------
# 2. Pass 1: Outline Generation & Validation
# ---------------------------------------------------------------------------


def validate_outline_draft(
    payload: dict,
    *,
    min_branches: int = 4,
    max_branches: int = 8,
) -> tuple[DraftMindMap, _DraftNode, list[_DraftNode]]:
    """Validate that the Pass 1 outline has exactly 1 root and 4-8 top-level branch nodes.

    Returns (validated_draft, root_node, branch_nodes).
    Raises ValidationFailure on constraint violations.
    """
    draft = validate_draft(payload)

    # 1. Identify root (in-degree == 0)
    incoming: dict[str, int] = {n.id: 0 for n in draft.nodes}
    children_map: dict[str, list[str]] = {n.id: [] for n in draft.nodes}
    for e in draft.edges:
        if e.target in incoming:
            incoming[e.target] += 1
        if e.source in children_map:
            children_map[e.source].append(e.target)

    roots = [n_id for n_id, count in incoming.items() if count == 0]
    if len(roots) != 1:
        raise ValidationFailure([f"Pass 1 outline must have exactly 1 root node, found {len(roots)}"])

    root_id = roots[0]
    root_node = next(n for n in draft.nodes if n.id == root_id)

    # 2. Extract direct children of root
    direct_child_ids = set(children_map.get(root_id, []))
    branch_nodes = [n for n in draft.nodes if n.id in direct_child_ids]

    # 3. Branch count check (4-8 branches)
    branch_count = len(branch_nodes)
    if branch_count < min_branches or branch_count > max_branches:
        raise ValidationFailure(
            [
                f"Pass 1 outline must have between {min_branches} and {max_branches} top-level branches, found {branch_count}"
            ]
        )

    # 4. Enforce depth = 1 (outline must not generate deep sub-trees)
    for b_node in branch_nodes:
        descendants = children_map.get(b_node.id, [])
        if descendants:
            raise ValidationFailure(
                [f"Pass 1 outline branch '{b_node.label}' should not have deeper sub-nodes in outline pass"]
            )

    return draft, root_node, branch_nodes


async def generate_outline(
    deps: GenerationDeps,
    *,
    seed_text: str,
    detail_level: str | None = None,
    min_branches: int = 4,
    max_branches: int = 8,
    max_retries: int = 2,
) -> tuple[DraftMindMap, _DraftNode, list[_DraftNode]]:
    """Execute Pass 1: Generate a high-level structural outline with 4-8 branches."""
    system_prompt = build_outline_system_prompt()
    headings = extract_headings_from_text(seed_text)
    validation_errors: list[str] = []

    for _attempt in range(max_retries + 1):
        prompt = build_outline_user_prompt(
            seed_text=seed_text,
            detail_level=detail_level,
            headings=headings or None,
            previous_validation_errors=validation_errors or None,
        )

        try:
            response = await deps.llm.generate(
                [Message(role="user", content=prompt)],
                system=system_prompt,
                max_tokens=deps.settings.llm.max_output_tokens,
                temperature=0.3,
            )
            payload = extract_json_object(response.text or "")
        except (JSONExtractionError, Exception) as exc:
            validation_errors = [f"JSON extraction error in outline: {exc}"]
            continue

        try:
            return validate_outline_draft(
                payload,
                min_branches=min_branches,
                max_branches=max_branches,
            )
        except ValidationFailure as exc:
            validation_errors = exc.errors

    raise ValidationFailure(
        [f"Failed to generate valid outline after {max_retries + 1} attempts: {'; '.join(validation_errors)}"]
    )


# ---------------------------------------------------------------------------
# 3. Pass 2: Bounded Parallel Expansion
# ---------------------------------------------------------------------------


async def expand_branches_parallel(
    deps: GenerationDeps,
    *,
    namespace: str,
    root_node: NodeDraft | _DraftNode,
    branch_nodes: list[NodeDraft] | list[_DraftNode],
    concurrency_limit: int | None = None,
    max_retries: int = 2,
) -> list[BranchExpansionResult]:
    """Execute Pass 2: Expand each branch concurrently, bounded by an asyncio.Semaphore."""
    limit = concurrency_limit or deps.settings.generation.branch_expansion_concurrency
    semaphore = asyncio.Semaphore(limit)

    # Convert _DraftNode to NodeDraft if necessary
    node_drafts: list[NodeDraft] = []
    for b in branch_nodes:
        if isinstance(b, NodeDraft):
            node_drafts.append(b)
        else:
            node_drafts.append(
                NodeDraft(
                    id=uuid4(),
                    label=b.label,
                    description=b.description,
                    node_type=b.node_type,
                )
            )

    root_label = root_node.label

    async def _bounded_expand(b: NodeDraft) -> BranchExpansionResult:
        async with semaphore:
            logger.info("Starting expansion for branch: %s", b.label)
            return await expand_branch(
                deps=deps,
                namespace=namespace,
                selected_node=b,
                ancestor_labels=[root_label],
                breadcrumb_path=f"{root_label} > {b.label}",
                max_retries=max_retries,
            )

    tasks = [_bounded_expand(b) for b in node_drafts]
    results = await asyncio.gather(*tasks)
    return list(results)


# ---------------------------------------------------------------------------
# 4. Merge & Graph Validation
# ---------------------------------------------------------------------------


def merge_expanded_branches(
    *,
    outline_root: _DraftNode | NodeDraft,
    branch_results: list[BranchExpansionResult],
    target_node_range: tuple[int, int] = (10, 35),
    target_max_depth: int = 4,
) -> tuple[DraftMindMap, list[NodeDraft], list[EdgeDraft], list[RetrievedChunk], list[str]]:
    """Merge expanded branches with the root into a complete, validated mind map.

    Avoids ID collisions by namespacing provisional IDs for each branch.
    Validates final graph topology with validate_draft and soft quality checks.
    """
    # Check for branch expansion failures
    failed_branches = [r for r in branch_results if r.status != "completed"]
    if failed_branches:
        err_msgs = [f"Branch '{r.selected_node.label}': {'; '.join(r.errors)}" for r in failed_branches]
        raise ValidationFailure([f"Failed expanding branches: {'; '.join(err_msgs)}"])

    all_draft_nodes: list[_DraftNode] = []
    all_draft_edges: list[_DraftEdge] = []
    all_supporting_chunks: list[RetrievedChunk] = []

    # 1. Add global root
    root_provisional_id = "root"
    all_draft_nodes.append(
        _DraftNode(
            id=root_provisional_id,
            label=outline_root.label,
            description=outline_root.description,
            node_type=getattr(outline_root, "node_type", "default"),
            sources=["seed"],
        )
    )

    # 2. Merge each branch's draft nodes and edges with collision-free provisional IDs
    for b_idx, result in enumerate(branch_results, start=1):
        draft = result.draft
        if draft is None:
            continue

        all_supporting_chunks.extend(result.retrieved_chunks)

        # Find branch root (in-degree == 0 in branch draft)
        incoming: dict[str, int] = {n.id: 0 for n in draft.nodes}
        for e in draft.edges:
            if e.target in incoming:
                incoming[e.target] += 1
        branch_roots = [n_id for n_id, count in incoming.items() if count == 0]
        if not branch_roots:
            continue
        original_branch_root_id = branch_roots[0]

        # Map each provisional ID in branch draft to a globally unique provisional ID
        id_remapping: dict[str, str] = {}
        for n_idx, n in enumerate(draft.nodes, start=1):
            if n.id == original_branch_root_id:
                new_id = f"b{b_idx}_root"
            else:
                new_id = f"b{b_idx}_n{n_idx}"
            id_remapping[n.id] = new_id

            all_draft_nodes.append(
                _DraftNode(
                    id=new_id,
                    label=n.label,
                    description=n.description,
                    background_color=n.background_color,
                    text_color=n.text_color,
                    node_type=n.node_type,
                    sources=n.sources,
                )
            )

        # Connect global root to this branch root
        all_draft_edges.append(
            _DraftEdge(
                source=root_provisional_id,
                target=id_remapping[original_branch_root_id],
                label=result.selected_node.label,
            )
        )

        # Add internal branch edges with remapped IDs
        for e in draft.edges:
            all_draft_edges.append(
                _DraftEdge(
                    source=id_remapping[e.source],
                    target=id_remapping[e.target],
                    label=e.label,
                )
            )

    # 3. Validate complete merged mind map topology (single root, no cycles, unique IDs)
    merged_payload = {
        "nodes": [n.model_dump() for n in all_draft_nodes],
        "edges": [e.model_dump() for e in all_draft_edges],
    }
    merged_draft = validate_draft(merged_payload)

    # 4. Run soft quality checks
    quality_report = run_quality_checks(
        merged_draft,
        target_node_range=target_node_range,
        target_max_depth=target_max_depth,
        supporting_context=all_supporting_chunks,
    )

    # 5. Resolve provisional IDs to wire-contract records
    nodes, edges = resolve_to_records(merged_draft, supporting_context=all_supporting_chunks)

    return merged_draft, nodes, edges, all_supporting_chunks, quality_report.warnings


# ---------------------------------------------------------------------------
# 5. Full Two-Pass Generation Orchestration
# ---------------------------------------------------------------------------


async def execute_two_pass_generation(
    deps: GenerationDeps,
    *,
    namespace: str,
    seed_text: str,
    detail_level: str | None = None,
    concurrency_limit: int | None = None,
    min_branches: int = 4,
    max_branches: int = 8,
    max_retries: int = 2,
    target_node_range: tuple[int, int] = (10, 35),
    target_max_depth: int = 4,
) -> tuple[list[NodeDraft], list[EdgeDraft], list[RetrievedChunk], list[str]]:
    """Execute the complete two-pass generation pipeline:

    Pass 1: Generate outline (1 root + 4-8 branches).
    Pass 2: Concurrently expand branches bounded by concurrency limit.
    Merge: Merge expanded subtrees into a complete validated mind map.
    """
    logger.info("Executing Pass 1: Outline generation")
    outline_draft, root_node, branch_nodes = await generate_outline(
        deps,
        seed_text=seed_text,
        detail_level=detail_level,
        min_branches=min_branches,
        max_branches=max_branches,
        max_retries=max_retries,
    )
    logger.info("Pass 1 completed with %d branches", len(branch_nodes))

    logger.info("Executing Pass 2: Parallel branch expansion")
    branch_results = await expand_branches_parallel(
        deps,
        namespace=namespace,
        root_node=root_node,
        branch_nodes=branch_nodes,
        concurrency_limit=concurrency_limit,
        max_retries=max_retries,
    )

    logger.info("Executing Merge & Validation")
    merged_draft, nodes, edges, chunks, warnings = merge_expanded_branches(
        outline_root=root_node,
        branch_results=branch_results,
        target_node_range=target_node_range,
        target_max_depth=target_max_depth,
    )
    logger.info("Two-pass generation succeeded: %d nodes, %d edges", len(nodes), len(edges))

    return nodes, edges, chunks, warnings
