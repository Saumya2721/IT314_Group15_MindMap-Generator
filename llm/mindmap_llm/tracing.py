"""
LangSmith tracing bootstrap and span instrumentation.

Call configure_langsmith(settings.langsmith) ONCE at worker startup (in worker/settings.py::on_startup).
When LANGSMITH_TRACING is false or unset, all spans are no-ops — zero network calls,
zero overhead.

Design guarantees:
- Swallows all setup exceptions so a tracing outage or misconfiguration NEVER crashes tasks.
- trace_span() gracefully falls back to a no-op identity decorator if langsmith is missing.
- Sets os.environ variables before any internal LangChain/LangGraph calls so auto-instrumentation
  activates seamlessly.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from config import LangSmithConfig

logger = logging.getLogger(__name__)

_configured = False

RunType = Literal["tool", "chain", "llm", "retriever", "embedding", "prompt", "parser"]


def configure_langsmith(cfg: LangSmithConfig) -> None:
    """
    Configure LangSmith environment variables and verify configuration.
    Idempotent: safe to call multiple times.
    """
    global _configured
    if _configured:
        return

    if not cfg.tracing_enabled:
        logger.debug("LangSmith tracing is disabled (LANGSMITH_TRACING unset or false).")
        return

    if not cfg.api_key:
        logger.warning("LANGSMITH_TRACING=true but LANGSMITH_API_KEY is empty. Tracing will remain disabled.")
        return

    try:
        # Populate standard and legacy environment variables for LangSmith & LangGraph SDKs
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = cfg.api_key
        os.environ["LANGSMITH_ENDPOINT"] = cfg.endpoint
        os.environ["LANGSMITH_PROJECT"] = cfg.project
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = cfg.api_key
        os.environ["LANGCHAIN_PROJECT"] = cfg.project
        os.environ["LANGCHAIN_ENDPOINT"] = cfg.endpoint

        # Tag environment metadata
        if cfg.environment:
            os.environ["LANGSMITH_ENVIRONMENT"] = cfg.environment

        logger.info(
            "LangSmith tracing configured: project=%s endpoint=%s environment=%s",
            cfg.project,
            cfg.endpoint,
            cfg.environment,
        )
        _configured = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to configure LangSmith tracing: %s", exc)


def trace_span(
    name: str | None = None,
    run_type: RunType = "chain",
    **traceable_kwargs: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator for instrumenting functions as LangSmith spans.
    Falls back to a transparent no-op decorator if tracing is disabled or langsmith is not installed.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        # Fast path: when tracing is disabled (default in dev & test), act as zero-overhead identity
        tracing_enabled = os.environ.get("LANGSMITH_TRACING", "").lower() in ("true", "1") or os.environ.get(
            "LANGCHAIN_TRACING_V2", ""
        ).lower() in ("true", "1")
        if not tracing_enabled:
            return fn

        try:
            from langsmith import traceable

            wrapped = traceable(run_type=run_type, name=name, **traceable_kwargs)(fn)
            return cast(Callable[..., Any], wrapped)
        except Exception:  # noqa: BLE001
            return fn

    return decorator


def annotate_current_span(
    *,
    name: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Dynamically update the active LangSmith span's name, tags, or metadata.
    Safe no-op if tracing is disabled or no active run exists.
    """
    try:
        from langsmith.run_helpers import get_current_run_tree

        run = get_current_run_tree()
        if not run:
            return
        if name:
            run.name = name
        if tags:
            if run.tags is None:
                run.tags = []
            run.tags.extend(tags)
        if metadata and hasattr(run, "extra") and isinstance(run.extra, dict):
            run_metadata = run.extra.setdefault("metadata", {})
            run_metadata.update(metadata)
    except Exception:  # noqa: BLE001
        pass
