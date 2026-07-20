"""Minimal Slice 3 persistence models (projection / quarantine / sync state)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.core.time import utcnow
from app.models.base import QueryModel


class McProjectionRecord(QueryModel, table=True):
    """Idempotent projection upsert keyed by (source_type, source_id)."""

    __tablename__ = "mc_projection_record"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "source_id",
            name="uq_mc_projection_record_source",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    source_type: str = Field(index=True, max_length=64)
    source_id: str = Field(index=True, max_length=256)
    source_url: str | None = Field(default=None, max_length=1024)
    source_updated_at: datetime | None = None
    projected_at: datetime = Field(default_factory=utcnow)
    last_observed_at: datetime = Field(default_factory=utcnow)
    partition_key: str = Field(default="", max_length=256, index=True)
    tombstoned: bool = Field(default=False)
    payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False),
    )


class McQuarantine(QueryModel, table=True):
    """Quarantine row for malformed/unauthorized/invalid inputs."""

    __tablename__ = "mc_quarantine"  # pyright: ignore[reportAssignmentType]

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    reason_code: str = Field(index=True, max_length=64)
    source_type: str | None = Field(default=None, max_length=64)
    source_id: str | None = Field(default=None, max_length=256)
    source_url: str | None = Field(default=None, max_length=1024)
    source_updated_at: datetime | None = None
    projected_at: datetime = Field(default_factory=utcnow)
    message: str = Field(default="", max_length=2048)
    diagnostic: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False),
    )


class McSyncState(QueryModel, table=True):
    """Single-row-ish sync health / cursor state for the GitHub adapter."""

    __tablename__ = "mc_sync_state"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        UniqueConstraint("adapter_key", name="uq_mc_sync_state_adapter_key"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    adapter_key: str = Field(default="github", max_length=64)
    status: str = Field(default="idle", max_length=32)
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = Field(default=None, max_length=2048)
    consecutive_failures: int = Field(default=0)
    meta: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONB, nullable=False),
    )
