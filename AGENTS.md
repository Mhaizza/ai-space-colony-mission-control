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
- Keep PRs focused and based on latest `main`.
- Include: what changed, why, test evidence (`make check` or targeted commands), linked issue, and screenshots/logs when UI or operator workflow changes.

## Security & Configuration Tips
- Never commit secrets. Copy from `.env.example` and keep real values in local `.env`.
- Report vulnerabilities privately via GitHub security advisories, not public issues.

## Cursor Cloud specific instructions

The items below describe one specific Cursor Cloud VM environment as observed at setup time. They are environment-specific operational notes, not universal repository invariants: do not treat VM package availability, pre-installed service versions, or OS-level service commands as facts about this repository or about every development environment.

Standard commands live in `Makefile`, `README.md`, and this file's "Build, Test, and Development Commands"; only the non-obvious cloud caveats are below.

- __Update script only refreshes deps__ (`make setup` = `uv sync --extra dev` + `npm install`). Everything below (services, DB, env, migrations) must be done by the agent at session start; it is not automated.
- __If Docker is unavailable in a given VM__, fall back to a host-local dev loop against a local PostgreSQL and Redis instance instead of `docker compose` / `make docker-*`. In one observed Cursor Cloud VM, PostgreSQL 16 and Redis 7 were already installed and were started with `sudo pg_ctlcluster 16 main start` and `sudo redis-server --daemonize yes`; treat these exact versions and commands as an example from that VM, not a guarantee for every environment. Ensure the target database exists (e.g. `sudo -u postgres createdb mission_control`) and that `DATABASE_URL` matches whatever credentials your local PostgreSQL role already uses.
- __Avoid resetting the `postgres` role password by default.__ Prefer setting `DATABASE_URL` to match your existing local PostgreSQL credentials. Only reset the role password (e.g. `sudo -u postgres psql -c "ALTER USER postgres WITH PASSWORD '...';"`) as an exceptional local-environment recovery step if the role's password is genuinely unknown or unusable, and keep `DATABASE_URL` in sync with whatever you set.
- __If `python` is not on PATH in your environment, use `python3`__ (e.g. `python3 scripts/ensure_local_auth_env.py`).
- __`backend/.env` needs more than the generator writes.__ `scripts/ensure_local_auth_env.py` only writes `AUTH_MODE`/`LOCAL_AUTH_TOKEN`/`BASE_URL`/`MUTATIONS_HARD_DISABLED`. For a host-local loop you must also add:
  - `DATABASE_URL` pointing at your local PostgreSQL instance (the code default targets a non-existent `openclaw_agency` database; `.env.example` recommends a `mission_control` database instead).
  - `CORS_ORIGINS=http://localhost:3000`; without it browser sign-in fails with "Unable to reach backend to validate token" (CORS preflight blocked on `/api/v1/auth/verify`).
  - `RQ_REDIS_URL=redis://localhost:6379/0` (this matches the code default, but setting it explicitly avoids surprises).
- __Frontend runtime config:__ create `frontend/.env.local` with `NEXT_PUBLIC_API_URL=auto` and `NEXT_PUBLIC_AUTH_MODE=local` (compose passes these as build args; `next dev` needs them in an env file).
- __Migrations__ auto-run on backend startup when `ENVIRONMENT=dev` (this makes `DB_AUTO_MIGRATE` default to true); or run `make backend-migrate` explicitly.
- __Run (host-local loop):__ see "Build, Test, and Development Commands" above for the backend/frontend dev commands and ports.
- __ADR-23 mutation boundary:__ the application hard-disables inherited write/action routes and only allows an explicit, closed mutation allowlist. As of Slice 5A Checkpoint D, the allowed mutation routes are `POST /api/v1/mission/refresh`, `POST /api/v1/mission/approvals`, `POST /api/v1/mission/approvals/{request_id}/decisions`, and `POST /api/v1/mission/approvals/{request_id}/supersede`. Every other `POST`/`PUT`/`PATCH`/`DELETE` route is rejected with HTTP 405 by the mutation guard. Sign in by pasting `LOCAL_AUTH_TOKEN` on `/sign-in`.
- __Onboarding gate for local dev:__ Boards/Activity/Gateways redirect to `/onboarding` until the logged-in user's profile has both a name and a timezone. The local-auth user is auto-created with `name="Local User"`, so in practice only the timezone is usually missing; there is no allowed mutation route to update it via the API, so for local development set it directly in the DB: `UPDATE users SET timezone='UTC' WHERE clerk_user_id='local-auth-user';`.
- __Empty data is expected:__ boards/agents are org-scoped and populated only by the GitHub adapter (`GITHUB_PAT`, disabled by default), so lists are empty without it.
- __Don't run `next build` while `next dev` is running__; both write `.next`, so restart `npm run dev` after a build.
- Optional `webhook-worker` (RQ) via `make rq-worker`; only needed for background webhook dispatch, not for UI/API browsing.
