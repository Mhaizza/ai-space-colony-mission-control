"""Read-only projection query service for the Mission Control dashboard (Slice 3.5).

Reads exclusively from the three Slice 3 projection tables
(``mc_projection_record`` / ``mc_quarantine`` / ``mc_sync_state``) and
materializes the views consumed by the read-only dashboard APIs.

Invariants (ADR-23): this module performs no writes and never touches GitHub.
Workflow parsing is payload-local and read-only; assignment/authority
derivation is intentionally excluded (Slice 3.5 keeps workflow views minimal).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.time import utcnow
from app.mission.types import SourceType
from app.mission.workflow_record import parse_workflow_record_from_comment
from app.models.mc_projection import McProjectionRecord, McQuarantine, McSyncState
from app.models.mc_sync_audit import McSyncAudit
from app.schemas.mission import (
    MissionAdapterStatus,
    MissionAuditEntry,
    MissionAuditSummary,
    MissionCard,
    MissionCardKind,
    MissionOverview,
    MissionProjectionSummary,
    MissionPRStatusEntry,
    MissionPRStatusSummary,
    MissionQuarantineEntry,
    MissionQuarantineReasonCount,
    MissionQuarantineSummary,
    MissionSourceTypeCount,
    MissionSyncStatus,
    MissionWorkflowRecordSummary,
    MissionWorkflowSummary,
)

WORKFLOW_MARKER = "ai-workflow-record:v1"

DEFAULT_QUARANTINE_LIMIT = 50
DEFAULT_CARD_LIMIT = 100
DEFAULT_RECORD_LIMIT = 100
DEFAULT_AUDIT_LIMIT = 10

# CI/status projection source types surfaced by the PR-status read view.
PR_STATUS_SOURCE_TYPES: tuple[str, ...] = (
    SourceType.GITHUB_CHECK_RUN.value,
    SourceType.GITHUB_CHECK_SUITE.value,
    SourceType.GITHUB_COMMIT_STATUS.value,
    SourceType.GITHUB_WORKFLOW_RUN.value,
)


def _parse_iso(value: object) -> datetime | None:
    """Parse a GitHub ISO-8601 timestamp into a naive UTC datetime."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.replace(tzinfo=None)
    return parsed


def get_adapter_status() -> MissionAdapterStatus:
    """Return the non-secret GitHub adapter runtime summary (never the PAT)."""
    return MissionAdapterStatus(
        enabled=settings.github_adapter_enabled,
        project_owner=settings.github_project_owner,
        project_number=settings.github_project_number,
        self_repo=f"{settings.github_self_owner}/{settings.github_self_repo}",
        poll_interval_seconds=settings.github_poll_interval_seconds,
    )


async def get_sync_status(session: AsyncSession) -> MissionSyncStatus | None:
    """Return the latest GitHub adapter sync state, or None if never run."""
    state = (
        await session.exec(select(McSyncState).where(col(McSyncState.adapter_key) == "github"))
    ).first()
    if state is None:
        return None
    return MissionSyncStatus(
        adapter_key=state.adapter_key,
        status=state.status,
        last_started_at=state.last_started_at,
        last_finished_at=state.last_finished_at,
        last_success_at=state.last_success_at,
        last_error=state.last_error,
        consecutive_failures=state.consecutive_failures,
    )


async def get_projection_summary(session: AsyncSession) -> MissionProjectionSummary:
    """Aggregate live/tombstoned projection-record counts per source type."""
    rows = (
        await session.exec(
            select(
                col(McProjectionRecord.source_type),
                col(McProjectionRecord.tombstoned),
                func.count(),
            ).group_by(
                col(McProjectionRecord.source_type),
                col(McProjectionRecord.tombstoned),
            )
        )
    ).all()

    live_by_type: dict[str, int] = {}
    tombstoned_by_type: dict[str, int] = {}
    for source_type, tombstoned, count in rows:
        bucket = tombstoned_by_type if bool(tombstoned) else live_by_type
        bucket[str(source_type)] = bucket.get(str(source_type), 0) + int(count)

    all_types = sorted(set(live_by_type) | set(tombstoned_by_type))
    by_source_type = [
        MissionSourceTypeCount(
            source_type=source_type,
            live=live_by_type.get(source_type, 0),
            tombstoned=tombstoned_by_type.get(source_type, 0),
        )
        for source_type in all_types
    ]
    live_total = sum(item.live for item in by_source_type)
    tombstoned_total = sum(item.tombstoned for item in by_source_type)
    return MissionProjectionSummary(
        total=live_total + tombstoned_total,
        live=live_total,
        tombstoned=tombstoned_total,
        by_source_type=by_source_type,
    )


