"""Slice 5A — Architecture Proposal: Trust & Approval Model

## Status

Design-only. No implementation until Human approval and design-review PASS.
This proposal itself is scoped to **Checkpoint A** of the checkpoint-based
implementation plan below: architecture only, no production code, no
migration, no route.

## Authority

- Slice 5 direction: Human-approved "Approved Slice 5A Direction" (Trust &
  Approval Model), splitting Slice 5 into 5A (this proposal) and 5B (Mission
  Operations UX, explicitly out of scope here).
- ADR-23 (Accepted) governs all existing read/write boundaries. **This
  proposal's mutation routes (Checkpoint D) additionally require ADR-23
  revision 16 (D8a — Internal Governance Mutations) to be Accepted** before
  they may be implemented or allowlisted — see
  `Mhaizza/ai-space-colony-sim` Issue #165 / PR #166. Checkpoint A of this
  proposal runs in parallel with that ADR revision's own review; Checkpoint D
  is blocked until D8a is Accepted.
- Baseline: Slice 4 (Issue #6/#8/#9, merged), Slice 3.5 stabilization
  checkpoint (Issue #5).

---

## Problem statement

Slice 3/3.5/4 give Mission Control a read-only projection of GitHub workflow
state (`ai-workflow-record:v1` records, cards, PR/CI status) but no way for
an operator to record an internal governance decision — "this implementation
review passes," "this Mission needs Human sign-off before proceeding" —
without that decision either living only in a GitHub comment (which ADR-23
D4 already governs, and which Mission Control cannot post per D8's read-only
GitHub boundary) or not being recorded at all.

Slice 5A adds an **internal, policy-based approval workflow**, persisted
entirely inside Mission Control, that:

1. Never writes to GitHub in any form (D8/D8a).
2. Supports both Mission-level approvals ("Mission #172 requires Human
   Sign-off") and action-level approvals (architecture review,
   implementation review, security review, human sign-off), each governed by
   a versioned, immutable policy (majority / unanimous / veto decision
   rules; configurable rejection and expiration behavior).
3. Resolves the acting principal from server-verified authenticated identity
   only — never from a client-supplied value — through a Hybrid Principal
   Registry that Mission Control owns internally and that may optionally
   link to external identities (e.g. GitHub logins) as provenance, never as
   trust.
4. Keeps decisions immutable and requests supersedable, with a documented
   distinction between the two (decision supersession corrects an active
   request's effective vote; request supersession starts a new review
   cycle).

---

## Slice 5A scope

### In scope

| Area | Change |
| --- | --- |
| **Principal domain** | New `mc_principal` table: Mission-Control-owned identity (`human` / `ai` / `system`), with optional external-identity linkage (provenance only, never trusted for authorization). |
| **Policy domain** | New `mc_approval_policy` table: versioned, immutable-per-version, typed (Pydantic-validated) policy records — decision rule (`majority`/`unanimous`/`veto`), quorum, allowed approver roles/principal types, trust requirements, rejection behavior, expiration behavior. |
| **Approval persistence** | New `mc_approval_request` (mission- or action-scoped, system- or human-created), `mc_approval_decision` (append-only, immutable, supersedable via `supersedes_decision_id`), `mc_approval_event` (append-only lifecycle events, distinct from decisions). |
| **Idempotency** | New `mc_approval_operation` table (dedicated, not columns on the domain tables — see Decision Log) for `Idempotency-Key` bookkeeping across all three mutation endpoints. |
| **Policy evaluator** | Pure domain function `evaluate_approval(policy, request, effective_decisions, now)` — deterministic, no I/O, returns a typed evaluation result. |
| **Principal resolver / authorization** | Resolves `mc_principal` from `AuthContext` (existing Clerk/local-auth layer), never from a request body; enforces role/trust/enabled checks before policy evaluation. |
| **Approval service** | Transactional orchestration (`BEGIN…SELECT…FOR UPDATE…COMMIT`) for request creation, decision submission, and decision supersession. |
| **Read APIs** | `GET /api/v1/mission/approvals`, `GET /api/v1/mission/approvals/{request_id}` — frontend-ready derived state (quorum, effective decisions, lifecycle history, current Mission effect). |
| **Mutation APIs** (Checkpoint D, gated on ADR-23 D8a) | `POST /api/v1/mission/approvals`, `POST /api/v1/mission/approvals/{request_id}/decisions`, `POST /api/v1/mission/approvals/{request_id}/supersede`. |
| **Expiration/reconciliation** | Server-side periodic tick (extends the existing `PollingScheduler`, already used for Slice 4's retention GC) evaluating `expires_at <= now` for pending requests; bounded `max_auto_retries` for recreate-on-expire. |
| **Automatic request triggers** | Deterministic logical-trigger-key (`mission_ref + action_key + head_sha`) hooked into `GitHubSyncService`'s existing result path — read-only observation only, never a new GitHub call. |
| **Migration** | One additive Alembic migration for the six new tables. |

### Explicitly out of scope

| Item | Reason |
| --- | --- |
| GitHub writes (merge, review, comment, assignment) | ADR-23 D8/D8a; structurally forbidden. |
| Inbound webhooks | ADR-23 D3; requires separate architecture approval. |
| Generic workflow engine / arbitrary policy scripting | Explicitly rejected direction; policies are a closed, typed shape. |
| Policy editor UI / principal admin UI | Explicitly deferred; policies/principals use seeded/migration-safe bootstrap only in Slice 5A. |
| Slice 5B Mission Operations UX redesign | Separate slice, separate approval. |
| A new `mc_mission` source-of-truth table | Mission identity continues to derive from existing GitHub projection/card data (`mc_projection_record`'s `github_issue`/`github_pull_request` partitions); no second source of truth for GitHub Issue/PR data. |

---

## Architecture boundaries

### ADR-23 invariants preserved

- `MUTATIONS_HARD_DISABLED=true` remains unchanged; `enforce_mutations_hard_disabled`'s fail-closed startup check is untouched.
- `MutationHardDisableMiddleware`'s allowlist gains exactly three new entries, and only once ADR-23 revision 16 (D8a) is Accepted — Checkpoints B/C register no route and change no allowlist.
- No GitHub mutations, no inbound webhooks, ever, from any Slice 5A component.
- No credential (GitHub PAT, local-auth token) reaches request/response bodies, logs, audit rows, or persisted approval state.
- Client-supplied `principal_id`, `role`, `trust_level`, effective approval status, or Mission effect are never accepted — every one of those is server-derived.
- No new `mc_mission` source-of-truth table; Mission reference derives from existing projection data.

### New module additions (backend)

```
backend/app/mission/
  principals.py            # PrincipalType, resolution-input dataclasses
  approval_policy.py        # Pydantic policy schema + validation
  approval_evaluator.py     # pure evaluate_approval() domain service
  principal_resolver.py     # AuthContext -> McPrincipal (server-side only)
  approval_service.py       # transactional create/decide/supersede orchestration
  approval_reconciliation.py  # expiration tick, wired into PollingScheduler
  approval_triggers.py      # deterministic system-created request triggers

backend/app/models/
  mc_approval.py            # McPrincipal, McApprovalPolicy, McApprovalRequest,
                             # McApprovalDecision, McApprovalEvent, McApprovalOperation

backend/app/schemas/
  mission_approvals.py      # request/response/read models

backend/app/api/
  mission_approvals.py      # GET routes (Checkpoint B/C); POST routes added
                             # only in Checkpoint D, after ADR-23 D8a Accepted

backend/migrations/versions/
  <hash>_add_mc_approval_domain.py
```

No existing modules are removed or significantly refactored. `app/core/mutation_guard.py`'s `MUTATION_ALLOWLIST` and `app/mission/types.py` gain three new allowlist-entry constants, added only in Checkpoint D.

---

## Data model impact

### New tables (all `mc_`-prefixed, UUID PKs, `_JSON_PORTABLE` for typed JSON, `utcnow` defaults — matching existing Slice 3/3.5/4 conventions)

```sql
CREATE TABLE mc_principal (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_type      VARCHAR(16)  NOT NULL,   -- human | ai | system
    display_name        VARCHAR(256) NOT NULL,
    role_slug           VARCHAR(64),              -- from the existing closed RoleSlug registry
    trust_level         VARCHAR(32)  NOT NULL,
    enabled             BOOLEAN      NOT NULL DEFAULT TRUE,
    external_provider   VARCHAR(64),               -- e.g. "clerk"; provenance only
    external_subject    VARCHAR(256),               -- e.g. clerk_user_id; provenance only
    created_at          TIMESTAMPTZ  NOT NULL,
    updated_at          TIMESTAMPTZ  NOT NULL,
    UNIQUE (external_provider, external_subject)
);

CREATE TABLE mc_approval_policy (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_key          VARCHAR(128) NOT NULL,
    version             INTEGER      NOT NULL,
    definition          JSONB        NOT NULL,    -- Pydantic-validated typed shape, never arbitrary
    created_at          TIMESTAMPTZ  NOT NULL,
    UNIQUE (policy_key, version)
);

CREATE TABLE mc_approval_request (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_id                UUID NOT NULL REFERENCES mc_approval_policy(id),
    scope_type               VARCHAR(16) NOT NULL,  -- mission | action
    mission_source_repo      VARCHAR(256) NOT NULL,
    mission_card_kind        VARCHAR(16)  NOT NULL, -- issue | pull_request
    mission_card_number      INTEGER      NOT NULL,
    action_key                VARCHAR(64),
    created_by_principal_id   UUID NOT NULL REFERENCES mc_principal(id),
    creation_source            VARCHAR(16) NOT NULL, -- system | human
    status                     VARCHAR(16) NOT NULL, -- pending | approved | rejected | expired | superseded
    created_at                 TIMESTAMPTZ NOT NULL,
    expires_at                 TIMESTAMPTZ,
    resolved_at                TIMESTAMPTZ,
    supersedes_request_id      UUID REFERENCES mc_approval_request(id),
    trigger_key                 VARCHAR(512),          -- deterministic system-trigger dedup key
    auto_retry_count             INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE mc_approval_decision (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id             UUID NOT NULL REFERENCES mc_approval_request(id),
    principal_id            UUID NOT NULL REFERENCES mc_principal(id),
    decision                 VARCHAR(16) NOT NULL,  -- approve | reject
    reason                    TEXT,
    created_at                TIMESTAMPTZ NOT NULL,
    supersedes_decision_id     UUID REFERENCES mc_approval_decision(id)
);

CREATE TABLE mc_approval_event (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id     UUID NOT NULL REFERENCES mc_approval_request(id),
    event_type      VARCHAR(32) NOT NULL,  -- request_created | quorum_reached |
                                            -- request_approved | request_rejected |
                                            -- request_expired | mission_blocked |
                                            -- request_superseded
    detail           JSONB,
    created_at        TIMESTAMPTZ NOT NULL
);

CREATE TABLE mc_approval_operation (
    idempotency_key  VARCHAR(128) PRIMARY KEY,
    principal_id      UUID NOT NULL REFERENCES mc_principal(id),
    endpoint            VARCHAR(128) NOT NULL,
    payload_hash          VARCHAR(128) NOT NULL,
    response_snapshot       JSONB NOT NULL,
    created_at                TIMESTAMPTZ NOT NULL
);

CREATE INDEX ix_mc_approval_request_mission
    ON mc_approval_request (mission_source_repo, mission_card_kind, mission_card_number);
CREATE INDEX ix_mc_approval_request_status_expires
    ON mc_approval_request (status, expires_at);
CREATE INDEX ix_mc_approval_decision_request
    ON mc_approval_decision (request_id);
CREATE INDEX ix_mc_approval_event_request
    ON mc_approval_event (request_id, created_at);
```

### No breaking changes

- No existing columns removed or renamed.
- No existing indexes dropped.
- Migration is additive; safe to apply to a running Slice 4 deployment.
- One migration file; policy compliant.

---

## API design

### Read (Checkpoint B/C-adjacent, no ADR-23 gating — GET only)

`GET /api/v1/mission/approvals`, `GET /api/v1/mission/approvals/{request_id}` — follow the existing `require_user_auth` + flat-response-model + `limit` pagination pattern from `app/api/mission.py`. Response includes request status, Mission reference, action key, policy key/version, decision rule, quorum satisfied + current counts, effective decisions, lifecycle history, expiration, and current Mission effect. The frontend never reconstructs supersession chains or quorum state itself — the backend evaluator is the sole source of truth.

### Mutation (Checkpoint D — requires ADR-23 revision 16 Accepted)

`POST /api/v1/mission/approvals` (client provides `policy_key`, Mission reference, `scope_type`, `action_key`, `reason`; backend resolves `policy_key` to its exact active version — the client never chooses a policy version), `POST /api/v1/mission/approvals/{request_id}/decisions` (client sends only `{decision, reason}`; backend returns the persisted decision plus the latest evaluation), `POST /api/v1/mission/approvals/{request_id}/supersede` (`{supersedes_decision_id, decision, reason}`, validated per the Decision Immutability rules below).

### Closed error taxonomy

400 `approval_policy_invalid` / `mission_ref_invalid` / `decision_reason_too_long`; 401 `authentication_required`; 403 `principal_not_registered` / `principal_disabled` / `principal_not_authorized` / `principal_trust_insufficient`; 404 `approval_request_not_found` / `decision_not_found`; 409 `approval_request_terminal` / `approval_decision_exists` / `invalid_supersede` / `idempotency_key_reused` / `approval_request_already_exists`; 422 normal schema validation; 500 `approval_internal_error`. No stack traces, DB details, credentials, or sensitive authorization claims leak in any error body.

---

## Approval semantics (normative, matches the approved direction verbatim)

- **Effective decision:** one principal contributes at most one effective vote per request; a second `POST /decisions` from the same effective decision returns `409 approval_decision_exists` — changing it requires the explicit supersede operation. One principal with multiple roles never satisfies multiple quorum slots.
- **Majority:** quorum first, then `approve > reject` among effective decisions; incomplete quorum stays `pending` even if the received votes already favor one side.
- **Unanimous:** quorum fully satisfied and no effective rejection exists.
- **Veto:** only roles the policy explicitly names as veto-authorized can trigger an immediate rejection on a `reject` decision; no other rejection is treated as a veto.
- **Decision immutability:** a decision's `decision` value is never updated in place; a correction creates a new decision with `supersedes_decision_id` pointing at the prior one. A principal may only supersede its own effective decision (no administrative override in Slice 5A).
- **Request vs. decision supersession:** decision supersession corrects an active request's effective vote; request supersession represents a new review cycle (`supersedes_request_id`) — a rejected request is never reopened, only superseded by a new one.
- **Terminal states:** `approved`, `rejected`, `expired`, `superseded`. Any decision attempt after terminal resolution returns `409 approval_request_terminal`.
- **Rejection effect:** policy-selected — leave Mission state unchanged, or set Mission Control's internal Mission-blocked signal. Never touches GitHub Issue/PR state (ADR-23 D8a).
- **Expiration:** server-side only, via the reconciliation tick — never a browser timer. Policy-selected — expire, block Mission, or recreate (bounded by `max_auto_retries`).
- **Idempotency:** `Idempotency-Key` header on every mutation; same key + same payload replays the original result; same key + different payload returns `409 idempotency_key_reused`. Bookkeeping lives in `mc_approval_operation`, not on the domain tables (see Decision Log).
- **System-generated request idempotency:** a deterministic trigger key (`mission_ref + action_key + head_sha`) prevents duplicate requests from repeated poll observations of the same revision; a new head/revision may create a new review cycle via request supersession.

---

## Security risks

| Risk | Mitigation |
| --- | --- |
| Client-supplied principal spoofing | Request schemas never accept `principal_id`/`role`/`trust_level`; the resolver derives `McPrincipal` exclusively from `AuthContext` server-side. Tested explicitly (Checkpoint C). |
| Mutation routes reaching GitHub | Approval service has no dependency on `GitHubReadClient` or any write-capable GitHub client; tested via call-count assertions (Checkpoint D). |
| Mutation-hard-disable bypass | `MUTATION_ALLOWLIST` gains exactly three entries, only after ADR-23 D8a is Accepted; `enforce_mutations_hard_disabled`'s fail-closed startup check is untouched and re-verified by tests. |
| Concurrent decision race | Row-locked (`SELECT … FOR UPDATE`) transaction per request; second concurrent writer observes the first's result or a `409` if the request became terminal. |
| Partial write on event-persistence failure | Decision + evaluation + status update + events are one atomic transaction; any failure rolls back the whole write. |
| Idempotency-key replay with different payload | `mc_approval_operation.payload_hash` comparison; mismatch returns `409 idempotency_key_reused`. |
| Unbounded expire→recreate loop | `max_auto_retries` bound enforced by the reconciliation job. |
| Duplicate system-created requests from repeated polling | Deterministic trigger key makes creation idempotent; no-op on repeated observation of the same revision. |

No new external network calls. No secrets flow into any new table or response.

---

## Migration strategy

Single Alembic migration, additive only (six new tables, indexes as listed above). Safe to apply to a live Slice 4 deployment with zero downtime. Rollback drops only the six new tables and their indexes without affecting existing data.

---

## Checkpoint-based implementation plan

Per Human ruling, implementation proceeds in Human-approved checkpoints, not one PR:

- **Checkpoint A — Architecture** (this proposal + its review + ADR-23 D8a in the sibling repo). No production implementation.
- **Checkpoint B — Approval Domain Foundation.** Principal/policy domain types, persistence models, one additive migration, the pure policy evaluator, focused tests. No route registered.
- **Checkpoint C — Identity + Approval Service.** Principal resolver, authorization checks, transactional approval service, concurrency, idempotency, atomicity tests. No route registered.
- **Checkpoint D — API + Mutation Boundary.** Read APIs; the three mutation APIs; `MUTATION_ALLOWLIST` extension; closed error taxonomy; the full Required Security Invariants test list. **Only after ADR-23 revision 16 (D8a) is Accepted.**
- **Checkpoint E — Lifecycle Automation.** Expiration/reconciliation, bounded recreation, deterministic automatic triggers, system-generated request idempotency, OpenAPI/Orval regeneration, final regression validation.

Each checkpoint requires Human approval before the next begins.

### Acceptance criteria (full slice, evaluated at Checkpoint E)

- `GET /api/v1/mission/approvals[/​{id}]` return correct derived state; empty before any request exists.
- Every Required Security Invariant (per the approved direction's list of 13) has a passing test.
- `mypy --strict` clean; pytest suite passes; ESLint + TypeScript clean (OpenAPI/Orval regeneration only, no new UI per 5B exclusion).
- One migration file; migration integrity gate passes.
- `MUTATIONS_HARD_DISABLED=true` + `MutationHardDisableMiddleware` tests unchanged in behavior for every pre-existing route, and prove the three new routes are reachable only through the ADR-23-authorized allowlist.
- Upstream pin SHA unchanged.

---

## Design review result

See `docs/slice-5a-architecture-review.md`.
"""
