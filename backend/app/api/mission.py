"""Mission Control Slice 3 API: manual read-only refresh."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import require_user_auth
from app.core.auth import AuthContext
from app.core.config import settings
from app.db.session import get_session
from app.mission.github_client import GitHubReadClient
from app.mission.principal_registry import parse_principal_registry_json
from app.mission.sync import GitHubSyncService, SyncConfig

router = APIRouter(prefix="/mission", tags=["mission"])
AUTH_DEP = Depends(require_user_auth)
SESSION_DEP = Depends(get_session)


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


def mission_adapter_runtime_summary() -> dict[str, Any]:
    """Non-secret adapter status for diagnostics (never includes the PAT)."""
    return {
        "enabled": settings.github_adapter_enabled,
        "project_owner": settings.github_project_owner,
        "project_number": settings.github_project_number,
        "self_repo": f"{settings.github_self_owner}/{settings.github_self_repo}",
        "poll_interval_seconds": settings.github_poll_interval_seconds,
    }
