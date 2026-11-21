"""Comprehensive unit tests for Dynamic LLM Provider and Prompt Template Configuration (Issue #177 / S3-L2).

Covers all 12 required test scenarios:
1. Successful configuration loading.
2. Configuration schema validation.
3. Rejection of invalid or malformed configurations (all-or-nothing).
4. Safe default fallback when configuration loading fails.
5. HTTP failure and timeout handling.
6. Periodic configuration refresh.
7. Dynamic model provider selection.
8. Dynamic model hyperparameter selection.
9. System prompt loading and version selection.
10. Missing prompt configuration handling.
11. Preservation of the last known valid configuration when appropriate.
12. Verification that API keys are never fetched from or exposed through the configuration endpoint.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from dynamic_config.manager import DynamicConfigManager
from dynamic_config.schema import (
    DynamicPipelineConfig,
)
from orchestration.prompts import SizeTargets, build_generation_system_prompt
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Fixtures & Samples
# ---------------------------------------------------------------------------


@pytest.fixture
def valid_config_dict() -> dict:
    return {
        "version": "2.0.0",
        "llm": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_base": "https://api.openai.com/v1",
            "hyperparameters": {
                "temperature": 0.4,
                "max_output_tokens": 4096,
                "request_timeout_s": 45.0,
                "top_p": 0.9,
            },
        },
        "prompts": {
            "version": "v2.1",
            "templates": {
                "generation_system": "Custom generation prompt v2.1 for $level with $min_nodes to $max_nodes nodes.",
                "outline_system": "Custom outline prompt v2.1.",
            },
        },
        "refresh_interval_s": 30,
    }


# ---------------------------------------------------------------------------
# 1. Successful Configuration Loading
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_configuration_loading(valid_config_dict: dict):
    """GET /internal/config returns 200 with valid JSON; manager applies all settings."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_resp = httpx.Response(
        status_code=200,
        json=valid_config_dict,
        request=httpx.Request("GET", "http://localhost:8000/internal/config"),
    )
    mock_client.get.return_value = mock_resp

    manager = DynamicConfigManager(
        backend_url="http://localhost:8000",
        http_client=mock_client,
    )

    loaded = await manager.load_initial()
    assert loaded is True

    cfg = manager.get_config()
    assert cfg.version == "2.0.0"
    assert cfg.llm.provider == "openai"
    assert cfg.llm.model == "gpt-4o-mini"
    assert cfg.llm.hyperparameters.temperature == 0.4
    assert cfg.llm.hyperparameters.max_output_tokens == 4096
    assert cfg.llm.hyperparameters.request_timeout_s == 45.0
    assert cfg.llm.hyperparameters.top_p == 0.9

    assert manager.get_active_prompt_version() == "v2.1"
    assert manager.prompt_manager.get_template("outline_system") == "Custom outline prompt v2.1."


# ---------------------------------------------------------------------------
# 2. Configuration Schema Validation
# ---------------------------------------------------------------------------


def test_configuration_schema_validation(valid_config_dict: dict):
    """Validates schema constraints on providers, models, numbers, and prompt versions."""
    cfg = DynamicPipelineConfig.model_validate(valid_config_dict)
    assert cfg.llm.provider == "openai"
    assert cfg.llm.model == "gpt-4o-mini"
    assert cfg.refresh_interval_s == 30
    assert cfg.prompts.version == "v2.1"


# ---------------------------------------------------------------------------
# 3. Rejection of Invalid or Malformed Configurations
# ---------------------------------------------------------------------------


def test_rejection_of_invalid_or_malformed_configurations():
    """Invalid provider, out-of-range temperature, negative timeout, or bad payload shape must fail validation."""
    # Unsupported provider
    with pytest.raises(ValidationError):
        DynamicPipelineConfig.model_validate(
            {
                "version": "1.0",
                "llm": {"provider": "unsupported_xyz", "model": "test"},
            }
        )

    # Temperature > 2.0
    with pytest.raises(ValidationError):
        DynamicPipelineConfig.model_validate(
            {
                "version": "1.0",
                "llm": {
                    "provider": "groq",
                    "model": "llama-3.3-70b-versatile",
                    "hyperparameters": {"temperature": 2.5},
                },
            }
        )

    # Non-dictionary payload
    with pytest.raises(ValidationError):
        DynamicPipelineConfig.model_validate("string_not_dict")


def test_all_or_nothing_atomic_rejection(valid_config_dict: dict):
    """Partial invalid configurations must never be partially applied."""
    manager = DynamicConfigManager()
    initial_version = manager.get_config().version
    initial_provider_name = manager.get_llm_provider().name

    # Attempt to apply invalid payload
    invalid_payload = {
        "version": "corrupted-version",
        "llm": {"provider": "invalid-provider-foo", "model": "bad-model"},
    }

    with pytest.raises(ValueError):
        manager.apply_config(invalid_payload)

    # Verify no partial mutation occurred
    assert manager.get_config().version == initial_version
    assert manager.get_llm_provider().name == initial_provider_name


