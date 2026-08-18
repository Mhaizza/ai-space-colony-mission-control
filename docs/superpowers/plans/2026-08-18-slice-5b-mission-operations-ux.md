# Slice 5B Mission Operations UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Mission-centric governance UX on `/mission` that lets an authenticated human inspect all approval requests for a selected Mission card, see backend-derived eligibility/state, submit approve/reject decisions, and supersede their own effective decision without introducing a second frontend governance engine.

**Architecture:** Extend the existing Slice 5A read model rather than adding a parallel approval subsystem. The backend gains caller-aware detail fields plus optional Mission filters on the existing paginated list route; the frontend selects a card on `/mission`, queries approvals for that exact Mission identity, and renders a Mission-specific governance component tree. Mutations continue to use the existing three ADR-23 D8a routes and always re-authorize server-side.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy async sessions, Pydantic/SQLModel schemas, pytest, Next.js/React/TypeScript, TanStack Query, Orval-generated API hooks/types, Vitest/Testing Library.

**Spec:** `docs/superpowers/specs/2026-08-18-slice-5b-mission-operations-ux-design.md`

## Global Constraints

- Mission-centric only; no global approvals inbox.
- The legacy Board approval domain and `BoardApprovalsPanel` remain semantically unchanged.
- Backend remains authoritative for request status, quorum, missing requirements, effective decisions, authorization capability, supersession, and mission effect.
- Frontend must not reconstruct quorum, authorization, supersession chains, effective status, or mission effect.
- No GitHub writes.
- No new approval mutation routes.
- No new ADR-23 mutation allowlist entries.
- No database migration is expected or authorized by this plan.
- Authenticated AI approver UX remains out of scope.
- Use generated OpenAPI/Orval clients for Mission approval endpoints; do not add a handwritten parallel API client.
- Every mutation keeps write-path authorization even when the read model says `can_decide=true`.
- TDD is required for each task: failing test first, minimal implementation, passing test, then commit.
- Each implementation checkpoint is independently reviewable and should be merged before the next checkpoint begins.

---

## File Map

### Backend

- `backend/app/schemas/mission_approvals.py`
  - Add caller-aware detail view type and optional list-query contract if query models are introduced.
- `backend/app/mission/approval_read_service.py`
  - Add Mission filters to list query before pagination.
  - Build caller-specific `can_decide` and `current_principal_decision` fields from trusted backend state.
- `backend/app/mission/approval_service.py`
  - Extract/reuse the smallest shared decision-eligibility helper if eligibility is currently enforced inline.
  - Mutation behavior must remain unchanged apart from calling the shared helper.
- `backend/app/api/mission_approvals.py`
  - Accept optional Mission list filters.
  - Pass authenticated `AuthContext` into caller-aware detail read path.
- `backend/tests/mission/test_approval_read_service.py`
  - Extend read-service tests for filters, caller capability, effective own decision, and terminal states.
- `backend/tests/api/test_mission_approvals.py` (create if this exact file does not already exist; otherwise extend the existing Mission approvals API test module discovered during implementation)
  - Pin query/filter behavior, caller-aware detail schema, and auth/error behavior.

### Generated client

- `frontend/src/api/generated/mission-approvals/mission-approvals.ts`
- `frontend/src/api/generated/model/*`
  - Regenerated only from OpenAPI changes; do not hand-edit generated files.

### Frontend Mission governance

- `frontend/src/app/mission/page.tsx`
  - Add selected-card state and mount Mission Governance panel without creating a new Mission route.
- `frontend/src/components/mission/governance/MissionGovernancePanel.tsx`
  - Own filtered list query, request ordering, selected approval id, localized list/detail error handling.
- `frontend/src/components/mission/governance/ApprovalRequestCard.tsx`
  - Render scan-friendly list item using only list-response fields.
- `frontend/src/components/mission/governance/ApprovalDetail.tsx`
  - Own detail query and compose backend-derived detail views.
- `frontend/src/components/mission/governance/QuorumStatus.tsx`
  - Presentation-only renderer for backend-provided quorum fields.
- `frontend/src/components/mission/governance/DecisionHistory.tsx`
  - Presentation-only renderer for `effective_decisions`.
