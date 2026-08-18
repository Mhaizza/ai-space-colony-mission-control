# Slice 5B — Checkpoint B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This plan is READ-ONLY Mission Governance UX; no mutation code is authorized by this plan.

**Approved spec:** `docs/superpowers/specs/2026-08-19-slice-5b-checkpoint-b-mission-governance-drawer-design.md` (PR #26, commit `cf0b524432d05d7cb874675d915b7afcf6aa7ff6`, Human-approved).

**Baseline:** `main @ c397ccade940be3dcd76d22c9b12994e93a456a8` (Slice 5B Checkpoint A, merged).

## Baseline and Preconditions

- Checkpoint A's backend/contract surface is already merged and verified present on `main` (see "Repository Findings" below): `MissionCard.source_repo`, the three `mission_*` list filters, and `ApprovalDetailResponse.can_decide`/`current_principal_decision`, all already reflected in the generated Orval client.
- Before starting implementation, the executing branch/worktree must start from current `main` (post Checkpoint A), not from an old Checkpoint A branch or this planning branch.
- Every task below is TDD: write the failing test first, run it and confirm the RED failure reason matches what's expected, implement the minimal change, run GREEN, then move to the next task. Do not batch multiple tasks' implementation before their own tests exist.

## Approved Scope

Everything in the "Scope" section of the approved spec: selectable Mission cards, an immediately-opening right-side governance drawer, a Mission-scoped read-only approval list pane, a read-only approval detail pane, full loading/empty/error/terminal state handling isolated per pane, and desktop two-column / narrow drill-in responsive behavior. No mutation UI or calls.

## Out of Scope

Everything under "Non-goals" / "Hard out of scope" in the approved spec: any mutation UI or call (approve/reject/change-decision/create/supersede), `Idempotency-Key` frontend state, mutation retry lifecycle, a new Mission route tree, any backend/API/schema/migration change, policy editor, role/principal admin, notifications, bulk approvals, AI approver UI, global approvals inbox, GitHub writes, and any semantic reuse or modification of the legacy Board approval domain.

## Repository Findings

Inspected at `main @ c397ccade940be3dcd76d22c9b12994e93a456a8` (this planning worktree, `/workspace/slice5b-checkpoint-b-spec`).

**Mission page** — `frontend/src/app/mission/page.tsx` (588 lines), a client component (`"use client"`, `export const dynamic = "force-dynamic"`). Only `dynamic` and the default-exported `MissionControlPage` are exported; `cardKindLabel`, `EmptyState`, and `StatusBadge` are private, unexported local helpers — Checkpoint B **cannot import them** and must define its own small local equivalents inside the new `governance/` files rather than trying to import these. Cards are currently rendered at lines ~356–386 as static, non-interactive `<div>` rows keyed `${card.kind}-${card.number}`, inside a `<section>` titled "Cards", using an `EmptyState` fallback when the list is empty. `MissionCard` is imported from `@/api/generated/model` and already has `source_repo: string` (Checkpoint A).

**Generated client (mission-approvals)** — `frontend/src/api/generated/mission-approvals/mission-approvals.ts`:
- `useListApprovalsApiV1MissionApprovalsGet(params?: ListApprovalsApiV1MissionApprovalsGetParams, options?: { query?: Partial<UseQueryOptions<...>> })` — the list query hook. `ListApprovalsApiV1MissionApprovalsGetParams` (in `@/api/generated/model`) is `{ mission_source_repo?: string | null; mission_card_kind?: "issue" | "pull_request" | null; mission_card_number?: number | null; limit?: number; offset?: number }`.
- `useGetApprovalDetailApiV1MissionApprovalsRequestIdGet(requestId: string, options?: { query?: Partial<UseQueryOptions<...>> })` — the detail query hook. `requestId` is a **required** positional string argument (not optional) — to conditionally disable the query when nothing is selected, pass `options.query.enabled: selectedApprovalRequestId != null` and pass `requestId={selectedApprovalRequestId ?? ""}` (the empty-string placeholder is never sent because `enabled: false` short-circuits the fetch).
- Both hooks return `UseQueryResult<TData, TError>` where `TData` is a discriminated union on `status`: list is `{ data: LimitOffsetPageTypeVarCustomizedApprovalListItem; status: 200 } | { data: HTTPValidationError; status: 422 }`; detail is `{ data: ApprovalDetailResponse; status: 200 } | { data: HTTPValidationError; status: 422 }`. There is no distinct typed 404 branch — `frontend/src/api/mutator.ts`'s `customFetch` throws `ApiError` for any non-2xx response (confirmed: `if (!response.ok) { ... throw new ApiError(...) }`), so a 404 (unknown request id) surfaces as `query.error`/`query.isError`, identical to any other detail-fetch failure. This matches the approved spec's single "detail error + Retry" state exactly — no separate not-found UI is needed or in scope.
- `LimitOffsetPageTypeVarCustomizedApprovalListItem` = `{ items: ApprovalListItem[]; total: number; limit: number; offset: number }`.
- `ApprovalListItem` fields: `request_id, status, mission_source_repo, mission_card_kind, mission_card_number, action_key, policy_key, policy_version, created_at, expires_at`.
- `ApprovalDetailResponse` adds: `decision_rule, quorum_satisfied, quorum_requirements: QuorumRequirementView[], missing_requirements: string[], effective_decisions: EffectiveDecisionView[], lifecycle: LifecycleEventView[], resolved_at, mission_effect, can_decide: boolean, current_principal_decision: CurrentPrincipalDecisionView | null`.
- `QuorumRequirementView` = `{ slot, eligible_roles: string[], satisfied: boolean }`. `EffectiveDecisionView` = `{ principal_id, decision, reason, role_slugs_at_decision: string[], created_at }`. `LifecycleEventView` = `{ event_type, triggered_by_principal_id, detail, created_at }`. `CurrentPrincipalDecisionView` = `{ decision_id, decision, reason, created_at }`.
- All required fields confirmed present in the generated client at this baseline — **no backend/API blocker**.

**Drawer/UI primitives** — no dedicated Sheet/Drawer primitive exists in `frontend/src/components/ui/`. `dialog.tsx` wraps `@radix-ui/react-dialog` but is a centered, backdrop-blocking modal (`fixed inset-0 ... flex items-center justify-center`), which is explicitly the wrong pattern for this drawer (the approved spec forbids a full-screen modal as the narrow-viewport pattern, and the drawer must keep the Mission overview reachable/visible on desktop, not block it with a modal overlay). Checkpoint B will build its own lightweight, non-modal side panel as a plain positioned `<div>` (no Radix), matching how `frontend/src/components/organisms/DashboardSidebar.tsx` already hand-rolls its own slide-over sidebar (`fixed ... -translate-x-full ... [[data-sidebar=open]_&]:translate-x-0 md:relative md:translate-x-0`) rather than using a third-party primitive for a non-modal, always-dismissable panel.

**Shared UI components available for reuse:**
- `Button` — `frontend/src/components/ui/button.tsx`, `variant: "primary" | "secondary" | "outline" | "ghost"`, `size: "sm" | "md" | "lg"`. Use `variant="outline" size="sm"` for `Retry`, `variant="ghost" size="sm"` for `Back`.
- `Badge` — `frontend/src/components/ui/badge.tsx`, `variant: "default" | "outline" | "accent" | "success" | "warning" | "danger"`. Use for the terminal-status badge (e.g. `approved` → `success`, `rejected`/`expired` → `warning`, `superseded` → `outline`, `pending` → `accent` — exact mapping decided in Task 1).
- `cn` — `frontend/src/lib/utils.ts`, standard `clsx`/`tailwind-merge` class combiner, used throughout the UI primitives above.
- `formatTimestamp` / `formatRelativeTimestamp` — `frontend/src/lib/formatters.ts`, already imported and used by `/mission` itself for exactly this kind of ISO-timestamp-to-display formatting (prefer this over the Board-domain's separate `@/lib/datetime` helpers, to stay consistent with the rest of the Mission surface).

**Responsive convention** — no JS media-query hook (`matchMedia`/`useMediaQuery`) exists anywhere in the frontend; confirmed via repo-wide search. All responsive behavior in the app is pure Tailwind CSS breakpoint utility classes. The closest analogous existing "collapse to a single column below desktop" pattern is `frontend/src/components/templates/DashboardShell.tsx` / `DashboardSidebar.tsx`: the app shell's sidebar/nav switches from a mobile slide-over (driven by a `sidebarOpen` boolean + `data-sidebar` attribute) to a fixed always-visible column at the **`md:` breakpoint** (768px) — e.g. `md:hidden`, `hidden md:flex`, `grid-cols-1 md:grid-cols-[260px_1fr]`. **Mechanism selected for Checkpoint B:** reuse the same `md:` breakpoint and the same "JS state drives conditional Tailwind classes, no viewport-detection JS" technique. Both `ApprovalListPane` and `ApprovalDetailPane` are always mounted; visibility is controlled by `selectedApprovalRequestId != null` composed with `md:`-prefixed classes:
  - List pane: `className={selectedApprovalRequestId ? "hidden md:block" : "block"}` (visible unless narrow-with-a-selection).
  - Detail pane: `className={selectedApprovalRequestId ? "block" : "hidden md:block"}` (visible unless narrow-with-no-selection).
  - At `md:` and above both classes resolve to visible, giving the two-column desktop layout; below `md:` exactly one is visible at a time, giving the drill-in.
  Because Vitest/jsdom has no real CSS layout engine and does not evaluate media queries, component tests for "narrow layout" verify the **conditional class-toggling logic** (i.e. that the right Tailwind classes are applied to the right pane given `selectedApprovalRequestId`), not actual rendered geometry at a given viewport width — this is the standard, and only meaningful, way to unit-test Tailwind-breakpoint-driven visibility in this stack; the correctness of Tailwind's own `md:` media-query CSS is a third-party guarantee that does not need re-testing here.
  This satisfies the "no suitable repo convention exists → STOP" clause's inverse: a suitable, actively-used repo convention **does** exist (the `DashboardShell`/`DashboardSidebar` `md:` split), so no Human input is required on this point.

**Loading skeleton convention** — no shared `<Skeleton>` component exists. The established local pattern (seen in `frontend/src/components/BoardGoalPanel.tsx`) is a plain `animate-pulse` div: `<div className="h-4 w-32 animate-pulse rounded-full bg-[color:var(--surface-muted)]" />`. Checkpoint B reuses this exact pattern (sized appropriately per skeleton row) rather than introducing a new skeleton primitive.

**Error/Retry convention** — no existing `Retry`-button pattern exists anywhere in the frontend (confirmed via repo-wide search). Checkpoint B introduces the pattern using the existing `Button` primitive plus each generated query hook's own `refetch()` method (`onClick={() => query.refetch()}`) — this is standard TanStack Query usage, not a new architectural primitive.

**Test conventions** — Vitest + Testing Library, colocated `ComponentName.test.tsx` next to `ComponentName.tsx` (see `BoardApprovalsPanel.tsx`/`.test.tsx`, `ActivityFeed.tsx`/`.test.tsx`). `frontend/src/components/BoardApprovalsPanel.test.tsx` establishes the pattern for wrapping a component that uses TanStack Query in a fresh `QueryClientProvider` per test (`retry: false` in `defaultOptions`) and using `vi.mock("@/auth/clerk", () => ({ useAuth: () => ({ isSignedIn: true }) }))` where auth state matters. Because the approved spec's "Component responsibilities" section assigns hook-consumption directly to `ApprovalListPane`/`ApprovalDetailPane` (not to a container that fetches and passes props down), their tests must control the generated hooks' return values directly via `vi.mock("@/api/generated/mission-approvals/mission-approvals")`, returning a controlled partial `UseQueryResult` shape (`{ data, isLoading, isError, error, refetch }`) per test case — a standard Vitest module-mocking technique; no repo precedent mocks a GET query hook this way today (existing tests mock mutation hooks and pass GET data as props instead), but it is the only correct technique given the Human-approved component boundary requires the panes to own their own hook calls. `frontend/vitest.config.ts`'s coverage gate (`thresholds: 100%`) is scoped to only two explicit files (`src/lib/backoff.ts`, `src/components/activity/ActivityFeed.tsx`); none of the new Checkpoint B files are added to that `include` list by this plan, matching how Checkpoint A's backend work did not need to expand its own equivalent scoped-coverage gate.

**Legacy Board approvals (reference only)** — `frontend/src/components/BoardApprovalsPanel.tsx` (972 lines) + `BoardApprovalsPanel.test.tsx`, using the separate `@/api/generated/approvals/approvals` generated tag and `ApprovalRead` model. Confirmed structurally and semantically independent of the Mission `mission-approvals` tag/models used here. Not modified by this plan; Task 10 below adds a regression check that its existing test suite is unaffected.

**Blockers:** none. Checkpoint A's backend contract and generated client fully support the approved Checkpoint B UX.

## Implementation Sequence

### Task 1 — Pure governance list-ordering and status helpers

**Goal:** Extract the two small, pure, independently-testable logic units the rest of the drawer depends on: (a) sorting an `ApprovalListItem[]` into pending-first/newest-within-group order, and (b) classifying a `status` string as terminal vs. not and mapping it to a `Badge` variant. Keeping these pure and separate from any component lets them be tested without React/Testing Library/query mocking at all.

**Files:**
- Create: `frontend/src/components/mission/governance/governanceStatus.ts`
- Test: `frontend/src/components/mission/governance/governanceStatus.test.ts`

**Test to write FIRST** (`governanceStatus.test.ts`):
```ts
import { describe, expect, it } from "vitest";
import { isTerminalStatus, orderApprovals, statusBadgeVariant } from "./governanceStatus";
import type { ApprovalListItem } from "@/api/generated/model";

describe("isTerminalStatus", () => {
  it("returns true for approved/rejected/expired/superseded", () => { /* ... */ });
  it("returns false for pending", () => { /* ... */ });
});

describe("statusBadgeVariant", () => {
  it("maps each known status to a Badge variant", () => { /* asserts exact mapping table */ });
});

describe("orderApprovals", () => {
  it("places all pending items before all terminal items", () => { /* ... */ });
  it("orders pending items newest-first by created_at", () => { /* ... */ });
  it("orders terminal items newest-first by created_at, after all pending", () => { /* ... */ });
  it("does not mutate the input array", () => { /* asserts input array reference/order unchanged */ });
});
```

**Expected RED:** `governanceStatus.ts` does not exist — import fails / `Cannot find module './governanceStatus'`.

**Minimal implementation:** `governanceStatus.ts` exports:
```ts
import type { ApprovalListItem } from "@/api/generated/model";
import type { BadgeProps } from "@/components/ui/badge";

const TERMINAL_STATUSES = new Set(["approved", "rejected", "expired", "superseded"]);

export function isTerminalStatus(status: string): boolean {
  return TERMINAL_STATUSES.has(status);
}

export function statusBadgeVariant(status: string): NonNullable<BadgeProps["variant"]> {
  switch (status) {
    case "approved": return "success";
    case "rejected": return "danger";
    case "expired": return "warning";
    case "superseded": return "outline";
    default: return "accent"; // pending and any unrecognized status
  }
}

export function orderApprovals(items: ApprovalListItem[]): ApprovalListItem[] {
  return [...items].sort((a, b) => {
    const aTerminal = isTerminalStatus(a.status);
    const bTerminal = isTerminalStatus(b.status);
    if (aTerminal !== bTerminal) return aTerminal ? 1 : -1;
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });
}
```

**Validation:** `cd frontend && npx vitest run src/components/mission/governance/governanceStatus.test.ts`

**Expected GREEN:** all cases pass; no other files affected.

**Commit boundary:** `docs(slice5b): checkpoint B task 1 — governance list ordering and status helpers` *(implementation-phase commit message; not part of this planning commit)*.

---

### Task 2 — Mission card selection state on `/mission`

**Goal:** Make Mission cards clickable and introduce `selectedMissionCard` state on the page, without yet rendering any drawer content (drawer mounts as an empty/placeholder shell in this task — full drawer UI is Task 3+). Proves the selection wiring in isolation.

**Files:**
- Modify: `frontend/src/app/mission/page.tsx`
- Test: `frontend/src/app/mission/page.test.tsx` *(new — no test file exists for this page today; follow the colocated-next-to-source-under-`app/`-route convention already used for other route tests, e.g. `frontend/src/app/activity/page.test.tsx`, `frontend/src/app/approvals/page.test.tsx`)*

**Test to write FIRST:** Render `MissionControlPage` with a mocked `useMissionOverviewApiV1MissionOverviewGet` (via `vi.mock("@/api/generated/mission/mission")`) returning a fixture `workflow.cards` array with one Issue and one PullRequest card (each including `source_repo`). Assert:
1. Each card row is rendered as an interactive element (e.g. has `role="button"` or is a real `<button>`) rather than a plain `<div>`.
2. Clicking a card renders `MissionGovernanceDrawer` (a temporary minimal stub component for this task — assert on a `data-testid="mission-governance-drawer"` root, or on the header text `source_repo · kind · #number`) with that card's exact `source_repo`/`kind`/`number`/`title`.
3. No drawer is rendered before any card is clicked.

**Expected RED:** cards are still plain non-interactive `<div>`s with no click handler and no `selectedMissionCard` state — the click assertion fails because there is nothing to click that changes rendered output, and `MissionGovernanceDrawer` does not exist yet (import fails).

**Minimal implementation:**
- Create the smallest possible `MissionGovernanceDrawer` stub in `frontend/src/components/mission/governance/MissionGovernanceDrawer.tsx` accepting `{ card: SelectedMissionCard; onClose: () => void }` and rendering only the header text (`ApprovalListPane`/`ApprovalDetailPane` are not mounted yet — added in later tasks).
- In `page.tsx`: add `const [selectedMissionCard, setSelectedMissionCard] = useState<SelectedMissionCard | null>(null);` (define `SelectedMissionCard` as `{ source_repo: string; kind: MissionCard["kind"]; number: number; title: string | null }`, exported from the new `MissionGovernanceDrawer.tsx` or a small colocated `types.ts` in `governance/` so both files share one definition).
- Change the card row from `<div>` to `<button type="button" onClick={() => setSelectedMissionCard({ source_repo: card.source_repo, kind: card.kind, number: card.number, title: card.title })}>` (preserving existing visual content/classes as closely as practical).
- Render `{selectedMissionCard ? <MissionGovernanceDrawer card={selectedMissionCard} onClose={() => setSelectedMissionCard(null)} /> : null}` at the end of the page body.

**Validation:** `cd frontend && npx vitest run src/app/mission/page.test.tsx && npx tsc -p tsconfig.json --noEmit`

**Expected GREEN:** all three assertions pass; typecheck clean.

**Commit boundary:** `feat(slice5b): checkpoint B task 2 — mission card selection`.

---

### Task 3 — MissionGovernanceDrawer shell: responsive structure, header, mount points, selection reset

**Goal:** Replace the Task 2 stub with the real drawer shell: correct desktop/narrow structural classes (per the "Responsive convention" finding above), the full header (`source_repo` + `kind` + `#number` + `title`), mount points for `ApprovalListPane`/`ApprovalDetailPane` (still stubs at this point), ownership of `selectedApprovalRequestId`, and the reset-on-Mission-change behavior.

**Files:**
- Modify: `frontend/src/components/mission/governance/MissionGovernanceDrawer.tsx`
- Test: `frontend/src/components/mission/governance/MissionGovernanceDrawer.test.tsx`

**Test to write FIRST:**
1. Renders the header with `source_repo`, a human-readable kind label, `#number`, and `title` all present in the accessible text content, for a given `card` prop.
2. Renders both an `ApprovalListPane`-mount region and an `ApprovalDetailPane`-mount region (stub children at this point via a lightweight test double passed as children, or by asserting on `data-testid` container elements the real panes will later fill).
3. Given a `selectedApprovalRequestId` state that starts non-null (simulate via a wrapper that lets the test set an initial "already selected" state, e.g. exposing a test-only prop or by first simulating list-row selection once list rendering exists in Task 5 — if list selection isn't wired yet, this specific "reset" assertion is deferred to Task 8's integration test; this task instead asserts only that changing the `card` prop causes `MissionGovernanceDrawer`'s internally-owned `selectedApprovalRequestId` to be null immediately after the prop change, using an exposed test hook such as calling a `data-testid="detail-pane"` stub that reports the current `selectedApprovalRequestId` it was passed).
4. Asserts the list-pane mount and detail-pane mount elements each carry the expected conditional Tailwind classes (`hidden md:block` / `block`) matching the "Responsive convention" mechanism, for both `selectedApprovalRequestId == null` and (once settable within this component's own test, e.g. by simulating a callback the stub list pane invokes) `!= null`.

**Expected RED:** `MissionGovernanceDrawer` still only renders the header text from Task 2; no pane mount points, no responsive classes, no `selectedApprovalRequestId` state — the mount-point and class assertions fail to find matching elements.

**Minimal implementation:** Rewrite `MissionGovernanceDrawer.tsx` to:
- own `const [selectedApprovalRequestId, setSelectedApprovalRequestId] = useState<string | null>(null);`
- reset it via `useEffect(() => setSelectedApprovalRequestId(null), [card.source_repo, card.kind, card.number]);` (or the "store info from previous renders" conditional-setState-during-render pattern already used in `DashboardShell.tsx`, to avoid an extra render — prefer matching that existing repo idiom over introducing a fresh `useEffect` if straightforward).
- render the fixed/positioned drawer container (right-side panel classes, no Radix), the header, and two mount regions with the conditional visibility classes described in "Responsive convention", passing `selectedApprovalRequestId` and `setSelectedApprovalRequestId` down as props to (still-stub) `ApprovalListPane`/`ApprovalDetailPane`.

**Validation:** `cd frontend && npx vitest run src/components/mission/governance/MissionGovernanceDrawer.test.tsx && npx tsc -p tsconfig.json --noEmit`

**Expected GREEN:** all assertions pass.

**Commit boundary:** `feat(slice5b): checkpoint B task 3 — governance drawer shell`.

---

### Task 4 — ApprovalListPane: data fetching, loading, empty, error+Retry

**Goal:** Wire `ApprovalListPane` to the real generated list hook with the exact filter tuple, and implement the loading/empty/error states (ordering and row rendering come in Task 5, to keep this task's diff small and focused).

**Files:**
- Create: `frontend/src/components/mission/governance/ApprovalListPane.tsx`
- Test: `frontend/src/components/mission/governance/ApprovalListPane.test.tsx`

**Test to write FIRST** (mocking `@/api/generated/mission-approvals/mission-approvals` via `vi.mock`, controlling `useListApprovalsApiV1MissionApprovalsGet`'s return value per case, per the "Test conventions" finding):
1. Calls `useListApprovalsApiV1MissionApprovalsGet` with exactly `{ mission_source_repo: card.source_repo, mission_card_kind: card.kind, mission_card_number: card.number }` (assert on the mock's call arguments) — no extra/derived fields, no partial tuple.
2. `isLoading: true, data: undefined` → renders the localized `animate-pulse` skeleton (assert on a stable test id/class, not exact pixel styling).
3. `data: { status: 200, data: { items: [], total: 0, limit: 200, offset: 0 } }` → renders exactly `No approvals for this Mission`.
4. `isError: true, error: new Error("boom")` → renders a localized error message and a `Retry` button; clicking `Retry` calls the mocked `refetch()`.
5. A successful non-empty response with `data.status === 200` renders as many row containers as `items.length` (row *content*/ordering assertions belong to Task 5 — this task only proves rows exist and their count/`request_id` `key`s are correct, using unordered fixture data).

**Expected RED:** `ApprovalListPane.tsx` does not exist.

**Minimal implementation:**
```tsx
"use client";
import { useListApprovalsApiV1MissionApprovalsGet } from "@/api/generated/mission-approvals/mission-approvals";
import type { SelectedMissionCard } from "./types"; // from Task 2/3

export function ApprovalListPane({
  card,
  selectedApprovalRequestId,
  onSelect,
}: {
  card: SelectedMissionCard;
  selectedApprovalRequestId: string | null;
  onSelect: (requestId: string) => void;
}) {
  const query = useListApprovalsApiV1MissionApprovalsGet({
    mission_source_repo: card.source_repo,
    mission_card_kind: card.kind,
    mission_card_number: card.number,
  });

  if (query.isLoading) return /* skeleton */;
  if (query.isError) return /* localized error + Retry calling query.refetch() */;

  const items = query.data?.status === 200 ? query.data.data.items : [];
  if (items.length === 0) return <p>No approvals for this Mission</p>;

  return (
    <div>
      {items.map((item) => (
        <div key={item.request_id} /* row, click → onSelect(item.request_id) */ />
      ))}
    </div>
  );
}
```

**Validation:** `cd frontend && npx vitest run src/components/mission/governance/ApprovalListPane.test.tsx && npx tsc -p tsconfig.json --noEmit`

**Expected GREEN:** all cases pass.

**Commit boundary:** `feat(slice5b): checkpoint B task 4 — mission approval list data/loading/empty/error`.

---

### Task 5 — ApprovalListPane: ordering, row content, selection

**Goal:** Apply Task 1's `orderApprovals`, render real row content (status badge via `statusBadgeVariant`, `action_key`, `policy_key`/`policy_version`, `created_at`, `expires_at` per the approved spec's "Mission governance list behavior" summary-field list carried over from the parent Slice 5B design), and wire row selection into `onSelect`.

**Files:**
- Modify: `frontend/src/components/mission/governance/ApprovalListPane.tsx`
- Test: `frontend/src/components/mission/governance/ApprovalListPane.test.tsx` (extend)

**Test to write FIRST:**
1. Given a fixture list with 2 pending items (different `created_at`) and 2 terminal items (different `status`/`created_at`) in scrambled input order, asserts the rendered row order is: pending newest-first, then terminal newest-first (mirrors `orderApprovals`'s own test cases, applied end-to-end through the component).
2. Clicking a row calls `onSelect(item.request_id)` with the exact `request_id` of the clicked row (not a derived/guessed id).
3. The row whose `request_id === selectedApprovalRequestId` carries a distinguishable "selected" visual state (e.g. an `aria-current="true"` or a selected class) while others do not.

**Expected RED:** rows currently render in raw `items` array order (Task 4's minimal implementation) and have no click wiring to `onSelect` with the real id, and no selected-state marker.

**Minimal implementation:** `const ordered = orderApprovals(items);` before mapping; each row becomes a `<button onClick={() => onSelect(item.request_id)} aria-current={item.request_id === selectedApprovalRequestId}>` rendering the `Badge` (via `statusBadgeVariant(item.status)`), `action_key`, `policy_key`/`policy_version`, and `formatTimestamp(item.created_at)` / `formatTimestamp(item.expires_at)`.

**Validation:** `cd frontend && npx vitest run src/components/mission/governance/ApprovalListPane.test.tsx && npx tsc -p tsconfig.json --noEmit`

**Expected GREEN:** all cases pass, including Task 4's carried-forward cases.

**Commit boundary:** `feat(slice5b): checkpoint B task 5 — mission approval list ordering and rows`.

---

### Task 6 — ApprovalDetailPane: placeholder, loading, error+Retry, selection wiring

**Goal:** Wire `ApprovalDetailPane` to the detail hook with correct `enabled` gating and the placeholder/loading/error states (full field rendering is Task 7).

**Files:**
- Create: `frontend/src/components/mission/governance/ApprovalDetailPane.tsx`
- Test: `frontend/src/components/mission/governance/ApprovalDetailPane.test.tsx`

**Test to write FIRST** (mocking `useGetApprovalDetailApiV1MissionApprovalsRequestIdGet`):
1. `selectedApprovalRequestId: null` → renders exactly `Select an approval to view details`; asserts the mocked hook was called with `options.query.enabled === false` (never triggers a real fetch when nothing is selected).
2. `selectedApprovalRequestId: "abc"` → asserts the mocked hook was called with `requestId === "abc"` and `options.query.enabled === true`.
3. `isLoading: true` → renders the localized detail skeleton (distinct testid from the list skeleton).
4. `isError: true` → renders a localized error message + `Retry`; clicking `Retry` calls the mocked `refetch()` and does **not** call `onSelect`/clear `selectedApprovalRequestId` (the pane has no such prop to call in the first place — this assertion just proves no side-effecting callback exists on the error path beyond `refetch()`).

**Expected RED:** `ApprovalDetailPane.tsx` does not exist.

**Minimal implementation:**
```tsx
"use client";
import { useGetApprovalDetailApiV1MissionApprovalsRequestIdGet } from "@/api/generated/mission-approvals/mission-approvals";

export function ApprovalDetailPane({ selectedApprovalRequestId }: { selectedApprovalRequestId: string | null }) {
  const query = useGetApprovalDetailApiV1MissionApprovalsRequestIdGet(
    selectedApprovalRequestId ?? "",
    { query: { enabled: selectedApprovalRequestId != null } },
  );

  if (selectedApprovalRequestId == null) return <p>Select an approval to view details</p>;
  if (query.isLoading) return /* skeleton */;
  if (query.isError) return /* localized error + Retry calling query.refetch() */;

  const detail = query.data?.status === 200 ? query.data.data : null;
  if (!detail) return null; // 422 shape defensively; unreachable in practice for a GET-by-id
  return /* Task 7 */;
}
```

**Validation:** `cd frontend && npx vitest run src/components/mission/governance/ApprovalDetailPane.test.tsx && npx tsc -p tsconfig.json --noEmit`

**Expected GREEN:** all cases pass.

**Commit boundary:** `feat(slice5b): checkpoint B task 6 — approval detail placeholder/loading/error`.

---

### Task 7 — ApprovalDetailPane: full backend-derived field rendering + terminal read-only badge

**Goal:** Render every field enumerated in the approved spec's "Backend-authoritative governance boundary" section, and mark terminal requests as read-only, without computing any of them client-side.

**Files:**
- Modify: `frontend/src/components/mission/governance/ApprovalDetailPane.tsx`
- Test: `frontend/src/components/mission/governance/ApprovalDetailPane.test.tsx` (extend)

**Test to write FIRST:**
1. Given a full `ApprovalDetailResponse` fixture (pending status, non-empty `quorum_requirements`, `missing_requirements`, `effective_decisions`, `lifecycle`), asserts each of `status`, `policy_key`/`policy_version`, `decision_rule`, `quorum_satisfied`, every `quorum_requirements[].slot`/`eligible_roles`/`satisfied`, every `missing_requirements[]` entry, every `effective_decisions[]` entry's `decision`/`reason`, every `lifecycle[]` entry's `event_type`, `created_at`/`expires_at`/`resolved_at`, and `mission_effect` are present in rendered output — i.e. every field is displayed, none silently dropped.
2. `can_decide: true` and `current_principal_decision: null` render as plain informational text (assert the literal strings `Approve`/`Reject`/`Change decision` are **absent** from the DOM) — this is also re-verified end-to-end in Task 10's scope-guard suite, but pinning it here too catches a regression at the smallest possible unit.
3. `current_principal_decision` non-null renders its `decision`/`reason`/`created_at` as plain text, still with no interactive control.
4. For each terminal `status` (`approved`, `rejected`, `expired`, `superseded`), the same full-detail assertions from case 1 still hold (nothing hidden), AND a `Badge` with the `statusBadgeVariant(status)` variant is rendered (the "clearly read-only" signal), AND no interactive mutation control exists regardless of `can_decide`'s value on a terminal request (server sends `can_decide: false` for terminal per Checkpoint A's own contract, but this test does not rely on that — it explicitly fixtures `can_decide: true` on a terminal-status detail to prove the frontend renders no control either way, matching "Checkpoint B has no mutation affordances at all" from the approved spec).

**Expected RED:** the pane currently returns `null`/a stub for the loaded-successfully branch (Task 6's placeholder).

**Minimal implementation:** Render every field from the fixture list above as plain text/lists, using `formatTimestamp` for the three timestamp fields, `Badge`/`statusBadgeVariant` (from Task 1) for the status indicator, and plain text (no `<button>`/`<a role="button">`/click handler) for `can_decide`/`current_principal_decision`.

**Validation:** `cd frontend && npx vitest run src/components/mission/governance/ApprovalDetailPane.test.tsx && npx tsc -p tsconfig.json --noEmit`

**Expected GREEN:** all cases pass, including Task 6's carried-forward cases.

**Commit boundary:** `feat(slice5b): checkpoint B task 7 — full approval detail rendering`.

---

### Task 8 — Mission-switch and detail-error integration behavior

**Goal:** Prove, at the `MissionGovernanceDrawer` integration level (real `ApprovalListPane`/`ApprovalDetailPane`, mocked generated hooks), two cross-component invariants that no single pane's isolated unit test can establish on its own, because the properties involved are owned by, or span, more than one component:

- **Scenario A — Mission switch:** switching the selected Mission card resets the approval selection, keeps the drawer open, and re-fetches for the new identity, with no stale data crossing over. This is the plan's answer to the spec-review's non-blocking observation: **"drawer stays open during Mission switch" is made an explicit, standalone assertion here**, not left implicit.
- **Scenario B — Detail-error isolation:** when a selected approval's detail fetch fails, the sibling `ApprovalListPane` (list visibility, selected-row marker) and the parent-owned `selectedApprovalRequestId`/`selectedMissionCard` state all persist unchanged. Task 6's own unit test of `ApprovalDetailPane` in isolation cannot prove this — that test's render tree contains no list pane at all, and `selectedApprovalRequestId` is owned by `MissionGovernanceDrawer` (Task 3), not by the detail pane itself — so this is proven here instead, at the level where the state and the sibling component actually live.

**Files:**
- Modify: `frontend/src/components/mission/governance/MissionGovernanceDrawer.tsx` (wire real panes in place of Task 3's stubs, if not already done incidentally by Tasks 4–7's own component wiring — verify and adjust import wiring only, no new behavior)
- Test: `frontend/src/components/mission/governance/MissionGovernanceDrawer.test.tsx` (extend)

**Test to write FIRST:**

*Scenario A — Mission switch:*
1. Render the drawer for Mission A; mock the list hook to return one item; select that item (assert detail hook is called with its `request_id`).
2. Re-render the same drawer instance with Mission B's `card` prop (simulating the parent page updating `selectedMissionCard`).
3. Assert, all in the same test:
   - the drawer root element is **still present in the DOM** (explicit "stays open" assertion — not merely inferred from other assertions passing);
   - the list hook's most recent call used Mission B's exact `source_repo`/`kind`/`number` (not Mission A's, not a mix);
   - the detail hook is now called with `enabled: false` (no `request_id` carried over from Mission A) — i.e. `selectedApprovalRequestId` was reset to `null`;
   - the detail pane renders the placeholder text `Select an approval to view details` again.

*Scenario B — Detail-error isolation (addresses checklist items 10 "detail error does not remove list" and 11 "detail Retry preserves selection"):*
1. Render the drawer for one Mission card; mock the list hook to return at least two items (e.g. `request_id: "req-1"` and `"req-2"`); select `"req-1"` (the row using the plan's existing selected-state marker from Task 5, `aria-current="true"`) and mock the detail hook to return `isError: true, error: new Error("boom"), refetch: <spy>` for that `request_id`.
2. Assert, before any retry:
   - every list row from step 1 is still rendered (the list did **not** disappear or get replaced by the detail error UI — list and detail are separate DOM regions, so both are present simultaneously);
   - the row for `"req-1"` still carries `aria-current="true"` (the selected-row marker is unchanged by the sibling's error state);
   - the detail pane shows its localized error message and a `Retry` control.
3. Click `Retry`. Assert:
   - the mocked detail `refetch` spy was called;
   - the detail hook's most recent call is still for `requestId === "req-1"` (same request id — not cleared, not swapped);
   - the list rows are still all present and `"req-1"`'s row is still `aria-current="true"` (selection was not cleared by the error+Retry cycle);
   - the drawer header's Mission identity text (`source_repo`/`kind`/`#number`/`title`) is unchanged from step 1 (Mission selection was not touched by a detail-only failure).

**Expected RED:** For Scenario A: if Task 3's reset wiring has any gap (e.g. reset only fires on `source_repo` change but not `kind`/`number` change, or a stale query result renders before the new one resolves), this test catches it. For Scenario B: if `MissionGovernanceDrawer` (or either pane) has any code path that clears `selectedApprovalRequestId` on a detail-query error, or that unmounts/hides the list pane when the detail pane errors, this test catches it. If Tasks 3–7 were implemented correctly per this plan (no such clearing/unmounting code path is proposed anywhere in Tasks 1–7), both scenarios may already be GREEN on first run — still write and run them explicitly rather than assuming.

**Minimal implementation:** none anticipated beyond what Tasks 3–7 already built; if RED, fix the gap identified by the failing assertion (most likely the reset dependency array in Task 3 for Scenario A, or an unintended state/visibility coupling introduced in Task 3/4/6 for Scenario B).

**Validation:** `cd frontend && npx vitest run src/components/mission/governance/MissionGovernanceDrawer.test.tsx && npx tsc -p tsconfig.json --noEmit`

**Expected GREEN:** all Scenario A assertions and all Scenario B assertions pass.

**Commit boundary:** `test(slice5b): checkpoint B task 8 — mission switch and detail-error integration tests` (or folded into Task 3's commit if no code change was needed for either scenario — state explicitly in the commit message which case(s) applied).

---

### Task 9 — Responsive drill-in behavior (narrow layout + Back)

**Goal:** Add the `Back` control for the narrow/drill-in layout and verify the conditional visibility classes described in "Responsive convention" respond correctly to `selectedApprovalRequestId` changes end-to-end.

**Files:**
- Modify: `frontend/src/components/mission/governance/MissionGovernanceDrawer.tsx`
- Test: `frontend/src/components/mission/governance/MissionGovernanceDrawer.test.tsx` (extend)

**Test to write FIRST:**
1. With `selectedApprovalRequestId === null`, the list-pane mount carries a class list **not** containing `hidden` alone-outside-`md:` (i.e. matches `"block"`), and the detail-pane mount's class list contains `hidden md:block`.
2. After selecting an approval (real list row click through to `onSelect`), the list-pane mount's classes become `hidden md:block` and the detail-pane mount's classes become `block`; a `Back` button (`Button variant="ghost"`) is now rendered.
3. Clicking `Back` sets `selectedApprovalRequestId` back to `null` (assert detail hook re-called with `enabled: false`, list pane classes revert to case 1, `Back` button no longer rendered) **without** touching `selectedMissionCard` (the drawer header's Mission identity text is unchanged before/after clicking `Back`).

**Expected RED:** no `Back` control exists yet (Tasks 3–8 built selection/reset but not the dedicated `Back` affordance); class-toggling assertions may already pass from Task 3's wiring — confirm explicitly rather than assume.

**Minimal implementation:** Render `<Button variant="ghost" size="sm" onClick={() => setSelectedApprovalRequestId(null)}>Back</Button>` conditionally when `selectedApprovalRequestId != null`, positioned so it is only meaningfully reachable in the narrow layout (e.g. `md:hidden` on the Back button itself, matching the approved spec's "Back" being a narrow-only affordance — desktop shows both panes simultaneously and has no need for it).

**Validation:** `cd frontend && npx vitest run src/components/mission/governance/MissionGovernanceDrawer.test.tsx && npx tsc -p tsconfig.json --noEmit`

**Expected GREEN:** all cases pass.

**Commit boundary:** `feat(slice5b): checkpoint B task 9 — narrow drill-in back control`.

---

### Task 10 — Negative mutation/scope-guard tests + legacy Board regression

**Goal:** Explicit, hard negative assertions that Checkpoint B introduced zero mutation surface anywhere, plus confirmation the legacy Board approval domain is untouched.

**Files:**
- Test only — extend `frontend/src/components/mission/governance/ApprovalDetailPane.test.tsx` and/or add `frontend/src/components/mission/governance/MissionGovernanceDrawer.test.tsx` cases; no new production files.
- Reference (read-only, not modified): `frontend/src/components/BoardApprovalsPanel.test.tsx`

**Test to write FIRST:**
1. Across every fixture combination already used in Tasks 6–7 (pending/terminal × `can_decide` true/false × `current_principal_decision` null/non-null), assert `screen.queryByRole("button", { name: /approve/i })`, `.../reject/i`, and `.../change decision/i` are all `null`.
2. `vi.mock` (or a `vi.fn()` spy) on `useSubmitApprovalDecisionApiV1MissionApprovalsRequestIdDecisionsPost`, `useSupersedeApprovalDecisionApiV1MissionApprovalsRequestIdSupersedePost`, and `useCreateApprovalApiV1MissionApprovalsPost` (all three generated mutation hooks from `mission-approvals.ts`) — render the full drawer through every state combination exercised by every prior task's test cases, then assert none of the three mutation-hook spies/mocks was ever invoked as a function call (not merely "not imported" — actually asserting zero calls).
3. Confirm via `grep`/static check (documented as a plan step, not a runtime test) that no new file imports `useSubmitApprovalDecisionApiV1MissionApprovalsRequestIdDecisionsPost`, `useSupersedeApprovalDecisionApiV1MissionApprovalsRequestIdSupersedePost`, or `useCreateApprovalApiV1MissionApprovalsPost` at all — the strongest possible guarantee, stronger than a runtime spy alone.
4. Run the existing, unmodified `frontend/src/components/BoardApprovalsPanel.test.tsx` suite and confirm it still passes unchanged (proves no accidental Board-domain regression).

**Expected RED:** not applicable in the traditional sense — this task's tests should already be GREEN if Tasks 1–9 were implemented per this plan (no mutation code exists to fail); still write and run them explicitly as the scope-guard, per the approved spec's testing-strategy requirement that these be **pinned**, not merely assumed.

**Minimal implementation:** none anticipated. If any assertion fails, that is itself the signal of scope creep introduced in an earlier task — fix by removing the offending mutation-related code, not by loosening this task's assertions.

**Validation:**
```bash
cd frontend
npx vitest run src/components/mission/governance/ --reporter=verbose
npx vitest run src/components/BoardApprovalsPanel.test.tsx
grep -rn "useSubmitApprovalDecisionApiV1MissionApprovalsRequestIdDecisionsPost\|useSupersedeApprovalDecisionApiV1MissionApprovalsRequestIdSupersedePost\|useCreateApprovalApiV1MissionApprovalsPost" src/components/mission/governance/ src/app/mission/page.tsx
```
(the final `grep` must produce **no output** — any match is a scope-guard failure.)

**Expected GREEN:** all scope-guard tests pass; Board suite unchanged and green; `grep` empty.

**Commit boundary:** `test(slice5b): checkpoint B task 10 — mutation scope guard and board regression check`.

---

### Task 11 — Full validation / regression verification

**Goal:** Repository-wide validation before PR handoff, per the parent plan's "Final Verification Checklist" and this document's own quality bar.

**Files:** none (validation only).

**Commands:**
```bash
cd frontend
npm run lint
npx tsc -p tsconfig.json --noEmit
npm run test
npm run build
cd ..
git diff --check
```

Also run, from repo root, whatever `make check` / documented canonical CI-equivalent target the repository defines at implementation time (do not assume it is unchanged from this planning baseline — re-verify the exact target name against the `Makefile` current at the start of implementation).

**Expected passing behavior:** lint clean; typecheck exit 0; full Vitest suite green (including every task's tests above and the full pre-existing suite, e.g. `BoardApprovalsPanel.test.tsx` and all other existing frontend tests, none regressed); `next build` succeeds; `git diff --check` exit 0.

**Commit boundary:** none (validation-only task; if it surfaces a fix, that fix belongs to the task whose behavior was wrong, with its own commit, not a catch-all "fix everything" commit here).

## Final Verification Checklist (carried forward from the parent plan, Checkpoint-B-scoped)

- [ ] `git diff --check` passes.
- [ ] Every task's targeted tests pass.
- [ ] Frontend lint/typecheck/tests pass.
- [ ] `next build` succeeds.
- [ ] No generated Orval artifact is hand-edited or regenerated (Checkpoint B makes zero backend/schema changes, so `npm run api:gen` is never run during this checkpoint).
- [ ] No migration file added.
- [ ] No mutation allowlist entry changed.
- [ ] No GitHub write capability added.
- [ ] `BoardApprovalsPanel`/`.test.tsx` have no semantic changes (confirmed via Task 10's regression run).
- [ ] Exact PR head SHA recorded at handoff.
- [ ] CI + CodeQL results correspond to that exact SHA.
- [ ] Independent Implementation Review PASS before Human merge approval.

## Self-Review

### Spec coverage

- Selectable Mission cards + drawer-opens-immediately: Task 2.
- Drawer shell, header, state ownership, responsive mount structure, Mission-switch reset wiring: Task 3.
- Exact Mission-identity filter tuple, list loading/empty/error+Retry: Task 4.
- List ordering (pending-first, newest-within-group) + row selection: Task 1 + Task 5.
- Detail placeholder/loading/error+Retry + exact-id fetch/enabled gating: Task 6.
- Full backend-authoritative detail rendering + terminal read-only badge + caller-aware fields as plain data: Task 1 + Task 7.
- Mission-switch integration (drawer stays open, explicit standalone assertion): Task 8 Scenario A.
- Detail-error cross-component isolation (list stays visible, selected row and selection state persist through error+Retry): Task 8 Scenario B.
- Narrow drill-in + Back: Task 9.
- Zero-mutation scope guard + legacy Board regression: Task 10.
- Repository-wide validation: Task 11.

Every numbered test item (1–20) from the human brief's "TEST PLAN" section maps to at least one task above (1↔Task 2, 2↔Task 4, 3↔Task 8 Scenario A, 4↔Task 8 Scenario A, 5↔Task 5, 6↔Task 5, 7↔Task 4, 8↔Task 4, 9↔Task 6, 10↔Task 8 Scenario B, 11↔Task 8 Scenario B, 12↔Task 7, 13↔Task 9, 14↔Task 9, 15↔Task 9, 16–18↔Task 7/Task 10, 19↔Task 10, 20↔Task 10). Items 10 ("detail error does not remove list") and 11 ("detail Retry preserves selection") were previously mis-mapped to Task 6 in an earlier revision of this plan — Task 6's unit test proves `ApprovalDetailPane` itself has no clearing side-effect, which is necessary but not sufficient, since the list's visibility and the `selectedApprovalRequestId` state it must not clear both live outside that pane. Task 8 Scenario B now provides the actual integration-level proof for both items.

### Mutation leakage

None. No task creates, imports, or calls `useCreateApprovalApiV1MissionApprovalsPost`, `useSubmitApprovalDecisionApiV1MissionApprovalsRequestIdDecisionsPost`, or `useSupersedeApprovalDecisionApiV1MissionApprovalsRequestIdSupersedePost`. Task 10 makes this an explicit, enforced (grep + spy) guarantee rather than an assumption.

### Checkpoint C/D leakage

None. No `Idempotency-Key` state, no dialog/confirmation component, no decision-submission wiring anywhere in Tasks 1–11.

### Board/Mission mixing

None. Every task operates exclusively on `frontend/src/components/mission/governance/**` and `frontend/src/app/mission/page.tsx`, consuming only `@/api/generated/mission-approvals/mission-approvals` and `@/api/generated/mission/mission`. `BoardApprovalsPanel.tsx`/`.test.tsx` are read-only reference points, explicitly not modified, with Task 10 adding a regression check rather than any change.

### Frontend governance reconstruction

None. `governanceStatus.ts` (Task 1) computes only *presentation* ordering/badge-variant from already-server-computed `status`/`created_at` — it never derives quorum, effective decisions, supersession, mission effect, or eligibility. Task 7 explicitly enumerates and renders every backend-derived field as-is.

### Invented APIs/paths

None. Every hook name, model field, file path, and shared UI component cited in "Repository Findings" and each task was confirmed by direct inspection of this planning worktree at `c397ccade940be3dcd76d22c9b12994e93a456a8` (grep/read of the actual generated client, actual `page.tsx`, actual `ui/` components, actual test files). No file path or symbol name in this plan is assumed or guessed.

### Result

PASS — ready for Human plan review.

## Validation (of this plan document)

- `git diff --check`: to be run and confirmed clean before commit (docs-only diff).
- `make docs-check` (or canonical equivalent): to be run and confirmed clean before commit.

## Execution Handoff

Implementation begins only after separate Human implementation approval, starting from current `main` in a fresh isolated branch/worktree (not this planning branch), executing Tasks 1–11 in order via `superpowers:executing-plans` or `superpowers:subagent-driven-development`, each task's own commit boundary respected, full validation (Task 11) run before PR handoff, and the same exact-head CI/CodeQL/Implementation-Review/Human-merge-approval gate the parent plan requires for every checkpoint.
