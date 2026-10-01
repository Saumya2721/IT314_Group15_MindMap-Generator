"""Structured output the generation pipeline must produce (FR-10, US-03).

LLM output is probabilistic, so nothing reaches the database or the canvas
until it has passed these checks. The rules come from the domain requirements:
DR-01 (nodes and edges), DR-02 (one root), DR-03 (unique ids) and DR-04
(relationships carry a label).
"""

from collections import Counter

from pydantic import BaseModel, Field, model_validator


class NodeOut(BaseModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=255)
    description: str | None = None


class EdgeOut(BaseModel):
    source: str
    target: str
    label: str | None = Field(default=None, max_length=100)


class MindMapOut(BaseModel):
    nodes: list[NodeOut]
    edges: list[EdgeOut]

    @model_validator(mode="after")
    def check_graph(self) -> "MindMapOut":
        if not self.nodes:
            raise ValueError("a mind map needs at least one node")

        duplicates = [i for i, n in Counter(node.id for node in self.nodes).items() if n > 1]
        if duplicates:
            raise ValueError(f"duplicate node ids: {sorted(duplicates)}")

        known = {node.id for node in self.nodes}
        seen: set[tuple[str, str, str | None]] = set()
        for edge in self.edges:
            if edge.source not in known or edge.target not in known:
                raise ValueError(f"edge {edge.source}->{edge.target} points at an unknown node")
            if edge.source == edge.target:
                raise ValueError(f"self-loop on node {edge.source}")
            key = (edge.source, edge.target, edge.label)
            if key in seen:
                raise ValueError(f"duplicate edge {edge.source}->{edge.target}")
            seen.add(key)

        # Exactly one root: a node no edge points at. A disconnected node
        # would also show up as a second root.
        targets = {edge.target for edge in self.edges}
        roots = [node.id for node in self.nodes if node.id not in targets]
        if len(roots) != 1:
            raise ValueError(f"expected exactly one root node, found {len(roots)}: {roots}")
        return self
