# Slice 5B Mission Operations UX — Design

## Status

Human-approved design direction, refined by repository self-review after PR #22
merged to `main`.

This document defines **Slice 5B v1: Mission-centric governance UX** for the
existing Slice 5A Trust & Approval Model. It is a design artifact only. No
production implementation is authorized by this document alone; implementation
must proceed from a separately reviewed implementation plan.

## Baseline

- Repository: `Mhaizza/ai-space-colony-mission-control`
- Baseline branch: `main`
- Baseline after PR #22 merge: `c6b002ae46bc570a396fc4f9a80534ed42e82d63`
- Existing Mission frontend surface: `frontend/src/app/mission/page.tsx`
- The current `/mission` page is an overview/dashboard with a Cards list; there
  is no existing dedicated Mission-detail route.
- Slice 5A already provides the approval domain, evaluator, principal resolver,
  read APIs, mutation APIs, expiration/reconciliation, and automatic trigger
  plumbing.
- ADR-23 mutation boundaries remain authoritative.

## Human decisions captured by this design

1. Slice 5B v1 is **Mission-centric only**. No global approvals inbox is added.
2. The new Mission approval UX is a distinct domain from the legacy Board
   approval UX and must not reuse `BoardApprovalsPanel` as its source model.
3. The backend remains authoritative for approval state, quorum, authorization,
   effective decisions, and mission effect. The frontend must not reconstruct
   any of those semantics.
4. Slice 5B may add a **small backend read-model extension** so the frontend can
   render deterministic decision controls without guessing caller permissions.
5. A Mission may have multiple simultaneous approval actions. The UI displays
   **all approval requests for the selected Mission/card** as separate items and
   opens one request detail at a time.
6. Because the repository has no dedicated Mission-detail route today, v1 uses
   a **selected Mission/card context on the existing `/mission` surface** rather
   than introducing a new route tree.

## Problem statement

Slice 5A makes governance decisions possible through API calls but does not yet
provide the operator-facing Mission Operations UX needed to understand or act on
those approvals in context.

The current `/mission` page already exposes projected GitHub cards, but cards
are display-only. Slice 5B should let an operator select a card, inspect that
Mission/card's governance state, and act on approval requests without turning
the browser into a second policy engine.

## Goals

Slice 5B v1 must let an authenticated human operator:

- Select a projected Mission/card from the existing `/mission` experience.
- See every approval request associated with that Mission identity.
- Understand status, action, policy/version, quorum state, missing requirements,
  effective decisions, lifecycle history, expiry/resolution, and mission effect.
- Approve or reject a pending request when authorized by the backend.
- Change their own current effective decision through the existing supersede
  mutation.
- Recover cleanly from loading, read, mutation, and retry conditions.
- Remain inside Mission context throughout the governance flow.

## Explicitly out of scope

- Global approvals inbox.
- A new top-level Mission detail route or nested approval route in v1.
- Policy editor UI.
- Principal, role, or trust administration UI.
- Notification center or approval notifications.
- Bulk approval actions.
- Authenticated AI approver UX.
- GitHub merge, review, comment, assignment, or any other GitHub write.
- New approval mutation routes.
- New ADR-23 mutation allowlist entries.
- Approval-domain database migration unless implementation discovers a
  separately reviewed blocker; the intended design requires no migration.
- Replacing or semantically changing the existing Board approval system.
- Frontend reconstruction of quorum, supersession chains, authorization,
  effective status, or mission effect.

## Information architecture

The existing `/mission` page becomes the primary interaction surface.

```text
/mission
  ├── Existing overview sections
  └── Cards
        └── Select Mission/card
              └── Mission Governance context
                    ├── Approval request list
                    └── Select approval request
                          └── Approval Detail
                                ├── Governance Summary
                                ├── Quorum Status
                                ├── Effective Decisions
                                ├── Lifecycle History
                                └── Decision Actions
```

The selected Mission/card context may be rendered as an inline expandable panel
or side sheet. The implementation plan should choose the smallest approach that
fits the existing page structure and accessibility patterns.

No new route is required for v1.

## Mission identity

Mission approval rows already identify their source using:

- `mission_source_repo`
- `mission_card_kind`
- `mission_card_number`

The selected projected card must be mapped to exactly this identity tuple using
backend/projected data already exposed by the Mission surface. The frontend must
not infer a second Mission identifier or introduce a new source of truth.

## Mission-scoped approval list API requirement