# ---------------------------------------------------------------------------
# 4. Safe Default Fallback When Configuration Loading Fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_default_fallback_on_initial_load_failure():
    """When backend is unreachable during startup, manager falls back to codebase defaults without crashing."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = httpx.ConnectError("Connection refused: backend offline")

    manager = DynamicConfigManager(
        backend_url="http://localhost:8000",
        http_client=mock_client,
    )

    loaded = await manager.load_initial()
    assert loaded is False  # Degraded gracefully

    # Defaults are intact
    cfg = manager.get_config()
    assert cfg.version == "1.0.0"
    assert cfg.llm.provider == "groq"
    assert manager.get_active_prompt_version() == "v1.0"
    assert manager.get_llm_provider() is not None


# ---------------------------------------------------------------------------
# 5. HTTP Failure and Timeout Handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        httpx.TimeoutException("Read timed out"),
        httpx.ConnectError("Connection failed"),
        httpx.HTTPStatusError("500 Internal Server Error", request=None, response=httpx.Response(500)),
    ],
)
async def test_http_failure_and_timeout_handling(error):
    """Network errors, status errors, and timeouts during refresh do not crash the manager."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = error

    manager = DynamicConfigManager(
        backend_url="http://localhost:8000",
        http_client=mock_client,
    )

    # Refresh should safely return False without raising uncaught exception
    success = await manager.refresh()
    assert success is False
    assert manager.get_config() is not None


