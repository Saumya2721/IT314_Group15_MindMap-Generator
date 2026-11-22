"""Schema validation for dynamic pipeline configuration (S3-L2)."""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool, field_validator, model_validator

logger = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = {"groq", "openai", "mock", "custom"}


class ModelHyperparameters(BaseModel):
    """Configurable model hyperparameters."""

    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=8000, gt=0)
    request_timeout_s: float = Field(default=90.0, gt=0.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)


class LLMProviderConfig(BaseModel):
    """Dynamic LLM provider selection and model configuration."""

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_base: str | None = None
    hyperparameters: ModelHyperparameters = Field(default_factory=ModelHyperparameters)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        clean = v.strip().lower()
        if clean not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported model provider '{v}'. Must be one of {sorted(SUPPORTED_PROVIDERS)}")
        return clean

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("Model identifier must not be empty.")
        return clean


class PromptTemplatesConfig(BaseModel):
    """Versioned system prompt templates."""

    version: str = Field(default="v1.0", min_length=1)
    templates: dict[str, str] = Field(default_factory=dict)


class PipelineFlags(BaseModel):
    """Runtime overrides supplied by the backend."""

    enable_two_pass: StrictBool
    research_engine: Literal["subgraph", "inline"]
    image_extractor: Literal["gemini", "openrouter", "tesseract"]


class DynamicPipelineConfig(BaseModel):
    """Full dynamic pipeline configuration payload received from backend GET /internal/config."""

    version: str = Field(default="1.0.0", min_length=1)
    llm: LLMProviderConfig
    prompts: PromptTemplatesConfig = Field(default_factory=PromptTemplatesConfig)
    refresh_interval_s: int = Field(default=60, gt=0)
    flags: PipelineFlags | None = None

    @model_validator(mode="before")
    @classmethod
    def preprocess_and_sanitize(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ValueError("Dynamic configuration payload must be a JSON object dictionary.")

        sanitized = dict(data)

        # Security Invariant: API keys and secrets MUST remain in environment variables.
        # Reject/strip any API keys, tokens, or passwords accidentally delivered in payload.
        forbidden_keys = {
            "api_key",
            "secret",
            "token",
            "password",
            "llm_api_key",
            "openai_api_key",
            "groq_api_key",
        }
        for k in list(sanitized.keys()):
            if k.lower() in forbidden_keys:
                logger.warning(
                    "Security Notice: Dynamic config payload contained credential key '%s'. "
                    "Stripping to enforce environment-variable-only secrets.",
                    k,
                )
                del sanitized[k]

        # Handle flat payload shapes if llm dictionary is not nested
        if "llm" not in sanitized and "provider" in sanitized:
            llm_dict: dict[str, Any] = {
                "provider": sanitized.get("provider"),
                "model": sanitized.get("model"),
                "api_base": sanitized.get("api_base"),
                "hyperparameters": {
                    "temperature": sanitized.get("temperature", 0.2),
                    "max_output_tokens": sanitized.get("max_output_tokens", 8000),
                    "request_timeout_s": sanitized.get("request_timeout_s", 90.0),
                },
            }
            sanitized["llm"] = llm_dict

        return sanitized
