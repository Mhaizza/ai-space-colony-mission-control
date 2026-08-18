# Slice 5B Mission Operations UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Mission-centric governance UX on `/mission` that uses a trusted projected Mission identity, renders backend-derived approval state, allows authorized human decisions, and supports caller-owned decision supersession without creating a frontend governance engine.

**Architecture:** Checkpoint A completes the read contract first: `MissionCard.source_repo` comes from projected `repository.nameWithOwner`, `GET /mission/approvals` gains validated Mission filters applied before pagination, and approval detail gains caller-aware capability fields backed by shared server authorization semantics. Checkpoints B-D then add read-only Mission governance UX, approve/reject, and change-decision flows using generated Orval clients and existing ADR-23 mutation routes.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy async sessions, SQLModel schemas, pytest, Next.js/React/TypeScript, TanStack Query, Orval-generated hooks/types, Vitest/Testing Library.

**Spec:** `docs/superpowers/specs/2026-08-18-slice-5b-mission-operations-ux-design.md`

## Global Constraints

- Mission-centric only; no global approvals inbox.
- `/mission` remains the v1 surface; do not add a Mission route tree.
- Legacy Board approval semantics remain unchanged.
- Backend is authoritative for request status, quorum, missing requirements, effective decisions, caller capability, supersession, and mission effect.
- Frontend must not reconstruct quorum, authorization, supersession chains, effective status, or mission effect.
- `MissionCard.source_repo` must come from projected `content.repository.nameWithOwner`; never parse `MissionCard.url` or hardcode a repository name.
- No new GitHub request is needed to derive `source_repo`.
- No GitHub writes.
- No new approval mutation routes or ADR-23 allowlist entries.
- No database migration.
- Existing mutation routes still re-authorize at command time.
- Use generated Orval clients/types; do not add a parallel hand-written Mission approvals client.
- TDD: failing test first, minimal implementation, targeted pass, then broader validation.
- Each checkpoint gets its own branch/PR/exact-head review and Human merge approval.

---

## File Structure Map

### Existing backend files to modify

- `backend/app/schemas/mission.py` — add required `MissionCard.source_repo` read-model field.
- `backend/app/mission/read_service.py` — derive `source_repo` from projected GraphQL project-item payload.
- `backend/app/schemas/mission_approvals.py` — add `CurrentPrincipalDecisionView` and caller-aware detail fields.
- `backend/app/mission/approval_read_service.py` — Mission filters and caller-aware detail assembly.
- `backend/app/api/mission_approvals.py` — validate/pass Mission filter tuple and pass authenticated caller to detail read path.
- `backend/app/mission/approval_service.py` — only if needed to extract/reuse the existing decision-eligibility rule; no semantic changes.

### Existing backend tests to modify

- `backend/tests/mission/test_read_service.py` — trusted `MissionCard.source_repo` coverage.
- `backend/tests/mission/test_approval_read_service.py` — filtered list and caller-aware detail tests.
- `backend/tests/mission/test_mission_approvals_api.py` — filter validation/OpenAPI/route contract tests.
- `backend/tests/mission/test_mission_approvals_security.py` — fail-closed caller-capability/security boundary tests where appropriate.

### Generated frontend artifacts

- `frontend/src/api/generated/model/missionCard.ts` — generated `source_repo` field.
- `frontend/src/api/generated/model/currentPrincipalDecisionView.ts` — generated caller decision view.
- `frontend/src/api/generated/model/approvalDetailResponse.ts` — generated caller-aware fields.
- `frontend/src/api/generated/mission-approvals/mission-approvals.ts` — generated filtered list/detail/mutation signatures.
- Other generated model/index files changed by Orval are committed as generated output; never hand-edit them.

### Frontend implementation files

Prefer a small focused component boundary:

