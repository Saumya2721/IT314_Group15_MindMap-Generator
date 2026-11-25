"""Validated pipeline settings and their persistent admin overrides."""

from __future__ import annotations

import os
from typing import Annotated, Any, Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

ResearchEngine = Literal["subgraph", "inline"]
ImageExtractor = Literal["gemini", "openrouter", "tesseract"]
PromptVersion = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")]


class PipelineConfig(BaseModel):
    enable_two_pass: StrictBool
    research_engine: ResearchEngine
    image_extractor: ImageExtractor
    prompt_template_version: PromptVersion
    llm_provider: str = Field(default="groq")
    llm_model: str = Field(default="openai/gpt-oss-20b")
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=8000, gt=0)
    langsmith_tracing: StrictBool = Field(default=False)


class PipelineConfigPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enable_two_pass: StrictBool | None = None
    research_engine: ResearchEngine | None = None
    image_extractor: ImageExtractor | None = None
    prompt_template_version: PromptVersion | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    langsmith_tracing: StrictBool | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_case(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {str(k).lower(): v for k, v in data.items()}
        return data

    @model_validator(mode="after")
    def validate_patch(self) -> PipelineConfigPatch:
        values = self.model_dump(exclude_unset=True)
        if not values or any(value is None for value in values.values()):
            raise ValueError("Provide at least one setting, with no null values.")
        return self


def pipeline_pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(503, "Database pool not initialized")
    return pool


def effective_config(overrides: dict[str, object]) -> PipelineConfig:
    defaults = {
        "enable_two_pass": os.getenv("ENABLE_TWO_PASS", "false").strip().lower() in {"true", "1", "yes", "on"},
        "research_engine": os.getenv("RESEARCH_ENGINE", "subgraph").strip().lower(),
        "image_extractor": os.getenv("IMAGE_EXTRACTOR", "gemini").strip().lower(),
        "prompt_template_version": "v1.0",
        "llm_provider": os.getenv("LLM_PROVIDER", "groq"),
        "llm_model": os.getenv("LLM_MODEL", "openai/gpt-oss-20b"),
        "temperature": float(os.getenv("LLM_TEMPERATURE", "0.2")),
        "max_output_tokens": int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "8000")),
        "langsmith_tracing": os.getenv("LANGSMITH_TRACING", "false").strip().lower() in {"true", "1", "yes", "on"},
    }
    return PipelineConfig.model_validate(defaults | overrides)


async def read_config_overrides(pool) -> dict[str, object]:
    overrides = await pool.fetchval("SELECT settings FROM public.pipeline_config WHERE singleton")
    if overrides is None:
        raise HTTPException(503, "Pipeline configuration is not initialized")
    return overrides
