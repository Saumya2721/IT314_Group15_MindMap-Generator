"""
Fallback path for decision-model calls.

Two layers:
  1. LLMBackedDecisionModel — asks the primary LLM to do the same
     classify/score job via a constrained prompt. Slower and pricier
     than Jev, but always available if an LLMProvider is configured.
  2. HeuristicDecisionModel — zero-dependency, deterministic heuristics.
     Used only if even the primary LLM call fails, so the pipeline
     degrades to "works, less precisely" rather than failing outright
     (NFR-07: graceful error handling; the job should still be able to
     complete rather than dying on an optional optimization).
"""

from __future__ import annotations

import json
import re

from providers.base import LLMProvider, Message
from providers.decision_model import ClassifyResult, DecisionModel, ScoreResult


class LLMBackedDecisionModel(DecisionModel):
    def __init__(self, llm: LLMProvider):
        self._llm = llm

    @property
    def name(self) -> str:
        return f"llm_fallback:{self._llm.name}"

    async def health_check(self) -> bool:
        return True  # if the LLM provider is wired up, assume reachable

    async def classify(
        self, *, state: str, question: str, labels: list[str]
    ) -> ClassifyResult:
        prompt = (
            f"Context:\n{state}\n\n"
            f"Question: {question}\n"
            f"Respond with ONLY one of these labels, nothing else: {', '.join(labels)}"
        )
        resp = await self._llm.generate(
            [Message(role="user", content=prompt)], max_tokens=20, temperature=0.0
        )
        raw = (resp.text or "").strip().lower()
        chosen = next((l for l in labels if l.lower() in raw), labels[0])
        return ClassifyResult(
            label=chosen,
            confidence=0.6,  # uncalibrated — flagged lower than a real System One output
            label_probabilities={l: (1.0 if l == chosen else 0.0) for l in labels},
        )

    async def score(
        self, *, state: str, items: list[str], question: str
    ) -> ScoreResult:
        numbered = "\n".join(f"[{i}] {item[:500]}" for i, item in enumerate(items))
        prompt = (
            f"Context:\n{state}\n\nQuestion: {question}\n\nItems:\n{numbered}\n\n"
            'Respond with ONLY a JSON array of {"index": int, "score": float 0-1}, one per item.'
        )
        resp = await self._llm.generate(
            [Message(role="user", content=prompt)], max_tokens=1000, temperature=0.0
        )
        scores = [0.5] * len(items)
        try:
            match = re.search(r"\[.*\]", resp.text or "", re.DOTALL)
            parsed = json.loads(match.group(0)) if match else []
            for entry in parsed:
                idx = int(entry["index"])
                if 0 <= idx < len(scores):
                    scores[idx] = float(entry["score"])
        except (ValueError, KeyError, TypeError, AttributeError):
            pass  # keep the neutral 0.5 defaults rather than raising
        return ScoreResult(scores=scores)


class HeuristicDecisionModel(DecisionModel):
    """Last-resort fallback with no model calls at all."""

    @property
    def name(self) -> str:
        return "heuristic"

    async def health_check(self) -> bool:
        return True

    async def classify(
        self, *, state: str, question: str, labels: list[str]
    ) -> ClassifyResult:
        # Deterministic, order-preserving default: first label wins.
        return ClassifyResult(
            label=labels[0],
            confidence=1.0 / len(labels),
            label_probabilities={l: 1.0 / len(labels) for l in labels},
        )

    async def score(
        self, *, state: str, items: list[str], question: str
    ) -> ScoreResult:
        # Simple lexical overlap between `question` and each item as a stand-in for relevance.
        q_terms = set(re.findall(r"\w+", question.lower()))

        def overlap(item: str) -> float:
            terms = set(re.findall(r"\w+", item.lower()))
            if not q_terms or not terms:
                return 0.5
            return len(q_terms & terms) / len(q_terms | terms)

        return ScoreResult(scores=[overlap(item) for item in items])
