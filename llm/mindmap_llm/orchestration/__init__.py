"""Orchestration package for Mind Map Generation pipeline.

Exports the unified shared branch expansion engine:
  - expand_branch: Consolidated single function for Full Gen Pass 2,
                   Node Regeneration, and Chat Elaboration.
  - BranchExpansionResult: Standardized output structure.
  - check_branch_quality: Unified content quality evaluation.
  - BranchQualityReport: Quality check metrics and warnings.
"""

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
