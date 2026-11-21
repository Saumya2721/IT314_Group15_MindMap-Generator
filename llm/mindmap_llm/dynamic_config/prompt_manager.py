"""Versioned prompt template manager (S3-L2)."""

from __future__ import annotations

import logging
from string import Template
from typing import Any

from dynamic_config.schema import PromptTemplatesConfig

logger = logging.getLogger(__name__)

# Base built-in default templates defined in the codebase
DEFAULT_GENERATION_SYSTEM_TEMPLATE = (
    "You are the synthesis engine of a mind-mapping tool. You turn raw user input "
    "(notes, transcripts, outlines, brain dumps) into structured mind maps as JSON.\n"
    "Size for this request ($level level): $min_nodes to $max_nodes nodes, "
    "$min_branches to $max_branches first-level branches, and $min_depth to $max_depth "
    "levels below the root.\n$mode_rule\nFORMAT EXAMPLE:\n$example"
)

DEFAULT_REGENERATE_NODE_SYSTEM_TEMPLATE = """\
You are the branch refinement engine of a mind-mapping tool. You regenerate ONLY the branch \
rooted at the selected node, following a user's instruction or comment, while strictly preserving \
the integrity of the overall map.
"""

DEFAULT_OUTLINE_SYSTEM_TEMPLATE = (
    "You are an expert curriculum designer and knowledge architect. Your task is to generate "
    "a high-level, macro-structural topic outline for a mind map based on the provided seed material."
)

DEFAULT_CHAT_SYSTEM_TEMPLATE = (
    "You are a helpful topological mind map assistant. You answer questions about the mind map "
    "and propose structural refinements."
)

BUILTIN_DEFAULT_TEMPLATES: dict[str, str] = {
    "generation_system": DEFAULT_GENERATION_SYSTEM_TEMPLATE,
    "regenerate_node_system": DEFAULT_REGENERATE_NODE_SYSTEM_TEMPLATE,
    "outline_system": DEFAULT_OUTLINE_SYSTEM_TEMPLATE,
    "chat_system": DEFAULT_CHAT_SYSTEM_TEMPLATE,
}


class PromptTemplateManager:
    """Manages versioned system prompt templates with safe default fallbacks."""

    def __init__(
        self,
        default_version: str = "v1.0",
        initial_templates: dict[str, str] | None = None,
    ):
        self._versions: dict[str, dict[str, str]] = {}
        # Base built-in templates
        self._defaults = dict(BUILTIN_DEFAULT_TEMPLATES)
        if initial_templates:
            self._defaults.update(initial_templates)

        self._versions["default"] = dict(self._defaults)
        self._versions[default_version] = dict(self._defaults)
        self._active_version = default_version

    @property
    def active_version(self) -> str:
        return self._active_version

    def get_registered_versions(self) -> list[str]:
        return sorted(self._versions.keys())

    def register_version(
        self,
        version: str,
        templates: dict[str, str],
        set_active: bool = False,
    ) -> None:
        clean_version = version.strip() or "v1.0"
        if clean_version not in self._versions:
            self._versions[clean_version] = dict(self._defaults)
        self._versions[clean_version].update(templates)

        if set_active:
            self._active_version = clean_version
            logger.info("Activated prompt template version '%s'", clean_version)

    def set_active_version(self, version: str) -> bool:
        clean_version = version.strip()
        if clean_version in self._versions:
            self._active_version = clean_version
            logger.info("Switched active prompt version to '%s'", clean_version)
            return True
        logger.warning(
            "Prompt version '%s' not registered. Available: %s. Preserving '%s'",
            version,
            sorted(self._versions.keys()),
            self._active_version,
        )
        return False

    def load_config(self, config: PromptTemplatesConfig) -> None:
        """Load templates from incoming dynamic configuration."""
        if not config or not config.version:
            logger.warning("Empty or invalid prompt config; keeping active version '%s'", self._active_version)
            return

        self.register_version(config.version, config.templates, set_active=True)

    def get_template(self, name: str, version: str | None = None) -> str:
        """Retrieve the template string for `name`.

        Falls back to default built-in template if missing from the active version.
        """
        target_version = version or self._active_version
        version_templates = self._versions.get(target_version)

        if version_templates and name in version_templates and version_templates[name].strip():
            return version_templates[name]

        # Fallback to default
        default_val = self._defaults.get(name) or BUILTIN_DEFAULT_TEMPLATES.get(name) or ""
        logger.debug(
            "Template '%s' not found in version '%s'; fell back to safe default.",
            name,
            target_version,
        )
        return default_val

    def render_template(
        self,
        name: str,
        version: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Render a versioned template using safe substitution.

        If rendering fails due to template syntax issues, falls back to the default template.
        """
        template_str = self.get_template(name, version=version)
        try:
            return Template(template_str).safe_substitute(**kwargs)
        except Exception as exc:
            logger.warning(
                "Failed to render template '%s' (version='%s'): %s. Using default fallback.",
                name,
                version or self._active_version,
                exc,
            )
            default_template = self._defaults.get(name, "")
            return Template(default_template).safe_substitute(**kwargs)
