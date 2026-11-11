from __future__ import annotations

from memory.callback_handler import MemoryCallbackHandler
from memory.context_assembler import MemoryContextAssembler
from memory.schemas import CallbackPayload, MemoryContext, TokenBudget
from memory.summarizer import RollingSummarizer


class MemoryService:
    """Public-facing service that ties the memory module components together."""

    def __init__(
        self,
        budget: TokenBudget | None = None,
        llm_provider=None,
    ) -> None:
        self.budget = budget if budget is not None else TokenBudget()
        self.llm_provider = llm_provider
        self.summarizer = RollingSummarizer()
        self.assembler = MemoryContextAssembler(
            budget=self.budget,
            summarizer=self.summarizer,
        )
        # db_admin set to None here; callers may replace after construction
        self.callback_handler = MemoryCallbackHandler(None)

    async def build_context(
        self,
        map_id: str,
        raw_messages: list[dict],
        existing_summary: str = "",
        window_size: int = 6,
    ) -> MemoryContext:
        """Build and return a MemoryContext for the given map and messages."""
        return await self.assembler.assemble(
            map_id=map_id,
            raw_messages=raw_messages,
            existing_summary=existing_summary,
            llm_provider=self.llm_provider,
            window_size=window_size,
        )

    def format_context_for_llm(self, context: MemoryContext) -> str:
        """Format a MemoryContext as a string for LLM injection."""
        return self.assembler.format_for_llm(context)

    async def handle_job_callback(self, payload: CallbackPayload) -> dict:
        """Delegate a job-completion callback to the callback handler."""
        return await self.callback_handler.handle_completion(payload)

    def get_summary(self, map_id: str) -> str | None:
        """Return the cached rolling summary for a mind map, or None."""
        return self.callback_handler.get_cached_summary(map_id)
