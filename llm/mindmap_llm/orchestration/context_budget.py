"""Provider-aware prompt budgeting for map generation (S3-L7).

Token estimates use the same four-characters-per-token fallback as chat.  A
provider's exact tokenization can differ, so the input ceilings leave headroom
for message framing and reduce (but cannot guarantee elimination of) 413/429s.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from chat.token_budget import estimate_tokens
from rag.retrieval import RetrievedChunk


@dataclass(frozen=True)
class ProviderLimits:
    context_window: int
    input_ceiling: int
    output_ceiling: int


def provider_limits(provider: str, model: str) -> ProviderLimits:
    """Conservative ceilings; do not treat a model's full window as input capacity."""
    name = f"{provider}:{model}".lower()
    if "gemini" in name:
        if "flash" in name:
            return ProviderLimits(128_000, 32_000, 8_192)
        return ProviderLimits(32_000, 24_000, 4_096)
    if "qwen" in name and "groq" in name:
        # Groq Qwen free-tier rejects >=1000 requested output tokens (OTPM).
        return ProviderLimits(8_192, 6_000, 900)
    if "groq" in name:
        return ProviderLimits(8_192, 6_000, 2_048)
    if "qwen" in name:
        return ProviderLimits(8_192, 6_000, 900)
    return ProviderLimits(8_192, 6_000, 2_048)


@dataclass
class BudgetedContext:
    seed: str
    rag_chunks: list[RetrievedChunk]
    memories: list[str]
    history: list[str]
    external_payloads: list[str]
    max_output_tokens: int
    input_limit: int
    breakdown: dict[str, int] = field(default_factory=dict)
    pruned: list[str] = field(default_factory=list)

    @property
    def memory_text(self) -> str:
        return "\n".join(self.memories)


class ContextBudgetManager:
    """Fit a rendered prompt to a provider limit with stable pruning priority.

    Render receives the current context and returns the *complete user prompt*,
    including any RAG source headers.  This avoids under-counting prompt syntax.
    The system prompt is measured separately and never pruned.
    """

    def __init__(self, provider: str, model: str, max_output_tokens: int, active_history_window: int = 4):
        limits = provider_limits(provider, model)
        self.max_output_tokens = min(max(1, max_output_tokens), limits.output_ceiling)
        self.input_limit = max(0, min(limits.input_ceiling, limits.context_window - self.max_output_tokens - 128))
        self.active_history_window = max(0, active_history_window)

    def allocate(
        self,
        *,
        system_prompt: str,
        seed: str,
        rag_chunks: list[RetrievedChunk],
        render: Callable[[BudgetedContext], str],
        memories: list[str] | None = None,
        history: list[str] | None = None,
        external_payloads: list[str] | None = None,
    ) -> BudgetedContext:
        context = BudgetedContext(
            seed=seed,
            rag_chunks=list(rag_chunks),
            memories=list(memories or []),
            history=list(history or []),
            external_payloads=list(external_payloads or []),
            max_output_tokens=self.max_output_tokens,
            input_limit=self.input_limit,
        )
        system_tokens = estimate_tokens(system_prompt) + 8  # message framing
        if system_tokens >= self.input_limit:
            raise ValueError("System prompt alone exceeds the provider's input token budget")

        def total() -> int:
            return system_tokens + estimate_tokens(render(context)) + 8

        # Long-term memory is capped at 10% of the input ceiling, even when
        # there is otherwise unused context capacity.
        memory_cap = self.input_limit // 10
        while context.memories and estimate_tokens(context.memory_text) > memory_cap:
            context.memories.pop(0)
            context.pruned.append("memory")

        # Stage 1: remove the lowest-ranked RAG chunk first; keep high scores.
        while context.rag_chunks and total() > self.input_limit:
            worst = min(range(len(context.rag_chunks)), key=lambda i: context.rag_chunks[i].relevance_score)
            context.rag_chunks.pop(worst)
            context.pruned.append("rag")

        # Stage 2: remove oldest chat turns OUTSIDE the recent active window.
        while len(context.history) > self.active_history_window and total() > self.input_limit:
            context.history.pop(0)
            context.pruned.append("history")

        # Stage 3: drop dense search/page-reader/scholar payloads, oldest first.
        while context.external_payloads and total() > self.input_limit:
            context.external_payloads.pop(0)
            context.pruned.append("external")

        # Emergency fallback: otherwise a very large active conversation or
        # seed can still breach the ceiling, so bound them without exceeding it.
        while context.history and total() > self.input_limit:
            context.history.pop(0)
            context.pruned.append("active_history")
        while context.memories and total() > self.input_limit:
            context.memories.pop(0)
            context.pruned.append("memory")
        if total() > self.input_limit and context.seed:
            excess_chars = (total() - self.input_limit) * 4 + 16
            while context.seed and total() > self.input_limit:
                context.seed = context.seed[: max(0, len(context.seed) - excess_chars)]
                excess_chars = 16
            context.pruned.append("seed")
        if total() > self.input_limit:
            raise ValueError("Mandatory prompt instructions cannot fit the provider input budget")

        context.breakdown = {
            "system": system_tokens,
            "seed": estimate_tokens(context.seed),
            "memories": estimate_tokens(context.memory_text),
            "rag": sum(estimate_tokens(chunk.record.chunk_text) for chunk in context.rag_chunks),
            "history": sum(estimate_tokens(text) for text in context.history),
            "external": sum(estimate_tokens(text) for text in context.external_payloads),
            "input_total": total(),
            "input_limit": self.input_limit,
            "output_reserved": self.max_output_tokens,
        }
        return context