- `frontend/src/components/mission/governance/LifecycleHistory.tsx`
  - Presentation-only renderer for `lifecycle`.
- `frontend/src/components/mission/governance/DecisionActions.tsx`
  - Render controls strictly from `status`, `can_decide`, and `current_principal_decision`.
- `frontend/src/components/mission/governance/DecisionDialog.tsx`
  - Capture approve/reject/change intent, optional reason, and confirmation.
- `frontend/src/components/mission/governance/idempotency.ts`
  - Small pure helper for one-key-per-user-intent semantics.
- `frontend/src/components/mission/governance/__tests__/MissionGovernancePanel.test.tsx`
- `frontend/src/components/mission/governance/__tests__/ApprovalDetail.test.tsx`
- `frontend/src/components/mission/governance/__tests__/DecisionActions.test.tsx`
- `frontend/src/components/mission/governance/__tests__/idempotency.test.ts`
  - Focused component/pure-function tests; avoid putting all behavior into one oversized page test.

---

# Checkpoint A — Caller-aware read model + Mission filters

## Task 1: Add server-side Mission filters to the existing list route

**Files:**
- Modify: `backend/app/api/mission_approvals.py`
- Modify: `backend/app/mission/approval_read_service.py`
- Test: `backend/tests/mission/test_approval_read_service.py`
- Test: existing Mission approvals API test module, or create `backend/tests/api/test_mission_approvals.py` if none exists

**Interfaces:**
- Consumes: existing `GET /api/v1/mission/approvals` and `ApprovalListItem`.
- Produces: the same route and response type, plus optional query parameters:
  - `mission_source_repo: str | None`
  - `mission_card_kind: MissionCardKind | None`
  - `mission_card_number: int | None`
- Filtering occurs in SQL before `paginate(...)`.

- [ ] **Step 1: Write failing read-service tests for exact Mission filtering**

Add tests that create approvals for at least two Mission identities and assert the selected triple returns only matching rows. The service call should have an explicit shape:

```python
page = await list_approvals(
    session,
    mission_source_repo="Mhaizza/ai-space-colony-sim",
    mission_card_kind="pull_request",
    mission_card_number=172,
)
assert {item.mission_card_number for item in page.items} == {172}
```

Also test that calling with all three filters `None` preserves current global-list behavior.

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
cd backend
uv run pytest tests/mission/test_approval_read_service.py -q
```

Expected: FAIL because `list_approvals` does not yet accept Mission filter arguments and/or the SQL query does not filter.

- [ ] **Step 3: Implement filter arguments in `approval_read_service.list_approvals`**

Use an explicit signature:

```python
async def list_approvals(
    session: AsyncSession,
    *,
    mission_source_repo: str | None = None,
    mission_card_kind: str | None = None,
    mission_card_number: int | None = None,
) -> DefaultLimitOffsetPage[ApprovalListItem]:
```

Build the existing `select(McApprovalRequest, McApprovalPolicy)` statement, then add `.where(...)` clauses only for non-`None` filters before the existing order/pagination path. Do not filter the returned page in Python.

- [ ] **Step 4: Add failing API tests for query parameter forwarding**

Test requests such as:

```text
GET /api/v1/mission/approvals?mission_source_repo=Mhaizza%2Fai-space-colony-sim&mission_card_kind=pull_request&mission_card_number=172
```

Assert 200 and that rows belong only to the requested Mission identity. Add a no-filter request asserting backward-compatible global listing.

- [ ] **Step 5: Implement optional FastAPI query parameters**

Update `list_approvals(...)` in `backend/app/api/mission_approvals.py` to accept the three optional typed query parameters and forward them verbatim to `approval_read_service.list_approvals(...)`. Do not add a new route.

- [ ] **Step 6: Run focused backend tests**

```bash
cd backend
uv run pytest tests/mission/test_approval_read_service.py tests/api/test_mission_approvals.py -q
```

If the repository uses a differently named existing API test module, substitute that exact file discovered locally rather than creating a duplicate suite.

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

```bash
git add backend/app/api/mission_approvals.py backend/app/mission/approval_read_service.py backend/tests/mission/test_approval_read_service.py backend/tests/api/test_mission_approvals.py
git commit -m "feat: filter mission approvals by mission identity"
```

If the API test module has a different existing path, stage that path instead.

## Task 2: Extract shared decision eligibility and add caller-aware detail fields

**Files:**
- Modify: `backend/app/schemas/mission_approvals.py`
- Modify: `backend/app/mission/approval_read_service.py`
- Modify: `backend/app/mission/approval_service.py`
- Modify: `backend/app/api/mission_approvals.py`
- Test: `backend/tests/mission/test_approval_read_service.py`
- Test: existing approval service tests covering submit/supersede authorization
- Test: Mission approvals API test module

**Interfaces:**
- Consumes: `ResolvedPrincipal`, validated policy definition, `McApprovalRequest`, `effective_decisions(...)`.
- Produces:

```python
class CurrentPrincipalDecisionView(SQLModel):
    decision_id: UUID
    decision: str
    reason: str | None
    created_at: datetime

