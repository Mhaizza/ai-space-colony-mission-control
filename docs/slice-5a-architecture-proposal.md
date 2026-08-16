"""Slice 5A — Architecture Proposal: Trust & Approval Model

## Status

Design-only. No implementation until Human approval and design-review PASS.
This proposal itself is scoped to **Checkpoint A** of the checkpoint-based
implementation plan below: architecture only, no production code, no
migration, no route.

**Revision 2** closes ten review findings from Codex's automated review of
revision 1, plus three raised directly by the Human: multiple roles per
principal (`mc_principal_role` association, not a single column), a
deterministic active-policy-version rule (`is_active` flag + partial unique
index, not an implicit "highest version" inference), and actor provenance on
`mc_approval_event` (`triggered_by_principal_id`). Also closed: idempotency
keys now scoped to `(key, principal_id, endpoint)`; expiration reconciliation
now runs independently of the GitHub-adapter-gated `PollingScheduler`;
`mc_approval_decision` now snapshots the deciding principal's roles/trust
level at submission time; the veto decision rule now states its complete
approval/rejection predicate; `mutation_guard.py`'s literal-path matching is
flagged as needing route-template-aware matching for D8a's two parameterized
routes; `ai`-type approvers are explicitly excluded from Slice 5A policies
pending an authenticated AI decision path; read-route checkpoint placement is
now consistently Checkpoint D throughout; `POST /approvals` gains an explicit
`supersedes_request_id` input and a deterministic system-trigger predecessor
rule; and `mc_approval_request.trigger_key` gains a database uniqueness
constraint with upsert-based creation.

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
| **Principal domain** | New `mc_principal` table: Mission-Control-owned identity (`human` / `ai` / `system`), with optional external-identity linkage (provenance only, never trusted for authorization). New `mc_principal_role` association table: a principal may hold more than one role. |
| **Policy domain** | New `mc_approval_policy` table: versioned, immutable-per-version, typed (Pydantic-validated) policy records — decision rule (`majority`/`unanimous`/`veto`), quorum, allowed approver roles/principal types, trust requirements, rejection behavior, expiration behavior. |
| **Approval persistence** | New `mc_approval_request` (mission- or action-scoped, system- or human-created), `mc_approval_decision` (append-only, immutable, supersedable via `supersedes_decision_id`), `mc_approval_event` (append-only lifecycle events, distinct from decisions). |
| **Idempotency** | New `mc_approval_operation` table (dedicated, not columns on the domain tables — see Decision Log) for `Idempotency-Key` bookkeeping across all three mutation endpoints. |
| **Policy evaluator** | Pure domain function `evaluate_approval(policy, request, effective_decisions, now)` — deterministic, no I/O, returns a typed evaluation result. |
| **Principal resolver / authorization** | Resolves `mc_principal` from `AuthContext` (existing Clerk/local-auth layer), never from a request body; enforces role/trust/enabled checks before policy evaluation. **Slice 5A's resolver covers `human` and `system` principals only** — `AuthContext` is explicitly user-only (`actor_type: Literal["user"]`, verified in `app/core/auth.py`); authenticated agents use the separate `AgentAuthContext` (`app/core/agent_auth.py`), which no route or resolver in this slice consumes. A policy naming an `ai` approver role would therefore include a vote that can never be cast, potentially making its quorum permanently unsatisfiable. `ai` remains a valid persisted `mc_principal.principal_type` (for future data modeling and for `system`-created records attributed to an AI-authored source), but **no policy created in Slice 5A may name an `ai` principal type as an eligible approver** — the policy schema (Checkpoint B) validates this and rejects such a policy. Wiring an authenticated AI decision path via `AgentAuthContext` is deferred to a future slice. |
| **Approval service** | Transactional orchestration (`BEGIN…SELECT…FOR UPDATE…COMMIT`) for request creation, decision submission, and decision supersession. |
| **Read APIs** | `GET /api/v1/mission/approvals`, `GET /api/v1/mission/approvals/{request_id}` — frontend-ready derived state (quorum, effective decisions, lifecycle history, current Mission effect). |
| **Mutation APIs** (Checkpoint D, gated on ADR-23 D8a) | `POST /api/v1/mission/approvals`, `POST /api/v1/mission/approvals/{request_id}/decisions`, `POST /api/v1/mission/approvals/{request_id}/supersede`. |
| **Expiration/reconciliation** | Server-side periodic tick evaluating `expires_at <= now` for pending requests; bounded `max_auto_retries` for recreate-on-expire. **Runs on its own scheduled task, started independently from application startup — not by extending the existing `PollingScheduler`.** `PollingScheduler` is instantiated only inside `_start_github_adapter`, which `app/main.py` calls only when `GITHUB_PAT` is configured (verified directly). Approval governance has no dependency on the GitHub adapter being enabled, so tying reconciliation to that conditional startup would silently stop expiring/blocking/recreating approval requests in the explicitly-supported adapter-disabled configuration, or after a PAT is later removed while approval records remain. |
| **Automatic request triggers** | Deterministic logical-trigger-key (`mission_ref + action_key + head_sha`) hooked into `GitHubSyncService`'s existing result path — read-only observation only, never a new GitHub call. |
| **Migration** | One additive Alembic migration for the seven new tables. |

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
- `MutationHardDisableMiddleware`'s allowlist gains exactly three new entries, and only once ADR-23 revision 16 (D8a) is Accepted — Checkpoints B/C register no route (read or mutation) and change no allowlist; all API routes, including the two GET routes, are registered in Checkpoint D (see Checkpoint-based implementation plan below — this proposal previously described the read routes inconsistently as "Checkpoint B/C-adjacent," which is corrected here).
- Two of D8a's three routes are parameterized (`{request_id}`), but `MutationHardDisableMiddleware` currently compares the *concrete runtime* `scope["path"]` against `MUTATION_ALLOWLIST` by literal string equality (verified directly in `app/core/mutation_guard.py`) — a literal `{request_id}` allowlist entry never matches a real UUID path, so those two routes would 405 permanently even when properly authorized. Checkpoint D's `mutation_guard.py` change is therefore not just "add three entries" as originally stated: the matcher itself needs route-template-aware matching (e.g. compiling each parameterized allowlist entry to a path-segment regex, matched against `scope["path"]`) for exactly D8a's two parameterized entries, with the existing literal-match behavior preserved unchanged for D3's and D8a's non-parameterized entry (`POST /api/v1/mission/approvals`).
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
  approval_reconciliation.py  # expiration tick, own scheduled task
                             # (independent of PollingScheduler/_start_github_adapter)
  approval_triggers.py      # deterministic system-created request triggers

backend/app/models/
  mc_approval.py            # McPrincipal, McPrincipalRole, McApprovalPolicy,
                             # McApprovalRequest, McApprovalDecision,
                             # McApprovalEvent, McApprovalOperation

backend/app/schemas/
  mission_approvals.py      # request/response/read models

backend/app/api/
  mission_approvals.py      # GET and POST routes, all registered in
                             # Checkpoint D (POST routes additionally gated
                             # on ADR-23 D8a Accepted; GET routes are not
                             # ADR-23-gated but are registered at the same
                             # checkpoint as the rest of this module, per the
                             # Human-approved checkpoint plan)

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
    trust_level         VARCHAR(32)  NOT NULL,
    enabled             BOOLEAN      NOT NULL DEFAULT TRUE,
    external_provider   VARCHAR(64),               -- e.g. "clerk"; provenance only
    external_subject    VARCHAR(256),               -- e.g. clerk_user_id; provenance only
    created_at          TIMESTAMPTZ  NOT NULL,
    updated_at          TIMESTAMPTZ  NOT NULL,
    UNIQUE (external_provider, external_subject)
);

-- A principal may legitimately hold more than one role (the Approval
-- semantics section already requires policies to check role-based quorum
-- and veto eligibility across a principal's full role set) — modeled as an
-- association, not a single nullable column on mc_principal.
CREATE TABLE mc_principal_role (
    principal_id  UUID NOT NULL REFERENCES mc_principal(id),
    role_slug     VARCHAR(64) NOT NULL,  -- from the existing closed RoleSlug registry
    PRIMARY KEY (principal_id, role_slug)
);

CREATE TABLE mc_approval_policy (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_key          VARCHAR(128) NOT NULL,
    version             INTEGER      NOT NULL,
    is_active           BOOLEAN      NOT NULL DEFAULT FALSE,
    definition          JSONB        NOT NULL,    -- Pydantic-validated typed shape, never arbitrary
    created_at          TIMESTAMPTZ  NOT NULL,
    UNIQUE (policy_key, version)
);

-- Deterministic active-version selection: at most one row per policy_key may
-- have is_active = true. Publishing a new version sets the new row's
-- is_active = true and the prior active row's is_active = false in the same
-- transaction — never a query-time "highest version wins" inference, so a
-- deliberate rollback to an older version is representable. An approval
-- request resolves policy_key to whichever row currently has is_active =
-- true at creation time, then pins that row's id (not the key) forever
-- (see mc_approval_request.policy_id below) — later activation changes
-- never affect an already-created request.
CREATE UNIQUE INDEX ux_mc_approval_policy_active
    ON mc_approval_policy (policy_key) WHERE is_active;

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

-- trigger_key is the sole idempotency mechanism for system-created requests
-- (mission_ref + action_key + head_sha, see Automatic request triggers
-- below) and must be enforced by the database, not by a SELECT-then-INSERT
-- race in application code — concurrent polling/manual-refresh overlap, or
-- multiple application replicas, can both observe "no existing request" for
-- the same key and both attempt to insert. Creation therefore always goes
-- through an upsert (`INSERT ... ON CONFLICT (trigger_key) DO NOTHING`,
-- returning the existing row on conflict), never a plain SELECT-then-INSERT
-- that has no not-yet-existing row to lock via `SELECT ... FOR UPDATE`.
-- Each bounded auto-retry (expire -> recreate) obtains a distinct
-- trigger_key by suffixing the retry count (e.g. `<mission_ref>|<action_key>|<head_sha>|retry:<n>`),
-- so retries never collide with each other or the original.
CREATE UNIQUE INDEX ux_mc_approval_request_trigger_key
    ON mc_approval_request (trigger_key) WHERE trigger_key IS NOT NULL;

CREATE TABLE mc_approval_decision (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id             UUID NOT NULL REFERENCES mc_approval_request(id),
    principal_id            UUID NOT NULL REFERENCES mc_principal(id),
    decision                 VARCHAR(16) NOT NULL,  -- approve | reject
    reason                    TEXT,
    -- Snapshot of the authorization facts actually used to admit this
    -- decision, captured at submission time. mc_principal is mutable
    -- (role membership, trust_level, enabled can all change later); without
    -- this snapshot, reconstructing a request's history after such a change
    -- would have to either apply the principal's *current* attributes
    -- retroactively (silently changing what quorum/veto looked like at the
    -- time) or have no persisted basis for the policy evaluation this
    -- decision actually received. role_slugs_at_decision is the principal's
    -- full mc_principal_role set as of submission, not just one role.
    role_slugs_at_decision    JSONB NOT NULL,   -- array of role_slug strings
    trust_level_at_decision   VARCHAR(32) NOT NULL,
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
    -- The principal whose action produced this event, when there is one:
    -- a human/system principal's decision or request creation. NULL for
    -- events with no single attributable actor (e.g. a reconciliation-tick
    -- expiration, which is a scheduled system process, not a principal
    -- acting through an authenticated request).
    triggered_by_principal_id  UUID REFERENCES mc_principal(id),
    detail           JSONB,
    created_at        TIMESTAMPTZ NOT NULL
);

-- Idempotency identity is scoped to (idempotency_key, principal_id,
-- endpoint), never the key alone: a bare-key primary key would let two
-- different principals' independently-chosen identical keys collide, and
-- the normative replay rule ("same key + same payload replays the original
-- result") would then replay the *first* principal's response for the
-- *second* principal's request — or, if the first principal's key happens
-- to match a value the second principal predictably reuses, let one
-- principal's operation be silently served in place of another's. Every
-- comparison (replay vs. reuse-with-different-payload) is scoped to this
-- same triple.
CREATE TABLE mc_approval_operation (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key   VARCHAR(128) NOT NULL,
    principal_id      UUID NOT NULL REFERENCES mc_principal(id),
    endpoint            VARCHAR(128) NOT NULL,
    payload_hash          VARCHAR(128) NOT NULL,
    response_snapshot       JSONB NOT NULL,
    created_at                TIMESTAMPTZ NOT NULL,
    UNIQUE (idempotency_key, principal_id, endpoint)
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

### Read (Checkpoint D, not ADR-23-gated — GET only)

`GET /api/v1/mission/approvals`, `GET /api/v1/mission/approvals/{request_id}` — follow the existing `require_user_auth` + flat-response-model + `limit` pagination pattern from `app/api/mission.py`. Response includes request status, Mission reference, action key, policy key/version, decision rule, quorum satisfied + current counts, effective decisions, lifecycle history, expiration, and current Mission effect. The frontend never reconstructs supersession chains or quorum state itself — the backend evaluator is the sole source of truth.

### Mutation (Checkpoint D — requires ADR-23 revision 16 Accepted)

`POST /api/v1/mission/approvals` — client provides `policy_key`, Mission reference, `scope_type`, `action_key`, `reason`, and an optional `supersedes_request_id`. The backend resolves `policy_key` to whichever `mc_approval_policy` row currently has `is_active = true` (the deterministic active-version rule defined in Data model impact above) and pins that row's `id`, not the key — the client never chooses a policy version directly. When `supersedes_request_id` is supplied, the backend validates it names a request sharing the same Mission reference and `action_key` and currently in a terminal state (`approved`/`rejected`/`expired`/`superseded`; anything else is `409 invalid_supersede`) before creating the new request as its successor — this is how a human operator starts a new review cycle after a rejected/expired request. System-created requests (from Automatic request triggers, below) resolve their predecessor the same way, automatically: when a new head/revision is observed for a `mission_ref`/`action_key` pair that already has a terminal request, the trigger sets `supersedes_request_id` to that request's id before creating the new one — never left to the client to specify for the automated path.

`POST /api/v1/mission/approvals/{request_id}/decisions` (client sends only `{decision, reason}`; backend returns the persisted decision plus the latest evaluation), `POST /api/v1/mission/approvals/{request_id}/supersede` (`{supersedes_decision_id, decision, reason}`, validated per the Decision Immutability rules below).

### Closed error taxonomy

400 `approval_policy_invalid` / `mission_ref_invalid` / `decision_reason_too_long`; 401 `authentication_required`; 403 `principal_not_registered` / `principal_disabled` / `principal_not_authorized` / `principal_trust_insufficient`; 404 `approval_request_not_found` / `decision_not_found`; 409 `approval_request_terminal` / `approval_decision_exists` / `invalid_supersede` / `idempotency_key_reused` / `approval_request_already_exists`; 422 normal schema validation; 500 `approval_internal_error`. No stack traces, DB details, credentials, or sensitive authorization claims leak in any error body.

---

## Approval semantics (normative, matches the approved direction verbatim)

- **Effective decision:** one principal contributes at most one effective vote per request; a second `POST /decisions` from the same effective decision returns `409 approval_decision_exists` — changing it requires the explicit supersede operation. A principal may hold multiple roles (`mc_principal_role`, one row per role); this still contributes exactly one effective vote per request — a principal's full role set is checked for eligibility/veto authority, but does not multiply their vote into multiple quorum slots.
- **Majority:** quorum first, then `approve > reject` among effective decisions; incomplete quorum stays `pending` even if the received votes already favor one side.
- **Unanimous:** quorum fully satisfied and no effective rejection exists.
- **Veto — complete predicate:** (1) if any effective decision is a `reject` from a principal holding a policy-designated veto-authorized role, the request is immediately `rejected` — this check applies regardless of quorum state, including before quorum would otherwise be satisfied; (2) absent any such veto-authorized rejection, the request becomes `approved` once quorum is satisfied and at least one effective decision is `approve`; (3) a `reject` from a principal *not* holding a veto-authorized role is recorded as an effective decision (visible in history, still counts toward quorum's participant count) but neither triggers immediate rejection nor blocks approval on its own — only case (1) rejects, and only case (2) approves. A request satisfying quorum with only non-veto rejections and zero approvals remains `pending` (approval requires an affirmative `approve`, not merely the absence of a veto).
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
| Duplicate system-created requests from repeated polling or multiple replicas | `trigger_key` carries a database `UNIQUE` constraint (`ux_mc_approval_request_trigger_key`); creation is an upsert (`ON CONFLICT DO NOTHING`), not a racy SELECT-then-INSERT. Each bounded auto-retry gets its own distinct trigger key. |
| Idempotency key collision across principals | `mc_approval_operation` identity and replay/reuse comparison are scoped to `(idempotency_key, principal_id, endpoint)`, never the key alone. |
| Reconciliation silently not running (adapter-disabled deployments) | Expiration reconciliation runs on its own scheduled task, started independently of `_start_github_adapter`'s `GITHUB_PAT`-gated startup. |
| Mutation-guard parameterized-route bypass or permanent 405 | D8a's two `{request_id}` routes require route-template-aware matching in `mutation_guard.py`, not literal-string comparison; tested explicitly (Checkpoint D) against both a blocked-when-disabled case and an allowed-when-enabled case with a real UUID path. |
| Approval history reinterpreted after a principal's role/trust changes | `mc_approval_decision` snapshots `role_slugs_at_decision`/`trust_level_at_decision` at submission time; historical evaluation always replays against the snapshot, never the principal's current attributes. |
| Unauthenticatable approver role (`ai`) making quorum permanently unsatisfiable | Policy schema validation (Checkpoint B) rejects any policy naming `ai` as an eligible approver in Slice 5A, since no authenticated resolution path for `ai` principals exists yet. |

No new external network calls. No secrets flow into any new table or response.

---

## Migration strategy

Single Alembic migration, additive only (seven new tables, indexes as listed above). Safe to apply to a live Slice 4 deployment with zero downtime. Rollback drops only the seven new tables and their indexes without affecting existing data.

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
