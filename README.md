# ShapeFinder

ShapeFinder is the foundation for a stock-chart similarity application. The future product will compare normalized chart behaviour across securities and historical periods; it is not a forecasting or trading-recommendation tool.

## Phase 1 scope

This phase establishes a typed React/Vite client, a FastAPI service, configuration, tests, and architectural boundaries. It includes a live health check between the client and API. It does **not** fetch market data, scan history, calculate similarity, show invented match scores, or require credentials.

## Architecture

```text
frontend (React UI)
        │ HTTP
backend API routes
        │
application services
        ├── market-data provider protocol
        ├── similarity engine protocol
        └── repository protocol
```

The core layer contains provider-neutral domain types. Infrastructure implementations will be injected behind protocols in later phases, so Twelve Data, SQLite, and future alternatives do not leak into the API or UI. Database-specific code will remain behind repository interfaces, allowing a later move from SQLite to PostgreSQL.

## Project layout

- `frontend/` — React, TypeScript, Vite, Vitest, ESLint, and Prettier
- `backend/src/shape_finder/` — FastAPI entrypoint and separated core, application, API, and infrastructure packages
- `backend/tests/` — backend API tests
- `docs/architecture.md` — boundaries and future extension points
- `.env.example` — safe local configuration template

## Local development

Prerequisites: Node.js 20+ and Python 3.11+.

### Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
uvicorn shape_finder.main:app --reload
```

The API is available at `http://localhost:8000`; its health endpoint is `GET /api/v1/health`.

### Frontend

In a separate terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Set `VITE_API_BASE_URL` in an untracked `.env` if the API uses a different origin.

## Quality checks

```bash
cd frontend
npm run check

cd ../backend
ruff check .
ruff format --check .
mypy src
pytest
```

## Security notes

Market-data credentials will be backend-only. `.env` files are ignored, and no API credential is needed in Phase 1.

