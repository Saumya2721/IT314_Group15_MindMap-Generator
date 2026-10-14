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
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=timeout_s,
        )

    @property
    def name(self) -> str:
        return "jev"

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get("/health")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def classify(self, *, state: str, question: str, labels: list[str]) -> ClassifyResult:
        payload = {
            "state": state,
            "questions": [
                {"id": "q1", "type": "choice", "prompt": question, "choices": labels}
            ],
        }
        try:
            resp = await self._client.post("/decide", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise DecisionModelUnavailable(f"Jev classify call failed: {exc}") from exc

        data = resp.json()
        answer = data["answers"]["q1"]
        probs: dict[str, float] = answer.get("probabilities", {})
        label = answer.get("choice") or max(probs, key=probs.get)
        return ClassifyResult(
            label=label,
            confidence=probs.get(label, answer.get("confidence", 1.0)),
            label_probabilities=probs,
        )

    async def score(self, *, state: str, items: list[str], question: str) -> ScoreResult:
        questions = [
            {"id": f"item_{i}", "type": "score", "prompt": question, "context": item}
            for i, item in enumerate(items)
        ]
        payload = {"state": state, "questions": questions}
        try:
            resp = await self._client.post("/decide", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise DecisionModelUnavailable(f"Jev score call failed: {exc}") from exc

        data = resp.json()
        answers = data["answers"]
        scores = [float(answers[f"item_{i}"]["score"]) for i in range(len(items))]
        return ScoreResult(scores=scores)

    async def aclose(self) -> None:
        await self._client.aclose()
