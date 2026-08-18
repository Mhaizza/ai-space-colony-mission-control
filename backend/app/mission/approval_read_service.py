"""Read-only query service for the Slice 5A approval domain (Checkpoint D).

Mirrors `app.mission.read_service`'s pattern (session in, schema-shaped
objects out) for the Checkpoint B/C approval tables. This module performs no
writes.

Quorum/mission-effect state is never persisted on `mc_approval_request` --
only `status` is. Both are recomputed live here via
`approval_evaluator.evaluate_approval()`, fed by
`approval_service.effective_decisions()` (the same supersession-aware
decision-filtering helper the write path uses), so the read path can never
drift from write-time evaluation semantics. `QuorumRequirementView.satisfied`
is derived purely from the evaluator's own canonical `missing_requirements`
list -- no second/independent matching algorithm exists in this module, and
no principal-to-slot assignment is ever exposed (see that view's docstring).
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import utcnow
from app.db.pagination import paginate
from app.mission.approval_evaluator import EffectiveDecision, evaluate_approval
from app.mission.approval_service import (
    can_principal_decide,
    effective_decisions,
    validate_policy_definition,
)
from app.mission.principal_resolver import ResolvedPrincipal
from app.models.mc_approval import (
    McApprovalEvent,
    McApprovalPolicy,
    McApprovalRequest,
)
from app.schemas.mission_approvals import (
    ApprovalDetailResponse,
    ApprovalListItem,
    CurrentPrincipalDecisionView,
    EffectiveDecisionView,
    LifecycleEventView,
    QuorumRequirementView,
)
from app.schemas.pagination import DefaultLimitOffsetPage


async def list_approvals(
    session: AsyncSession,
    *,
    mission_source_repo: str | None = None,
    mission_card_kind: str | None = None,
    mission_card_number: int | None = None,
) -> DefaultLimitOffsetPage[ApprovalListItem]:
    """Return a paginated list of approval requests, newest first.

    When all three Mission filters are supplied, they are applied as SQL
    `WHERE` predicates before `paginate()` runs -- never as a Python-side
    filter over an already-paginated page. Callers must supply either all
    three filters or none (partial tuples are rejected at the API layer
    before this function is ever called).
    """
    statement = (
        select(McApprovalRequest, McApprovalPolicy)
        .join(McApprovalPolicy, col(McApprovalRequest.policy_id) == col(McApprovalPolicy.id))
    )
    if (
        mission_source_repo is not None
        and mission_card_kind is not None
        and mission_card_number is not None
    ):
        statement = statement.where(
            col(McApprovalRequest.mission_source_repo) == mission_source_repo,
            col(McApprovalRequest.mission_card_kind) == mission_card_kind,
            col(McApprovalRequest.mission_card_number) == mission_card_number,
        )
    statement = statement.order_by(col(McApprovalRequest.created_at).desc())

    def _transform(rows: Sequence[object]) -> Sequence[ApprovalListItem]:
        items: list[ApprovalListItem] = []
        for row in rows:
            # `row` is a SQLAlchemy `Row` (2-tuple-like, not a plain `tuple`
            # instance) when fastapi-pagination executes a multi-entity
            # `select()` under the hood -- index positionally rather than
            # isinstance-checking against `tuple`.
            request_raw: object = row[0]  # type: ignore[index]
            policy_raw: object = row[1]  # type: ignore[index]
            if not isinstance(request_raw, McApprovalRequest) or not isinstance(
                policy_raw, McApprovalPolicy
            ):  # pragma: no cover - defensive
                msg = "Expected (McApprovalRequest, McApprovalPolicy) rows from approvals pagination query."
                raise TypeError(msg)
            request: McApprovalRequest = request_raw
            policy: McApprovalPolicy = policy_raw
            items.append(
                ApprovalListItem(
                    request_id=request.id,
                    status=request.status,
                    mission_source_repo=request.mission_source_repo,
                    mission_card_kind=request.mission_card_kind,
                    mission_card_number=request.mission_card_number,
                    action_key=request.action_key,
                    policy_key=policy.policy_key,
                    policy_version=policy.version,
                    created_at=request.created_at,
                    expires_at=request.expires_at,
                )
            )
        return items

    return await paginate(session, statement, transformer=_transform)


async def get_approval_detail(
    session: AsyncSession, request_id: UUID, *, principal: ResolvedPrincipal
) -> ApprovalDetailResponse | None:
    """Return the full backend-derived detail view for one request, or `None` if not found.

    `principal` is the already-resolved, authenticated caller (resolved by
    the route from server-verified `AuthContext` via `resolve_principal`,
    never from client input). It drives two caller-specific fields:
    `can_decide` (via the shared `can_principal_decide` eligibility check --
    never a second/independent algorithm) and `current_principal_decision`
    (the caller's own entry in the same supersession-aware
    `effective_decisions()` list the write path uses, never a separately
    reconstructed chain).
    """
    request = await session.get(McApprovalRequest, request_id)
    if request is None:
        return None

    policy = await session.get(McApprovalPolicy, request.policy_id)
    assert policy is not None  # guarded by FK
    definition = validate_policy_definition(policy)

    decisions = await effective_decisions(session, request_id)
    evaluation = evaluate_approval(
        definition,
        [
            EffectiveDecision(
                principal_id=d.principal_id,
                decision=d.decision,  # type: ignore[arg-type]
                role_slugs_at_decision=frozenset(d.role_slugs_at_decision),
                trust_level_at_decision=d.trust_level_at_decision,
            )
            for d in decisions
        ],
        utcnow(),
    )

    quorum_requirements = [
        QuorumRequirementView(
            slot=slot.slot,
            eligible_roles=slot.eligible_roles,
            satisfied=slot.slot not in evaluation.missing_requirements,
        )
        for slot in definition.quorum.slots
    ]

    events = (
        await session.exec(
            select(McApprovalEvent)
            .where(col(McApprovalEvent.request_id) == request_id)
            .order_by(col(McApprovalEvent.created_at).asc())
        )
    ).all()

    own_decision = next((d for d in decisions if d.principal_id == principal.id), None)
    current_principal_decision = (
        CurrentPrincipalDecisionView(
            decision_id=own_decision.id,
            decision=own_decision.decision,
            reason=own_decision.reason,
            created_at=own_decision.created_at,
        )
        if own_decision is not None
        else None
    )

    return ApprovalDetailResponse(
        request_id=request.id,
        status=request.status,
        mission_source_repo=request.mission_source_repo,
        mission_card_kind=request.mission_card_kind,
        mission_card_number=request.mission_card_number,
        action_key=request.action_key,
        policy_key=policy.policy_key,
        policy_version=policy.version,
        decision_rule=definition.decision_rule,
        quorum_satisfied=evaluation.quorum_satisfied,
        quorum_requirements=quorum_requirements,
        missing_requirements=evaluation.missing_requirements,
        effective_decisions=[
            EffectiveDecisionView(
                principal_id=d.principal_id,
                decision=d.decision,
                reason=d.reason,
                role_slugs_at_decision=list(d.role_slugs_at_decision),
                created_at=d.created_at,
            )
            for d in decisions
        ],
        lifecycle=[
            LifecycleEventView(
                event_type=event.event_type,
                triggered_by_principal_id=event.triggered_by_principal_id,
                detail=event.detail,
                created_at=event.created_at,
            )
            for event in events
        ],
        created_at=request.created_at,
        expires_at=request.expires_at,
        resolved_at=request.resolved_at,
        mission_effect=evaluation.mission_effect,
        can_decide=can_principal_decide(
            principal=principal, request=request, definition=definition
        ),
        current_principal_decision=current_principal_decision,
    )
