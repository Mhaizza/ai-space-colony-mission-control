"""Slice 4 sync audit persistence model (per-run outcome log).

One row per completed adapter sync run. Read-only projection data: written by
the sync result path (Slice 4 T3/T4) and surfaced through read-only APIs; it
carries operational counts only and never stores credentials (ADR-23).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlmodel import Field

from app.models.base import QueryModel


class McSyncAudit(QueryModel, table=True):
    """Immutable per-run audit row for a GitHub adapter sync."""

    __tablename__ = "mc_sync_audit"  # pyright: ignore[reportAssignmentType]

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    adapter_key: str = Field(max_length=128)
    started_at: datetime
    finished_at: datetime | None = None
    is_partial: bool = Field(default=False)
    projected: int = Field(default=0)
    quarantined: int = Field(default=0)
    tombstoned: int = Field(default=0)
    error_summary: str | None = None