async def get_quarantine_summary(
    session: AsyncSession,
    *,
    limit: int = DEFAULT_QUARANTINE_LIMIT,
) -> MissionQuarantineSummary:
    """Return quarantine totals, per-reason counts, and a recent-entry window."""
    reason_rows = (
        await session.exec(
            select(col(McQuarantine.reason_code), func.count()).group_by(
                col(McQuarantine.reason_code)
            )
        )
    ).all()
    by_reason = [
        MissionQuarantineReasonCount(reason_code=str(reason_code), count=int(count))
        for reason_code, count in sorted(reason_rows, key=lambda row: str(row[0]))
    ]
    total = sum(item.count for item in by_reason)

    recent_rows = list(
        await session.exec(
            select(McQuarantine).order_by(col(McQuarantine.projected_at).desc()).limit(limit)
        )
    )
    recent = [
        MissionQuarantineEntry(
            id=row.id,
            reason_code=row.reason_code,
            source_type=row.source_type,
            source_id=row.source_id,
            source_url=row.source_url,
            message=row.message,
            projected_at=row.projected_at,
        )
        for row in recent_rows
    ]
    return MissionQuarantineSummary(total=total, by_reason=by_reason, recent=recent)


async def get_audit_summary(
    session: AsyncSession,
    *,
    limit: int = DEFAULT_AUDIT_LIMIT,
) -> MissionAuditSummary:
    """Return the total sync-run count and the most recent audit entries."""
    total = int((await session.exec(select(func.count()).select_from(McSyncAudit))).one())

    recent_rows = list(
        await session.exec(
            select(McSyncAudit).order_by(col(McSyncAudit.started_at).desc()).limit(limit)
        )
    )
    recent = [
        MissionAuditEntry(
            adapter_key=row.adapter_key,
            started_at=row.started_at,
            finished_at=row.finished_at,
            is_partial=row.is_partial,
            projected=row.projected,
            quarantined=row.quarantined,
            tombstoned=row.tombstoned,
            error_summary=row.error_summary,
        )
        for row in recent_rows
    ]
    return MissionAuditSummary(total=total, recent=recent)


def _pr_status_field(payload: dict[str, Any], key: str) -> str | None:
    """Pull a single scalar string from a projection payload (never the payload)."""
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


async def get_pr_status_summary(session: AsyncSession) -> MissionPRStatusSummary:
    """Return live CI/status projection records (no raw payload exposed)."""
    rows = list(
        await session.exec(
            select(McProjectionRecord)
            .where(
                col(McProjectionRecord.source_type).in_(PR_STATUS_SOURCE_TYPES),
                col(McProjectionRecord.tombstoned).is_(False),
            )
            .order_by(col(McProjectionRecord.projected_at).desc())
        )
    )
    items = [
        MissionPRStatusEntry(
            source_type=row.source_type,
            source_id=row.source_id,
            # commit_status carries ``state``; checks/workflow_runs carry a
            # ``status`` phase and a terminal ``conclusion`` — prefer conclusion.
            state=_pr_status_field(row.payload or {}, "state"),
            check_status=(
                _pr_status_field(row.payload or {}, "conclusion")
                or _pr_status_field(row.payload or {}, "status")
            ),
            source_url=row.source_url,
            projected_at=row.projected_at,
        )
        for row in rows
    ]
    return MissionPRStatusSummary(total=len(items), items=items)


def _card_from_project_item(
    payload: dict[str, Any],
    *,
    state_by_number: dict[int, str | None],
) -> MissionCard | None:
    """Build a dashboard card from a projected project-item payload."""
    content = payload.get("content")
    if not isinstance(content, dict):
        return None
    typename = content.get("__typename")
    number = content.get("number")
    if not isinstance(number, int):
        return None
    kind: MissionCardKind
    if typename == "Issue":
        kind = "issue"
    elif typename == "PullRequest":
        kind = "pull_request"
    else:
        return None
    title = content.get("title")
    url = content.get("url")
    return MissionCard(
        number=number,
        kind=kind,
        title=title if isinstance(title, str) else None,
        url=url if isinstance(url, str) else None,
        state=state_by_number.get(number),
        updated_at=_parse_iso(content.get("updatedAt")),
    )