class ApprovalDetailResponse(SQLModel):
    # existing fields unchanged
    can_decide: bool
    current_principal_decision: CurrentPrincipalDecisionView | None
```

- Produces one shared server-side eligibility helper used by both read and mutation paths. Prefer a pure helper that accepts already-resolved trusted state rather than opening its own session.

- [ ] **Step 1: Write failing tests for caller capability**

Cover at minimum:

```python
assert eligible_detail.can_decide is True
assert ineligible_role_detail.can_decide is False
assert terminal_detail.can_decide is False
```

Add cases for principal type/trust restrictions already enforced by the policy service. The expected outcome must match the mutation service's current authorization semantics exactly.

- [ ] **Step 2: Write failing tests for caller effective decision**

Create two principals with decisions on the same request. For caller A:

```python
assert detail.current_principal_decision is not None
assert detail.current_principal_decision.decision_id == caller_a_effective.id
```

After superseding caller A's decision, assert the field points to the successor id and never caller B's decision or caller A's superseded record.

- [ ] **Step 3: Run focused tests and verify failure**

```bash
cd backend
uv run pytest tests/mission/test_approval_read_service.py -q
```

Expected: FAIL because the schema/read service does not expose the caller fields.

- [ ] **Step 4: Extract the smallest shared eligibility helper from write-path checks**

Do not duplicate policy/role/trust/request-state logic in the read service. Extract a pure helper in the approval domain, using the exact existing rules from `approval_service.py`. Its interface should be shaped like:

```python
def can_principal_decide(
    *,
    principal: ResolvedPrincipal,
    request: McApprovalRequest,
    policy_definition: ApprovalPolicyDefinition,
) -> bool:
    ...
```

If write-path eligibility additionally depends on facts not represented above, include those exact trusted facts in the signature rather than weakening the rule. Mutation functions must call this helper and preserve their existing error codes/messages when it returns false.

- [ ] **Step 5: Add schema types and caller-aware read-service input**

Add `CurrentPrincipalDecisionView` and the two new fields to `ApprovalDetailResponse`.

Change the detail service to receive the authenticated caller context, for example:

```python
async def get_approval_detail(
    session: AsyncSession,
    request_id: UUID,
    *,
    auth: AuthContext,
) -> ApprovalDetailResponse | None:
```

Resolve the principal using the existing `resolve_principal(auth, session)`. Use the same effective decision set already loaded for quorum. Find the decision whose `principal_id` equals the resolved principal id; because `effective_decisions(...)` already removes superseded records, this directly yields the caller's effective decision or `None`.

- [ ] **Step 6: Define fail-closed read semantics for unregistered/disabled callers in tests and implementation**

Use the existing authenticated HTTP request semantics; do not invent a client-supplied principal fallback. The chosen behavior must be explicit and tested. Preferred behavior for the detail route is to translate existing `PrincipalResolutionError` through the same `to_http_exception(...)` mapping used by mutation routes, so caller-specific fields are never fabricated for an unresolved principal.

- [ ] **Step 7: Wire authenticated context through the detail route**

Replace the current ignored `_ = auth` path with:

```python
try:
    detail = await approval_read_service.get_approval_detail(
        session,
        request_id,
        auth=auth,
    )
except PrincipalResolutionError as exc:
    raise to_http_exception(exc) from exc
