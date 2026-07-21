"""Slice 4 — Architecture Proposal: Projection Retention, Audit & Enriched Read Views

## Status

Design-only. No implementation until Human approval and design-review PASS.

## Authority

- Issue: Mhaizza/ai-space-colony-mission-control#6
- ADR-23 (Accepted) governs all read/write boundaries.
- Baseline: Slice 3.5 stabilization checkpoint (Issue #5, commit `06b01ab`).

---

## Problem statement

Slice 3.5 delivers a minimal read-only dashboard populated from the Slice 3
projection pipeline. Three gaps prevent the dashboard from being operationally
useful at scale:

1. **No retention policy.** Tombstoned projection records accumulate without
   bound. A table with tens of thousands of stale rows degrades query
   performance and makes dashboard counts misleading.

2. **No per-run sync audit trail.** The `mc_sync_state` table records the
   _current_ adapter state but not the history of individual sync runs (start
   time, finish time, record counts, partial/full outcome). Operators cannot
   investigate past sync behaviour without log grepping.

3. **Minimal projection views.** Slice 3.5 surfaces cards and workflow records.
   It does not materialize board-level aggregates, PR check status, or
   approval-linked items — all of which exist in the projection payload but are
   not exposed via the API.

---

## Slice 4 scope

### In scope

| Area | Change |
| --- | --- |
| **Retention / GC** | Server-side async task: purge `mc_projection_record` rows that are tombstoned and older than a configurable TTL (`MC_RETENTION_TOMBSTONE_DAYS`, default 30). Idempotent; safe to run on every poll cycle. |
| **Sync audit log** | New `mc_sync_audit` table (one row per completed sync run): `id`, `adapter_key`, `started_at`, `finished_at`, `is_partial`, `projected`, `quarantined`, `tombstoned`, `error_summary`. Read-only. Populated by the existing `GitHubSyncService` result path. |
| **Enriched read endpoints** | Two new read-only GET endpoints: `GET /api/v1/mission/audit` (paginated sync audit log) and `GET /api/v1/mission/pr-status` (live PR check-run / commit-status summary derived from projected data). |
| **Index tuning** | Add composite index on `(tombstoned, projected_at)` for retention queries; add index on `mc_sync_audit(adapter_key, started_at)` for audit queries. |
| **Frontend dashboard** | Extend the `/mission` dashboard to show sync history (last N runs, pass/partial/fail indicators) and PR check-run summary. Orval client regenerated. |

### Explicitly out of scope

| Item | Reason |
| --- | --- |
| GitHub mutations | ADR-23 D8; requires separate approval. |
| Inbound webhooks | ADR-23 D9; requires separate architecture approval. |
| Assignment authority derivation in dashboard | Deferred until Slice 5 trust model review. |
| Full board approvals materialization | Deferred; depends on Slice 5 approval flow. |
| Upstream pin movement | No upstream work planned; Slice 4 is purely local divergence. |

---

## Architecture boundaries

### ADR-23 invariants preserved

- `MUTATIONS_HARD_DISABLED=true` remains unchanged.
- `MutationHardDisableMiddleware` allowlist stays at exactly one entry
  (`POST /api/v1/mission/refresh`).
- No GitHub mutations, no inbound webhooks.
- Credentials absent from browser bundles, API responses, logs, new audit
  rows, and enriched projection views.
- Upstream pin SHA unchanged.

### New module additions (backend)

```
backend/app/mission/
  retention.py          # async tombstone GC job
  audit.py              # audit-row writer called from sync result path

backend/app/models/
  mc_sync_audit.py      # McSyncAudit SQLModel table

backend/app/schemas/
  mission.py            # +MissionAuditEntry, +MissionPRStatusEntry schemas

backend/app/api/
  mission.py            # +GET /audit, +GET /pr-status read routes

backend/migrations/versions/
  <hash>_add_mc_sync_audit_and_retention_index.py
```

No existing modules are removed or significantly refactored.

---

## Data model impact

### New table: `mc_sync_audit`

```sql
CREATE TABLE mc_sync_audit (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    adapter_key  VARCHAR(128) NOT NULL,
    started_at   TIMESTAMP WITH TIME ZONE NOT NULL,
    finished_at  TIMESTAMP WITH TIME ZONE,
    is_partial   BOOLEAN NOT NULL DEFAULT FALSE,
    projected    INTEGER NOT NULL DEFAULT 0,
    quarantined  INTEGER NOT NULL DEFAULT 0,
    tombstoned   INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT
);

CREATE INDEX ix_mc_sync_audit_key_started
    ON mc_sync_audit (adapter_key, started_at DESC);
```

### Index additions on `mc_projection_record`

```sql
CREATE INDEX ix_mc_proj_tombstoned_projected_at
    ON mc_projection_record (tombstoned, projected_at);
```

### No breaking changes

- No existing columns removed or renamed.
- No existing indexes dropped.
- Migration is additive and safe to apply to a running Slice 3.5 deployment.
- One migration file; policy compliant.

---

## API design

All new endpoints are read-only GET routes, authenticated by the existing
`require_user_auth` dependency.

### `GET /api/v1/mission/audit`

Returns the N most recent sync audit rows.

**Query parameters**: `limit` (1–200, default 50), `adapter_key` (optional filter).

**Response** (`MissionAuditSummary`):
```json
{
  "total": 142,
  "recent": [
    {
      "id": "...",
      "adapter_key": "default",
      "started_at": "...",
      "finished_at": "...",
      "is_partial": false,
      "projected": 312,
      "quarantined": 1,
      "tombstoned": 0,
      "error_summary": null
    }
  ]
}
```

### `GET /api/v1/mission/pr-status`

Returns projected PR check-run and commit-status summaries derived from
existing `mc_projection_record` rows where `source_type` is
`github_check_run`, `github_check_suite`, `github_commit_status`, or
`github_workflow_run`.

**Query parameters**: `card` (optional issue/PR number), `limit` (1–500, default 100).

**Response** (`MissionPRStatusSummary`):
```json
{
  "total": 48,
  "items": [
    {
      "source_type": "github_check_run",
      "source_id": "...",
      "card": 42,
      "name": "CI / check",
      "conclusion": "success",
      "url": "...",
      "updated_at": "..."
    }
  ]
}
```

Both endpoints follow the existing `MissionOverview`/`MissionQuarantineSummary`
patterns: flat response models, no nested credentials, pagination via `limit`.

---

## Security risks

| Risk | Mitigation |
| --- | --- |
| Audit rows exposing internal error details | `error_summary` is a truncated (≤512 char) string; no stack traces or credentials. |
| PR status endpoint revealing private repo data | All data is derived from the existing projection table which stores only public GitHub data read via `{read:project}` scope. No additional GitHub calls. |
| Retention job causing data loss | GC only deletes `tombstoned=true` records older than configurable TTL. Live records are never touched. Operation is logged and can be disabled via `MC_RETENTION_TOMBSTONE_DAYS=0`. |
| New endpoints bypassing mutation guard | Both endpoints are GET; `MutationHardDisableMiddleware` is unaffected. |

No new external network calls. No secrets flow.

---

## Migration strategy

Single Alembic migration:
- Add `mc_sync_audit` table.
- Add composite index on `mc_projection_record(tombstoned, projected_at)`.
- Add index on `mc_sync_audit(adapter_key, started_at)`.

Migration is:
- Additive (no drops, no renames).
- Safe to apply to a live Slice 3.5 deployment with zero downtime.
- Compliant with the one-migration-per-PR policy.

Rollback: the migration adds only indexes and a new table; reverting removes
them without affecting existing data.

---

## Implementation plan

### Pre-conditions

- Human approval of this design document.
- Design-review PASS recorded.
- Slice 3.5 stabilization checkpoint (Issue #5) closed.

### Implementation order

1. Add `mc_sync_audit` SQLModel + Alembic migration.
2. Add `MissionAuditEntry` / `MissionPRStatusEntry` schemas.
3. Extend `GitHubSyncService` to write audit rows (no behaviour change).
4. Add retention job (`retention.py`) integrated into polling loop, configurable off.
5. Add `GET /audit` and `GET /pr-status` API routes.
6. Regenerate Orval client.
7. Extend `/mission` frontend dashboard.
8. Tests: audit writer, retention job, new API endpoints, migration smoke.

### Acceptance criteria

- `GET /api/v1/mission/audit` returns sync run history; empty list before first sync.
- `GET /api/v1/mission/pr-status` returns PR check-run data from projected records.
- Retention job deletes tombstoned records older than TTL; live records are untouched.
- `mypy --strict` clean; pytest suite passes; ESLint + TypeScript clean.
- One migration file; migration integrity gate passes.
- `MUTATIONS_HARD_DISABLED=true` + `MutationHardDisableMiddleware` tests unchanged.
- Upstream pin SHA unchanged.

---

## Design review result

See `docs/slice-4-architecture-review.md`.
"""