`GET /api/v1/mission/approvals` is currently a paginated global list. Client-side
filtering of one paginated page would be incorrect because matching approvals
could exist on later pages.

Therefore Slice 5B must extend the **existing GET list route** with optional
server-side filters for the selected Mission identity:

```text
mission_source_repo
mission_card_kind
mission_card_number
```

When all three are supplied, the backend returns only approval requests for that
Mission identity while preserving the existing pagination contract and newest-
first behavior.

This is a read-only extension:

- no new route,
- no mutation allowlist change,
- no new trust boundary,
- no database migration.

Tests must prove the filter tuple is applied server-side before pagination and
does not alter unfiltered list behavior.

## Mission governance list behavior

All approval requests returned for the selected Mission are shown.

Each request summary uses fields already appropriate to the list contract:

- Human-readable action name.
- Backend-provided request status.
- Policy key and version.
- Created timestamp.
- Expiry timestamp when present.

Quorum details and `mission_effect` are intentionally **not required on the list
item** in v1. They belong to the selected request detail, where the backend
already evaluates them authoritatively. This avoids turning the paginated list
endpoint into an N-request evaluation surface solely for card decoration.

Ordering rules:

1. Pending requests first in the presentation layer.
2. Within the same status group, newest request first.
3. Terminal requests remain visible.

The underlying API remains newest-first; the UI may stably group pending versus
terminal using the backend-provided `status` field only. It must not invent a
concept such as "most important approval" or choose one request as active
through heuristics.

## Approval detail behavior

Selecting an approval request loads its existing backend detail model while
keeping the selected Mission/card visible as context.

The detail renders backend-derived values directly:

- `status`
- `policy_key`
- `policy_version`
- `decision_rule`
- `quorum_satisfied`
- `quorum_requirements`
- `missing_requirements`
- `effective_decisions`
- `lifecycle`
- `created_at`
- `expires_at`
- `resolved_at`
- `mission_effect`

The detail must not perform an independent quorum calculation or rebuild a
supersession chain.

## Caller-aware backend read-model extension

The current `ApprovalDetailResponse` does not tell the UI whether the
authenticated caller can make a decision or which currently-effective decision
belongs to that caller.

Add two backend-derived fields:

```text
can_decide: bool
current_principal_decision: CurrentPrincipalDecisionView | null
```

`CurrentPrincipalDecisionView` includes at minimum:

```text
decision_id
decision
reason
created_at
```

These fields are caller-specific and must be derived from authenticated server
identity, never client input.

### Authorization reuse requirement

`can_decide` must use the **same authorization semantics as the mutation path**.
Implementation must not create a second, drifting authorization algorithm in
`approval_read_service.py`.

If decision eligibility is currently enforced inline in `approval_service.py`,
extract the smallest shared helper needed so both read-model capability
calculation and mutation authorization consume the same rule.

The helper remains server-side and derives principal identity, role, type,
trust, request state, and policy eligibility from trusted backend state.

`can_decide` is a UX capability field, not a replacement for command-time
authorization. Every mutation must re-authorize because state may change after
the read.

### Current principal decision requirement

`current_principal_decision` identifies only the caller's currently-effective
decision on the request.

If the caller has changed a decision through supersession, the field points to
the new effective decision, never the superseded record.

## UI states

### No Mission/card selected

The existing Mission overview remains usable. Governance detail is not loaded.
The Cards area gives a clear affordance to select a card for governance context.

### Mission selected, approvals loading

Show a localized governance skeleton. Do not block unrelated `/mission` overview
content.

### Mission selected, no approval requests

Show a neutral empty state such as `No governance requests for this Mission.`
Do not treat this as an error.

### Pending request

Render:

- `Pending approval` status.
- Backend-provided quorum requirements and missing requirements in detail.
- Expiry information when available.
- Effective decisions and lifecycle.

Actions:

- `can_decide=true` and no current caller decision -> **Approve** / **Reject**.
- `can_decide=true` and current caller decision present -> current decision +
  **Change decision**.
- `can_decide=false` -> no mutation controls.

### Approved / Rejected / Expired / Superseded

Read-only.

Render backend state, timestamps, decisions, mission effect, and lifecycle as
applicable. No mutation controls.

### Read error

Keep the Mission overview and selected card context usable. Render a localized
governance error such as `Unable to load governance status` with Retry.

### Mutation error

Keep the previously loaded backend state visible. Surface the mutation error and
allow a retry of the same user intent when safe.

