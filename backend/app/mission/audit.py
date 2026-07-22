"""Sync-run audit persistence (Slice 4 T3, ADR-23).

Writes one :class:`McSyncAudit` row per completed adapter sync run. This is a
read-side side effect only: no GitHub calls, no sync logic, no mutation
endpoints. Audit writes must never fail a sync run, so failures here are
caught and logged rather than raised.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.logging import get_logger
from app.mission.redaction import redact_secrets
from app.mission.sync import SyncResult
from app.models.mc_sync_audit import McSyncAudit

logger = get_logger(__name__)

_ERROR_SUMMARY_MAX_LENGTH = 512


def _build_error_summary(errors: list[str], *, secrets: list[str] | None = None) -> str | None:
    if not errors:
        return None
    joined = redact_secrets("; ".join(errors), secrets=secrets)
    return joined[:_ERROR_SUMMARY_MAX_LENGTH]


async def write_sync_audit(
    session: AsyncSession,
    result: SyncResult,
    *,
    adapter_key: str,
    started_at: datetime,
    finished_at: datetime | None = None,
    secrets: list[str] | None = None,
) -> None:
    """Persist a sync run outcome as an :class:`McSyncAudit` row.

    Never raises: a failure to write the audit row must not fail the sync
    it is reporting on (ADR-23), so any exception is logged and swallowed.
    """
    try:
        row = McSyncAudit(
            adapter_key=adapter_key,
            started_at=started_at,
            finished_at=finished_at,
            is_partial=result.partial,
            projected=result.projected,
            quarantined=result.quarantined,
            tombstoned=result.tombstoned,
            error_summary=_build_error_summary(result.errors, secrets=secrets),
        )
        session.add(row)
        await session.commit()
    except Exception:  # noqa: BLE001 — audit failure must never fail sync
        logger.exception("mission.sync.audit_write_failed", extra={"adapter_key": adapter_key})
