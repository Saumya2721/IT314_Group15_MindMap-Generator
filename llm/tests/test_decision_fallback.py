from __future__ import annotations

import pytest
from providers.decision_fallback import HeuristicDecisionModel, LLMBackedDecisionModel
from providers.decision_model import DecisionModelUnavailable
from providers.registry import SafeDecisionModel

from tests.conftest import FakeLLMProvider


@pytest.mark.asyncio
async def test_llm_backed_classify_picks_a_valid_label():
    llm = FakeLLMProvider(responses=["balanced"])
    model = LLMBackedDecisionModel(llm)
    result = await model.classify(
        state="some seed text", question="detail level?", labels=["overview", "balanced", "detailed"]
    )
    assert result.label == "balanced"


@pytest.mark.asyncio
async def test_llm_backed_classify_falls_back_to_first_label_on_junk_output():
    llm = FakeLLMProvider(responses=["I have no idea what you mean"])
    model = LLMBackedDecisionModel(llm)
    result = await model.classify(state="x", question="detail level?", labels=["overview", "balanced", "detailed"])
    assert result.label == "overview"  # first label is the safe default


@pytest.mark.asyncio
async def test_heuristic_score_prefers_lexical_overlap():
    model = HeuristicDecisionModel()
    result = await model.score(
        state="x",
        items=["photosynthesis converts light to energy", "unrelated passage about cars"],
        question="photosynthesis energy",
    )
    assert result.scores[0] > result.scores[1]


class AlwaysFailingDecisionModel:
    name = "always-fails"

    async def classify(self, **kwargs):
        raise DecisionModelUnavailable("simulated outage")

    async def score(self, **kwargs):
        raise DecisionModelUnavailable("simulated outage")

    async def health_check(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_safe_decision_model_falls_back_when_primary_unavailable():
    llm = FakeLLMProvider(responses=["detailed"])
    safe = SafeDecisionModel(primary=AlwaysFailingDecisionModel(), llm_fallback=llm)
    result = await safe.classify(state="x", question="detail level?", labels=["overview", "balanced", "detailed"])
    assert result.label == "detailed"
    assert len(llm.calls) == 1  # confirms the LLM fallback path was actually used


@pytest.mark.asyncio
async def test_safe_decision_model_works_with_no_primary_configured():
    llm = FakeLLMProvider(responses=["overview"])
    safe = SafeDecisionModel(primary=None, llm_fallback=llm)
    result = await safe.classify(state="x", question="detail level?", labels=["overview", "balanced", "detailed"])
    assert result.label == "overview"


def test_jev_provider_host_validation_and_endpoint_resolution():
    from providers.jev_provider import JevDecisionModel

    jev_official = JevDecisionModel(api_key="test_key", api_base="https://www.jevai.org")
    assert jev_official._is_jevai_host() is True
    assert jev_official._resolve_endpoint() == "/api/v1/decisions"

    jev_api_v1 = JevDecisionModel(api_key="test_key", api_base="https://jevai.org/api/v1")
    assert jev_api_v1._is_jevai_host() is True
    assert jev_api_v1._resolve_endpoint() == "/decisions"

    # Incomplete URL substring sanitization bypass prevention:
    # An attacker host having jevai.org as subdomain suffix of attacker or in path must NOT match
    attacker_sub = JevDecisionModel(api_key="test_key", api_base="https://attacker-jevai.org")
    assert attacker_sub._is_jevai_host() is False
    assert attacker_sub._resolve_endpoint() == "/v1/systemone"

    attacker_path = JevDecisionModel(api_key="test_key", api_base="https://attacker.com/jevai.org")
    assert attacker_path._is_jevai_host() is False
    assert attacker_path._resolve_endpoint() == "/v1/systemone"
