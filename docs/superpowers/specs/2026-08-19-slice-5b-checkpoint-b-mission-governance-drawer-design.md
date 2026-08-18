# Slice 5B Checkpoint B — Mission Governance Drawer — Design

## Status

Human-approved design direction (Sections 1-4 as delivered in the Checkpoint B closeout brief). This document is a design artifact only. Production implementation follows a separately reviewed implementation plan, produced only after this spec is Human-approved.

## Context / current state

- Repository: `Mhaizza/ai-space-colony-mission-control`
- Baseline: `main @ c397ccade940be3dcd76d22c9b12994e93a456a8`
- Slice 5B Checkpoint A is merged into `main` and verified present at this baseline:
  - `MissionCard.source_repo: string` (backend schema + generated Orval type).
  - `GET /api/v1/mission/approvals` accepts optional `mission_source_repo` / `mission_card_kind` / `mission_card_number` query filters (all-or-nothing, SQL-filtered before pagination), generated as `ListApprovalsApiV1MissionApprovalsGetParams`.
  - `ApprovalDetailResponse.can_decide: boolean` and `ApprovalDetailResponse.current_principal_decision: CurrentPrincipalDecisionView | null`, both caller-specific and backend-derived.
- `frontend/src/app/mission/page.tsx` (588 lines) currently renders a `Cards` section as static, non-interactive rows (`div` per card, keyed `${card.kind}-${card.number}`) inside the existing dashboard overview. No card is clickable and no governance UI exists on this page today.
- The legacy Board approval domain (`frontend/src/components/BoardApprovalsPanel.tsx` + its test) is a separate, semantically distinct system and is not touched by this design.
- Parent design: `docs/superpowers/specs/2026-08-18-slice-5b-mission-operations-ux-design.md`. Parent plan: `docs/superpowers/plans/2026-08-18-slice-5b-mission-operations-ux.md` (Checkpoint B outline, Tasks B1-B3). This document supersedes the parent plan's Checkpoint B section with the fuller, Human-approved drawer architecture below; it does not contradict the parent design's goals, non-goals, or security invariants.

## Checkpoint B goal

Add a read-only Mission Governance Drawer to the existing `/mission` page: selecting a projected Mission card opens a drawer that lists every backend-filtered approval request for that card's trusted Mission identity, and lets the operator inspect full backend-derived detail for any one of them. No decision can be taken yet.

## Scope

- Make Mission cards on `/mission` selectable.
- Open a right-side governance drawer on selection, scoped to the selected card's exact Mission identity (`source_repo` + `kind` + `number`).
- Render the server-filtered approval list inside the drawer (list pane).
- Render full backend-derived approval detail for a selected request (detail pane).
- Handle loading, empty, error, and terminal states for both panes, isolated from each other and from the rest of `/mission`.
- Responsive behavior: two-column drawer body on desktop, list→detail drill-in with Back on narrow viewports.
- Read-only rendering of `can_decide` / `current_principal_decision` as plain detail data (no controls).

## Non-goals

Everything under "Hard out of scope" below is explicitly deferred, not merely unplanned. In particular:

- No mutation UI or mutation calls of any kind (approve, reject, change decision, create).
- No `Idempotency-Key` frontend state or mutation retry lifecycle.
- No new Mission route tree — this stays on the existing `/mission` page.
- No backend/API contract changes. This checkpoint consumes Checkpoint A's contracts exactly as merged.
- No policy editor, principal/role admin, notifications, bulk approvals, AI approver UI, or global approvals inbox.
- No GitHub writes or GitHub mutation actions (review/comment/merge/assignment).
- No database migration.
- No semantic reuse, merge, or reinterpretation of the legacy Board approval domain.

## Approved architecture

A dedicated Mission governance drawer, layered on top of the existing `/mission` page rather than a new route.

```text
/mission
  page.tsx
    owns: selectedMissionCard
    └── Cards section (existing) -- cards become selectable
          └── click a card
                └── MissionGovernanceDrawer (opens immediately)
                      owns: selectedApprovalRequestId
                      ├── ApprovalListPane  (left column / step 1 on narrow)
                      └── ApprovalDetailPane (right column / step 2 on narrow)
```

Desktop: right-side drawer, two-column body — list on the left, detail on the right, both visible simultaneously.

Mobile/narrow: drill-in flow — list is shown first; selecting an approval replaces the visible pane with detail plus a Back control; Back returns to the list without losing the Mission selection.

The drawer header always shows the selected Mission's trusted identity and title: `source_repo` + `kind` + `#number` + `title`.

No new Mission route tree. No mutation UI in this checkpoint.

## Component responsibilities

