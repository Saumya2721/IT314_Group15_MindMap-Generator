## Summary

Describe the change in simple terms.

## Requirement traceability

List the requirement and story IDs this covers, for example `FR-07`, `NFR-06`, or `US-03`.

## Validation

- [ ] Tests added or updated
- [ ] Format, lint, type checks, and build pass
- [ ] A user can only reach their own maps (when the change touches data access)
- [ ] External services (LLM, search, export) are mocked in tests
- [ ] Documentation, `.env.example`, or migrations updated when needed
- [ ] No secrets or unrelated changes

## Definition of Done

The following quality gates must be green before merge (see `AGENT.md`):

- [ ] Lint passes with 0 errors (`ruff check` backend, `eslint src` frontend)
- [ ] Formatting passes with 0 errors (`ruff format --check` backend, `prettier --check` frontend)
- [ ] Type checks pass with 0 errors (`mypy` backend, frontend typecheck)
- [ ] Tests pass with 100% passing (`pytest tests -q` backend, `vitest run` frontend, `supabase test db` pgTAP)
- [ ] Sprint 1 regression tests cover map generation, auth, polling, and canvas load (US-01 through US-04)
- [ ] Migration CI verified: Supabase migrations reset + pgTAP tests pass on PRs touching `supabase/`
- [ ] Database migrations applied cleanly (`supabase db reset`) and backend contract aligned

## Notes

Anything reviewers should know: migrations, rollout, follow-up work.