- Create `frontend/src/components/mission/governance/MissionGovernancePanel.tsx` — selected Mission approval list, selection, localized loading/empty/error states.
- Create `frontend/src/components/mission/governance/ApprovalDetail.tsx` — backend-derived detail rendering and read-only states.
- Create `frontend/src/components/mission/governance/DecisionDialog.tsx` — approve/reject/change confirmation and optional reason.
- Create `frontend/src/components/mission/governance/idempotency.ts` — per-user-intent idempotency state helper if keeping it separate makes retry behavior testable.
- Modify `frontend/src/app/mission/page.tsx` — make cards selectable and mount governance context.
- Add colocated frontend tests following the repo's existing test naming/location convention; if the repo convention is `*.test.tsx` beside components, use that convention rather than introducing a new test directory.

---

# Checkpoint A — Mission/caller-aware read contract

Checkpoint A is backend/read-contract only plus generated client regeneration. **Do not add Mission governance UI in this checkpoint.**

### Task A1: Expose trusted `MissionCard.source_repo`

**Files:**
- Modify: `backend/app/schemas/mission.py`
- Modify: `backend/app/mission/read_service.py`
- Test: `backend/tests/mission/test_read_service.py`

**Interfaces:**
- Consumes projected project-item shape already fetched by `PROJECT_ITEMS_QUERY`: `content.repository.nameWithOwner`.
- Produces `MissionCard.source_repo: str` in the Mission overview API/OpenAPI.

- [ ] **Step 1: Add failing Issue and PR card tests**

Add fixtures/project-item payloads whose `content` includes:

```python
"repository": {"id": "R_1", "nameWithOwner": "Mhaizza/ai-space-colony-sim"}
```

Assert both Issue and PullRequest cards expose:

```python
assert card.source_repo == "Mhaizza/ai-space-colony-sim"
```

Also add malformed/missing repository coverage asserting no fabricated repo identity is returned.

- [ ] **Step 2: Run the targeted tests and verify failure**

Run:

```bash
cd backend
pytest tests/mission/test_read_service.py -k "source_repo or workflow_summary" -v
```

Expected: FAIL because `MissionCard` has no `source_repo` and `_card_from_project_item()` does not populate it.

- [ ] **Step 3: Add the schema field**

In `MissionCard` add:

```python
source_repo: str
```

Do not make this optional in the public model; a card without a trusted repo identity must not be governance-selectable.

- [ ] **Step 4: Derive from projected repository data**

In `_card_from_project_item()` read only:

```python
repository = content.get("repository")
if not isinstance(repository, dict):
    return None
source_repo = repository.get("nameWithOwner")
if not isinstance(source_repo, str) or not source_repo.strip():
    return None
```

Then construct:

```python
MissionCard(
    source_repo=source_repo,
    number=number,
    kind=kind,
    ...,
)
```

Do not parse `url` and do not call GitHub.

- [ ] **Step 5: Run targeted tests**

```bash
cd backend
pytest tests/mission/test_read_service.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/mission.py backend/app/mission/read_service.py backend/tests/mission/test_read_service.py
git commit -m "feat(slice5b): expose trusted mission card source repo"
```

### Task A2: Add Mission-scoped filters before pagination

**Files:**
- Modify: `backend/app/mission/approval_read_service.py`
- Modify: `backend/app/api/mission_approvals.py`
- Test: `backend/tests/mission/test_approval_read_service.py`
- Test: `backend/tests/mission/test_mission_approvals_api.py`

**Interfaces:**
- Produces optional GET query parameters `mission_source_repo`, `mission_card_kind`, `mission_card_number`.
- Unfiltered calls remain backward-compatible.
- Partial tuples are invalid.

- [ ] **Step 1: Write failing service tests for pre-pagination filtering**

Seed approval rows for at least two Mission identities and enough rows to prove filtering is part of the SQL statement, not a post-pagination Python filter.

Expected service call shape:

```python
await list_approvals(
    session,
    mission_source_repo="Mhaizza/ai-space-colony-sim",
    mission_card_kind="pull_request",
    mission_card_number=172,
)
```

Assert only the exact identity is returned.

- [ ] **Step 2: Run service tests and verify failure**

```bash
cd backend
pytest tests/mission/test_approval_read_service.py -k "filter or list_approvals" -v
```

