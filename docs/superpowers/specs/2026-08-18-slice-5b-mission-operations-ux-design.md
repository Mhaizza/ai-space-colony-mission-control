# Slice 5B Mission Operations UX — Design

## Status

Human-approved design direction, written after PR #22 merged to `main`.

This document defines **Slice 5B v1: Mission-centric governance UX** for the
existing Slice 5A Trust & Approval Model. It is a design artifact only. No
production implementation is authorized by this document alone; implementation
must proceed from a separately reviewed implementation plan.

## Baseline

- Repository: `Mhaizza/ai-space-colony-mission-control`
- Baseline branch: `main`
- Baseline after PR #22 merge: `c6b002ae46bc570a396fc4f9a80534ed42e82d63`
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
   **all approval requests for the Mission** as separate cards/items and opens
   one request detail at a time.

## Problem statement

Slice 5A makes governance decisions possible through API calls but does not yet
provide the operator-facing Mission Operations UX needed to understand or act on
those approvals in context.

The user should be able to open a Mission, immediately understand its current
governance requirements, inspect every approval request attached to that
Mission, and submit or change their own decision when the backend says they are
eligible.

The browser must remain a presentation and interaction layer. It must never
become a second policy engine.

## Goals

Slice 5B v1 must let an authenticated human operator:

- See every approval request associated with the current Mission.
- Understand status, action, policy/version, quorum state, missing requirements,
  effective decisions, lifecycle history, expiry/resolution, and mission effect.
- Approve or reject a pending request when authorized by the backend.
- Change their own current effective decision through the existing supersede
  mutation.
- Recover cleanly from loading, read, mutation, and retry conditions.
- Remain inside the Mission context throughout the governance flow.

## Explicitly out of scope

- Global approvals inbox.
- Policy editor UI.
- Principal, role, or trust administration UI.
- Notification center or approval notifications.
- Bulk approval actions.
- Authenticated AI approver UX.
- GitHub merge, review, comment, assignment, or any other GitHub write.
- New approval mutation routes.
- New ADR-23 mutation allowlist entries.
- Approval-domain schema migration unless implementation discovers a separately
  reviewed blocker; the intended design requires no migration.
- Replacing or semantically changing the existing Board approval system.
- Frontend reconstruction of quorum, supersession chains, authorization,
  effective status, or mission effect.

## Information architecture

The primary interaction surface is the existing **Mission detail** experience.
It gains a Governance section.

```text
Mission Detail
  └── Governance
        ├── Approval Request Card: architecture_review
        ├── Approval Request Card: implementation_review
        └── Approval Request Card: human_signoff
              └── Approval Detail
                    ├── Governance Summary
                    ├── Quorum Status
                    ├── Effective Decisions
                    ├── Lifecycle History
                    └── Decision Actions
```

No separate top-level approvals route is required for v1.

## Mission governance list behavior

All approval requests for the current Mission are shown.

Each request summary should display only the fields needed for scanning:

- Human-readable action name.
- Backend-provided request status.
- Policy key and version.
- Quorum summary based on backend-provided quorum state.
- Expiry or resolution timestamp when applicable.
- Mission effect when present.

Ordering rules:

1. Pending requests first.
2. Within the same status group, newest request first.
3. Terminal requests remain visible; they are not hidden into a separate
   browser-computed history bucket.

The frontend must not invent a concept such as "most important approval" or
choose one request as active through heuristics.

## Approval detail behavior

Selecting an approval card opens one approval request detail without removing
the user from Mission context. For v1, an inline expandable area or side sheet
is preferred over adding a dedicated nested approval route.

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

## Backend read-model extension

The current `ApprovalDetailResponse` is almost sufficient for Slice 5B, but it
does not tell the UI whether the authenticated caller can make a decision or
which currently-effective decision belongs to that caller.

Add two backend-derived fields:

```text
can_decide: bool
current_principal_decision: CurrentPrincipalDecisionView | null
```

`CurrentPrincipalDecisionView` should include at minimum:

```text
decision_id
decision
reason
created_at
```

These fields are caller-specific and must be derived from the authenticated
identity, never client input.

### Authorization reuse requirement

`can_decide` must use the **same authorization semantics as the mutation path**.
Implementation must not create a second, drifting authorization algorithm in
`approval_read_service.py`.

If decision eligibility is currently enforced inline in `approval_service.py`,
extract the smallest shared helper needed so both:

