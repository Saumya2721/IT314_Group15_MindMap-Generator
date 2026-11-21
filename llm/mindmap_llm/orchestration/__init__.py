"""Orchestration package for Mind Map Generation pipeline.

Exports the unified shared branch expansion engine:
  - expand_branch: Consolidated single function for Full Gen Pass 2,
                   Node Regeneration, and Chat Elaboration.
  - BranchExpansionResult: Standardized output structure.
  - check_branch_quality: Unified content quality evaluation.
  - BranchQualityReport: Quality check metrics and warnings.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orchestration.expansion import (
        BranchExpansionResult,
        BranchQualityReport,
        check_branch_quality,
        expand_branch,
    )

__all__ = [
    "BranchExpansionResult",
    "BranchQualityReport",
    "check_branch_quality",
    "expand_branch",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        import orchestration.expansion as exp

        return getattr(exp, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
