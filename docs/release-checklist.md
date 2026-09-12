# ShapeFinder release checklist

Last completed for the local 0.1.0 release candidate on 2026-09-12. Re-run deployment-specific items immediately before publication and deployment.

## Product and quality

- [x] Version is 0.1.0 in backend metadata, frontend package metadata, README, and health/readiness responses.
- [x] Desktop, laptop, tablet, and mobile widths were inspected with no horizontal overflow.
- [x] Reference, custom search, named-universe search, ranked results, preview, score details, comparison, and rebased overlay were smoke-tested with cached real-market data.
- [x] Empty, validation, provider-error, backend-unavailable, loading, and partial-coverage states were exercised.
- [x] Backend Ruff check and format check, strict mypy, and the full pytest coverage gate pass.
- [x] Frontend ESLint, Prettier, Vitest, TypeScript, and production Vite build pass after `npm ci`.
- [x] Browser console error/warning check passes during the release-candidate workflow.

## Dependencies and runtime

- [x] `npm audit` reports zero known vulnerabilities.
- [x] `pip-audit -r backend/requirements-dev.lock` reports zero known vulnerabilities.
- [x] Backend dependencies install from the lock file in a new virtual environment and the package imports as version 0.1.0.
- [x] SQLite migrations/readiness pass against the configured database; `/health` and `/readiness` return 200.
- [ ] Re-run a successful uncached live-provider smoke immediately before publication if quota is available. The Phase 12 run intentionally stopped after Twelve Data returned a safe rate-limit response; cached real-data workflows passed.
- [ ] Re-run repeated/concurrent stress sanity immediately before deployment on the selected production-sized runtime.

## Security and public repository

- [x] The actual local Twelve Data key has zero matches in tracked files, all Git revisions, and the production frontend build.
- [x] Generic provider-key, credential-bearing URL, and long secret-assignment patterns have zero Git-history matches.
- [x] Personal filesystem paths, temporary Codex paths, and usernames have zero tracked-file and Git-history matches.
- [x] `.env`, SQLite/WAL/shared-memory files, frontend build output, validation output, and temporary test artifacts are ignored and untracked.
- [x] `.env.example` contains only safe defaults/placeholders and documents every application variable.
- [x] README setup, methodology, universes, cache behavior, security, limitations, disclaimer, and deployment links are current.
- [x] The standard MIT License is present with the established public author name and no private email address.

## Deployment preparation

- [x] `docs/deployment-readiness.md` documents frontend/backend runtime, environment, topology, HTTPS, CORS, API URL, health/readiness, and remaining steps.
- [x] Persistent SQLite volume requirements and the effects of ephemeral storage are documented.
- [x] Twelve Data is documented as a backend-only deployment secret and excluded from all `VITE_*` values.
- [ ] Choose hosting providers, provision durable storage, configure production secrets/origins, and test backup/restore.
- [ ] Run the full checklist again against the final deployment configuration.

## Git publication

- [x] Phase 12 diff passes whitespace checks.
- [x] No runtime database, `.env`, build output, local validation report, or temporary file is staged.
- [ ] Create the public GitHub repository and configure its description/topics only when explicitly authorized.
- [ ] Push the intended branch only when explicitly authorized.
- [ ] Create the 0.1.0 pre-release tag/release only after the publication workflow is chosen.
- [ ] Confirm the publication commit exists and the working tree is clean immediately before pushing.
