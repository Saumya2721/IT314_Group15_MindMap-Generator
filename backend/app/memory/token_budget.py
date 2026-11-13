from __future__ import annotations

from memory.schemas import TokenBudget


def estimate_tokens(text: str) -> int:
    """Rough GPT-style token estimate: 1 token ≈ 4 characters."""
    return len(text) // 4


def estimate_message_tokens(role: str, content: str) -> int:
    """Estimate tokens for a single message: 4 overhead + content tokens."""
    return 4 + estimate_tokens(role) + estimate_tokens(content)


class TokenBudgetManager:
    """Manages token budget constraints for context assembly."""

    def __init__(self, budget: TokenBudget) -> None:
        self.budget = budget

    def get_available_for_history(self) -> int:
        """Return the number of tokens available for chat history."""
        return min(
            self.budget.max_history_tokens,
            self.budget.max_total_tokens - self.budget.reserved_for_response - self.budget.max_summary_tokens,
        )

    def fits_in_budget(self, messages: list, summary: str = "") -> bool:
        """Return True if total token usage fits within the budget."""
        total = self.calculate_context_tokens(messages, summary)
        return total <= (self.budget.max_total_tokens - self.budget.reserved_for_response)

    def trim_messages_to_budget(self, messages: list[dict], available_tokens: int) -> tuple[list[dict], bool]:
        """Trim oldest messages until tokens fit within available_tokens.

        Keeps the most recent messages.  Returns (trimmed_list, was_truncated).
        """
        was_truncated = False
        trimmed = list(messages)

        while trimmed:
            used = sum(estimate_message_tokens(m.get("role", ""), m.get("content", "")) for m in trimmed)
            if used <= available_tokens:
                break
            trimmed.pop(0)
            was_truncated = True

        return trimmed, was_truncated

    def calculate_context_tokens(self, messages: list[dict], summary: str = "") -> int:
        """Return total tokens for all messages plus optional summary."""
        total = sum(estimate_message_tokens(m.get("role", ""), m.get("content", "")) for m in messages)
        if summary:
            total += estimate_tokens(summary)
        return total
