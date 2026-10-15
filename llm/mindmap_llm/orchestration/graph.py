from __future__ import annotations

from langgraph.graph import END, StateGraph
from orchestration.deps import GenerationDeps
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