- [ ] **Step 3: Extend `list_approvals()` signature and SQL**

Use this interface:

```python
async def list_approvals(
    session: AsyncSession,
    *,
    mission_source_repo: str | None = None,
    mission_card_kind: str | None = None,
    mission_card_number: int | None = None,
) -> DefaultLimitOffsetPage[ApprovalListItem]:
```

When all three are non-None, append `where(...)` predicates to the statement **before** `paginate(...)`.

- [ ] **Step 4: Write failing API tests for tuple validation**

Cover:

```text
no filters -> 200
all 3 filters -> 200
repo only -> 422
repo + kind only -> 422
kind + number only -> 422
```

- [ ] **Step 5: Implement explicit route validation**

Add optional typed query parameters to `list_approvals()` route. Before calling the service:

```python
parts = (mission_source_repo, mission_card_kind, mission_card_number)
if any(part is not None for part in parts) and not all(part is not None for part in parts):
    raise HTTPException(status_code=422, detail={...})
```

Use the existing `MissionCardKind` type for `mission_card_kind` so invalid kinds fail validation.

- [ ] **Step 6: Run service + API tests**

```bash
cd backend
pytest tests/mission/test_approval_read_service.py tests/mission/test_mission_approvals_api.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/mission/approval_read_service.py backend/app/api/mission_approvals.py backend/tests/mission/test_approval_read_service.py backend/tests/mission/test_mission_approvals_api.py
git commit -m "feat(slice5b): filter approvals by mission identity"
```

### Task A3: Share decision eligibility between read and write paths

**Files:**
- Modify: `backend/app/mission/approval_service.py`
- Modify: `backend/app/mission/approval_read_service.py`
- Modify: `backend/app/schemas/mission_approvals.py`
- Modify: `backend/app/api/mission_approvals.py`
- Test: `backend/tests/mission/test_approval_read_service.py`
- Test: `backend/tests/mission/test_mission_approvals_security.py`

**Interfaces:**
- Produces `CurrentPrincipalDecisionView`.
- Produces `ApprovalDetailResponse.can_decide: bool`.
- Produces `ApprovalDetailResponse.current_principal_decision: CurrentPrincipalDecisionView | None`.
- Read and mutation paths consume one shared decision-eligibility rule.

- [ ] **Step 1: Inspect the existing mutation eligibility checks before editing**

Locate exactly where `submit_decision()` validates request state, principal type, role, trust, and policy eligibility. Preserve behavior byte-for-byte semantically; this task is extraction/reuse, not policy redesign.

- [ ] **Step 2: Write failing tests for caller-aware detail**

Cover at minimum:

```text
eligible pending caller -> can_decide true
ineligible role -> false
ineligible trust -> false
terminal request -> false
caller with no effective decision -> current_principal_decision null
caller with effective decision -> its decision view
caller supersedes -> successor returned
other principal decision -> never returned as caller decision
```

Also pin fail-closed behavior for unregistered/disabled authenticated callers according to the route semantics selected from existing principal error handling.

- [ ] **Step 3: Run tests and verify failure**

```bash
cd backend
pytest tests/mission/test_approval_read_service.py tests/mission/test_mission_approvals_security.py -k "can_decide or current_principal or eligibility" -v
```

- [ ] **Step 4: Add response schema**

In `mission_approvals.py`:

```python
class CurrentPrincipalDecisionView(SQLModel):
    decision_id: UUID
    decision: str
    reason: str | None
    created_at: datetime
```

Add to `ApprovalDetailResponse`:

```python
can_decide: bool
current_principal_decision: CurrentPrincipalDecisionView | None
```

- [ ] **Step 5: Extract the smallest shared eligibility helper**

Name the helper based on the existing service vocabulary. It must accept already-resolved trusted server objects rather than raw client values. Example shape:

```python
def can_principal_decide(
    *,
    principal: ResolvedPrincipal,
    request: McApprovalRequest,
    policy_definition: ApprovalPolicyDefinition,
) -> bool:
    ...
```

