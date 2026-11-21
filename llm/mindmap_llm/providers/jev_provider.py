"""
Adapter for Jev, TypeSafe AI's "System One" decision model.

Jev is early-access as of this writing (launched Sept 2026, waitlist-gated
API). Its publicly described interface: you send a block of state plus
typed questions built from primitives (choice / score / null) and get
back typed answers with calibrated probabilities in ~70-500ms, no text
generation, no tool calls, ~32K token context. It is explicitly NOT
meant for chat, code, prose, or open-ended reasoning — see
providers/decision_model.py for why this adapter only exposes
classify()/score().

IMPORTANT: verify the exact request/response JSON shape against
TypeSafe's current API docs before pointing this at production traffic —
early-access APIs change. The shape below (state + typed "questions"
list) matches TypeSafe's own public description of the interface; adjust
field names here if their finalized schema differs. Every call site in
this codebase treats DecisionModelUnavailable as the only contract it
relies on, so tightening this adapter to the real schema is an isolated,
low-risk change.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
from providers.decision_model import (
    ClassifyResult,
    DecisionModel,
    DecisionModelUnavailable,
    ScoreResult,
)


class JevDecisionModel(DecisionModel):
    def __init__(self, api_key: str, api_base: str, timeout_s: float = 3.0):
        if not api_key:
            raise DecisionModelUnavailable("TYPESAFE_API_KEY is not set")
        self._api_base = api_base.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._api_base,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout_s,
        )

    @property
    def name(self) -> str:
        return "jev"

    def _is_jevai_host(self) -> bool:
        host = (urlparse(self._api_base).hostname or "").lower()
        return host == "jevai.org" or host.endswith(".jevai.org")

    def _resolve_endpoint(self) -> str:
        base = self._api_base.rstrip("/")
        if self._is_jevai_host():
            if base.endswith("/api/v1"):
                return "/decisions"
            return "/api/v1/decisions"
        if base.endswith("/v1"):
            return "/systemone"
        return "/v1/systemone"

    def _extract_answers(self, data: dict) -> dict:
        if isinstance(data, dict):
            if data.get("code") is not None and data.get("code") != 0:
                raise DecisionModelUnavailable(f"Jev returned error code {data.get('code')}: {data.get('message')}")
            if "data" in data and isinstance(data["data"], dict):
                inner = data["data"]
                return inner.get("answers") or inner
            if "answers" in data and isinstance(data["answers"], dict):
                return data["answers"]
        return data if isinstance(data, dict) else {}

    async def health_check(self) -> bool:
        try:
            if self._is_jevai_host():
                resp = await self._client.get("/api/v1/decisions")
                return resp.status_code in (200, 405)
            endpoint = "/models" if self._api_base.endswith("/v1") else "/v1/models"
            resp = await self._client.get(endpoint)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def classify(self, *, state: str, question: str, labels: list[str]) -> ClassifyResult:
        endpoint = self._resolve_endpoint()
        payload = {
            "state": state,
            "questions": {
                "q1": {
                    "type": "choice",
                    "instructions": question,
                    "criteria": {lbl: lbl for lbl in labels},
                }
            },
        }
        try:
            resp = await self._client.post(endpoint, json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise DecisionModelUnavailable(f"Jev classify call failed: {exc}") from exc

        data = resp.json()
        answers = self._extract_answers(data)
        answer = answers.get("q1") or answers
        probs: dict[str, float] = answer.get("probabilities", {}) if isinstance(answer, dict) else {}
        label = str((answer.get("choice") or answer.get("decision")) if isinstance(answer, dict) else "")
        if not label:
            label = max(probs, key=lambda k: probs[k]) if probs else labels[0]
        confidence = probs.get(label, answer.get("confidence", 1.0) if isinstance(answer, dict) else 1.0)
        return ClassifyResult(
            label=label,
            confidence=confidence,
            label_probabilities=probs,
        )

    async def score(self, *, state: str, items: list[str], question: str) -> ScoreResult:
        if not items:
            return ScoreResult(scores=[])
        endpoint = self._resolve_endpoint()
        questions = {
            f"item_{i}": {
                "type": "score",
                "instructions": f"{question}\n\nItem to score: {item}",
                "criteria": ["irrelevant", "somewhat relevant", "highly relevant"],
            }
            for i, item in enumerate(items)
        }
        payload = {
            "state": state,
            "questions": questions,
        }
        try:
            resp = await self._client.post(endpoint, json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise DecisionModelUnavailable(f"Jev score call failed: {exc}") from exc

        data = resp.json()
        answers = self._extract_answers(data)
        scores: list[float] = []
        for i in range(len(items)):
            item_ans = answers.get(f"item_{i}", {})
            if isinstance(item_ans, dict):
                score_val = item_ans.get("score")
                if score_val is not None:
                    try:
                        scores.append(float(score_val))
                        continue
                    except (ValueError, TypeError):
                        pass
            scores.append(0.5)
        return ScoreResult(scores=scores)

    async def aclose(self) -> None:
        await self._client.aclose()
