"""
Token budgeting and estimation utilities for the chat turn agent (S2-L9, US-15).

Since the project does not vendor an external tokenizer (e.g. tiktoken),
this module provides a clearly documented estimation fallback based on the
standard ~4 characters per token heuristic plus message framing overhead.
"""

from __future__ import annotations

from dataclasses import dataclass

from contracts.models import ChatMessage

# Standard heuristic: 1 token ≈ 4 characters in English prose and code.
CHARS_PER_TOKEN = 4

# Overhead tokens per ChatMessage (role prefix, formatting tokens, newlines).
MESSAGE_FRAMING_OVERHEAD_TOKENS = 4


def estimate_tokens(text: str | None) -> int:
    """Estimate token count for a text string.

    Uses the standard 4-characters-per-token heuristic with a minimum of 1
    token for non-empty text and 0 for empty/None text.
    """
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def estimate_message_tokens(message: ChatMessage) -> int:
    """Estimate tokens for a ChatMessage including framing overhead."""
    return estimate_tokens(message.content) + MESSAGE_FRAMING_OVERHEAD_TOKENS


@dataclass(frozen=True)
class TokenBudget:
    """Configurable token budget for chat context assembly.

    Attributes:
        max_context_tokens: Total token window allocated for model context (input + output).
        reserved_response_tokens: Tokens reserved strictly for the model's response.
        summary_target_tokens: Target token ceiling for generated rolling summaries.
    """

    max_context_tokens: int = 4096
    reserved_response_tokens: int = 1024
    summary_target_tokens: int = 256

    @property
    def available_input_tokens(self) -> int:
        """Maximum tokens available for the input prompt (context window minus response reservation)."""
        return max(0, self.max_context_tokens - self.reserved_response_tokens)


def trim_history_to_budget(
    history: list[ChatMessage],
    *,
    budget: TokenBudget,
    current_message: str,
    summary: str | None = None,
    system_prompt: str = "",
    overhead_tokens: int = 0,
) -> tuple[list[ChatMessage], str | None]:
    """Trim conversation history and summary to fit within the configured token budget.

    Prioritises:
      1. Mandatory baseline: system prompt, current user message, overhead (e.g. map / chunks).
      2. Rolling summary (if provided and space allows).
      3. Recent messages from newest to oldest, preserving chronological order in the result.

    Returns:
        (kept_messages, effective_summary)
    """
    # 1. Calculate baseline mandatory consumption
    system_tokens = estimate_tokens(system_prompt)
    curr_tokens = estimate_tokens(current_message) + MESSAGE_FRAMING_OVERHEAD_TOKENS
    baseline_tokens = system_tokens + curr_tokens + overhead_tokens

    available_for_context = max(0, budget.available_input_tokens - baseline_tokens)

    # 2. Check if rolling summary can be included
    effective_summary: str | None = None
    summary_cost = 0

    if summary and summary.strip():
        # Include framing overhead for summary header
        cost = estimate_tokens(summary) + 8
        if cost <= available_for_context:
            effective_summary = summary
            summary_cost = cost
            available_for_context -= summary_cost

    # 3. Fit recent messages from newest to oldest
    kept: list[ChatMessage] = []
    for msg in reversed(history):
        msg_cost = estimate_message_tokens(msg)
        if msg_cost <= available_for_context:
            kept.append(msg)
            available_for_context -= msg_cost
        else:
            break

    # Restore chronological order
    kept.reverse()
    return kept, effective_summary