If the existing mutation path needs richer error codes than a bool, expose a result/validator that both paths can consume while preserving write-path errors. Do not implement two algorithms.

- [ ] **Step 6: Make detail caller-aware**

Pass `AuthContext` into the detail read path from the GET route, resolve the principal from authenticated server context, use the shared eligibility semantics, and find the caller's entry in the already-supersession-aware `effective_decisions()` result.

Do not infer caller identity from `effective_decisions` or request input.

- [ ] **Step 7: Re-run targeted backend tests**

```bash
cd backend
pytest tests/mission/test_approval_read_service.py tests/mission/test_mission_approvals_api.py tests/mission/test_mission_approvals_security.py tests/mission/test_approval_service.py -v
```

Expected: PASS with existing mutation semantics unchanged.

- [ ] **Step 8: Commit**

```bash
git add backend/app/mission/approval_service.py backend/app/mission/approval_read_service.py backend/app/schemas/mission_approvals.py backend/app/api/mission_approvals.py backend/tests/mission
git commit -m "feat(slice5b): expose caller approval capability"
```

### Task A4: Pin OpenAPI and regenerate Orval

**Files:**
- Modify backend OpenAPI/API tests in `backend/tests/mission/test_mission_approvals_api.py` and existing Mission overview schema tests if present.
- Regenerate: `frontend/src/api/generated/**`

**Interfaces:**
- Generated `MissionCard.source_repo` is required.
- Generated list hook exposes the three optional Mission filters.
- Generated `ApprovalDetailResponse` exposes `can_decide` and `current_principal_decision`.

- [ ] **Step 1: Add failing OpenAPI pins**

Assert:

```text
MissionCard.required contains source_repo
GET /api/v1/mission/approvals exposes mission_source_repo, mission_card_kind, mission_card_number
ApprovalDetailResponse.required contains can_decide
ApprovalDetailResponse has current_principal_decision schema
```

- [ ] **Step 2: Run OpenAPI tests**

```bash
cd backend
pytest tests/mission/test_mission_approvals_api.py -v
```

- [ ] **Step 3: Regenerate Orval using the repository's existing generation command**

Use the command documented in the repo/Makefile/package scripts. Do not manually edit generated files.

- [ ] **Step 4: Verify generated signatures**

Check generated files directly and ensure:

```ts
MissionCard.source_repo: string
```

and the list query parameter type includes all three Mission filters. Confirm `CurrentPrincipalDecisionView` and detail fields are generated.

- [ ] **Step 5: Run frontend typecheck**

```bash
cd frontend
npm run typecheck
```

Use the actual equivalent package script if named differently in `package.json`.

- [ ] **Step 6: Run Checkpoint A validation**

```bash
make check
```

Also run the repository's CodeQL/CI via PR after push; do not claim CodeQL locally if it is CI-only.

- [ ] **Step 7: Commit generated contract**

```bash
git add backend frontend/src/api/generated
git commit -m "chore(slice5b): regenerate mission approval client"
```

### Checkpoint A review gate

Before Checkpoint B:

1. Open PR containing Checkpoint A only.
2. Record exact head SHA.
3. Wait for CI + CodeQL on that exact SHA.
4. ChatGPT performs Implementation Review at exact SHA.
5. Human approves merge.
6. Merge and verify `main`.
7. Start Checkpoint B from the new `main`.

---

# Checkpoint B — Mission Governance read UX

No decision mutation UI in this checkpoint.

### Task B1: Make Mission cards selectable using trusted identity

**Files:**
- Modify: `frontend/src/app/mission/page.tsx`
- Create/modify corresponding page/component test following existing repo convention.

**Interfaces:**
- Selected Mission identity is exactly:

```ts
type SelectedMission = {
  source_repo: string;
  kind: MissionCard["kind"];
  number: number;
  title: string | null;
};
```

