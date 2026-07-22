"""Read-only Mission Control dashboard response schemas (Slice 3.5).

Contract definitions are single-sourced: source-type and quarantine-reason
identifiers are re-exported from :mod:`app.mission.types` so the read API never
duplicates the closed Slice 3 enum values.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final, Literal
from uuid import UUID

from sqlmodel import SQLModel

from app.mission.types import QuarantineReason, SourceType

# Closed set of sync-state ``status`` values written by the Slice 3 adapter.
# Kept as a documented constant (not a DB enum) because the projection table
# stores a plain string; the dashboard narrows against this set.
MissionSyncStatusValue = Literal["idle", "running", "healthy", "degraded", "error"]
MISSION_SYNC_STATUS_VALUES: Final[tuple[MissionSyncStatusValue, ...]] = (
    "idle",
    "running",
    "healthy",
    "degraded",
    "error",
)

MissionCardKind = Literal["issue", "pull_request"]

# Re-export the closed Slice 3 identifier sets so downstream consumers and the
# generated OpenAPI single-source them instead of re-hardcoding string unions.
MISSION_SOURCE_TYPES: Final[tuple[str, ...]] = tuple(source.value for source in SourceType)
MISSION_QUARANTINE_REASONS: Final[tuple[str, ...]] = tuple(
    reason.value for reason in QuarantineReason
)


class MissionAdapterStatus(SQLModel):
    """Non-secret GitHub adapter runtime configuration summary."""

    enabled: bool
    project_owner: str
    project_number: int
    self_repo: str
    poll_interval_seconds: int


class MissionSyncStatus(SQLModel):
    """Latest read-only sync health for the GitHub adapter."""

    adapter_key: str
    status: str
    last_started_at: datetime | None
    last_finished_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    consecutive_failures: int


class MissionSourceTypeCount(SQLModel):
    """Live/tombstoned projection counts for a single source type."""

    source_type: str
    live: int
    tombstoned: int


class MissionProjectionSummary(SQLModel):
    """Aggregate projection-record counts across all source types."""

    total: int
    live: int
    tombstoned: int
    by_source_type: list[MissionSourceTypeCount]


class MissionQuarantineReasonCount(SQLModel):
    """Quarantine-row count for a single reason code."""

    reason_code: str
    count: int


class MissionQuarantineEntry(SQLModel):
    """Single quarantine row for dashboard visibility (no diagnostics payload)."""

    id: UUID
    reason_code: str
    source_type: str | None
    source_id: str | None
    source_url: str | None
    message: str
    projected_at: datetime


class MissionQuarantineSummary(SQLModel):
    """Quarantine totals, per-reason breakdown, and a recent-entry window."""

    total: int
    by_reason: list[MissionQuarantineReasonCount]
    recent: list[MissionQuarantineEntry]


class MissionCard(SQLModel):
    """Project-linked issue or pull request derived from projected items."""

    number: int
    kind: MissionCardKind
    title: str | None
    url: str | None
    state: str | None
    updated_at: datetime | None


class MissionWorkflowRecordSummary(SQLModel):
    """Minimal view of a projected ``ai-workflow-record:v1`` comment.

    Parsing is read-only and payload-local (Slice 3.5 keeps workflow views
    minimal): assignment authority derivation is intentionally excluded.
    """

    comment_source_id: str
    card: int | None
    record_type: str | None
    worker: str | None
    role: str | None
    author: str | None
    url: str | None
    updated_at: datetime | None
    parsed_ok: bool


class MissionWorkflowSummary(SQLModel):
    """Minimal cards + workflow-record roll-up for the dashboard."""

    cards_total: int
    records_total: int
    cards: list[MissionCard]
    records: list[MissionWorkflowRecordSummary]


class MissionOverview(SQLModel):
    """Composite read-only Mission Control dashboard snapshot."""

    generated_at: datetime
    adapter: MissionAdapterStatus
    sync: MissionSyncStatus | None
    projections: MissionProjectionSummary
    quarantine: MissionQuarantineSummary
    workflow: MissionWorkflowSummary


class MissionAuditEntry(SQLModel):
    """Single completed sync-run audit row (mirrors ``McSyncAudit``)."""

    adapter_key: str
    started_at: datetime
    finished_at: datetime | None
    is_partial: bool
    projected: int
    quarantined: int
    tombstoned: int
    error_summary: str | None


class MissionAuditSummary(SQLModel):
    """Sync-audit total and a recent-entry window."""

    total: int
    recent: list[MissionAuditEntry]


class MissionPRStatusEntry(SQLModel):
    """Read-only pull-request status projected from a sync run."""

    source_type: str
    source_id: str
    state: str | None
    check_status: str | None
    source_url: str | None
    projected_at: datetime


class MissionPRStatusSummary(SQLModel):
    """Pull-request status total and entry list."""

    total: int
    items: list[MissionPRStatusEntry]
