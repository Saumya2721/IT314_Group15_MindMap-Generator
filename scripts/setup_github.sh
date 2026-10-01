#!/usr/bin/env bash
# Applies the repository settings and the branch ruleset for `main`.
#
# Usage:   bash scripts/setup_github.sh OWNER/REPO
# Needs:   the GitHub CLI (https://cli.github.com) logged in as the repo owner
#          with `gh auth login`. Safe to run more than once.
#
# Run it AFTER the CI workflow has run once on main, so the "ci-success"
# check already exists when the ruleset starts requiring it.

set -euo pipefail

REPO="${1:-}"
if [[ -z "$REPO" || "$REPO" != */* ]]; then
    echo "Usage: bash scripts/setup_github.sh OWNER/REPO" >&2
    exit 1
fi

gh auth status >/dev/null

echo "==> Repository settings (squash/rebase only, delete merged branches)"
gh api -X PATCH "repos/$REPO" \
    -F allow_squash_merge=true \
    -F allow_rebase_merge=true \
    -F allow_merge_commit=false \
    -F delete_branch_on_merge=true >/dev/null

echo "==> Secret scanning and push protection"
gh api -X PATCH "repos/$REPO" --input - >/dev/null <<'JSON'
{
  "security_and_analysis": {
    "secret_scanning": { "status": "enabled" },
    "secret_scanning_push_protection": { "status": "enabled" }
  }
}
JSON

echo "==> Dependabot alerts and security updates"
gh api -X PUT "repos/$REPO/vulnerability-alerts" >/dev/null
gh api -X PUT "repos/$REPO/automated-security-fixes" >/dev/null

echo "==> Workflows get read-only tokens by default"
gh api -X PUT "repos/$REPO/actions/permissions/workflow" \
    -f default_workflow_permissions=read \
    -F can_approve_pull_request_reviews=false >/dev/null

echo "==> CodeQL default setup"
if ! gh api -X PATCH "repos/$REPO/code-scanning/default-setup" --input - >/dev/null 2>&1 <<'JSON'
{
  "state": "configured",
  "languages": ["python", "javascript-typescript"],
  "query_suite": "default"
}
JSON
then
    echo "    Could not enable CodeQL (it needs Python or JavaScript code on main)."
    echo "    Re-run this script after the backend and frontend are merged."
fi

echo "==> Ruleset: Protect main"
RULESET_JSON=$(cat <<'JSON'
{
  "name": "Protect main",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "bypass_actors": [],
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "required_linear_history" },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 1,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": false,
        "require_last_push_approval": true,
        "required_review_thread_resolution": true,
        "allowed_merge_methods": ["squash", "rebase"]
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [{ "context": "ci-success" }]
      }
    }
  ]
}
JSON
)

EXISTING_ID=$(gh api "repos/$REPO/rulesets" --jq '.[] | select(.name == "Protect main") | .id')
if [[ -n "$EXISTING_ID" ]]; then
    echo "    Updating existing ruleset $EXISTING_ID"
    echo "$RULESET_JSON" | gh api -X PUT "repos/$REPO/rulesets/$EXISTING_ID" --input - >/dev/null
else
    echo "$RULESET_JSON" | gh api -X POST "repos/$REPO/rulesets" --input - >/dev/null
fi

echo "Done. main now requires a pull request, 1 approval from someone other than"
echo "the last pusher, and a passing ci-success check."
echo "Once CODEOWNERS lists everyone, set require_code_owner_review to true above and re-run."