## Decision interaction

### Approve / Reject

Open a small confirmation dialog containing:

- action being decided,
- proposed decision,
- optional `reason`,
- explicit confirmation.

Use the existing decision mutation route.

### Change decision

When the caller has an effective decision and `can_decide=true`, open the same
dialog in change mode and call the existing supersede route using:

- `supersedes_decision_id` from
  `current_principal_decision.decision_id`,
- new `decision`,
- optional `reason`.

Old decision rows remain immutable.

## Idempotency behavior

All approval mutations already require `Idempotency-Key`.

Generate one idempotency key per **user intent**, not per HTTP attempt.

- Fresh confirmed intent -> fresh key.
- Retry of the exact same intent after uncertain transport delivery -> reuse the
  same key.
- Do not generate a new key inside automatic HTTP retry logic.
- Changed payload or newly confirmed decision -> new intent and new key.

## Post-mutation state handling

Do not optimistically calculate quorum, status, or mission effect.

After a successful decision or supersede mutation:

1. Invalidate/refetch the selected approval detail.
2. Invalidate/refetch the selected Mission's approval list if summary status may
   have changed.
3. Render the returned backend-derived state.

## Frontend component boundary

Create Mission-governance-specific components rather than extending the legacy
Board approvals component.

Suggested ownership:

```text
frontend/src/components/mission/governance/
  MissionGovernancePanel
  ApprovalRequestCard
  ApprovalDetail
  GovernanceSummary
  QuorumStatus
  DecisionHistory
  LifecycleHistory
  DecisionActions
  DecisionDialog
```

Exact file splitting is an implementation detail.

The architectural boundary is that Mission approval UX has its own generated
Mission-approval types and does not depend on the legacy `ApprovalRead` / board
approval mutation model.

Use the generated OpenAPI/Orval client. Do not introduce a parallel hand-written
API client when generated hooks/types can represent the contract.

## Backend implementation boundary

Expected read-contract work includes:

- `backend/app/schemas/mission_approvals.py`
- `backend/app/mission/approval_read_service.py`
- `backend/app/api/mission_approvals.py`
- the smallest shared approval-authorization helper needed to keep read/write
  semantics identical
- backend route/service/OpenAPI tests
- regenerated frontend Orval artifacts

Checkpoint A must cover both read prerequisites needed by the UI:

1. Mission-scoped optional filters on the existing approval list GET route.
2. Caller-aware capability fields on approval detail.

The design does not authorize:

- new approval routes,
- new mutation allowlist entries,
- policy semantic changes,
- new GitHub capabilities,
- new authentication mechanisms,
- database migrations.

## Security and trust boundaries

The following Slice 5A invariants remain mandatory:

- Caller principal identity is resolved from authenticated server context only.
- Client-supplied `principal_id`, role, trust, effective approval status, or
  mission effect are never accepted.
- Human approval remains the authenticated approval path in this slice; AI
  approval remains deferred.
- `MUTATIONS_HARD_DISABLED` and ADR-23's closed allowlist remain unchanged.
- No Slice 5B component performs GitHub writes.

Mission-list filter parameters identify which records to read; they grant no
approval authority and must never influence principal eligibility.

## Testing strategy

### Backend tests

Add tests proving:

- Mission filter tuple is applied before pagination.
- Filtered list returns only the requested repo/kind/number identity.
- Unfiltered list behavior remains backward-compatible.
- Eligible authenticated principal -> `can_decide=true` on a pending request.
- Ineligible role/type/trust -> `can_decide=false`.
- Disabled or unregistered principal behavior is fail-closed and explicitly
  covered according to chosen route semantics.
- Terminal request -> no decision capability.
- `current_principal_decision` returns only the caller's effective decision.
- Superseding a caller decision exposes the successor, never the old decision.
- Another principal's decision is never surfaced as the caller's current
  decision.
- Existing quorum, missing-requirement, mission-effect, and lifecycle semantics
  are unchanged.
- Read and write paths consume shared decision-eligibility semantics.
- OpenAPI pins Mission list filters and caller-capability fields.

### Frontend tests

Add tests proving:

- No selected Mission -> existing overview remains usable; no governance query.
- Selecting a card issues a Mission-scoped approval list query using the exact
  projected Mission identity tuple.
- Empty filtered result -> neutral empty governance state.
- Multiple approval requests are all rendered.
- Pending requests are grouped before terminal requests; newest remains first
  within each group.
