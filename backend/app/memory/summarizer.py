from __future__ import annotations

import logging
from datetime import datetime

from memory.schemas import RollingSummary

logger = logging.getLogger(__name__)


class RollingSummarizer:
    """Generates rolling summaries of chat history to compress context."""

    def __init__(
        self,
        max_messages_before_summary: int = 10,
        summary_max_tokens: int = 512,
    ) -> None:
        self.max_messages_before_summary = max_messages_before_summary
        self.summary_max_tokens = summary_max_tokens

    def needs_summary(self, messages: list[dict]) -> bool:
        """Return True if the message list is long enough to warrant summarization."""
        return len(messages) >= self.max_messages_before_summary

    def build_summary_prompt(self, messages: list[dict], existing_summary: str = "") -> str:
        """Build a prompt string for LLM-based summarization."""
        lines: list[str] = [
            "Summarize the following conversation history concisely (max 3-4 sentences). "
            "Focus on the mind map topics discussed, user instructions, and what was generated.",
            "",
        ]

        if existing_summary:
            lines.append("[PREVIOUS SUMMARY]")
            lines.append(f"Previous summary: {existing_summary}")
            lines.append("")

        lines.append("[CONVERSATION]")
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            lines.append(f"{role.capitalize()}: {content}")

        lines.append("")
        lines.append("Provide only the summary, no preamble.")
        return "\n".join(lines)

    def _fallback_summary(self, messages: list[dict]) -> str:
        """Simple extractive fallback when no LLM provider is available."""
        user_contents = [m.get("content", "") for m in messages if m.get("role", "").lower() == "user"]
        last_three = user_contents[-3:]
        if not last_three:
            return "Topics discussed: (no user messages found)"
        return "Topics discussed: " + "; ".join(last_three)

    async def generate_summary(
        self,
        messages: list[dict],
        existing_summary: str = "",
        llm_provider=None,
    ) -> str:
        """Generate a summary string, using LLM if available, else fallback."""
        if llm_provider is None:
            return self._fallback_summary(messages)

        prompt = self.build_summary_prompt(messages, existing_summary)
        try:
            # Attempt to use the LLM provider
            try:
                from mindmap_llm.models import Message  # type: ignore
            except ImportError:
                # Lightweight stand-in when the llm package is not present
                class Message:  # type: ignore[no-redef]
                    def __init__(self, role: str, content: str) -> None:
                        self.role = role
                        self.content = content

            response = await llm_provider.generate(
                [Message(role="user", content=prompt)],
                system="You are a concise summarizer.",
                max_tokens=200,
                temperature=0.3,
            )
            return response.text
        except Exception as exc:
            logger.warning("LLM summarization failed, using fallback: %s", exc)
            return self._fallback_summary(messages)

    async def create_rolling_summary(
        self,
        map_id: str,
        messages: list[dict],
        existing_summary: str = "",
        llm_provider=None,
    ) -> RollingSummary:
        """Create and return a RollingSummary for the given messages."""
        summary = await self.generate_summary(messages, existing_summary, llm_provider)
        return RollingSummary(
            map_id=map_id,
            summary_text=summary,
            message_count=len(messages),
            last_updated=datetime.utcnow(),
        )
