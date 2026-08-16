"""API request/response schemas for the Slice 5A approval routes (Checkpoint D).

Every schema here is locked to what `app.mission.approval_service` and
`app.mission.approval_read_service` actually accept/return. In particular:

- `CreateApprovalRequest` has no `reason` or `supersedes_request_id` field --
  `approval_service.create_approval_request` accepts neither. A field
  accepted by an HTTP schema but silently discarded by the service would be
  a client-facing lie about the API contract, so both are simply absent
  rather than accepted-and-dropped (Checkpoint D plan Revision 2, blocker 3).
- No schema anywhere accepts `principal_id`, `role`, `trust_level`, or a
  policy version -- every one of those is server-resolved
  (`principal_resolver.resolve_principal` / the request's pinned
  `policy_id`), never client-supplied.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlmodel import SQLModel

from app.schemas.mission import MissionCardKind


class CreateApprovalRequest(SQLModel):
    """Request body for `POST /api/v1/mission/approvals`."""

    policy_key: str
    scope_type: str
    mission_source_repo: str
    mission_card_kind: MissionCardKind
    mission_card_number: int
    action_key: str | None = None
    expires_at: datetime | None = None


class ApprovalRequestResponse(SQLModel):
    """Response body for `POST /api/v1/mission/approvals`."""

    request_id: UUID
    policy_key: str
    policy_version: int
    status: str
    created_by_principal_id: UUID
    created_at: datetime
    expires_at: datetime | None


class SubmitDecisionRequest(SQLModel):
    """Request body for `POST /api/v1/mission/approvals/{request_id}/decisions`."""

    decision: Literal["approve", "reject"]
    reason: str | None = None


class SupersedeDecisionRequest(SQLModel):
    """Request body for `POST /api/v1/mission/approvals/{request_id}/supersede`."""

    supersedes_decision_id: UUID
    decision: Literal["approve", "reject"]
    reason: str | None = None


class ApprovalDecisionResponse(SQLModel):
    """Response body for the submit-decision and supersede-decision routes."""

    request_id: UUID
    decision_id: UUID
    principal_id: UUID
    decision: str
    reason: str | None
    status: str
    quorum_satisfied: bool
    mission_effect: str | None
    created_at: datetime


class ApprovalListItem(SQLModel):
    """One row of `GET /api/v1/mission/approvals`."""

    request_id: UUID
    status: str
    mission_source_repo: str
    mission_card_kind: str
    mission_card_number: int
    action_key: str | None
    policy_key: str
    policy_version: int
    created_at: datetime
    expires_at: datetime | None


class EffectiveDecisionView(SQLModel):
    """One currently-effective decision on a request, for the detail read model."""

    principal_id: UUID
    decision: str
    reason: str | None
    role_slugs_at_decision: list[str]
    created_at: datetime


class LifecycleEventView(SQLModel):
    """One lifecycle event on a request, for the detail read model."""

    event_type: str
    triggered_by_principal_id: UUID | None
    detail: dict[str, Any] | None
    created_at: datetime


class QuorumRequirementView(SQLModel):
    """One quorum slot's eligibility and current satisfaction state.

    `satisfied` is derived exclusively from `evaluate_approval()`'s own
    canonical `missing_requirements` list -- never a second/independent
    matching computation -- so read-time quorum state cannot drift from
    write-time evaluation. No principal-to-slot assignment is exposed here:
    when more than one maximum matching exists, the specific witness
    assignment is not itself meaningful (see `approval_evaluator`'s module
    docstring), only the matching's size is.
    """

    slot: str
    eligible_roles: list[str]
    satisfied: bool


class ApprovalDetailResponse(SQLModel):
    """Response body for `GET /api/v1/mission/approvals/{request_id}`.

    Fully backend-derived: the frontend must not reconstruct supersession
    chains, effective decisions, quorum, or mission effect from any of this
    response's fields -- every one of those is computed here.
    """

    request_id: UUID
    status: str
    mission_source_repo: str
    mission_card_kind: str
    mission_card_number: int
    action_key: str | None
    policy_key: str
    policy_version: int
    decision_rule: str
    quorum_satisfied: bool
    quorum_requirements: list[QuorumRequirementView]
    missing_requirements: list[str]
    effective_decisions: list[EffectiveDecisionView]
    lifecycle: list[LifecycleEventView]
    created_at: datetime
    expires_at: datetime | None
    resolved_at: datetime | None
    mission_effect: str | None
