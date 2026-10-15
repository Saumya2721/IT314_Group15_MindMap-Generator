from __future__ import annotations

import pytest
from providers.decision_fallback import HeuristicDecisionModel, LLMBackedDecisionModel
from providers.decision_model import (
    ClassifyResult,
    DecisionModel,
    DecisionModelUnavailable,
    ScoreResult,
)
from providers.registry import SafeDecisionModel
from tests.conftest import FakeLLMProvider


@pytest.mark.asyncio
async def test_llm_backed_classify_picks_a_valid_label() -> None:
    llm = FakeLLMProvider(responses=["balanced"])
    model = LLMBackedDecisionModel(llm)
    result = await model.classify(
        state="some seed text",
        question="detail level?",
        labels=["overview", "balanced", "detailed"],
    )
    assert result.label == "balanced"


@pytest.mark.asyncio
async def test_llm_backed_classify_falls_back_to_first_label_on_junk_output() -> None:
    llm = FakeLLMProvider(responses=["I have no idea what you mean"])
    model = LLMBackedDecisionModel(llm)
    result = await model.classify(
        state="x", question="detail level?", labels=["overview", "balanced", "detailed"]
    )
    assert result.label == "overview"  # first label is the safe default


@pytest.mark.asyncio
async def test_heuristic_score_prefers_lexical_overlap() -> None:
    model = HeuristicDecisionModel()
    result = await model.score(
        state="x",
        items=["photosynthesis converts light to energy", "unrelated passage about cars"],
        question="photosynthesis energy",
    )
    assert result.scores[0] > result.scores[1]


class AlwaysFailingDecisionModel(DecisionModel):
    name = "always-fails"

    async def classify(self, *, state: str, question: str, labels: list[str]) -> ClassifyResult:
        raise DecisionModelUnavailable("simulated outage")

    async def score(self, *, state: str, items: list[str], question: str) -> ScoreResult:
        raise DecisionModelUnavailable("simulated outage")

    async def health_check(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_safe_decision_model_falls_back_when_primary_unavailable() -> None:
    llm = FakeLLMProvider(responses=["detailed"])
    safe = SafeDecisionModel(primary=AlwaysFailingDecisionModel(), llm_fallback=llm)
    result = await safe.classify(
        state="x", question="detail level?", labels=["overview", "balanced", "detailed"]
    )
    assert result.label == "detailed"
    assert len(llm.calls) == 1  # confirms the LLM fallback path was actually used


@pytest.mark.asyncio
async def test_safe_decision_model_works_with_no_primary_configured() -> None:
    llm = FakeLLMProvider(responses=["overview"])
    safe = SafeDecisionModel(primary=None, llm_fallback=llm)
    result = await safe.classify(
        state="x", question="detail level?", labels=["overview", "balanced", "detailed"]
    )
    assert result.label == "overview"
