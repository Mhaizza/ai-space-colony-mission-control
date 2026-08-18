# Slice 5B Mission Operations UX — Design

## Status

Human-approved design direction, including the repository self-review revision approved on 2026-08-18.

This document defines **Slice 5B v1: Mission-centric governance UX** for the existing Slice 5A Trust & Approval Model. It is a design artifact only. Production implementation must follow the separately reviewed implementation plan.

## Baseline

- Repository: `Mhaizza/ai-space-colony-mission-control`
- Baseline branch: `main`
- Baseline after PR #22 merge: `c6b002ae46bc570a396fc4f9a80534ed42e82d63`
- Existing Mission frontend surface: `frontend/src/app/mission/page.tsx`
- `/mission` is currently an overview/dashboard with a Cards list; there is no dedicated Mission-detail route.
- Slice 5A already provides approval persistence, evaluator, principal resolution, read APIs, mutation APIs, reconciliation, and trigger plumbing.
- ADR-23 mutation boundaries remain authoritative.

## Human decisions

1. Slice 5B v1 is **Mission-centric only**. No global approvals inbox.
2. Mission approvals are distinct from the legacy Board approvals domain; do not reuse `BoardApprovalsPanel` as the source model.
3. Backend is authoritative for request state, quorum, authorization capability, effective decisions, supersession, and mission effect.
4. The frontend must not reconstruct governance semantics.
5. A Mission may have multiple approval requests; show all of them for the selected Mission/card.
6. v1 uses a selected-card governance context on the existing `/mission` page instead of adding a Mission route tree.
7. Slice 5B may make small **read-model contract extensions** needed to support deterministic UX.
8. `MissionCard` must expose trusted `source_repo`; the frontend must not parse a GitHub URL or hardcode a repository name.

## Problem statement

Slice 5A provides governance APIs but no operator-facing Mission UX. The current `/mission` page already exposes projected GitHub cards, but they are display-only. Slice 5B lets an authenticated human select a card, inspect all internal governance requests for that exact Mission identity, and act when the backend says the caller is eligible.

The browser remains a presentation and interaction layer, not a second policy engine.

## Goals

An authenticated human operator can:

- select a projected Mission/card on `/mission`;
- see every approval request for that exact Mission identity;
- inspect status, policy/version, quorum, missing requirements, effective decisions, lifecycle, timestamps, and mission effect;
- approve or reject a pending request when backend-derived capability permits;
- change their own effective decision through the existing supersede route;
- recover from localized read/mutation errors without losing the Mission overview.

## Explicitly out of scope

- Global approvals inbox.
- New Mission detail route or nested approval route.
- Policy editor UI.
- Principal/role/trust administration UI.
- Notifications or bulk approval actions.
- Authenticated AI approver UX.
- GitHub merge/review/comment/assignment or any GitHub write.
- New approval mutation routes.
- New ADR-23 mutation allowlist entries.
- Database migration for this design.
- Semantic changes to the legacy Board approval system.
- Frontend reconstruction of quorum, authorization, supersession chains, effective status, or mission effect.

## Information architecture

```text
/mission
  ├── existing overview sections
  └── Cards
        └── select Mission/card
              └── Mission Governance context
                    ├── approval request list
                    └── select request
                          └── Approval Detail
                                ├── Governance Summary
                                ├── Quorum Status
                                ├── Effective Decisions
                                ├── Lifecycle History
                                └── Decision Actions
```

Use an inline expandable area or side sheet that keeps the selected card visible. No new route is required for v1.

## Mission identity contract

Approval rows identify a Mission with:

```text
mission_source_repo
mission_card_kind
mission_card_number
```

The selected `MissionCard` must expose the same identity directly:

```text
source_repo: str
kind: "issue" | "pull_request"
number: int
```

### Trusted source for `MissionCard.source_repo`

The existing GitHub Projects GraphQL query already projects `content.repository.nameWithOwner` for both Issue and PullRequest project items. `backend/app/mission/read_service.py::_card_from_project_item()` must read that projected field and populate `MissionCard.source_repo`.

Rules:

- derive from projected backend data only;
- make no new GitHub request;
- do not parse `MissionCard.url` in the browser or backend as the primary identity source;
- do not hardcode repository names;
- if projected repository data is absent or malformed, fail closed by omitting that card from governance-selectable results rather than inventing an identity;
- adding `source_repo` is an API/read-model change only and requires no migration.

The generated Orval `MissionCard` type must include `source_repo` before Checkpoint B begins.

## Mission-scoped approval list API

`GET /api/v1/mission/approvals` is currently a globally paginated list. Filtering only one client-side page would be incomplete.

