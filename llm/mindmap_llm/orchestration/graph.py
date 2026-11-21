from __future__ import annotations

from langgraph.graph import END, StateGraph
from orchestration.deps import GenerationDeps
from orchestration.expansion import expand_branch
from orchestration.nodes import make_nodes
from orchestration.state import GenerationState


def _validate_router(state: GenerationState) -> str:
    """After validate_node: proceed to finalize_success on success, retry
    generate_node with feedback on a recoverable failure, or give up and
    go to finalize_failure once max_generation_retries is exhausted."""
    if not state.get("validation_errors"):
        return "finalize_success"
    max_retries = state.get("_max_retries", 2)
    if state.get("retry_count", 0) <= max_retries:
        return "generate"
    return "finalize_failure"


def build_generation_graph(deps: GenerationDeps):
    nodes = make_nodes(deps)

    graph = StateGraph(GenerationState)
    graph.add_node("ingest", nodes["ingest"])
    graph.add_node("classify_routing", nodes["classify_routing"])
    graph.add_node("retrieve", nodes["retrieve"])
    graph.add_node("generate", nodes["generate"])
    graph.add_node("validate", nodes["validate"])
    graph.add_node("finalize_success", nodes["finalize_success"])
    graph.add_node("finalize_failure", nodes["finalize_failure"])

    graph.set_entry_point("ingest")
    graph.add_edge("ingest", "classify_routing")
    graph.add_edge("classify_routing", "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", "validate")
    graph.add_conditional_edges(
        "validate",
        _validate_router,
        {
            "finalize_success": "finalize_success",
            "generate": "generate",
            "finalize_failure": "finalize_failure",
        },
    )
    graph.add_edge("finalize_success", END)
    graph.add_edge("finalize_failure", END)

    return graph.compile()


async def run_generation(
    deps: GenerationDeps,
    *,
    initial_state: GenerationState,
    max_retries: int,
) -> GenerationState:
    graph = build_generation_graph(deps)
    seeded_state: GenerationState = {**initial_state, "_max_retries": max_retries, "retry_count": 0}  # type: ignore[typeddict-item]
    final_state = await graph.ainvoke(seeded_state)
    return final_state


def _validate_branch_router(state: GenerationState) -> str:
    """After validate_branch: proceed to finalize_success on success, retry
    generate_branch with feedback on a recoverable failure, or give up and
    go to finalize_failure once max_generation_retries is exhausted."""
    if not state.get("validation_errors"):
        return "finalize_success"
    max_retries = state.get("_max_retries", 2)
    if state.get("retry_count", 0) <= max_retries:
        return "generate_branch"
    return "finalize_failure"


def build_regenerate_node_graph(deps: GenerationDeps):
    nodes = make_nodes(deps)

    graph = StateGraph(GenerationState)
    graph.add_node("retrieve_branch", nodes["retrieve_branch"])
    graph.add_node("generate_branch", nodes["generate_branch"])
    graph.add_node("validate_branch", nodes["validate_branch"])
    graph.add_node("finalize_success", nodes["finalize_success"])
    graph.add_node("finalize_failure", nodes["finalize_failure"])

    graph.set_entry_point("retrieve_branch")
    graph.add_edge("retrieve_branch", "generate_branch")
    graph.add_edge("generate_branch", "validate_branch")
    graph.add_conditional_edges(
        "validate_branch",
        _validate_branch_router,
        {
            "finalize_success": "finalize_success",
            "generate_branch": "generate_branch",
            "finalize_failure": "finalize_failure",
        },
    )
    graph.add_edge("finalize_success", END)
    graph.add_edge("finalize_failure", END)

    return graph.compile()


async def run_regenerate_node(
    deps: GenerationDeps,
    *,
    initial_state: GenerationState,
    max_retries: int,
) -> GenerationState:
    """Execute node-level regeneration using the unified shared expand_branch engine (S3-A3)."""
    selected_node = initial_state.get("selected_node")
    if not selected_node:
        raise ValueError("regenerate_node requires state['selected_node']")

    ancestor_labels = initial_state.get("ancestor_labels", [])
    breadcrumb = initial_state.get("breadcrumb_path") or " > ".join([*ancestor_labels, selected_node.label])
    comment = initial_state.get("comment") or initial_state.get("regeneration_instruction")
    branch_nodes = initial_state.get("branch_nodes", [])
    allowed_ids = initial_state.get("allowed_node_ids")
    if allowed_ids is None:
        allowed_ids = {str(selected_node.id), *(str(n.id) for n in branch_nodes)}

    res = await expand_branch(
        deps,
        namespace=initial_state["namespace"],
        selected_node=selected_node,
        ancestor_labels=ancestor_labels,
        breadcrumb_path=breadcrumb,
        comment=comment,
        parent_edges=initial_state.get("parent_edges"),
        branch_context_nodes=branch_nodes,
        allowed_node_ids=allowed_ids,
        max_retries=max_retries,
        report_progress=True,
    )

    if res.status == "completed":
        return {
            **initial_state,
            "status": "completed",
            "nodes": res.nodes,
            "edges": res.edges,
            "draft_payload": res.draft.model_dump() if res.draft else None,
            "retrieved_chunks": res.retrieved_chunks,
            "quality_warnings": res.quality_warnings,
            "validation_errors": [],
            "branch_result": res,
        }
    else:
        return {
            **initial_state,
            "status": "failed",
            "error_message": "Generation failed after retries: " + "; ".join(res.errors),
            "failed_stage": "validate",
            "validation_errors": res.errors,
            "branch_result": res,
        }
