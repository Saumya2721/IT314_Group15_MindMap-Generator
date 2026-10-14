"""
Interface for a "System One"-class decision model — a model that returns
typed, calibrated decisions instead of generating text (e.g. Jev from
TypeSafe AI). Deliberately narrow: only classify / score primitives,
because that's genuinely all this class of model does. There is no
`generate_text` method here on purpose — if a call site needs prose or
open-ended reasoning, it belongs on LLMProvider, not this interface.

Every call site that uses a DecisionModel MUST have a fallback path
(a cheap heuristic, or falling back to LLMProvider) because:
  - DECISION_MODEL_ENABLED can be False,
  - Jev is early-access as of this writing and may be unreachable,
  - the call can simply time out or error.
This keeps NFR-07 (graceful error handling) true even if the decision
model vendor changes its API or has an outage.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ClassifyResult:
    label: str
    confidence: float  # 0.0-1.0, calibrated probability of `label`
    label_probabilities: dict[str, float] = field(default_factory=dict)


@dataclass
class ScoreResult:
    """One score per input item, e.g. one per retrieved chunk."""
    scores: list[float]  # same length/order as the input list, 0.0-1.0


class DecisionModel(ABC):
    @abstractmethod
    async def classify(
        self,
        *,
        state: str,
        question: str,
        labels: list[str],
    ) -> ClassifyResult:
        """
        `state` is the block of context (e.g. seed text, a chat
        instruction). `question` is a natural-language description of
        what's being decided. `labels` is the closed label set — System
        One models require known-up-front answer options.
        """
        raise NotImplementedError

    @abstractmethod
    async def score(
        self,
        *,
        state: str,
        items: list[str],
        question: str,
    ) -> ScoreResult:
        """Score each of `items` (e.g. retrieved chunks) for relevance to
        `question` given `state` (e.g. the seed topic)."""
        raise NotImplementedError

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        raise NotImplementedError


class DecisionModelUnavailable(Exception):
    """Raised by adapters on timeout / error / disabled config so call
    sites can catch this one exception type and fall back, rather than
    guessing at vendor-specific exception classes."""