- [ ] Write a failing test that clicks an Issue/PR card and captures selected identity from `card.source_repo`, `card.kind`, and `card.number`.
- [ ] Assert no code path derives repo from `card.url`.
- [ ] Run the targeted frontend test and verify failure.
- [ ] Add accessible selected-card affordance (`button` semantics or equivalent keyboard-operable control) without breaking existing card display.
- [ ] Run targeted test and typecheck.
- [ ] Commit: `feat(slice5b): select mission governance context`.

### Task B2: Render server-filtered approval list

**Files:**
- Create: `frontend/src/components/mission/governance/MissionGovernancePanel.tsx`
- Modify: `frontend/src/app/mission/page.tsx`
- Add component tests.

**Interfaces:**
- Consume generated `GET /mission/approvals` hook with exactly:

```text
mission_source_repo = selected.source_repo
mission_card_kind = selected.kind
mission_card_number = selected.number
```

- [ ] Write failing tests for no selection, loading, empty, error + Retry, multiple approvals, and pending-first/newest-within-group ordering.
- [ ] Run targeted tests and verify failure.
- [ ] Implement list query enabled only when a selected Mission exists.
- [ ] Do not client-filter a global page.
- [ ] Render summary fields only: action, status, policy/version, created_at, expires_at.
- [ ] Run tests + typecheck.
- [ ] Commit: `feat(slice5b): add mission governance approval list`.

### Task B3: Add read-only request detail

**Files:**
- Create: `frontend/src/components/mission/governance/ApprovalDetail.tsx`
- Modify: `MissionGovernancePanel.tsx`
- Add tests.

**Interfaces:**
- Selected request identity: `request_id` from generated `ApprovalListItem`.
- Detail uses generated approval detail hook only; no independent evaluator.

- [ ] Write failing tests for detail loading/error and rendering status, policy/version, decision rule, quorum requirements, missing requirements, effective decisions, lifecycle, expires/resolved timestamps, and mission effect.
- [ ] Write tests that terminal statuses have no mutation buttons in Checkpoint B.
- [ ] Implement the detail view.
- [ ] Ensure no frontend function computes quorum, trust eligibility, or supersession chains.
- [ ] Run targeted tests + `npm run typecheck` + frontend lint.
- [ ] Commit: `feat(slice5b): render mission governance detail`.

### Checkpoint B review gate

Repeat exact-head CI/CodeQL/Implementation Review/Human merge gate before Checkpoint C.

---

# Checkpoint C — Human approve/reject UX

### Task C1: Add deterministic per-intent idempotency state

**Files:**
- Create: `frontend/src/components/mission/governance/idempotency.ts` if a standalone helper fits repo style; otherwise keep equivalent logic private to `DecisionDialog.tsx` with direct tests.
- Add tests.

**Interface:**

For one confirmed intent `(request_id, decision, reason)`, create one UUID key and retain it while retrying uncertain delivery. A changed/new intent creates a new key.

- [ ] Write failing tests: same intent retry -> same key; changed decision/reason/new confirmation -> new key.
- [ ] Implement using `crypto.randomUUID()` or the repo's existing UUID utility.
- [ ] Do not regenerate the key inside mutation retry callbacks.
- [ ] Run tests.
- [ ] Commit: `feat(slice5b): track approval idempotency intents`.

### Task C2: Add approve/reject dialog and mutations

**Files:**
- Create: `frontend/src/components/mission/governance/DecisionDialog.tsx`
- Modify: `ApprovalDetail.tsx`
- Modify: `MissionGovernancePanel.tsx` only for query invalidation wiring if needed.
- Add tests.

**Interfaces:**
- Show controls only when `detail.status` is pending, `detail.can_decide === true`, and `detail.current_principal_decision == null`.
- Use generated submit-decision mutation with required typed `Idempotency-Key` header.

- [ ] Write failing visibility tests for eligible/ineligible/terminal/caller-already-decided states.
- [ ] Write failing mutation test proving optional reason and the intent key are sent.
- [ ] Write failure test proving previously loaded detail stays visible.
- [ ] Write success test proving detail + selected Mission list are invalidated/refetched.
- [ ] Implement dialog and generated mutation hook.
- [ ] Do not optimistically calculate new status/quorum.
- [ ] Run targeted tests, typecheck, lint, then frontend test suite.
- [ ] Commit: `feat(slice5b): add human approval decision UX`.