async def _load_live(
    session: AsyncSession,
    source_type: SourceType,
) -> list[McProjectionRecord]:
    """Load every non-tombstoned projection row for a source type."""
    return list(
        await session.exec(
            select(McProjectionRecord).where(
                col(McProjectionRecord.source_type) == source_type.value,
                col(McProjectionRecord.tombstoned).is_(False),
            )
        )
    )


def _state_by_number(rows: list[McProjectionRecord]) -> dict[int, str | None]:
    """Map GitHub issue/PR number to its projected REST ``state`` field."""
    mapping: dict[int, str | None] = {}
    for row in rows:
        payload = row.payload or {}
        number = payload.get("number")
        state = payload.get("state")
        if isinstance(number, int):
            mapping[number] = state if isinstance(state, str) else None
    return mapping


def _workflow_record_summary(row: McProjectionRecord) -> MissionWorkflowRecordSummary:
    """Parse one projected comment into a minimal workflow-record view."""
    payload = row.payload or {}
    body = payload.get("body")
    user = payload.get("user")
    author = user.get("login") if isinstance(user, dict) else None
    parsed = parse_workflow_record_from_comment(body if isinstance(body, str) else "")
    record = parsed.record
    return MissionWorkflowRecordSummary(
        comment_source_id=row.source_id,
        card=record.card if record is not None else None,
        record_type=record.type if record is not None else None,
        worker=record.worker if record is not None else None,
        role=record.role if record is not None else None,
        author=author if isinstance(author, str) else None,
        url=row.source_url,
        updated_at=row.source_updated_at,
        parsed_ok=parsed.ok,
    )


async def get_workflow_summary(
    session: AsyncSession,
    *,
    card_limit: int = DEFAULT_CARD_LIMIT,
    record_limit: int = DEFAULT_RECORD_LIMIT,
) -> MissionWorkflowSummary:
    """Materialize the minimal cards + workflow-record roll-up (v0.1)."""
    items = await _load_live(session, SourceType.GITHUB_PROJECT_ITEM)
    issues = await _load_live(session, SourceType.GITHUB_ISSUE)
    pulls = await _load_live(session, SourceType.GITHUB_PULL_REQUEST)
    comments = await _load_live(session, SourceType.GITHUB_ISSUE_COMMENT)

    state_by_number = {**_state_by_number(issues), **_state_by_number(pulls)}
    cards = [
        card
        for card in (
            _card_from_project_item(item.payload or {}, state_by_number=state_by_number)
            for item in items
        )
        if card is not None
    ]
    cards.sort(key=lambda card: card.number, reverse=True)

    record_rows = [
        row
        for row in comments
        if isinstance(row.payload.get("body"), str) and WORKFLOW_MARKER in row.payload["body"]
    ]
    record_rows.sort(
        key=lambda row: (row.source_updated_at or datetime.min),
        reverse=True,
    )
    records = [_workflow_record_summary(row) for row in record_rows[:record_limit]]

    return MissionWorkflowSummary(
        cards_total=len(cards),
        records_total=len(record_rows),
        cards=cards[:card_limit],
        records=records,
    )


async def get_overview(
    session: AsyncSession,
    *,
    quarantine_limit: int = DEFAULT_QUARANTINE_LIMIT,
    card_limit: int = DEFAULT_CARD_LIMIT,
    record_limit: int = DEFAULT_RECORD_LIMIT,
) -> MissionOverview:
    """Compose the full read-only Mission Control dashboard snapshot."""
    return MissionOverview(
        generated_at=utcnow(),
        adapter=get_adapter_status(),
        sync=await get_sync_status(session),
        projections=await get_projection_summary(session),
        quarantine=await get_quarantine_summary(session, limit=quarantine_limit),
        workflow=await get_workflow_summary(
            session,
            card_limit=card_limit,
            record_limit=record_limit,
        ),
    )