```

Keep the existing 404 behavior for a missing request.

- [ ] **Step 8: Re-run write-path authorization tests**

Run the approval-service tests that cover role/type/trust/terminal-state errors and prove extracting the helper did not change mutation behavior.

- [ ] **Step 9: Run Checkpoint A backend tests**

```bash
cd backend
uv run pytest tests/mission/test_approval_read_service.py -q
uv run pytest tests -q -k "approval and (mission or decision or principal)"
```

Expected: PASS.

- [ ] **Step 10: Commit Task 2**

```bash
git add backend/app/schemas/mission_approvals.py backend/app/mission/approval_read_service.py backend/app/mission/approval_service.py backend/app/api/mission_approvals.py backend/tests
git commit -m "feat: expose caller approval capability"
```

## Task 3: Regenerate and pin the Mission approvals client contract

**Files:**
- Modify generated: `frontend/src/api/generated/mission-approvals/mission-approvals.ts`
- Modify generated: `frontend/src/api/generated/model/*`
- Test: existing OpenAPI/schema tests in backend

**Interfaces:**
- Consumes: updated backend OpenAPI schema.
- Produces generated TypeScript params/types containing Mission list filters, `canDecide`/`can_decide` according to the repository's Orval naming mode, and `currentPrincipalDecision`/`current_principal_decision` according to generated conventions.

- [ ] **Step 1: Add/adjust backend OpenAPI assertion tests before regeneration**

Pin that:

- list route exposes all three optional Mission filters;
- detail response requires `can_decide`;
- detail response includes nullable `current_principal_decision` with `decision_id`, `decision`, `reason`, `created_at`.

- [ ] **Step 2: Run OpenAPI tests and verify they fail before regeneration/schema completion if applicable**

Use the repository's existing OpenAPI test command/module discovered locally.

- [ ] **Step 3: Regenerate Orval using the repository command**

Use the existing documented generation command from the repo (`package.json`, Makefile, or AGENTS.md). Do not hand-edit generated files.

- [ ] **Step 4: Type-check generated contract**

```bash
cd frontend
npm run typecheck
```

If the repo names the script differently, use the exact existing TypeScript check script.

Expected: PASS.

- [ ] **Step 5: Run `git diff --check` and inspect generated diff**

Verify generated changes are limited to the Mission approval contract/models expected from the schema change.

- [ ] **Step 6: Commit Task 3**

```bash
git add backend/tests frontend/src/api/generated
git commit -m "chore: regenerate mission approval client"
```

### Checkpoint A review gate

Before starting Checkpoint B:

```bash
git diff --check
make check
```

Open a PR containing only Checkpoint A. Wait for CI + CodeQL at the exact head SHA. Independent review must confirm no new mutation route, no allowlist change, no migration, and no changed write semantics. Merge only after Human approval.

---

# Checkpoint B — Mission Governance read UX

## Task 4: Add Mission-card selection on `/mission`

**Files:**
- Modify: `frontend/src/app/mission/page.tsx`
- Test: create `frontend/src/app/mission/__tests__/page.test.tsx` only if the repo's current frontend test organization supports colocated app tests; otherwise extend the established Mission page test location discovered locally.

**Interfaces:**
- Consumes: existing `MissionCard` values from `overview.workflow.cards`.
- Produces selected Mission identity:

```ts
type SelectedMission = {
  sourceRepo: string;
  cardKind: MissionCard["kind"];
  cardNumber: number;
  title: string | null;
};
```

Use the actual repo/source field available in the current `MissionCard`/overview model. If `MissionCard` itself does not carry source repo, derive it only from an existing backend-provided Mission field in the same overview response; do not hardcode a repository name.

- [ ] **Step 1: Write a failing UI test for selecting a Mission card**

Render the Mission page with at least two cards. Click one card and assert the governance region receives that card's exact backend Mission identity.

- [ ] **Step 2: Run the focused frontend test and verify failure**

```bash
cd frontend
npm test -- --run <exact-test-file>
```

Expected: FAIL because cards are not selectable and governance is not mounted.

- [ ] **Step 3: Add selected-card state without introducing a new route**

Keep `/mission` as the surface. Make cards keyboard-accessible buttons or interactive elements with clear selected styling and `aria-pressed`/equivalent semantics.

- [ ] **Step 4: Mount a placeholder `MissionGovernancePanel` only when a card is selected**

Pass exact Mission identity as props; do not fetch approval data in the page component itself.

- [ ] **Step 5: Run focused tests and typecheck**

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

```bash
git add frontend/src/app/mission frontend/src/components/mission/governance
git commit -m "feat: select mission governance context"
```

## Task 5: Implement filtered approval list + request cards

**Files:**
- Create: `frontend/src/components/mission/governance/MissionGovernancePanel.tsx`
- Create: `frontend/src/components/mission/governance/ApprovalRequestCard.tsx`
- Create: `frontend/src/components/mission/governance/__tests__/MissionGovernancePanel.test.tsx`

**Interfaces:**
- Consumes Mission identity props and generated list hook.
- Produces ordered approval request cards and selected `requestId`.
- Ordering rule: pending first; within same pending/non-pending group, `created_at` newest first. Do not derive a single “active approval.”

- [ ] **Step 1: Write failing tests for query params and list states**

Assert the generated list hook receives all three Mission filters. Test loading, localized read error with Retry, empty state, and multiple returned approvals.

- [ ] **Step 2: Write failing ordering test**

Given terminal-newer, pending-older, pending-newer rows, assert rendered order is pending-newer, pending-older, terminal-newer.

- [ ] **Step 3: Implement `MissionGovernancePanel`**

Use generated query hooks only. Keep backend pagination intact. Because filters are server-side, the returned page represents the selected Mission rather than a client-filtered slice of a global page.

For v1, request card summary uses only list fields already guaranteed by `ApprovalListItem`: status, action key, policy key/version, created/expiry, Mission identity. Do not compute quorum or mission effect in the list.

- [ ] **Step 4: Implement `ApprovalRequestCard` as presentation-only**

The card must not call APIs or infer authorization. Clicking selects its `request_id` for detail.

- [ ] **Step 5: Run focused tests and typecheck**

```bash
cd frontend
npm test -- --run frontend/src/components/mission/governance/__tests__/MissionGovernancePanel.test.tsx
npm run typecheck
```

Use repository-relative test paths accepted by the configured Vitest command.

- [ ] **Step 6: Commit Task 5**

```bash
git add frontend/src/components/mission/governance
git commit -m "feat: list mission governance approvals"
```

## Task 6: Implement read-only approval detail states

**Files:**
- Create: `frontend/src/components/mission/governance/ApprovalDetail.tsx`
- Create: `frontend/src/components/mission/governance/QuorumStatus.tsx`
- Create: `frontend/src/components/mission/governance/DecisionHistory.tsx`
- Create: `frontend/src/components/mission/governance/LifecycleHistory.tsx`
- Create: `frontend/src/components/mission/governance/__tests__/ApprovalDetail.test.tsx`
- Modify: `MissionGovernancePanel.tsx`

**Interfaces:**
- Consumes: generated detail hook and full `ApprovalDetailResponse`.
- Produces read-only detail rendering for pending, approved, rejected, expired, superseded, loading, and read-error states.

- [ ] **Step 1: Write failing tests for backend-derived rendering**

Use fixtures that explicitly set `quorum_satisfied`, `quorum_requirements`, `missing_requirements`, `effective_decisions`, `lifecycle`, `resolved_at`, `expires_at`, and `mission_effect`. Assert displayed values match fixture fields directly.

- [ ] **Step 2: Add a test that guards against frontend quorum inference**

Provide intentionally non-intuitive fixture data, e.g. two approving decisions while `quorum_satisfied=false` and a non-empty `missing_requirements`. Assert UI shows the backend false/missing state rather than inferring success from decision count.

- [ ] **Step 3: Implement detail composition**

Keep `QuorumStatus`, `DecisionHistory`, and `LifecycleHistory` presentation-only. No role matching, supersession traversal, or policy evaluation is allowed in these components.

- [ ] **Step 4: Implement terminal-state read-only presentation**

Approved/rejected/expired/superseded render status and history with no mutation controls. At Checkpoint B, pending is also read-only because mutation UX is not yet in scope.

- [ ] **Step 5: Run focused tests and typecheck**

Expected: PASS.

- [ ] **Step 6: Commit Task 6**

```bash
git add frontend/src/components/mission/governance
git commit -m "feat: show mission approval details"
```

### Checkpoint B review gate

Run:

```bash
git diff --check
make check
```

Open a PR containing only Checkpoint B. Independent review must verify `/mission` remains the only new Mission governance surface, all approval requests are visible for a selected card, no Board approval component/model was reused as the Mission approval source, and the frontend performs no governance calculations. Merge only after CI + CodeQL + Human approval.

---

# Checkpoint C — Human approve/reject UX

## Task 7: Add per-intent idempotency lifecycle and decision dialog

**Files:**
- Create: `frontend/src/components/mission/governance/idempotency.ts`
- Create: `frontend/src/components/mission/governance/__tests__/idempotency.test.ts`
- Create: `frontend/src/components/mission/governance/DecisionDialog.tsx`
- Create: `frontend/src/components/mission/governance/DecisionActions.tsx`
- Create: `frontend/src/components/mission/governance/__tests__/DecisionActions.test.tsx`
- Modify: `ApprovalDetail.tsx`

**Interfaces:**
- Consumes: `detail.status`, `detail.can_decide`, `detail.current_principal_decision`.
- Produces approve/reject intent payload:

```ts
type DecisionIntent = {
  requestId: string;
  decision: "approve" | "reject";
  reason: string | null;
  idempotencyKey: string;
};
```

- [ ] **Step 1: Write failing pure tests for key lifecycle**

Prove:

- opening a new confirmed intent creates one key;
- retrying the same unchanged intent returns/reuses that same key;
- changing decision or reason and reconfirming produces a new key;
- successful completion clears the retained retry intent.

Use `crypto.randomUUID()` behind a tiny injectable/default key factory so the helper can be tested deterministically without mocking the entire browser crypto object.

- [ ] **Step 2: Implement the minimal idempotency intent helper**

Keep it local to Mission governance. It should manage identity of a confirmed user intent, not retry policy itself. Do not generate keys inside the API mutation function on every attempt.

- [ ] **Step 3: Write failing control-visibility tests**

Assert:

- pending + `can_decide=true` + `current_principal_decision=null` -> Approve and Reject visible;
- pending + `can_decide=false` -> no mutation controls;
- terminal + `can_decide=true` fixture -> still no mutation controls;
- pending + current decision -> no fresh Approve/Reject controls in Checkpoint C; Change Decision belongs to Checkpoint D.

- [ ] **Step 4: Implement `DecisionActions` and `DecisionDialog`**

The dialog shows action, proposed decision, optional reason, cancel, and explicit confirm. It receives backend capability/state via props and does not inspect roles or decisions to calculate eligibility.

- [ ] **Step 5: Write failing mutation tests**

Mock the generated submit-decision mutation hook. Assert the request includes the confirmed `decision`, optional `reason`, and the retained `Idempotency-Key`. Simulate a transport failure, click Retry without changing payload, and assert the same header value is reused.

- [ ] **Step 6: Implement approve/reject mutation using generated client**

Use the existing generated `POST /mission/approvals/{request_id}/decisions` hook. Do not add fetch/axios wrappers.

- [ ] **Step 7: Invalidate/refetch backend truth after success**

After success:

- invalidate/refetch the selected approval detail query;
- invalidate/refetch the filtered approval list query because request status/summary may change;
- clear the retained completed intent key.

Do not optimistically set quorum/status/mission effect in local state.

- [ ] **Step 8: Keep prior state visible on mutation error**

Render the mutation error near the decision controls while preserving the loaded detail.

- [ ] **Step 9: Run focused tests, lint, and typecheck**

Expected: PASS.

- [ ] **Step 10: Commit Task 7**

```bash
git add frontend/src/components/mission/governance
git commit -m "feat: decide mission approvals"
```

### Checkpoint C review gate

Run full validation and open a Checkpoint C PR. Review must verify one key per confirmed user intent, same-key retry for uncertain delivery, no optimistic governance calculation, and generated client usage only.

---

# Checkpoint D — Change own effective decision

## Task 8: Add supersede decision UX

**Files:**
- Modify: `frontend/src/components/mission/governance/DecisionActions.tsx`
- Modify: `frontend/src/components/mission/governance/DecisionDialog.tsx`
- Modify: `frontend/src/components/mission/governance/__tests__/DecisionActions.test.tsx`
- Modify: `frontend/src/components/mission/governance/idempotency.ts` only if the existing intent type needs an explicit operation discriminator

**Interfaces:**
- Consumes: `current_principal_decision.decision_id` from backend read model.
- Produces supersede payload through existing generated route:

```ts
{
  supersedes_decision_id: detail.current_principal_decision.decision_id,
  decision: "approve" | "reject",
  reason: string | null,
}
```

plus one retained `Idempotency-Key` for that confirmed change intent.

- [ ] **Step 1: Write failing tests for Change decision visibility**

Assert pending + `can_decide=true` + non-null current decision shows current caller decision and `Change decision`. Assert null caller decision does not show Change decision.

- [ ] **Step 2: Write failing supersede payload test**

Mock the generated supersede hook and assert `supersedes_decision_id` comes exactly from `current_principal_decision.decision_id`; it must never be discovered by scanning `effective_decisions` in the frontend.

- [ ] **Step 3: Reuse the decision dialog in change mode**

Display the current decision, new proposed decision, optional reason, and explicit confirmation. A changed payload is a new intent and receives a fresh idempotency key.

- [ ] **Step 4: Implement supersede mutation via generated hook**

Use existing `POST /mission/approvals/{request_id}/supersede`. Keep backend 403/409/etc. errors authoritative if state changes between read and write.

- [ ] **Step 5: Refetch backend truth after success**

Invalidate/refetch detail and filtered list exactly as in Checkpoint C. Do not mutate the old decision record in frontend state.

- [ ] **Step 6: Add stale-read race test**

Simulate `can_decide=true` detail followed by a mutation rejection from backend because request state changed. Assert UI keeps old loaded data, surfaces the error, and offers a read refresh/retry rather than overriding backend state.

- [ ] **Step 7: Run focused frontend tests**

Expected: PASS.

- [ ] **Step 8: Run final repository validation**

```bash
git diff --check
make docs-check
make check
```

Expected: PASS. If the environment cannot run a command, record the exact blocker rather than claiming success.

- [ ] **Step 9: Commit Task 8**

```bash
git add frontend/src/components/mission/governance
git commit -m "feat: change mission approval decisions"
```

### Checkpoint D review gate

Open the final Slice 5B v1 checkpoint PR. Wait for CI + CodeQL at the exact head SHA. Independent implementation review must verify that supersession uses only caller-owned backend-provided `decision_id`, old decisions remain immutable, and no route/ADR/GitHub capability expanded. Merge only after Human approval.

---

# Final Acceptance Review

After Checkpoint D is merged, verify `main` against the design spec:

- Selecting a Mission card on `/mission` exposes Governance without creating a new global inbox.
- Filtered list fetches the selected Mission identity server-side before pagination.
- Every approval request for the selected Mission page is rendered; pending requests sort first.
- Selecting a request loads backend-derived detail.
- Eligible pending callers can approve/reject.
- Callers with an effective decision can change it through supersede.
- Terminal requests are read-only.
- Read and mutation errors remain localized and do not break the Mission page.
- Frontend contains no policy evaluator, quorum matcher, role-based authorization heuristic, or supersession-chain reconstruction.
- Existing Board approvals retain their prior API/component semantics.
- No migration, no new mutation route, no mutation allowlist expansion, and no GitHub write capability were introduced.
- `git diff --check`, repository validation, CI, and CodeQL are green at the reviewed exact heads.

# Execution Sequence

Implement and merge in this order only:

```text
Checkpoint A
  Task 1 filters
  Task 2 caller-aware detail/shared eligibility
  Task 3 OpenAPI/Orval regeneration
  -> exact-SHA review + merge

Checkpoint B
  Task 4 Mission selection
  Task 5 filtered approval cards
  Task 6 read-only detail
  -> exact-SHA review + merge

Checkpoint C
  Task 7 approve/reject + idempotency
  -> exact-SHA review + merge

Checkpoint D
  Task 8 supersede own decision
  -> exact-SHA review + merge
```

Do not start a later checkpoint on an unmerged earlier checkpoint branch. Each checkpoint starts from the latest reviewed `main` in a fresh isolated worktree/branch.
