"""Decides how a seed is treated: as a document to be covered, or as a
topic that needs research. Pure functions, no I/O, so routing is easy to
test and the decision model only handles what is genuinely ambiguous."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

SeedMode = Literal["document", "topic"]

# A typed seed shorter than this, with no uploaded file, is a topic.
TOPIC_MODE_MAX_WORDS = int(os.environ.get("TOPIC_MODE_MAX_WORDS", "250"))


@dataclass(frozen=True)
class ResearchPlan:
    """True/False force a tool on/off; None leaves it to the decision model."""

    mode: SeedMode
    web_search: bool | None
    arxiv_search: bool | None
    reason: str


def classify_seed_mode(seed_text: str, *, has_files: bool, max_words: int = TOPIC_MODE_MAX_WORDS) -> SeedMode:
    if has_files:
        return "document"
    return "topic" if len(seed_text.split()) <= max_words else "document"


def plan_research(
    *,
    seed_text: str,
    has_files: bool,
    available_tools: set[str],
    purpose: str | None = None,
) -> ResearchPlan:
    mode = classify_seed_mode(seed_text, has_files=has_files)

    web: bool | None = None
    if mode == "topic" and "web_search" in available_tools:
        web = True

    arxiv: bool | None = None
    if "arxiv_search" in available_tools and purpose is not None:
        arxiv = purpose == "research"

    reason = f"mode={mode}, purpose={purpose or 'unset'}"
    return ResearchPlan(mode=mode, web_search=web, arxiv_search=arxiv, reason=reason)
