# Working Guide

## Before work

Read this file and `docs/engineering/CODE_RULES.md`. Read `docs/engineering/TESTING.md` before writing tests, `docs/engineering/CONFIG.md` before adding an environment variable, and the matching files in `docs/requirements/` for the story you are working on. Check the current branch and `git status`. Understand existing code before changing it.

## While working

Stay inside the requested scope and leave other people's unrelated work alone. Write the smallest correct solution, keep public interfaces typed, and validate external input. Never hardcode secrets. Store timestamps in UTC. Add comments only where they explain a business rule, a security decision, or a non-obvious edge case. Remove dead code instead of commenting it out. Do not add a dependency without a clear reason.

LLM output is untrusted. It must pass the schema checks in `llm/mindmap_llm/schemas.py` before it reaches the database or the canvas.

## Tests

Every feature or fix needs tests, and every bug fix needs a regression test. Mock the LLM, search, arXiv and export services: CI must never call a real external API. Test the success path, the important failures, and the authorization boundary (a user must only reach their own maps).

## Git

Never commit directly to `main`, force-push, bypass branch protection, or merge your own pull request. Work on a feature branch, keep commits small, and describe them clearly.

## Before finishing

Run format, lint, type checks, tests, and the affected build. Update documentation when behavior, schema, configuration, or a technical decision changes. Check `git diff` for unintended changes and secrets.

A task is not done while any required check is failing.