Suggested decomposition (exact file split may adapt to existing repo conventions — e.g. colocated test placement, existing `components/mission/` or `components/organisms/` patterns — but must preserve these responsibilities and must not merge them into fewer components that blur ownership):

- `frontend/src/app/mission/page.tsx` — owns `selectedMissionCard`; renders the (now-selectable) Cards section; mounts `MissionGovernanceDrawer` when a card is selected.
- `frontend/src/components/mission/governance/MissionGovernanceDrawer.tsx` — owns `selectedApprovalRequestId`; owns the responsive drawer shell (desktop two-column vs. narrow drill-in); renders the drawer header (`source_repo` + `kind` + `#number` + `title`); mounts `ApprovalListPane` and `ApprovalDetailPane`; resets `selectedApprovalRequestId` to `null` whenever `selectedMissionCard` changes.
- `frontend/src/components/mission/governance/ApprovalListPane.tsx` — consumes the generated Mission-filtered list hook for the drawer's Mission identity; renders loading/empty/error/Retry states; renders the ordered approval rows; reports selection up to the drawer.
- `frontend/src/components/mission/governance/ApprovalDetailPane.tsx` — consumes the generated detail hook for `selectedApprovalRequestId`; renders the placeholder state when nothing is selected; renders loading/error/Retry states; renders full backend-derived detail, including terminal read-only rendering and the caller-aware fields as plain data.

## State ownership

Exactly two pieces of Checkpoint-B-owned client state, each owned by exactly one component:

```ts
// owned by frontend/src/app/mission/page.tsx
selectedMissionCard: {
  source_repo: string;
  kind: MissionCard["kind"];
  number: number;
  title: string | null;
} | null;

// owned by MissionGovernanceDrawer.tsx
selectedApprovalRequestId: string | null;
```

No other component introduces parallel selection state. `ApprovalListPane` and `ApprovalDetailPane` are controlled by props/callbacks from `MissionGovernanceDrawer`; they hold no selection state of their own (transient local UI state such as a Retry-button pending flag is fine, but never a second copy of `selectedMissionCard` or `selectedApprovalRequestId`).

## Data flow

Card selection:

```text
click Mission card
  → page.tsx sets selectedMissionCard
  → MissionGovernanceDrawer opens immediately (does not wait for list data)
  → ApprovalListPane fetches using the generated list hook with exactly:
        mission_source_repo = selectedMissionCard.source_repo
        mission_card_kind   = selectedMissionCard.kind
        mission_card_number = selectedMissionCard.number
```

Approval selection:

```text
select an approval row in ApprovalListPane
  → MissionGovernanceDrawer sets selectedApprovalRequestId
  → ApprovalDetailPane fetches GET /api/v1/mission/approvals/{request_id}
    via the generated detail hook
  → renders backend-authoritative detail
```

Mission switch (while drawer is open):

```text
Mission A selected, Approval X selected
  → user clicks Mission B card
  → page.tsx updates selectedMissionCard to Mission B
  → drawer stays open
  → MissionGovernanceDrawer resets selectedApprovalRequestId to null
  → ApprovalListPane re-fetches for Mission B's identity
  → ApprovalDetailPane returns to the "Select an approval" placeholder
```

No state from Mission A's list or detail may leak into Mission B's render (no stale cache display without a matching identity — the generated hook's own query-key scoping by the filter params is sufficient here since each distinct identity is a distinct cache entry; the reset above is what prevents the *previously selected request id* from carrying over to a Mission it doesn't belong to).

## Mission identity rules

- The only source of Mission identity is the selected card's own trusted fields: `source_repo`, `kind`, `number` (all originating from Checkpoint A's `MissionCard.source_repo` plus the existing `kind`/`number` fields).
- No code path parses `MissionCard.url` to derive or cross-check identity.
- No hardcoded repository name anywhere in the drawer.
- All three filter parameters are always sent together to the generated list hook; Checkpoint B never sends a partial filter tuple (the backend would reject it with 422, and no code path should construct that request in the first place).
- No client-side filtering of a globally-fetched approvals page. The generated hook is called with the filters as query parameters; filtering happens server-side per Checkpoint A's contract.
- Only the generated Orval client/hooks are used for these calls. No parallel hand-written Mission approvals client is introduced.

## List ordering

Presentation order inside `ApprovalListPane`:

1. Pending requests first.
2. Newest-first within the pending group.
3. Terminal requests (Approved / Rejected / Expired / Superseded) after all pending requests.
4. Newest-first within the terminal group.

Grouping/ordering uses only the backend `status` and `created_at` fields already present on `ApprovalListItem`; the frontend performs no "most important" heuristic beyond this fixed two-group, newest-first-within-group rule. Historical/terminal approvals are never hidden or truncated out of the list.

