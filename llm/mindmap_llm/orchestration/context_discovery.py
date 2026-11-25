"""Dynamic Model Context Window Discovery and Introspection (AUD-09, S3-L7).

Discovers and verifies the active model's maximum context window size on its first
invocation, then caches the result in memory for zero-overhead subsequent requests.

Supports:
  1. Google Gemini API: queries client.models.get(model=...) for input_token_limit.
  2. OpenRouter API: queries model specifications when available.
  3. Curated fallback catalog: maps current/modern models (Gemini 3.8/3.6 Flash,
     Llama 3.3 70B, etc.) to verified token limits if offline or probe fails.
  4. Resilient error shielding: never raises or blocks turn execution.
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

# Process-wide memory cache: model_name -> context_window_tokens
_MODEL_CONTEXT_CACHE: dict[str, tuple[int, float]] = {}

# Catalog of known model context limits (fallback when dynamic probe is offline/unsupported)
_KNOWN_MODEL_LIMITS: dict[str, int] = {
    # Gemini models (1M - 2M tokens)
    "gemini-3.8-flash": 1_048_576,
    "gemini-3.6-flash": 1_048_576,
    "gemini-2.0-flash": 1_048_576,
    "gemini-1.5-flash": 1_048_576,
    "gemini-1.5-pro": 2_097_152,
    # Llama 3.3 / 3.1 models (128k tokens)
    "llama-3.3-70b": 131_072,
    "llama-3.1-70b": 131_072,
    "llama-3.1-8b": 131_072,
    # Groq / Llama 3 models (8k tokens)
    "llama-3-70b": 8_192,
    "llama-3-8b": 8_192,
    "llama3-70b": 8_192,
    "llama3-8b": 8_192,
    "qwen3": 131_072,
    "qwen": 8_192,
    "gpt-oss": 131_072,
    "mixtral": 32_768,
}

DEFAULT_FALLBACK_CONTEXT_WINDOW = 8_192


def _catalog_lookup(model: str) -> int:
    """Find the best match from the known model catalog using substring matching."""
    norm = model.lower().strip()
    for key, limit in _KNOWN_MODEL_LIMITS.items():
        if key in norm:
            return limit
    return DEFAULT_FALLBACK_CONTEXT_WINDOW


def probe_gemini_context_window(model: str, api_key: str | None = None) -> int | None:
    """Probe Google Gemini API for model input_token_limit metadata."""
    key = api_key or os.getenv("GEMINI_API_KEY")
    if not key:
        return None

    try:
        import sys

        genai = sys.modules.get("google.genai")
        if genai is None:
            from google import genai
        assert genai is not None

        client = genai.Client(api_key=key)
        # Clean model name if prefixed
        clean_model = model.replace("models/", "")
        m_info = client.models.get(model=clean_model)
        limit = getattr(m_info, "input_token_limit", None)
        if limit and isinstance(limit, int) and limit > 0:
            logger.info("Successfully discovered Gemini model %s context window: %d tokens", model, limit)
            return limit
    except Exception as exc:
        logger.debug("Dynamic Gemini context probe for %s failed (%s); using catalog fallback", model, exc)
    return None


def probe_openrouter_context_window(model: str, api_key: str | None = None) -> int | None:
    """Probe OpenRouter API for model context_length."""
    key = api_key or os.getenv("OPENROUTER_API_KEY")
    if not key:
        return None

    try:
        import httpx

        headers = {"Authorization": f"Bearer {key}"}
        with httpx.Client(timeout=5.0) as client:
            resp = client.get("https://openrouter.ai/api/v1/models", headers=headers)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                for item in data:
                    if item.get("id") == model:
                        ctx = item.get("context_length")
                        if ctx and isinstance(ctx, int) and ctx > 0:
                            logger.info("Discovered OpenRouter model %s context length: %d tokens", model, ctx)
                            return ctx
    except Exception as exc:
        logger.debug("OpenRouter context probe for %s failed (%s); using catalog fallback", model, exc)
    return None


def probe_groq_context_window(model: str, api_key: str | None = None) -> int | None:
    """Probe Groq API for model context_window."""
    key = api_key or os.getenv("LLM_API_KEY")
    if not key:
        return None
    try:
        import httpx

        headers = {"Authorization": f"Bearer {key}"}
        with httpx.Client(timeout=5.0) as client:
            resp = client.get("https://api.groq.com/openai/v1/models", headers=headers)
            if resp.status_code == 200:
                for item in resp.json().get("data", []):
                    if item.get("id") == model:
                        ctx = item.get("context_window")
                        if ctx and isinstance(ctx, int) and ctx > 0:
                            logger.info("Discovered Groq model %s context window: %d tokens", model, ctx)
                            return ctx
    except Exception as exc:
        logger.debug("Groq context probe for %s failed (%s); using catalog fallback", model, exc)
    return None


def get_model_context_window(
    model: str,
    provider: str = "",
    *,
    api_key: str | None = None,
    api_base: str | None = None,
) -> int:
    """Dynamically discover and cache the context window for the active model (AUD-09, S3-L7).

    Guarantees:
      - First call for a model probes provider metadata if available or matches catalog.
      - Cached immediately in memory: subsequent calls return in 0ms.
      - Never raises or blocks execution if external probe fails.
    """
    cache_key = f"{provider.lower().strip()}:{model.lower().strip()}" if provider else model.lower().strip()
    cache_key += f":{api_base or ''}"
    cached = _MODEL_CONTEXT_CACHE.get(cache_key)
    if cached and cached[1] > time.monotonic():
        return cached[0]

    prov_norm = provider.lower().strip()
    model_norm = model.lower().strip()
    discovered: int | None = None

    # 1. First-time dynamic probe
    if "gemini" in prov_norm or "gemini" in model_norm:
        discovered = probe_gemini_context_window(model, api_key)
    elif "openrouter" in prov_norm or (api_base and "openrouter" in api_base.lower()):
        discovered = probe_openrouter_context_window(model, api_key)
    elif "groq" in prov_norm:
        discovered = probe_groq_context_window(model, api_key)

    # 2. Resilient catalog fallback if probe returned None
    ttl = 3600 if discovered is not None else 60
    if discovered is None:
        discovered = _catalog_lookup(model_norm)

    _MODEL_CONTEXT_CACHE[cache_key] = (discovered, time.monotonic() + ttl)
    logger.info("Active context window for [%s] established as %d tokens", cache_key, discovered)
    return discovered


def reset_model_context_cache() -> None:
    """Clear memory cache (for test isolation)."""
    _MODEL_CONTEXT_CACHE.clear()
