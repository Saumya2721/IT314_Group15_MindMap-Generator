"""
Conversation summariser for the chat turn agent (S2-L9, US-15).

Provides rolling summarisation of older messages to preserve long-range
conversational context (user preferences, decisions, topics, unresolved tasks)
without blowing the model's token context window.
"""

from __future__ import annotations

import logging

from contracts.models import ChatMessage
from providers.base import LLMProvider, Message

logger = logging.getLogger(__name__)

_DEFAULT_SUMMARY_MAX_TOKENS = 512


def build_summarize_system_prompt() -> str:
    """Build the system prompt for the conversation summariser."""
    return (
        "You are an expert conversation summariser for an AI-powered mind map assistant.\n"
        "Your task is to produce a concise, high-value rolling summary of conversation history.\n\n"
        "Key requirements:\n"
        "- Extract and preserve explicitly stated user preferences (e.g. beginner/advanced, preferred topics).\n"
        "- Extract and preserve key user decisions and requested changes.\n"
        "- Note main topics discussed and key facts established.\n"
        "- Retain relevant mind-map context and any unresolved questions or ongoing tasks.\n"
        "- Do NOT simply concatenate or transcribe the raw messages.\n"
        "- Do NOT invent or assume any facts, preferences, or decisions that were not explicitly stated.\n"
        "- Keep the summary compact, organized, and focused on context needed for future turns.\n"
    )


def build_summarize_user_prompt(
    messages: list[ChatMessage],
    previous_summary: str | None = None,
) -> str:
    """Build the user prompt combining previous summary with newly selected older messages."""
    lines: list[str] = []

    if previous_summary and previous_summary.strip():
        lines.append(f"## Previous Rolling Summary\n{previous_summary.strip()}\n")

    lines.append("## Messages to Incorporate")
    for msg in messages:
        prefix = "User" if msg.role == "user" else "Assistant"
        lines.append(f"{prefix}: {msg.content}")

    if previous_summary and previous_summary.strip():
        lines.append(
            "\nInstructions:\n"
            "Update the previous rolling summary by integrating the key information from the new messages.\n"
            "Preserve established preferences, decisions, and context without repeating redundant information."
        )
    else:
        lines.append(
            "\nInstructions:\n"
            "Generate a concise rolling summary preserving key preferences, decisions, topics, and unresolved questions."
        )

    return "\n".join(lines)


async def summarize_conversation(
    llm: LLMProvider,
    messages: list[ChatMessage],
    previous_summary: str | None = None,
    *,
    max_tokens: int = _DEFAULT_SUMMARY_MAX_TOKENS,
) -> str:
    """Summarise a list of conversation messages, optionally updating a previous summary.

    Args:
        llm: The LLMProvider to perform the summarisation turn.
        messages: Older messages to be folded into the summary.
        previous_summary: Existing rolling summary to be incrementally updated.
        max_tokens: Maximum token budget for the summary output.

    Returns:
        The updated rolling summary as a concise string.
        Falls back to previous_summary or an empty string on error or empty input.
    """
    if not messages:
        return previous_summary or ""

    system_prompt = build_summarize_system_prompt()
    user_prompt = build_summarize_user_prompt(messages, previous_summary)

    try:
        response = await llm.generate(
            [Message(role="user", content=user_prompt)],
            system=system_prompt,
            max_tokens=max_tokens,
            temperature=0.1,
        )
        text = (response.text or "").strip()
        if not text:
            logger.warning("Summariser returned empty output; keeping previous summary")
            return previous_summary or ""
        return text
    except Exception:
        logger.exception("Summarisation call failed; falling back to previous summary")
        return previous_summary or ""