Extend the **existing GET route** with optional query filters:

```text
mission_source_repo
mission_card_kind
mission_card_number
```

Contract:

- either all three Mission filters are absent, preserving current global-list behavior; or
- all three are supplied and are applied in SQL **before pagination**;
- partial Mission filter tuples are rejected with HTTP 422 by explicit request validation;
- newest-first API ordering remains unchanged;
- no new route, mutation, allowlist entry, trust boundary, or migration.

## Mission governance list behavior

For the selected card, render every item returned by the server-filtered list.

Summary fields:

- human-readable `action_key`;
- backend `status`;
- policy key/version;
- created timestamp;
- expiry timestamp when present.

Do not add quorum or mission effect to the list item solely for decoration. Those remain detail fields.

Presentation ordering:

1. pending requests first;
2. newest first within pending and terminal groups;
3. terminal requests remain visible.

Grouping uses backend `status` only. The frontend does not choose a “most important” approval.

## Caller-aware approval detail contract

Extend `ApprovalDetailResponse` with:

```text
can_decide: bool
current_principal_decision: CurrentPrincipalDecisionView | null
```

`CurrentPrincipalDecisionView` contains:

```text
decision_id: UUID
decision: str
reason: str | null
created_at: datetime
```

These fields are caller-specific and derived from authenticated server identity only.

### Shared authorization semantics

`can_decide` must use the same decision-eligibility semantics as mutation authorization. Do not implement a second authorization algorithm in `approval_read_service.py`.

Extract the smallest reusable server-side helper from the existing mutation path if needed. The helper must evaluate trusted principal type/roles/trust, policy eligibility, and request state. Every mutation still re-authorizes at command time; `can_decide` is only a UX capability signal.

### Current principal decision

`current_principal_decision` is the caller's currently-effective decision only. If that decision was superseded, return the successor decision. Never surface another principal's decision as the caller's own.

For an unregistered or disabled authenticated user, the read route must fail closed **without converting a normal governance read into a write**. Route semantics must be explicit and tested; do not silently grant capability.

## UI states

### No card selected

Keep the current Mission overview usable. Do not issue governance queries.

### Approvals loading

Show a localized governance skeleton; unrelated Mission overview sections remain usable.

### No approvals

Show a neutral empty state such as `No governance requests for this Mission.`

### Pending request

Show backend-derived quorum, missing requirements, decisions, lifecycle, and expiry.

- `can_decide=true` and `current_principal_decision=null` -> show **Approve** and **Reject**.
- `can_decide=true` and current decision present -> show current decision and **Change decision**.
- `can_decide=false` -> no mutation controls.

### Approved / Rejected / Expired / Superseded

Read-only. Show backend state, timestamps, decisions, mission effect, and lifecycle. No mutation controls.

### Read error

Keep `/mission` usable. Show localized `Unable to load governance status` with Retry.

### Mutation error

Keep previously loaded backend state visible and show the mutation error.

## Decision interaction

Approve/reject uses the existing decision mutation. Change-decision uses the existing supersede mutation with `supersedes_decision_id` from `current_principal_decision.decision_id`.

Each dialog includes:

- action being decided;
- proposed decision;
- optional reason;
- explicit confirmation.

Old decision rows remain immutable.

## Idempotency

All approval mutations require `Idempotency-Key`.

- Generate one key per confirmed user intent.
- Retry of the exact same uncertain delivery reuses the same key.
- Do not generate a key inside an HTTP retry loop.
- Changed payload/new confirmation is a new intent and gets a new key.

## Post-mutation reconciliation

Do not optimistically calculate status, quorum, or mission effect.

After success:

1. invalidate/refetch selected approval detail;
2. invalidate/refetch the selected Mission's approval list;
3. render backend-derived state.

## Component boundary

Mission-governance-specific components live under a focused namespace such as:

```text
frontend/src/components/mission/governance/
  MissionGovernancePanel.tsx
  ApprovalRequestCard.tsx
  ApprovalDetail.tsx
  DecisionDialog.tsx
```

The exact split may remain small, but do not extend the legacy Board approval model. Use generated Mission/Mission-approval Orval hooks/types.

## Backend implementation boundary

Checkpoint A may touch:

- `backend/app/schemas/mission.py`
- `backend/app/mission/read_service.py`
- `backend/tests/mission/test_read_service.py`
- `backend/app/schemas/mission_approvals.py`
- `backend/app/mission/approval_read_service.py`
- `backend/app/api/mission_approvals.py`
- `backend/app/mission/approval_service.py` only for the smallest shared eligibility extraction
- corresponding route/service/OpenAPI tests
- regenerated frontend Orval artifacts

Checkpoint A contains three read prerequisites:

