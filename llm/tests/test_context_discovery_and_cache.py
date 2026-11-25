"""Unit tests for Dynamic Context Discovery and Research Cache Scoping.

Covers:
  - AUD-09 & S3-L7: Dynamic model context window verification and caching.
  - NEW-09 & AUD-08: Research cache scoping and user isolation for private tools.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from orchestration.context_discovery import (
    DEFAULT_FALLBACK_CONTEXT_WINDOW,
    get_model_context_window,
    reset_model_context_cache,
)
from research.cache import (
    PUBLIC_CACHEABLE_TOOLS,
    ResearchCache,
    make_cache_key,
)
from research.models import ResearchSource

# ---------------------------------------------------------------------------
# 1. Dynamic Context Window Discovery Tests (AUD-09, S3-L7)
# ---------------------------------------------------------------------------


def test_catalog_fallback_for_modern_models():
    """Verify modern Gemini 3.8/3.6 and Llama 3.3 limits from catalog when probe is unavailable."""
    reset_model_context_cache()

    # Gemini 3.8 Flash (1M tokens)
    g38 = get_model_context_window("gemini-3.8-flash", "gemini")
    assert g38 == 1_048_576

    # Gemini 3.6 Flash (1M tokens)
    g36 = get_model_context_window("gemini-3.6-flash", "gemini")
    assert g36 == 1_048_576

    # Llama 3.3 70B (128k tokens)
    llama33 = get_model_context_window("llama-3.3-70b-versatile", "groq")
    assert llama33 == 131_072

    # Unknown model -> safe default fallback
    unknown = get_model_context_window("custom-finetune-v1", "unknown_prov")
    assert unknown == DEFAULT_FALLBACK_CONTEXT_WINDOW


def test_context_window_cached_after_first_call():
    """Verify context window lookup is cached in memory for zero-overhead repeat calls."""
    reset_model_context_cache()

    # First call
    w1 = get_model_context_window("gemini-3.8-flash", "gemini")
    assert w1 == 1_048_576

    # Modify catalog to prove cache is returning stored value without re-query
    with patch("orchestration.context_discovery._catalog_lookup", return_value=99999):
        w2 = get_model_context_window("gemini-3.8-flash", "gemini")
        assert w2 == 1_048_576  # must hit cache!


def test_dynamic_gemini_probe_success():
    """Verify dynamic probe queries Gemini client.models.get when SDK is available."""
    reset_model_context_cache()

    mock_genai = MagicMock()
    mock_client = MagicMock()
    mock_model_info = MagicMock()
    mock_model_info.input_token_limit = 2_097_152
    mock_client.models.get.return_value = mock_model_info
    mock_genai.Client.return_value = mock_client

    with patch.dict("sys.modules", {"google.genai": mock_genai}):
        limit = get_model_context_window("gemini-test-model", "gemini", api_key="dummy-gemini-key")
        assert limit == 2_097_152


# ---------------------------------------------------------------------------
# 2. Research Cache Scoping Tests (NEW-09, AUD-08)
# ---------------------------------------------------------------------------


def test_public_tools_share_global_cache_key():
    """Assert public tools (wikipedia, scholar, arxiv, web_search) share global cache keys."""
    for tool in PUBLIC_CACHEABLE_TOOLS:
        key_user1 = make_cache_key(tool, "quantum computing", user_id="user-111")
        key_user2 = make_cache_key(tool, "quantum computing", user_id="user-222")
        key_anon = make_cache_key(tool, "quantum computing", user_id=None)

        # Must all be identical and not contain user ID
        assert key_user1 == key_user2 == key_anon
        assert "user:user-111" not in key_user1
        assert key_user1.startswith(f"research_cache:{tool}:")


def test_private_tools_scope_cache_key_per_user():
    """Assert non-whitelisted tools (e.g. web_page_reader) isolate cache keys per user."""
    private_tool = "web_page_reader"
    query = "https://private-docs.internal.company.com/report.pdf"

    key_user1 = make_cache_key(private_tool, query, user_id="user-111")
    key_user2 = make_cache_key(private_tool, query, user_id="user-222")
    key_anon = make_cache_key(private_tool, query, user_id=None)

    # Must be completely separate keys
    assert key_user1 != key_user2
    assert key_user1 != key_anon
    assert "user:user-111" in key_user1
    assert "user:user-222" in key_user2
    assert key_anon is None


@pytest.mark.asyncio
async def test_research_cache_user_isolation_get_and_set():
    """Assert one user's private tool cached results are never accessible to another user."""
    cache = ResearchCache(redis_client=None)  # in-memory mode

    private_tool = "web_page_reader"
    query = "https://example.com/confidential"
    user1_sources = [
        ResearchSource(
            content="Confidential Q3 data for User 1",
            source_title="Q3 Report",
            source_url=query,
            tool=private_tool,
        )
    ]

    # Store for user 1
    stored = await cache.set(private_tool, query, user1_sources, user_id="user-111")
    assert stored is True

    # User 1 should get a hit
    hit1 = await cache.get(private_tool, query, user_id="user-111")
    assert hit1 is not None
    assert len(hit1) == 1
    assert hit1[0].content == "Confidential Q3 data for User 1"

    # User 2 MUST get a miss (cross-user data isolation)
    miss2 = await cache.get(private_tool, query, user_id="user-222")
    assert miss2 is None

    # Anonymous user MUST get a miss
    miss_anon = await cache.get(private_tool, query, user_id=None)
    assert miss_anon is None
