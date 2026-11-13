from __future__ import annotations

from memory.schemas import MemoryContext, MemoryMessage, TokenBudget
from memory.summarizer import RollingSummarizer
from memory.token_budget import TokenBudgetManager, estimate_message_tokens


class MemoryContextAssembler:
    """Assembles a token-budgeted MemoryContext from raw chat messages."""

    def __init__(
        self,
        budget: TokenBudget | None = None,
        summarizer: RollingSummarizer | None = None,
    ) -> None:
        self.budget = budget if budget is not None else TokenBudget()
        self.summarizer = summarizer if summarizer is not None else RollingSummarizer()
        self.budget_manager = TokenBudgetManager(self.budget)

    async def assemble(
        self,
        map_id: str,
        raw_messages: list[dict],
        existing_summary: str = "",
        llm_provider=None,
        window_size: int = 6,
    ) -> MemoryContext:
        """Build and return a MemoryContext from raw message dicts.

        Steps:
        1. Optionally generate a rolling summary and trim old messages.
        2. Keep recent window of messages.
        3. Trim remaining messages to the token budget.
        4. Build and return the MemoryContext.
        """
        final_summary = existing_summary if existing_summary else None
        remaining_msgs = list(raw_messages)

        # --- Rolling summary step ---
        if self.summarizer.needs_summary(raw_messages):
            rolling = await self.summarizer.create_rolling_summary(
                map_id=map_id,
                messages=raw_messages,
                existing_summary=existing_summary or "",
                llm_provider=llm_provider,
            )
            final_summary = rolling.summary_text
            keep_count = min(window_size, max(1, self.summarizer.max_messages_before_summary // 2))
            remaining_msgs = raw_messages[-keep_count:] if keep_count > 0 else []
        elif len(remaining_msgs) > window_size:
            remaining_msgs = remaining_msgs[-window_size:]

        # --- Budget trimming step ---
        available_tokens = self.budget_manager.get_available_for_history()
        trimmed_dicts, was_truncated = self.budget_manager.trim_messages_to_budget(remaining_msgs, available_tokens)

        # --- Convert to MemoryMessage objects ---
        trimmed_messages: list[MemoryMessage] = []
        for m in trimmed_dicts:
            role = m.get("role", "unknown")
            content = m.get("content", "")
            tokens = estimate_message_tokens(role, content)
            trimmed_messages.append(MemoryMessage(role=role, content=content, tokens=tokens))

        # --- Token accounting ---
        total_tokens = self.budget_manager.calculate_context_tokens(trimmed_dicts, final_summary or "")
        budget_remaining = self.budget.max_total_tokens - total_tokens - self.budget.reserved_for_response

        return MemoryContext(
            messages=trimmed_messages,
            summary=final_summary,
            total_tokens=total_tokens,
            budget_remaining=budget_remaining,
            is_truncated=was_truncated,
        )

    def format_for_llm(self, context: MemoryContext) -> str:
        """Format a MemoryContext into a string suitable for LLM injection."""
        lines: list[str] = ["[CONVERSATION CONTEXT]"]

        if context.summary:
            lines.append(f"Summary: {context.summary}")
            lines.append("")

        lines.append("Recent messages:")
        for msg in context.messages:
            lines.append(f"{msg.role}: {msg.content}")

        lines.append("")
        lines.append(f"[Token budget remaining: {context.budget_remaining}]")
        return "\n".join(lines)
