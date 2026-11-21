"""
Provider construction, in one place. Unchanged from the Postgres
version in how it builds providers — this module never touched the DB.
CHANGED: added a broad `except Exception` around the primary
decision-model call (the K17 fix) so a malformed 200 response (not just
a network error) also degrades to the fallback chain instead of
propagating.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from config import Settings
from providers.base import LLMProvider
from providers.decision_fallback import HeuristicDecisionModel, LLMBackedDecisionModel
from providers.decision_model import (
    ClassifyResult,
    DecisionModel,
    DecisionModelUnavailable,
    ScoreResult,
)

logger = logging.getLogger(__name__)


def get_llm_provider(
    settings: Settings,
    provider: str | None = None,
    model: str | None = None,
    hyperparameters: dict[str, Any] | None = None,
    api_base: str | None = None,
) -> LLMProvider:
    """Instantiate the active LLMProvider.

    Can be invoked with default static Settings or dynamically overridden
    by the dynamic configuration layer (S3-L2).
    API keys are strictly read from environment variables or settings, never from external payloads.
    """
    prov = (provider or settings.llm.provider).strip().lower()
    mod = model or settings.llm.model
    base_url = api_base or settings.llm.api_base
    timeout_s = settings.llm.request_timeout_s

    params = hyperparameters or {}
    if "request_timeout_s" in params and params["request_timeout_s"]:
        timeout_s = float(params["request_timeout_s"])

    if prov == "groq":
        from providers.groq_provider import GroqProvider

        api_key = os.environ.get("GROQ_API_KEY") or settings.llm.api_key
        return GroqProvider(
            api_key=api_key,
            model=mod,
            timeout_s=timeout_s,
            api_base=base_url,
        )

    if prov == "openai":
        from providers.openai_provider import OpenAIProvider

        api_key = os.environ.get("OPENAI_API_KEY") or settings.llm.api_key
        return OpenAIProvider(
            api_key=api_key,
            model=mod,
            timeout_s=timeout_s,
            api_base=base_url,
            default_temperature=float(params.get("temperature", 0.2)),
            default_max_tokens=int(params.get("max_output_tokens", settings.llm.max_output_tokens)),
        )

    if prov == "mock":
        from providers.mock_provider import MockProvider

        return MockProvider(model=mod)

    raise ValueError(
        f"Unknown LLM_PROVIDER={prov!r}. Add an adapter in providers/ "
        f"and register it in providers.registry.get_llm_provider()."
    )


class SafeDecisionModel(DecisionModel):
    """Jev -> LLM-backed classify/score -> zero-dependency heuristic."""

    def __init__(self, primary: DecisionModel | None, llm_fallback: LLMProvider):
        self._primary = primary
        self._llm_backed = LLMBackedDecisionModel(llm_fallback)
        self._heuristic = HeuristicDecisionModel()

    @property
    def name(self) -> str:
        return f"safe({self._primary.name if self._primary else 'none'})"

    async def health_check(self) -> bool:
        return True

    async def classify(self, *, state: str, question: str, labels: list[str]) -> ClassifyResult:
        if self._primary is not None:
            try:
                return await self._primary.classify(state=state, question=question, labels=labels)
            except DecisionModelUnavailable as exc:
                logger.warning("Decision model unavailable, falling back to LLM: %s", exc)
            except Exception as exc:  # noqa: BLE001 - a malformed response must also degrade, not raise
                logger.warning(
                    "Decision model call failed unexpectedly, falling back to LLM: %s",
                    exc,
                )
        try:
            return await self._llm_backed.classify(state=state, question=question, labels=labels)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM-backed classify fallback failed, using heuristic: %s", exc)
            return await self._heuristic.classify(state=state, question=question, labels=labels)

    async def score(self, *, state: str, items: list[str], question: str) -> ScoreResult:
        if self._primary is not None:
            try:
                return await self._primary.score(state=state, items=items, question=question)
            except DecisionModelUnavailable as exc:
                logger.warning("Decision model unavailable, falling back to LLM: %s", exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Decision model call failed unexpectedly, falling back to LLM: %s",
                    exc,
                )
        try:
            return await self._llm_backed.score(state=state, items=items, question=question)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM-backed score fallback failed, using heuristic: %s", exc)
            return await self._heuristic.score(state=state, items=items, question=question)


def get_decision_model(settings: Settings, llm_fallback: LLMProvider) -> SafeDecisionModel:
    primary: DecisionModel | None = None
    if settings.decision_model.enabled:
        provider = settings.decision_model.provider.lower()
        try:
            if provider == "jev":
                from providers.jev_provider import JevDecisionModel

                primary = JevDecisionModel(
                    api_key=settings.decision_model.api_key,
                    api_base=settings.decision_model.api_base,
                    timeout_s=settings.decision_model.request_timeout_s,
                )
            else:
                logger.warning("Unknown DECISION_MODEL_PROVIDER=%r, skipping", provider)
        except DecisionModelUnavailable as exc:
            logger.warning("Could not initialize decision model, will use fallback only: %s", exc)
    return SafeDecisionModel(primary=primary, llm_fallback=llm_fallback)