1. `MissionCard.source_repo` from trusted projected `repository.nameWithOwner`;
2. server-side Mission filters on the existing approval list GET route;
3. caller-aware `can_decide` and `current_principal_decision` on approval detail.

No migration, GitHub write, new mutation route, allowlist expansion, policy change, or auth mechanism is authorized.

## Security and trust invariants

- Principal identity comes from server-verified authentication only.
- Client-supplied principal/role/trust/effective status/mission effect is never accepted.
- Mission filter fields choose which records to read; they grant no decision authority.
- `MissionCard.source_repo` comes from projected `repository.nameWithOwner`, not URL parsing.
- Human remains the only authenticated approval path in this slice; AI approval remains deferred.
- ADR-23 mutation hard-disable and closed allowlist remain unchanged.
- No Slice 5B code writes GitHub.

## Testing strategy

### Backend

Tests must prove:

- Issue and PR `MissionCard` rows expose the projected `repository.nameWithOwner` as `source_repo`.
- malformed/missing repository projection cannot fabricate a repo identity.
- OpenAPI exposes `MissionCard.source_repo` and regenerated Orval type includes it.
- all three Mission list filters are applied before pagination.
- partial Mission filter tuples are rejected.
- unfiltered list remains backward-compatible.
- eligible pending caller -> `can_decide=true`.
- ineligible role/type/trust or terminal state -> `can_decide=false`.
- unregistered/disabled caller behavior is explicit and fail-closed.
- `current_principal_decision` returns only caller's effective decision and follows supersession.
- read and write paths share eligibility semantics.
- existing quorum/missing requirements/mission effect/lifecycle behavior is unchanged.

### Frontend

Tests must prove:

- no selected card -> no governance query;
- selecting a card sends exactly `source_repo + kind + number` to the generated filtered list hook;
- frontend does not parse the GitHub URL for repo identity;
- empty/multiple/loading/error states render locally;
- pending requests group before terminal requests and preserve newest-first inside each group;
- selecting a request loads detail;
- `can_decide` drives mutation-control visibility;
- terminal requests remain read-only;
- successful mutation refetches detail and list;
- failed mutation preserves loaded state;
- exact-intent retry reuses the idempotency key;
- changed intent gets a new key;
- frontend performs no quorum or authorization derivation.

## Acceptance criteria

Slice 5B v1 is acceptable when an authenticated human can select a projected card on `/mission`, see every governance request for the card's trusted Mission identity, inspect backend-derived detail, approve/reject when authorized, and change their own effective decision while remaining in Mission context.

Engineering acceptance additionally requires:

- no new GitHub writes;
- no new mutation route or allowlist entry;
- no database migration;
- no semantic Board approval change;
- generated API types match backend OpenAPI;
- full repository validation and CodeQL pass before merge.

## Implementation checkpoints

### Checkpoint A — Mission/caller-aware read contract

- Add trusted `MissionCard.source_repo`.
- Add validated optional Mission filters to `GET /mission/approvals`.
- Add `can_decide` and `current_principal_decision`.
- Share mutation/read decision-eligibility semantics.
- Add backend/OpenAPI tests.
- Regenerate Orval.
- No Mission governance UI.

### Checkpoint B — Mission Governance read UX

- Make Cards selectable on `/mission`.
- Add Mission-scoped approval list and request detail.
- Add loading/empty/error/terminal states.
- No decision mutation controls.

### Checkpoint C — Human decision UX

- Approve/reject dialog.
- Optional reason.
- Per-intent idempotency lifecycle.
- Refetch detail + list after success.

### Checkpoint D — Change decision UX

- Change-decision action from caller-owned effective decision.
- Existing supersede mutation.
- Reuse idempotency/refetch/error semantics.

## Rollout and review

Each checkpoint follows TDD and exact-head governance:

1. start from latest `main` in an isolated branch/worktree;
2. write failing tests first;
3. implement only the checkpoint;
4. run targeted and repository validation;
5. open PR;
6. wait for CI + CodeQL on exact head;
7. independent exact-SHA Implementation Review;
8. Human approval before merge.

Checkpoint B starts only after Checkpoint A is merged and generated clients are authoritative.

## Self-review conclusions

The design now explicitly closes all repository-discovered prerequisites:

1. `/mission` has no dedicated Mission-detail route, so v1 uses selected-card context.
2. approval list pagination requires server-side Mission filtering before pagination.
3. current `MissionCard` lacked repo identity; v1 now requires `source_repo` derived from the already-projected GraphQL `repository.nameWithOwner`, eliminating URL parsing/hardcoding.

No implementation placeholder remains that changes API, trust, or scope boundaries.