- Selecting a request loads backend detail.
- Pending + `can_decide=true` + no caller decision -> Approve/Reject visible.
- Pending + `can_decide=false` -> no mutation controls.
- Pending + caller effective decision -> Change decision visible.
- Terminal requests -> read-only.
- Loading -> localized skeleton and no mutation buttons.
- Read failure -> localized error and Retry; Mission overview remains usable.
- Mutation failure -> existing state remains visible.
- Successful mutation -> detail and relevant list are invalidated/refetched.
- Same retryable user intent reuses its idempotency key.
- Changed/new intent receives a new idempotency key.
- Frontend does not derive authorization or quorum from role/decision arrays.

## Acceptance criteria

Slice 5B v1 is acceptable when an authenticated human can:

1. Open `/mission` and select a projected Mission/card.
2. See all approval requests for that exact Mission identity.
3. Inspect one request's backend-derived governance detail without leaving the
   Mission context.
4. Understand status, policy/version, quorum, missing requirements, effective
   decisions, lifecycle, expiry/resolution, and mission effect.
5. Approve or reject when `can_decide=true`.
6. Change their own effective decision through the supersede flow.
7. Receive deterministic read-only UI for terminal requests.
8. Recover from read and mutation errors without losing the Mission overview.
9. See state after mutation only after reconciliation with backend truth.

Engineering acceptance additionally requires:

- No new GitHub writes.
- No new mutation route or ADR-23 allowlist expansion.
- No frontend policy/quorum/authorization engine.
- No semantic change to the legacy Board approval system.
- Required tests pass.
- Full repository validation and CodeQL pass before merge.

## Implementation checkpoints

### Checkpoint A — Mission/caller-aware read contract

Backend read-contract work plus generated client regeneration:

- Add optional Mission identity filters to `GET /mission/approvals`.
- Add `can_decide`.
- Add `current_principal_decision`.
- Reuse/extract shared authorization semantics.
- Add backend/OpenAPI tests.
- Regenerate Orval client.

No Mission governance UI yet.

### Checkpoint B — Mission Governance read UX

Frontend read-only UX on the existing `/mission` surface:

- Make projected cards selectable for governance context.
- Mission-scoped approval request list.
- All approval request cards/items.
- Request detail.
- Loading, empty, error, and terminal states.
- No decision mutations exposed yet.

### Checkpoint C — Human decision UX

Add:

- Approve.
- Reject.
- Optional reason.
- Per-intent idempotency-key lifecycle.
- Refetch after mutation.

### Checkpoint D — Change decision UX

Add caller-owned effective-decision supersession:

- Change decision action.
- `supersedes_decision_id` from backend read model.
- Existing supersede mutation.
- Refetch and terminal/error handling.

## Rollout and review strategy

Each checkpoint should use the established TDD and exact-head review workflow:

1. Start from latest `main` in an isolated branch/worktree.
2. Write failing tests first for the checkpoint contract/behavior.
3. Implement only checkpoint scope.
4. Run targeted tests and repository validation appropriate to the change.
5. Push/open PR.
6. Wait for CI and CodeQL at the exact PR head SHA.
7. Perform independent implementation review at that exact SHA.
8. Merge only after Human approval.

Do not start Checkpoint B before Checkpoint A's API contract is merged and the
generated client is authoritative. Mutation UX checkpoints must build on the
reviewed caller-capability read model rather than frontend permission
heuristics.

## Self-review conclusions

Repository self-review corrected two assumptions from the conversational draft:

1. There is no existing dedicated Mission-detail route; the design now uses a
   selected-card context on `/mission` for v1.
2. The approval list is globally paginated; the design now requires optional
   server-side Mission filters so the UI cannot produce incomplete results by
   filtering one client-side page.

It also intentionally keeps quorum and mission effect off the list-item contract
for v1; those remain authoritative detail fields.

No placeholder architecture decision remains in the spec. The implementation
plan may choose exact component file splitting and inline-versus-side-sheet
presentation, but those choices do not change trust, API, or scope boundaries.

## Design invariants summary

Slice 5B v1 is:

**`/mission` -> select projected Mission/card -> server-filtered approval list ->
select request -> backend-derived detail -> backend-authorized human decision ->
backend refetch.**

It is not a new workflow engine, approval inbox, policy editor, identity admin,
or GitHub-control surface.

The browser presents backend truth; it does not become a second governance
engine.