## Detail behavior

- On first list load, no approval is auto-selected. `ApprovalDetailPane` shows the placeholder: `Select an approval to view details`.
- Selecting a row sets `selectedApprovalRequestId` and triggers exactly one detail fetch for that id.
- The detail pane renders every backend-derived field returned by `ApprovalDetailResponse` (see "Backend-authoritative governance boundary" below) — nothing is computed or inferred client-side.
- `can_decide` and `current_principal_decision` are read and may be displayed as plain informational data (e.g. "you can decide on this request" or showing the caller's own recorded decision), but render no interactive control in this checkpoint. No `Approve`/`Reject`/`Change decision` affordance exists anywhere in Checkpoint B, regardless of `can_decide`'s value.

## Loading states

- **Drawer opening**: the drawer opens immediately on card selection; it never waits for the list request to resolve before becoming visible.
- **List loading**: `ApprovalListPane` shows a localized list skeleton scoped to the pane itself — never a global/full-screen spinner, and never one that blocks the rest of `/mission` or the drawer chrome.
- **Detail loading**: selecting an approval shows a loading skeleton inside `ApprovalDetailPane` alone; `ApprovalListPane` remains visible, stable, and interactive (the previously rendered list does not unmount or blank out while detail loads).

## Empty states

- Zero approvals for the selected Mission: `ApprovalListPane` shows `No approvals for this Mission`; `ApprovalDetailPane` keeps showing `Select an approval to view details`. The drawer does not auto-close.

## Error isolation

- **List error**: the drawer stays open; `ApprovalListPane` shows a localized error with a `Retry` control that re-issues only the list request. The drawer is never closed on a list error, and no frontend-computed fallback governance data is substituted.
- **Detail error**: `ApprovalListPane` remains available and the previously selected row stays selected (selection state is not cleared by a detail failure); `ApprovalDetailPane` shows a localized error with a `Retry` control that re-issues only the detail request for the same `selectedApprovalRequestId`. Neither the Mission selection nor the approval selection is reset by a detail error.

## Terminal/read-only behavior

For statuses `Approved`, `Rejected`, `Expired`, or `Superseded`, `ApprovalDetailPane` renders the exact same full detail view as for a pending request — status, policy/version, quorum status, quorum requirements, missing requirements, effective decisions, lifecycle history, timestamps, and mission effect are all still shown — but visibly marked read-only (e.g. a status badge and the structural absence of any mutation affordance; Checkpoint B has no mutation affordances at all, so this is automatically satisfied, but the visual "this is closed" signal — such as the terminal status badge itself — must still be present). Governance history for terminal requests is never hidden, collapsed away, or omitted.

## Desktop behavior

```text
[ Drawer header: source_repo · kind · #number · title ]
[ Approval List Pane  |  Approval Detail Pane ]
```

Both panes are visible simultaneously; selecting a row in the list pane updates the detail pane in place without navigating away from the list.

## Mobile/narrow behavior

```text
[ Drawer header ]
[ Approval List Pane ]
   ↓ select a row
[ Approval Detail Pane ]
[ ← Back ]
```

Back returns to the list pane on the same Mission selection (it does not close the drawer or reset `selectedMissionCard`). A full-screen modal is explicitly not the approved narrow-viewport pattern, and vertically stacking a long list above the detail pane as the primary narrow-screen layout is explicitly not approved either — the drill-in (list, then detail, then Back) is the only approved narrow behavior.

## Backend-authoritative governance boundary

The frontend renders, and never (re)computes, any of:

- `status`
- `policy_key` / `policy_version`
- `quorum_satisfied`
- `quorum_requirements`
- `missing_requirements`
- `effective_decisions`
- `lifecycle`
- `created_at` / `expires_at` / `resolved_at`
- `mission_effect`
- `can_decide`
- `current_principal_decision`

In particular, Checkpoint B contains no frontend logic that derives quorum, effective-decision/supersession chains, approval status, mission effect, or policy/trust eligibility. Every one of those values is read as-is from `ApprovalDetailResponse` (or `ApprovalListItem` for list-level summary fields) and displayed.

## Legacy Board separation

The legacy Board approval domain (`BoardApprovalsPanel`, `/boards/...` approval UI, the Board approval schemas/routes/generated client) is a separate system. Checkpoint B may visually reuse UI patterns from it where useful (e.g. card/list/detail layout conventions), but:

- No Mission governance component imports from or extends `BoardApprovalsPanel` or its Board-domain hooks/types.
- No Board approval route, schema, generated client symbol, or semantic behavior is modified.
- Mission governance state (`selectedMissionCard`, `selectedApprovalRequestId`) is never derived from or merged with Board approval state.

## Security / ADR-23 constraints

- All data consumed by the drawer comes from Checkpoint A's already-merged, unmodified read routes (`GET /api/v1/mission/approvals`, `GET /api/v1/mission/approvals/{request_id}`). No backend route, schema, or contract is changed by this checkpoint.
- No new mutation route, ADR-23 allowlist entry, or GitHub write capability is introduced — Checkpoint B issues no mutating requests at all.
- No database migration.
- `can_decide` / `current_principal_decision` are rendered strictly as read-only, backend-resolved information; the frontend does not use them to construct, imply, or enable any write action in this checkpoint.
- If implementation later discovers a genuine blocker in the Checkpoint A contract (missing field, wrong type, insufficient filter), that is reported as a blocker rather than silently worked around by widening this checkpoint's scope or reinterpreting the backend contract.

## Testing strategy

Vitest + Testing Library, following existing repo conventions for colocated/repo-standard test placement (matching how `BoardApprovalsPanel.test.tsx` and other `frontend/src/**/*.test.tsx` files are already placed next to their component).

Tests must pin at minimum:

1. Clicking a Mission card opens the drawer.
2. The list query is issued with exactly `source_repo`, `kind`, `number` from the selected card — no partial tuple, no derived/parsed values.
3. Switching to a different Mission card resets `selectedApprovalRequestId` to `null` and re-fetches the list for the new identity.
4. Pending approvals render before terminal approvals.
5. Newest-first ordering holds within each group (pending and terminal).
6. Zero approvals renders the exact empty-state copy and leaves the detail pane on its placeholder.
7. A list-fetch failure renders a localized error with a working `Retry` that re-issues only the list request, without closing the drawer.
8. Selecting an approval row triggers a detail request for exactly that row's `request_id`.
9. A detail-fetch failure does not remove or clear the list, and does not reset the Mission or approval selection.
10. A terminal-status approval renders the full detail view (all fields listed in "Backend-authoritative governance boundary" above are present) and is visibly marked read-only.
11. At a narrow viewport, the list is shown first and selecting an approval shows detail via drill-in (not two-column, not a modal).
12. At a narrow viewport, `Back` returns from detail to the list without losing the Mission selection.
13. No `Approve` control is rendered anywhere in the drawer, at any status, for any `can_decide` value.
14. No `Reject` control is rendered anywhere in the drawer, at any status, for any `can_decide` value.
15. No `Change decision` control is rendered anywhere in the drawer, at any status, for any `can_decide`/`current_principal_decision` combination.
16. No approval mutation hook (create/decision/supersede) is invoked by any Checkpoint B code path, under any test scenario in this suite.
17. Legacy Board approval UX/semantics (`BoardApprovalsPanel` and its existing tests) remain unchanged and passing.

## Acceptance criteria

Checkpoint B is acceptable when an authenticated human can, on the existing `/mission` page:

- select any projected Issue or PR card and see a governance drawer open immediately for that card's trusted Mission identity;
- see every approval request for that Mission, grouped pending-first then terminal, newest-first within each group, with no approvals silently hidden;
- select any one request and see its full backend-derived detail, including terminal requests rendered read-only with complete history;
- switch to a different Mission card without any stale approval selection or cross-Mission data leakage;
- recover from a list or detail load failure via a scoped `Retry` without losing the drawer, the Mission selection, or (for a detail failure) the approval selection;
- use the drawer correctly on both desktop (two-column) and narrow viewports (list→detail drill-in with Back);

and additionally, for engineering acceptance:

- zero mutation UI, zero mutation hook invocations, and zero `Idempotency-Key` handling anywhere in the new code;
- zero backend/API/schema changes;
- zero changes to the legacy Board approval domain's files or behavior;
- the frontend performs no quorum, effective-decision, supersession, status, or mission-effect computation.

## Explicit Checkpoint C/D deferrals

The following are out of scope for Checkpoint B and are explicitly deferred to later checkpoints, not silently dropped:

- **Checkpoint C** — Approve/Reject decision UX: the `DecisionDialog`, per-intent `Idempotency-Key` lifecycle, and the generated submit-decision mutation hook, gated on `can_decide === true` and `current_principal_decision == null`.
- **Checkpoint D** — Change-decision UX: the `Change decision` action from the caller's own effective decision, using the existing supersede mutation, gated on `can_decide === true` and `current_principal_decision != null`.

Neither checkpoint's UI, mutation wiring, or idempotency handling is present, scaffolded, or partially implemented in Checkpoint B.