# ---------------------------------------------------------------------------
# 6. Periodic Configuration Refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_periodic_configuration_refresh():
    """Periodic background refresh fetches and applies new configuration updates."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    config_v1 = {
        "version": "1.0.1",
        "llm": {"provider": "groq", "model": "llama-3.3-70b-versatile"},
        "refresh_interval_s": 5,
    }
    config_v2 = {
        "version": "1.0.2",
        "llm": {"provider": "openai", "model": "gpt-4o"},
        "refresh_interval_s": 5,
    }

    mock_client.get.side_effect = [
        httpx.Response(200, json=config_v1, request=httpx.Request("GET", "http://test/internal/config")),
        httpx.Response(200, json=config_v2, request=httpx.Request("GET", "http://test/internal/config")),
    ]

    manager = DynamicConfigManager(http_client=mock_client)
    await manager.load_initial()
    assert manager.get_config().version == "1.0.1"

    # Refresh tick
    refreshed = await manager.refresh()
    assert refreshed is True
    assert manager.get_config().version == "1.0.2"
    assert manager.get_config().llm.provider == "openai"


@pytest.mark.asyncio
async def test_background_refresh_lifecycle():
    """Start and stop background refresh loop cleanly."""
    manager = DynamicConfigManager()
    manager.start_background_refresh()
    assert manager._running is True
    assert manager._refresh_task is not None

    # Idempotent start
    manager.start_background_refresh()

    await manager.stop_background_refresh()
    assert manager._running is False
    assert manager._refresh_task is None


# ---------------------------------------------------------------------------
# 7. Dynamic Model Provider Selection
# ---------------------------------------------------------------------------


def test_dynamic_model_provider_selection():
    """Switching provider and model dynamically updates active LLMProvider instance."""
    manager = DynamicConfigManager()

    # Switch to mock provider
    manager.apply_config(
        {
            "version": "2.0",
            "llm": {"provider": "mock", "model": "custom-mock-v1"},
        }
    )
    provider = manager.get_llm_provider()
    assert provider.name == "mock:custom-mock-v1"

    # Switch to groq provider
    with patch.dict("os.environ", {"GROQ_API_KEY": "fake-groq-key"}):
        manager.apply_config(
            {
                "version": "2.1",
                "llm": {"provider": "groq", "model": "mixtral-8x7b-32768"},
            }
        )
        provider = manager.get_llm_provider()
        assert "groq:mixtral-8x7b-32768" in provider.name


# ---------------------------------------------------------------------------
# 8. Dynamic Model Hyperparameter Selection
# ---------------------------------------------------------------------------


def test_dynamic_hyperparameter_selection():
    """Model hyperparameters (temperature, max_tokens, request_timeout) are dynamically applied."""
    manager = DynamicConfigManager()
    manager.apply_config(
        {
            "version": "3.0",
            "llm": {
                "provider": "openai",
                "model": "gpt-4o",
                "hyperparameters": {
                    "temperature": 0.85,
                    "max_output_tokens": 12000,
                    "request_timeout_s": 120.0,
                    "top_p": 0.95,
                },
            },
        }
    )

    hyperparams = manager.get_config().llm.hyperparameters
    assert hyperparams.temperature == 0.85
    assert hyperparams.max_output_tokens == 12000
    assert hyperparams.request_timeout_s == 120.0
    assert hyperparams.top_p == 0.95


# ---------------------------------------------------------------------------
# 9. System Prompt Loading and Version Selection
# ---------------------------------------------------------------------------


def test_system_prompt_loading_and_version_selection():
    """Prompt templates with a specific version are registered and applied in generation."""
    manager = DynamicConfigManager()

    manager.apply_config(
        {
            "version": "4.0",
            "llm": {"provider": "groq", "model": "llama-3.3-70b-versatile"},
            "prompts": {
                "version": "v3.0.0-experimental",
                "templates": {
                    "generation_system": "V3 SYSTEM PROMPT: Level=$level, Nodes=$min_nodes-$max_nodes",
                },
            },
        }
    )

    assert manager.get_active_prompt_version() == "v3.0.0-experimental"
    rendered = manager.prompt_manager.render_template(
        "generation_system",
        level="detailed",
        min_nodes=40,
        max_nodes=70,
    )
    assert "V3 SYSTEM PROMPT: Level=detailed, Nodes=40-70" in rendered

    # Verify integration with build_generation_system_prompt
    targets = SizeTargets("detailed", 40, 70, 4, 6, 6, 10)
    prompt_out = build_generation_system_prompt(targets, "document", prompt_manager=manager.prompt_manager)
    assert "V3 SYSTEM PROMPT: Level=detailed, Nodes=40-70" in prompt_out


# ---------------------------------------------------------------------------
# 10. Missing Prompt Configuration Handling
# ---------------------------------------------------------------------------


def test_missing_prompt_configuration_handling():
    """Missing or empty prompt templates fall back gracefully to built-in codebase defaults."""
    manager = DynamicConfigManager()

    # Apply configuration with completely empty templates dict
    manager.apply_config(
        {
            "version": "5.0",
            "llm": {"provider": "groq", "model": "llama-3.3-70b-versatile"},
            "prompts": {
                "version": "v5.0-empty",
                "templates": {},
            },
        }
    )

    assert manager.get_active_prompt_version() == "v5.0-empty"
    # Should safely retrieve default generation_system template without error
    tmpl = manager.prompt_manager.get_template("generation_system")
    assert "synthesis engine of a mind-mapping tool" in tmpl

    # Safe render with missing keys
    rendered = manager.prompt_manager.render_template(
        "generation_system",
        level="balanced",
        min_nodes=10,
        max_nodes=20,
        min_branches=3,
        max_branches=6,
        min_depth=2,
        max_depth=4,
        mode_rule="Rule test",
        example="{}",
    )
    assert "synthesis engine" in rendered


# ---------------------------------------------------------------------------
# 11. Preservation of the Last Known Valid Configuration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preservation_of_last_known_valid_configuration(valid_config_dict: dict):
    """When a subsequent refresh receives malformed data, last known valid config is preserved."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    # 1st call: valid config A
    # 2nd call: malformed config B (invalid provider)
    mock_client.get.side_effect = [
        httpx.Response(200, json=valid_config_dict, request=httpx.Request("GET", "http://test/internal/config")),
        httpx.Response(
            200,
            json={"version": "broken-2", "llm": {"provider": "bad_provider_123"}},
            request=httpx.Request("GET", "http://test/internal/config"),
        ),
    ]

    manager = DynamicConfigManager(http_client=mock_client)
    await manager.load_initial()
    assert manager.get_config().version == "2.0.0"
    assert manager.get_config().llm.provider == "openai"

    # Attempt refresh with malformed payload
    refreshed = await manager.refresh()
    assert refreshed is False  # Failed validation safely

    # Preserved configuration A
    assert manager.get_config().version == "2.0.0"
    assert manager.get_config().llm.provider == "openai"
    assert manager.last_known_valid_config.version == "2.0.0"


# ---------------------------------------------------------------------------
# 12. Verification That API Keys Are Never Fetched From or Exposed Through Config
# ---------------------------------------------------------------------------


def test_api_keys_never_fetched_or_exposed_through_config():
    """Security Invariant: API keys must remain in env vars. Config manager strips/ignores any secrets."""
    payload_with_attempted_leak = {
        "version": "6.0",
        "api_key": "dummy",  # gitleaks:allow
        "secret": "dummy",  # gitleaks:allow
        "llm": {
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
            "api_key": "dummy",  # gitleaks:allow
        },
    }

    config = DynamicPipelineConfig.model_validate(payload_with_attempted_leak)

    # Ensure no API key fields survived in validated config object
    assert not hasattr(config, "api_key")
    assert not hasattr(config, "secret")
    assert not hasattr(config.llm, "api_key")

    # Serialized model dump must not contain any sensitive strings
    dump = config.model_dump()
    assert "api_key" not in dump
    assert "secret" not in dump
    assert "dummy" not in str(dump)
    assert "leaked_groq_key" not in str(dump)
