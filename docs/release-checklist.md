# ShapeFinder release checklist

- [ ] Version is correct in backend metadata, frontend package metadata, README, and health/readiness responses.
- [ ] Backend: Ruff check and format check pass; strict mypy passes; full pytest coverage gate passes.
- [ ] Frontend: ESLint, Prettier, Vitest, TypeScript, and production Vite build pass with `npm ci` dependencies.
- [ ] Dependency audits are reviewed; applicable findings are fixed or documented.
- [ ] Repository and built frontend are scanned for `.env` content, provider-key patterns, credential-bearing URLs, and backend-only variable names.
- [ ] SQLite initializes from empty storage; migration versions are current; readiness returns 200.
- [ ] Health remains independent of provider availability; safe provider/error responses are smoke-tested.
- [ ] A quota-conscious live-provider smoke is run only when credentials and quota are available; no payload dump is retained.
- [ ] Repeated/concurrent cached reads and searches complete without corruption, locking failures, or obvious unbounded memory growth.
- [ ] README setup steps, environment variables, CORS, limits, cache coverage, and known limitations are accurate.
- [ ] Runtime databases, `.env`, frontend build output, validation JSON, and temporary stress artifacts are not tracked.
- [ ] Git diff passes whitespace checks, the release commit exists, and the working tree is clean.
