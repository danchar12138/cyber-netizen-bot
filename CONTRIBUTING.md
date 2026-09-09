# Contributing

## Development workflow

1. Read `AGENTS.md` and the newest plan in `docs/plans/`.
2. Create a short-lived branch from `main`.
3. Keep API schemas, database migrations, generated client types, tests, and documentation aligned.
4. Run both backend and Web checks before opening a pull request.
5. Remove temporary files and describe migrations or operational impact in the pull request.

## Commits

Use Conventional Commits, for example `feat(config): add effective value resolver`.

Do not commit secrets, local databases, uploads, generated logs, build output, or editor state.