- read-model capability calculation, and
- mutation authorization

consume the same rule.

The helper remains server-side and derives principal identity, role, type,
trust, request state, and policy eligibility from trusted backend state.

### Current principal decision requirement

`current_principal_decision` identifies only the caller's currently-effective
decision on the request.

If the caller has changed a decision through supersession, the field must point
to the new effective decision, never the superseded record.

## UI states

### Pending

Render:

- `Pending approval` status.
- Backend-provided quorum requirements and missing requirements.
- Expiry information when available.
- Effective decisions and lifecycle.

Actions:

- When `can_decide=true` and `current_principal_decision=null`, show
  **Approve** and **Reject**.
- When `can_decide=true` and `current_principal_decision!=null`, show the
  caller's current decision and **Change decision**.
- When `can_decide=false`, do not show mutation controls.

### Approved

Read-only.

Render resolved time, mission effect, decisions, quorum state, and lifecycle.
No mutation controls.

### Rejected

Read-only.

Render rejection-related effective decisions and lifecycle using backend state.
No mutation controls.

### Expired

Read-only.

If reconciliation created a replacement request, represent that through
backend lifecycle data. The frontend never creates an automatic replacement.

### Superseded

Read-only.

Make it visually clear this request is no longer the current review cycle.
Lifecycle data explains the transition.

### Loading

Use governance/detail skeletons. Do not render mutation controls before the
caller-specific detail is loaded successfully.

### Read error

Keep the Mission page usable. Render a localized governance error such as
`Unable to load governance status` with a Retry action.

### Mutation error

Keep the previously loaded server state visible. Surface the mutation error and
allow the user to retry the same intent when safe.

## Decision interaction

### Approve / Reject

Open a small confirmation dialog that includes:

- The action being decided.
- The proposed decision.
- Optional `reason` text input.
- Explicit confirmation button.

Use the existing decision mutation route.

### Change decision

When the caller already has an effective decision and `can_decide=true`, open
the same decision dialog in change mode and submit through the existing
supersede route with:

- `supersedes_decision_id` from `current_principal_decision.decision_id`
- new `decision`
- optional `reason`

Old decision records remain immutable.

## Idempotency behavior

All approval mutations already require `Idempotency-Key`.

The UI must generate one idempotency key per **user intent**, not per HTTP
attempt.

Rules:

- A fresh confirmation of a new approve/reject/change-decision intent receives
  a fresh key.
- If the exact same intent is retried because transport/network delivery is
  uncertain, reuse the same key.
- Do not generate a new key inside an automatic HTTP retry loop.
- After the user changes the intended payload, treat it as a new intent and use
  a new key.

## Post-mutation state handling

Do not optimistically calculate new quorum or approval status in the browser.

After a successful decision or supersede mutation:

1. Invalidate/refetch the relevant Mission approval detail.
2. Render the newly returned backend-derived state.
3. Refresh the Mission governance list if list summary fields can change.

Mutation responses may be used for immediate acknowledgment, but the durable UX
state comes from the read model.

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

Exact file splitting is an implementation detail; the architectural rule is
that Mission approval UX has its own typed boundary and does not depend on the
legacy `ApprovalRead` / board approval mutation model.

Use the generated OpenAPI/Orval client for Mission approval endpoints. Do not
introduce an independent hand-written API client when generated hooks/types can
represent the contract.

If the backend response schema changes, regenerate the frontend API client and
commit the generated changes with the checkpoint that changes the contract.

## Backend implementation boundary

Expected touched areas for the read-model capability checkpoint include:

- `backend/app/schemas/mission_approvals.py`
- `backend/app/mission/approval_read_service.py`
- `backend/app/api/mission_approvals.py` if auth/read-service wiring requires it
- the smallest shared approval-authorization helper needed to keep read/write
  semantics identical
- corresponding backend tests
- OpenAPI-generated frontend client artifacts

The design does not authorize:

- new approval routes,
- new mutation allowlist entries,
- policy semantic changes,
- new GitHub capabilities,
- new authentication mechanisms, or
- database migrations.

## Security and trust boundaries

The following Slice 5A invariants remain mandatory:

- Caller principal identity is resolved from authenticated server context only.
- Client-supplied `principal_id`, role, trust, effective approval status, or
  mission effect are never accepted.
