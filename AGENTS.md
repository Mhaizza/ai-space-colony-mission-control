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
This environment has no Docker and no systemd. Postgres 16 and Redis 7 are installed natively (via apt) instead of via `compose.yml`. The update script only refreshes deps (`uv sync` for `backend`, `npm install` for `frontend`); everything below must be done per session.

- Start datastores each session (systemd is not running):
  - `sudo pg_ctlcluster 16 main start`
  - `sudo redis-server /etc/redis/redis.conf --daemonize yes`
- Postgres is reachable at `postgresql+psycopg://postgres:postgres@localhost:5432/mission_control` (role password `postgres`, db `mission_control`). If the db is missing, recreate with `sudo -u postgres createdb mission_control`. Data (and applied migrations) persist in the VM snapshot.
- Env files `.env`, `backend/.env`, `frontend/.env` are gitignored. If absent, recreate from the `*.env.example` files, then run `python scripts/ensure_local_auth_env.py` to generate `LOCAL_AUTH_TOKEN`, and set `BASE_URL=http://localhost:8000` in `backend/.env` (the example ships it blank, which fails startup). In dev, `DB_AUTO_MIGRATE=true` makes the backend run Alembic on startup.
- Run services (see `backend/README.md` and root `README.md` for the standard commands): backend `cd backend && uv run uvicorn app.main:app --reload --port 8000`; frontend `cd frontend && npm run dev`. Health: `GET /healthz`.
- Auth is local bearer-token mode. Log in at `/sign-in` by pasting `LOCAL_AUTH_TOKEN` from `backend/.env`; the frontend validates it via `GET /api/v1/users/me`. The token is kept in `sessionStorage` (`mc_local_auth_token`) plus an in-memory var, so a full reload is needed to fully log out.
- The app is intentionally read-only: `MUTATIONS_HARD_DISABLED=true` is required (startup fails if false), so all POST/PUT/PATCH/DELETE return HTTP 405 `mutations_hard_disabled` except the single mission manual-refresh route. Onboarding "Save Profile" and other writes failing this way is expected, not an environment bug.
- The GitHub adapter stays disabled unless `GITHUB_PAT` is set; leaving it empty is normal for local dev.
