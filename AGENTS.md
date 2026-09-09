# Repository instructions

## Global implementation conventions

- When implementing or adapting a program from an existing template, preserve every relevant configuration section, file header, modification history, and business comment. Do not copy only the core execution logic.
- Keep configuration, parser fields, SQL bind variables, target-table DDL, routing, tests, and documentation aligned, with no missing output or gap.
- Remove temporary programs, scripts, directories, and intermediate files when each request is complete. Do not leave redundant or mixed-in work products.

## Project conventions

- Read the newest file in `docs/plans/` before changing architecture or scope.
- Use `uv` for all Python dependency, environment, lock, build, and command workflows.
- Keep the domain and cognition packages independent from FastAPI, SQLAlchemy, Dramatiq, and model-vendor SDKs.
- Add a configuration definition, validation, management API, and admin UI for new runtime settings. Environment variables are reserved for bootstrap settings and secrets required before the database can be reached.
- Never return stored secret plaintext through an API, log, trace, export, or configuration diff.
- Prefer a tested vertical slice over disconnected scaffolding. Keep the API, worker, and Web app runnable after each change.

