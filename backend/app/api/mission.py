"""Mission Control Slice 3 API: manual read-only refresh."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import require_user_auth
from app.core.auth import AuthContext
from app.core.config import settings
from app.db.session import get_session
from app.mission import read_service
from app.mission.github_client import GitHubReadClient
from app.mission.principal_registry import parse_principal_registry_json
from app.mission.sync import GitHubSyncService, SyncConfig
from app.schemas.mission import (
    MissionAuditSummary,
    MissionOverview,
    MissionPRStatusSummary,
    MissionQuarantineSummary,
    MissionWorkflowSummary,
)

router = APIRouter(prefix="/mission", tags=["mission"])
AUTH_DEP = Depends(require_user_auth)
SESSION_DEP = Depends(get_session)

QUARANTINE_LIMIT_QUERY = Query(default=50, ge=1, le=200)
CARD_LIMIT_QUERY = Query(default=100, ge=1, le=500)
RECORD_LIMIT_QUERY = Query(default=100, ge=1, le=500)


class MissionRefreshResponse(BaseModel):
    ok: bool
    partial: bool
    projected: int
    quarantined: int
    tombstoned: int = 0
    errors: list[str] = Field(default_factory=list)
    effective_assignments: dict[str, dict[str, str]] = Field(default_factory=dict)


@router.post(
    "/refresh",
    response_model=MissionRefreshResponse,
    summary="Manual read-only GitHub sync refresh",
    description=(
        "Triggers the same outbound read-only GitHub sync path used by polling. "
        "Mutates no GitHub state (ADR-23 D3/D8 manual-refresh exception)."
    ),
)
async def refresh_mission_projection(
    auth: AuthContext = AUTH_DEP,
    session: AsyncSession = SESSION_DEP,
) -> MissionRefreshResponse:
    """Invoke read-only sync once."""
    _ = auth
    if not settings.github_adapter_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "github_adapter_disabled",
                "message": "GitHub adapter is not enabled (missing GITHUB_PAT or config).",
            },
        )

    token = settings.github_pat
    registry = parse_principal_registry_json(settings.mc_principal_registry_json)
    client = GitHubReadClient(token=token)
    try:
        service = GitHubSyncService(
            client=client,
            registry=registry,
            config=SyncConfig(
                project_owner=settings.github_project_owner,
                project_number=settings.github_project_number,
                self_owner=settings.github_self_owner,
                self_repo=settings.github_self_repo,
                mission_control_owner=settings.github_mission_control_owner or None,
                mission_control_repo=settings.github_mission_control_repo or None,
                mission_control_enabled=bool(
                    settings.github_mission_control_owner and settings.github_mission_control_repo
                ),
            ),
            token_for_redaction=token,
        )
        result = await service.run(session)
    finally:
        await client.aclose()

    assignments: dict[str, dict[str, str]] = {
        str(card): values for card, values in result.effective_assignments.items()
    }
    return MissionRefreshResponse(
        ok=result.ok,
        partial=result.partial,
        projected=result.projected,
        quarantined=result.quarantined,
        tombstoned=result.tombstoned,
        errors=result.errors,
        effective_assignments=assignments,
    )


@router.get(
    "/overview",
    response_model=MissionOverview,
    summary="Read-only Mission Control dashboard overview",
    description=(
        "Composite read-only snapshot derived from the Slice 3 projection tables: "
        "adapter/sync health, projection counts, quarantine visibility, and a "
        "minimal workflow roll-up. Reads only; never touches GitHub (ADR-23)."
    ),
)
async def mission_overview(
    quarantine_limit: int = QUARANTINE_LIMIT_QUERY,
    card_limit: int = CARD_LIMIT_QUERY,
    record_limit: int = RECORD_LIMIT_QUERY,
    auth: AuthContext = AUTH_DEP,
    session: AsyncSession = SESSION_DEP,
) -> MissionOverview:
    """Return the composite read-only dashboard snapshot."""
    _ = auth
    return await read_service.get_overview(
        session,
        quarantine_limit=quarantine_limit,
        card_limit=card_limit,
        record_limit=record_limit,
    )


@router.get(
    "/quarantine",
    response_model=MissionQuarantineSummary,
    summary="Read-only quarantine visibility",
    description=(
        "Quarantine totals, per-reason counts, and a recent-entry window from the "
        "mc_quarantine projection table. Diagnostic payloads are intentionally "
        "excluded from the read surface."
    ),
)
async def mission_quarantine(
    limit: int = QUARANTINE_LIMIT_QUERY,
    auth: AuthContext = AUTH_DEP,
    session: AsyncSession = SESSION_DEP,
) -> MissionQuarantineSummary:
    """Return the quarantine summary for dashboard visibility."""
    _ = auth
    return await read_service.get_quarantine_summary(session, limit=limit)


@router.get(
    "/workflow",
    response_model=MissionWorkflowSummary,
    summary="Read-only workflow and cards summary",
    description=(
        "Minimal (v0.1) cards + workflow-record roll-up derived from projected "
        "project items and ai-workflow-record:v1 comments. Payload-local parsing "
        "only; assignment authority derivation is intentionally excluded."
    ),
)
async def mission_workflow(
    card_limit: int = CARD_LIMIT_QUERY,
    record_limit: int = RECORD_LIMIT_QUERY,
    auth: AuthContext = AUTH_DEP,
    session: AsyncSession = SESSION_DEP,
) -> MissionWorkflowSummary:
    """Return the minimal workflow/cards summary."""
    _ = auth
    return await read_service.get_workflow_summary(
        session,
        card_limit=card_limit,
        record_limit=record_limit,
    )


@router.get(
    "/audit",
    response_model=MissionAuditSummary,
    summary="Read-only sync audit summary",
    description=(
        "Sync-run audit total and a recent-entry window from the mc_sync_audit "
        "table (Slice 4). Reads only; never touches GitHub (ADR-23)."
    ),
)
async def mission_audit(
    auth: AuthContext = AUTH_DEP,
    session: AsyncSession = SESSION_DEP,
) -> MissionAuditSummary:
    """Return the sync-audit summary for dashboard visibility."""
    _ = auth
    return await read_service.get_audit_summary(session)


@router.get(
    "/pr-status",
    response_model=MissionPRStatusSummary,
    summary="Read-only CI / PR status summary",
    description=(
        "Live CI/status projection records (check runs/suites, commit statuses, "
        "workflow runs) from mc_projection_record. Raw payloads are never exposed "
        "on the read surface (ADR-23)."
    ),
)
async def mission_pr_status(
    auth: AuthContext = AUTH_DEP,
    session: AsyncSession = SESSION_DEP,
) -> MissionPRStatusSummary:
    """Return the CI/PR-status summary for dashboard visibility."""
    _ = auth
    return await read_service.get_pr_status_summary(session)


def mission_adapter_runtime_summary() -> dict[str, Any]:
    """Non-secret adapter status for diagnostics (never includes the PAT)."""
    return {
        "enabled": settings.github_adapter_enabled,
        "project_owner": settings.github_project_owner,
        "project_number": settings.github_project_number,
        "self_repo": f"{settings.github_self_owner}/{settings.github_self_repo}",
        "poll_interval_seconds": settings.github_poll_interval_seconds,
    }
