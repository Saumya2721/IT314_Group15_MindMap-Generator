"""
Prompt assembly and memory context management for the chat turn agent (S2-L9, US-15).

Combines:
  1. Rolling conversation summary (historical context).
  2. Window of recent messages (immediate context, roles preserved).
  3. Current user message (never duplicated).
  4. Token budget enforcement.
"""

from __future__ import annotations

from dataclasses import dataclass

from chat.token_budget import (
    MESSAGE_FRAMING_OVERHEAD_TOKENS,
    TokenBudget,
    estimate_message_tokens,
    estimate_tokens,
    trim_history_to_budget,
)
from contracts.models import ChatMessage


@dataclass
class AssembledMemoryContext:
    """The assembled conversational memory ready to be merged into prompts."""

    summary: str | None
    recent_messages: list[ChatMessage]
    current_message: str
    estimated_tokens: int


def assemble_chat_context(
    *,
    current_message: str,
    history: list[ChatMessage],
    summary: str | None = None,
    budget: TokenBudget | None = None,
    system_prompt: str = "",
    overhead_tokens: int = 0,
) -> AssembledMemoryContext:
    """Assemble the conversation summary and recent message window within budget.

    Guarantees:
      - Total tokens respect the configured budget.
      - Recent messages preserve chronological order and roles.
      - Current message is tracked separately to prevent duplicate inclusion.
      - Summary is treated as historical context.
    """
    active_budget = budget or TokenBudget()

    kept_messages, effective_summary = trim_history_to_budget(
        history,
        budget=active_budget,
        current_message=current_message,
        summary=summary,
        system_prompt=system_prompt,
        overhead_tokens=overhead_tokens,
    )

    total_tokens = (
        estimate_tokens(system_prompt)
        + overhead_tokens
        + estimate_tokens(current_message)
        + MESSAGE_FRAMING_OVERHEAD_TOKENS
        + (estimate_tokens(effective_summary) + 8 if effective_summary else 0)
        + sum(estimate_message_tokens(m) for m in kept_messages)
    )

    return AssembledMemoryContext(
        summary=effective_summary,
        recent_messages=kept_messages,
        current_message=current_message,
        estimated_tokens=total_tokens,
    )


def format_memory_sections(context: AssembledMemoryContext) -> list[str]:
    """Format the summary and recent messages into prompt text sections.

    Distinguishes historical summary context from immediate conversation turns.
    Does not output the current user message (that belongs in the User Question block).
    """
    sections: list[str] = []

    if context.summary and context.summary.strip():
        sections.append(f"## Historical Conversation Summary (Background Context Only)\n{context.summary.strip()}")

    if context.recent_messages:
        lines = []
        for msg in context.recent_messages:
            prefix = "User" if msg.role == "user" else "Assistant"
            lines.append(f"{prefix}: {msg.content}")
        sections.append("## Recent Conversation\n" + "\n".join(lines))

    return sections
