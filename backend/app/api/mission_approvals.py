"""Slice 5A API: approval read + mutation routes (Checkpoint D).

Mutation routes call `app.mission.approval_service` directly with only the
authenticated `AuthContext` and API-level arguments -- they never open a
session or resolve a principal themselves; each service function opens its
own fresh session and resolves the principal internally (see
`approval_service`'s module docstring). Read routes use the ordinary
request-scoped session (`get_session`), since they perform no writes.

`Idempotency-Key` is required on all three mutation routes (the service
functions accept no default) and is validated here before the service is
ever called -- a missing/empty header never reaches the service layer.

The header is declared as a *required* FastAPI `Header` parameter (no
`default=`), not `str | None = Header(default=None)`, so the OpenAPI schema
-- and therefore the generated Orval client -- accurately describes it as
required. A wholly missing header is rejected by FastAPI's own request
validation (422) before the route body ever runs; `_require_idempotency_key`
exists only to additionally reject a *present but blank/whitespace-only*
header (400 `idempotency_key_required`), which FastAPI's own string-type
validation would otherwise accept.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import require_user_auth
from app.core.auth import AuthContext
from app.db.session import get_session
from app.mission import approval_read_service
from app.mission.approval_errors import to_http_exception
from app.mission.approval_service import (
    ApprovalServiceError,
    create_approval_request,
    submit_decision,
    supersede_decision,
)
from app.mission.principal_resolver import PrincipalResolutionError
from app.schemas.mission import MissionCardKind
from app.schemas.mission_approvals import (
    ApprovalDecisionResponse,
    ApprovalDetailResponse,
    ApprovalListItem,
    ApprovalRequestResponse,
    CreateApprovalRequest,
    SubmitDecisionRequest,
    SupersedeDecisionRequest,
)
from app.schemas.pagination import DefaultLimitOffsetPage

_PARTIAL_MISSION_FILTER_TUPLE = HTTPException(
    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    detail={
        "code": "partial_mission_filter_tuple",
        "message": (
            "mission_source_repo, mission_card_kind, and mission_card_number "
            "must be supplied together or not at all"
        ),
    },
)

router = APIRouter(prefix="/mission/approvals", tags=["mission-approvals"])
AUTH_DEP = Depends(require_user_auth)
SESSION_DEP = Depends(get_session)

_IDEMPOTENCY_KEY_REQUIRED = HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST,
    detail={
        "code": "idempotency_key_required",
        "message": "the Idempotency-Key header is required for this route",
    },
)


def _require_idempotency_key(idempotency_key: str) -> str:
    if not idempotency_key.strip():
        raise _IDEMPOTENCY_KEY_REQUIRED
    return idempotency_key


@router.get(
    "",
    response_model=DefaultLimitOffsetPage[ApprovalListItem],
    summary="List internal governance approval requests",
    description=(
        "Read-only, backend-derived list of Mission Control's own internal "
        "governance approval requests (ADR-23 D8a). Never touches GitHub."
    ),
)
async def list_approvals(
    auth: AuthContext = AUTH_DEP,
    session: AsyncSession = SESSION_DEP,
    mission_source_repo: str | None = None,
    mission_card_kind: MissionCardKind | None = None,
    mission_card_number: int | None = None,
) -> DefaultLimitOffsetPage[ApprovalListItem]:
    """Return a paginated list of approval requests.

    Either all three Mission filters are supplied (exact Mission-identity
    filtering, applied in SQL before pagination) or none are (existing
    backward-compatible global list). A partial tuple is rejected with 422
    before the service layer is ever called.
    """
    _ = auth
    parts = (mission_source_repo, mission_card_kind, mission_card_number)
    if any(part is not None for part in parts) and not all(part is not None for part in parts):
        raise _PARTIAL_MISSION_FILTER_TUPLE
    return await approval_read_service.list_approvals(
        session,
        mission_source_repo=mission_source_repo,
        mission_card_kind=mission_card_kind,
        mission_card_number=mission_card_number,
    )


@router.get(
    "/{request_id}",
    response_model=ApprovalDetailResponse,
    summary="Get one approval request's full backend-derived detail view",
    description=(
        "Quorum state, effective decisions, and lifecycle history are all "
        "computed server-side. The frontend must not reconstruct any of "
        "them from other endpoints."
    ),
)
async def get_approval_detail(
    request_id: UUID,
    auth: AuthContext = AUTH_DEP,
    session: AsyncSession = SESSION_DEP,
) -> ApprovalDetailResponse:
    """Return the detail view for one approval request."""
    _ = auth
    detail = await approval_read_service.get_approval_detail(session, request_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "approval_request_not_found",
                "message": f"no approval request {request_id}",
            },
        )
    return detail


@router.post(
    "",
    response_model=ApprovalRequestResponse,
    summary="Create an internal governance approval request",
    description=(
        "ADR-23 D8a mutation route. Persists only Mission-Control-owned "
        "approval state; never mutates GitHub. The acting principal is "
        "always server-resolved from authenticated identity."
    ),
)
async def create_approval(
    body: CreateApprovalRequest,
    auth: AuthContext = AUTH_DEP,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> ApprovalRequestResponse:
    """Create a new approval request."""
    key = _require_idempotency_key(idempotency_key)
    try:
        result = await create_approval_request(
            auth,
            policy_key=body.policy_key,
            scope_type=body.scope_type,
            mission_source_repo=body.mission_source_repo,
            mission_card_kind=body.mission_card_kind,
            mission_card_number=body.mission_card_number,
            action_key=body.action_key,
            expires_at=body.expires_at,
            idempotency_key=key,
        )
    except (PrincipalResolutionError, ApprovalServiceError) as exc:
        raise to_http_exception(exc) from exc
    return ApprovalRequestResponse(
        request_id=result.request_id,
        policy_key=result.policy_key,
        policy_version=result.policy_version,
        status=result.status,
        created_by_principal_id=result.created_by_principal_id,
        created_at=result.created_at,
        expires_at=result.expires_at,
    )


@router.post(
    "/{request_id}/decisions",
    response_model=ApprovalDecisionResponse,
    summary="Submit a decision on an approval request",
    description=(
        "ADR-23 D8a mutation route. One principal contributes at most one "
        "effective vote per request; quorum/status evaluation is entirely "
        "backend-derived."
    ),
)
async def submit_approval_decision(
    request_id: UUID,
    body: SubmitDecisionRequest,
    auth: AuthContext = AUTH_DEP,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> ApprovalDecisionResponse:
    """Cast a decision on an open approval request."""
    key = _require_idempotency_key(idempotency_key)
    try:
        result = await submit_decision(
            auth,
            request_id=request_id,
            decision=body.decision,
            reason=body.reason,
            idempotency_key=key,
        )
    except (PrincipalResolutionError, ApprovalServiceError) as exc:
        raise to_http_exception(exc) from exc
    return ApprovalDecisionResponse(
        request_id=result.request_id,
        decision_id=result.decision_id,
        principal_id=result.principal_id,
        decision=result.decision,
        reason=result.reason,
        status=result.status,
        quorum_satisfied=result.quorum_satisfied,
        mission_effect=result.mission_effect,
        created_at=result.created_at,
    )


@router.post(
    "/{request_id}/supersede",
    response_model=ApprovalDecisionResponse,
    summary="Supersede the caller's own prior decision on an approval request",
    description=(
        "ADR-23 D8a mutation route. A principal may only supersede its own "
        "still-effective decision; ownership, currently-effective, and "
        "terminal-state checks all happen in the service layer, not here."
    ),
)
async def supersede_approval_decision(
    request_id: UUID,
    body: SupersedeDecisionRequest,
    auth: AuthContext = AUTH_DEP,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> ApprovalDecisionResponse:
    """Supersede the caller's own prior decision on an open approval request."""
    key = _require_idempotency_key(idempotency_key)
    try:
        result = await supersede_decision(
            auth,
            request_id=request_id,
            decision_id=body.supersedes_decision_id,
            decision=body.decision,
            reason=body.reason,
            idempotency_key=key,
        )
    except (PrincipalResolutionError, ApprovalServiceError) as exc:
        raise to_http_exception(exc) from exc
    return ApprovalDecisionResponse(
        request_id=result.request_id,
        decision_id=result.decision_id,
        principal_id=result.principal_id,
        decision=result.decision,
        reason=result.reason,
        status=result.status,
        quorum_satisfied=result.quorum_satisfied,
        mission_effect=result.mission_effect,
        created_at=result.created_at,
    )