### Checkpoint C review gate

Repeat exact-head CI/CodeQL/Implementation Review/Human merge gate before Checkpoint D.

---

# Checkpoint D — Change decision UX

### Task D1: Add caller-owned decision supersession

**Files:**
- Modify: `frontend/src/components/mission/governance/ApprovalDetail.tsx`
- Modify: `frontend/src/components/mission/governance/DecisionDialog.tsx`
- Reuse idempotency helper/state.
- Add tests.

**Interfaces:**
- Show **Change decision** only when pending + `can_decide=true` + `current_principal_decision != null`.
- Send:

```ts
{
  supersedes_decision_id: detail.current_principal_decision.decision_id,
  decision: nextDecision,
  reason,
}
```

with a per-intent `Idempotency-Key` through the existing generated supersede mutation.

- [ ] Write failing visibility and payload tests.
- [ ] Write test proving the old decision ID comes only from `current_principal_decision`, not by searching all effective decisions.
- [ ] Write retry-key and mutation-error preservation tests.
- [ ] Write success test proving detail + list refetch.
- [ ] Implement minimal change mode in `DecisionDialog`.
- [ ] Run targeted frontend tests, typecheck, lint, frontend suite.
- [ ] Run `make check` before PR.
- [ ] Commit: `feat(slice5b): add approval decision supersession UX`.

### Checkpoint D review gate

Repeat exact-head CI/CodeQL/Implementation Review/Human merge gate. Slice 5B v1 is complete only after this checkpoint is merged and `main` is verified.

---

## Final Verification Checklist

Before claiming any checkpoint complete:

- [ ] `git diff --check` passes.
- [ ] Targeted tests for the checkpoint pass.
- [ ] Backend formatting/lint/typecheck pass when backend changed.
- [ ] Frontend lint/typecheck/tests pass when frontend changed.
- [ ] Generated Orval artifacts are regenerated, not hand-edited, when OpenAPI changes.
- [ ] `make check` passes before PR handoff unless the repo documents a narrower equivalent; report any unrelated failure separately rather than silently changing scope.
- [ ] No migration file was added.
- [ ] No mutation allowlist entry changed.
- [ ] No GitHub write capability was added.
- [ ] Board approval files have no semantic changes.
- [ ] Exact PR head SHA is recorded.
- [ ] CI + CodeQL results correspond to that exact SHA.
- [ ] Independent Implementation Review PASS before Human merge approval.

## Self-Review

### Spec coverage

- Trusted Mission repo identity: Task A1.
- Server-side pre-pagination filtering + partial tuple validation: Task A2.
- Caller capability/effective caller decision + shared authorization: Task A3.
- OpenAPI/Orval synchronization: Task A4.
- Selected-card Mission-centric read UX: B1-B3.
- Approve/reject + idempotency: C1-C2.
- Change decision/supersede: D1.
- Loading/empty/error/terminal states and backend-authoritative rendering: B2-B3/C2/D1.
- ADR-23/GitHub-write/Board-domain constraints: Global Constraints + verification gates.

### Placeholder scan

No `TBD`, `TODO`, “similar to”, or open architecture decision remains. Exact UI file splitting may adapt to existing repo test conventions, but API identities, trust boundaries, mutation routes, and required behaviors are fixed.

### Type consistency

The plan consistently uses:

```text
Mission identity = source_repo + kind + number
Approval list filters = mission_source_repo + mission_card_kind + mission_card_number
Caller decision = CurrentPrincipalDecisionView.decision_id
Decision capability = ApprovalDetailResponse.can_decide
```

No frontend URL parsing or independent authorization/quorum evaluator is permitted.

## Execution Handoff

Execute **Checkpoint A only** first. Do not begin Checkpoint B in the same PR. After Checkpoint A passes exact-head CI/CodeQL and Implementation Review, wait for Human merge approval, merge, verify `main`, then plan the execution handoff for Checkpoint B from the new baseline.