"""Dynamic configuration manager for LLM worker (S3-L2).

Fetches active pipeline configuration from backend GET /internal/config,
validates schema, dynamically configures model providers and hyperparameters,
and synchronizes versioned system prompt templates with fallback protection.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx
from config import Settings, get_settings
from dynamic_config.prompt_manager import PromptTemplateManager
from dynamic_config.schema import (
    DynamicPipelineConfig,
    LLMProviderConfig,
    ModelHyperparameters,
    PromptTemplatesConfig,
)
from providers.base import LLMProvider
from providers.registry import get_llm_provider

logger = logging.getLogger(__name__)


def build_default_pipeline_config(settings: Settings) -> DynamicPipelineConfig:
    """Construct safe codebase default configuration from static settings and env."""
    return DynamicPipelineConfig(
        version="1.0.0",
        llm=LLMProviderConfig(
            provider=settings.llm.provider,
            model=settings.llm.model,
            api_base=settings.llm.api_base,
            hyperparameters=ModelHyperparameters(
                temperature=0.2,
                max_output_tokens=settings.llm.max_output_tokens,
                request_timeout_s=settings.llm.request_timeout_s,
            ),
        ),
        prompts=PromptTemplatesConfig(
            version="v1.0",
            templates={},
        ),
        refresh_interval_s=60,
    )


class DynamicConfigManager:
    """Thread/asyncio-safe manager for dynamic LLM pipeline configuration."""

    def __init__(
        self,
        backend_url: str | None = None,
        settings: Settings | None = None,
        http_client: httpx.AsyncClient | None = None,
        initial_config: DynamicPipelineConfig | None = None,
    ):
        self._settings = settings or get_settings()
        self._backend_url = (
            backend_url
            or os.environ.get("BACKEND_INTERNAL_URL")
            or os.environ.get("BACKEND_URL")
            or "http://localhost:8000"
        ).rstrip("/")
        self._custom_client = http_client

        # Initialize fallback configuration
        defaults = initial_config or build_default_pipeline_config(self._settings)
        self._last_known_valid_config: DynamicPipelineConfig = defaults
        self._current_config: DynamicPipelineConfig = defaults

        # Prompt manager
        self._prompt_manager = PromptTemplateManager(
            default_version=defaults.prompts.version,
            initial_templates=defaults.prompts.templates,
        )

        # Active provider
        self._current_provider: LLMProvider | None = None
        self._rebuild_provider(defaults.llm)

        # Background refresh task control
        self._refresh_task: asyncio.Task[None] | None = None
        self._running = False
        self._lock = asyncio.Lock()

    @property
    def current_config(self) -> DynamicPipelineConfig:
        return self._current_config

    @property
    def last_known_valid_config(self) -> DynamicPipelineConfig:
        return self._last_known_valid_config

    @property
    def prompt_manager(self) -> PromptTemplateManager:
        return self._prompt_manager

    def get_config(self) -> DynamicPipelineConfig:
        return self._current_config

    def get_llm_provider(self) -> LLMProvider:
        if self._current_provider is None:
            self._rebuild_provider(self._current_config.llm)
        assert self._current_provider is not None
        return self._current_provider

    def get_active_prompt_version(self) -> str:
        return self._prompt_manager.active_version

    def _rebuild_provider(self, llm_cfg: LLMProviderConfig) -> None:
        """Instantiate new provider from validated config using env API keys."""
        try:
            new_provider = get_llm_provider(
                settings=self._settings,
                provider=llm_cfg.provider,
                model=llm_cfg.model,
                hyperparameters=llm_cfg.hyperparameters.model_dump(),
                api_base=llm_cfg.api_base,
            )
            self._current_provider = new_provider
            logger.info("Configured active LLM provider: %s", new_provider.name)
        except Exception as exc:
            logger.warning(
                "Failed to instantiate LLM provider for %s:%s: %s. Preserving current provider.",
                llm_cfg.provider,
                llm_cfg.model,
                exc,
            )
            if self._current_provider is None:
                # Fallback to standard provider
                self._current_provider = get_llm_provider(self._settings)

    async def fetch_remote_config(self) -> dict[str, Any]:
        """Fetch raw JSON configuration from GET /internal/config."""
        endpoint = f"{self._backend_url}/internal/config"
        timeout = float(os.environ.get("CONFIG_FETCH_TIMEOUT_S", "10.0"))

        if self._custom_client:
            resp = await self._custom_client.get(endpoint, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise ValueError("Expected JSON object from configuration endpoint.")
            return data

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(endpoint)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise ValueError("Expected JSON object from configuration endpoint.")
            return data

    def apply_config(self, raw_data: dict[str, Any]) -> bool:
        """Validate and apply a configuration dictionary.

        Enforces all-or-nothing schema validation. If validation fails,
        the invalid configuration is rejected completely, preserving the last
        known valid configuration.
        """
        try:
            validated = DynamicPipelineConfig.model_validate(raw_data)
        except Exception as exc:
            logger.warning(
                "Invalid configuration payload rejected: %s. Preserving last known valid configuration (version=%s)",
                exc,
                self._last_known_valid_config.version,
            )
            raise ValueError(f"Configuration validation failed: {exc}") from exc

        # Update valid configuration state atomically
        self._current_config = validated
        self._last_known_valid_config = validated

        # Update versioned prompt templates
        self._prompt_manager.load_config(validated.prompts)

        # Update dynamic LLM provider and hyperparameters
        self._rebuild_provider(validated.llm)

        logger.info(
            "Successfully applied dynamic configuration: version=%s, provider=%s, model=%s, prompt_version=%s",
            validated.version,
            validated.llm.provider,
            validated.llm.model,
            validated.prompts.version,
        )
        return True

    async def load_initial(self) -> bool:
        """Fetch and apply active pipeline configuration at worker startup.

        Gracefully degrades to codebase defaults if backend is unavailable.
        """
        logger.info("Fetching active pipeline configuration from %s/internal/config", self._backend_url)
        try:
            raw_data = await self.fetch_remote_config()
            self.apply_config(raw_data)
            return True
        except Exception as exc:
            logger.warning(
                "Initial backend configuration fetch failed (%s). Falling back to safe codebase defaults.",
                exc,
            )
            # Safe default fallback already loaded in __init__
            return False

    async def refresh(self) -> bool:
        """Perform a single non-blocking configuration refresh.

        Returns True on success, False on failure. Never raises or interrupts workers.
        """
        async with self._lock:
            try:
                raw_data = await self.fetch_remote_config()
                self.apply_config(raw_data)
                return True
            except Exception as exc:
                logger.warning(
                    "Dynamic configuration refresh failed: %s. Preserving active configuration.",
                    exc,
                )
                return False

    async def _refresh_loop(self) -> None:
        """Background periodic refresh task."""
        try:
            while self._running:
                interval = max(5, self._current_config.refresh_interval_s)
                await asyncio.sleep(interval)
                await self.refresh()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("Unexpected error in dynamic config refresh loop: %s", exc)

    def start_background_refresh(self) -> None:
        """Start the periodic background refresh task."""
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        self._running = True
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        logger.info(
            "Started dynamic config background refresh (interval=%ds)",
            self._current_config.refresh_interval_s,
        )

    async def stop_background_refresh(self) -> None:
        """Stop the periodic background refresh task cleanly."""
        self._running = False
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None
            logger.info("Stopped dynamic config background refresh.")
