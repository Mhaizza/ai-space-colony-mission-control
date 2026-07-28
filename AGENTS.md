# Repository Guidelines

## Project Structure & Module Organization
- `backend/`: FastAPI service. Main app code lives in `backend/app/` with API routes in `backend/app/api/`, data models in `backend/app/models/`, schemas in `backend/app/schemas/`, and service logic in `backend/app/services/`.
- `backend/migrations/`: Alembic migrations (`backend/migrations/versions/` for generated revisions).
- `backend/tests/`: pytest suite (`test_*.py` naming).
- `backend/templates/`: backend-shipped templates used by gateway flows.
- `frontend/`: Next.js app. Routes under `frontend/src/app/`, shared components under `frontend/src/components/`, utilities under `frontend/src/lib/`.
- `frontend/src/api/generated/`: generated API client; regenerate instead of editing by hand.
- `docs/`: contributor and operations docs (start at `docs/README.md`).

## Build, Test, and Development Commands
- `make setup`: install/sync backend and frontend dependencies.
- `make check`: closest CI parity run (lint, typecheck, tests/coverage, frontend build).
- `docker compose -f compose.yml --env-file .env up -d --build`: run full stack.
- Fast local loop:
  - `docker compose -f compose.yml --env-file .env up -d db`
  - `cd backend && uv run uvicorn app.main:app --reload --port 8000`
  - `cd frontend && npm run dev`
- `make api-gen`: regenerate frontend API client (backend must be on `127.0.0.1:8000`).

## Coding Style & Naming Conventions
- Python: Black + isort + flake8 + strict mypy. Max line length is 100. Use `snake_case`.
- TypeScript/React: ESLint + Prettier. Components use `PascalCase`; variables/functions use `camelCase`.
- For intentionally unused destructured TS variables, prefix with `_` to satisfy lint config.

## Testing Guidelines
- Backend: pytest via `make backend-test`; coverage policy via `make backend-coverage` (writes `backend/coverage.xml` and `backend/coverage.json`).
- Frontend: vitest + Testing Library via `make frontend-test` (coverage in `frontend/coverage/`).
- Add or update tests whenever behavior changes.

## Commit & Pull Request Guidelines
- Follow Conventional Commits (seen in history), e.g. `feat: ...`, `fix: ...`, `docs: ...`, `test(core): ...`.
- Keep PRs focused and based on latest `master`.
- Include: what changed, why, test evidence (`make check` or targeted commands), linked issue, and screenshots/logs when UI or operator workflow changes.

## Security & Configuration Tips
- Never commit secrets. Copy from `.env.example` and keep real values in local `.env`.
- Report vulnerabilities privately via GitHub security advisories, not public issues.

## Cursor Cloud specific instructions

Standard commands live in `Makefile`, `README.md`, and this file's "Build, Test, and Development Commands"; only the non-obvious cloud caveats are below.

- __No Docker in this VM.__ Ignore the `docker compose` / `make docker-*` flows. Use the host-local dev loop against the system PostgreSQL 16 and Redis 7 that are already installed.
- __Update script only refreshes deps__ (`make setup` = `uv sync` + `npm install`). Everything below (services, DB, env, migrations) must be done by the agent at session start — it is not automated.
- __Start services each session:__ `sudo pg_ctlcluster 16 main start` and `sudo redis-server --daemonize yes`. Ensure the `postgres` role password is `postgres` and DB `mission_control` exists (`sudo -u postgres psql -c "ALTER USER postgres WITH PASSWORD 'postgres';"`, `sudo -u postgres createdb mission_control`).
- __`python` is not on PATH; use `python3`__ (e.g. `python3 scripts/ensure_local_auth_env.py`).
- __`backend/.env` needs more than the generator writes.__ `scripts/ensure_local_auth_env.py` only writes `AUTH_MODE`/`LOCAL_AUTH_TOKEN`/`BASE_URL`/`MUTATIONS_HARD_DISABLED`. For the host-local loop you must also add:
  - `DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/mission_control` — without it the code default points at a non-existent `openclaw_agency` DB.
  - `CORS_ORIGINS=http://localhost:3000` — without it the browser sign-in fails with "Unable to reach backend to validate token" (CORS preflight blocked on `/api/v1/auth/verify`).
  - `RQ_REDIS_URL=redis://localhost:6379/0`.
- __Frontend runtime config:__ create `frontend/.env.local` with `NEXT_PUBLIC_API_URL=auto` and `NEXT_PUBLIC_AUTH_MODE=local` (compose passes these as build args; `next dev` needs them in an env file).
- __Migrations__ auto-run on backend startup (`ENVIRONMENT=dev` makes `DB_AUTO_MIGRATE` default true); or run `make backend-migrate`.
- __Run (host-local loop):__ backend `cd backend && uv run uvicorn app.main:app --reload --port 8000`; frontend `cd frontend && npm run dev`. Health: `http://localhost:8000/healthz`, UI: `http://localhost:3000`.
- __Read-only app (ADR-23):__ every `POST/PUT/PATCH/DELETE` returns HTTP 405 except `POST /api/v1/mission/refresh`. Sign in by pasting `LOCAL_AUTH_TOKEN` on `/sign-in`.
- __Onboarding gate for local dev:__ Boards/Activity/Gateways redirect to `/onboarding` until the logged-in user's profile has both a name and a timezone, but the profile-update endpoint is a disabled mutation. For local browsing, set the timezone directly in the DB: `UPDATE users SET timezone='UTC' WHERE clerk_user_id='local-auth-user';`.
- __Empty data is expected:__ boards/agents are org-scoped and populated only by the GitHub adapter (`GITHUB_PAT`, disabled by default), so lists are empty without it.
- __Don't run `next build` while `next dev` is running__ — both write `.next`; restart `npm run dev` after a build.
- Optional `webhook-worker` (RQ) via `make rq-worker`; only needed for background webhook dispatch, not for UI/API browsing.