- Human approval support remains the only authenticated approval path in this
  slice; AI approval remains deferred.
- `MUTATIONS_HARD_DISABLED` and ADR-23's closed allowlist remain unchanged.
- No Slice 5B component performs GitHub writes.

`can_decide` is a UX capability field, not a replacement for write-path
authorization. Every mutation must still re-authorize server-side at command
time because state or principal eligibility may have changed since the read.

## Testing strategy

### Backend tests

Add tests proving:

- Eligible authenticated principal -> `can_decide=true` on a pending request.
- Ineligible role/type/trust -> `can_decide=false`.
- Disabled or unregistered principal behavior is fail-closed and explicitly
  covered according to the route's chosen response semantics.
- Terminal request -> no decision capability.
- `current_principal_decision` returns only the caller's effective decision.
- Superseding a caller decision causes the read model to expose the successor,
  never the old decision.
- Another principal's decision is never surfaced as the caller's current
  decision.
- Existing quorum, missing-requirement, mission-effect, and lifecycle semantics
  are unchanged.
- Read and write paths consume shared decision-eligibility semantics.
- OpenAPI schema pins the caller-capability fields so generated clients cannot
  silently drift.

### Frontend tests

Add tests proving:

- Pending + `can_decide=true` + no caller decision -> Approve/Reject visible.
- Pending + `can_decide=false` -> no mutation controls.
- Pending + caller effective decision -> Change decision visible.
- Approved/rejected/expired/superseded -> read-only.
- Loading -> skeleton and no mutation buttons.
- Read failure -> localized error and Retry; Mission page remains usable.
- Mutation failure -> existing state remains visible.
- Successful mutation -> detail is invalidated/refetched.
- Same retryable user intent reuses its idempotency key.
- A changed user intent gets a new idempotency key.
- Multiple Mission approval requests are all rendered.
- Pending requests sort before terminal requests; requests within a group sort
  newest first.
- Frontend does not derive authorization or quorum from role/decision arrays.

## Acceptance criteria

Slice 5B v1 is acceptable when an authenticated human can:

1. Open Mission detail and see all approval requests for that Mission.
2. Inspect one request's backend-derived governance detail without leaving the
   Mission context.
3. Understand status, policy/version, quorum, missing requirements, effective
   decisions, lifecycle, expiry/resolution, and mission effect.
4. Approve or reject when `can_decide=true`.
5. Change their own effective decision through the supersede flow.
6. Receive deterministic read-only UI for terminal requests.
7. Recover from read and mutation errors without losing the Mission page.
8. See state after mutation only after reconciliation with backend truth.

Engineering acceptance additionally requires:

- No new GitHub writes.
- No new mutation route or ADR-23 allowlist expansion.
- No frontend policy/quorum/authorization engine.
- No semantic change to the legacy Board approval system.
- Required tests pass.
- Full repository validation and CodeQL pass before merge.

## Implementation checkpoints

Implementation should be split into independently reviewable checkpoints unless
a later implementation plan demonstrates a safer smaller split.

### Checkpoint A — Caller-aware read-model capability

Backend-only contract/capability work plus generated client regeneration:

- Add `can_decide`.
- Add `current_principal_decision`.
- Reuse/extract shared authorization semantics.
- Add backend/OpenAPI tests.
- Regenerate Orval client.

No Mission UI yet.

### Checkpoint B — Mission Governance read UX

Frontend read-only UX:

- Mission Governance section.
- All approval request cards.
- Request detail.
- Loading/error/terminal states.
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
2. Write failing tests first for the checkpoint's contract/behavior.
3. Implement only the checkpoint scope.
4. Run targeted tests, then repository validation appropriate to the change.
5. Push/open PR.
6. Wait for CI and CodeQL at the exact PR head SHA.
7. Perform independent implementation review at that exact SHA.
8. Merge only after Human approval.

Do not start Checkpoint B before Checkpoint A's API contract is merged and the
generated client is authoritative. Likewise, mutation UX checkpoints must build
on the reviewed caller-capability read model rather than introducing frontend
permission heuristics.

## Design invariants summary

Slice 5B v1 is:

**Mission detail -> Governance -> all approval requests -> one request detail ->
backend-authorized human decision -> backend refetch.**

It is not a new workflow engine, approval inbox, policy editor, identity admin,
or GitHub-control surface.

The browser presents backend truth; it does not become a second governance
engine.
