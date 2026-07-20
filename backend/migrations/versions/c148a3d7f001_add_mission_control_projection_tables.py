"""Add minimal Mission Control Slice 3 projection tables.

Revision ID: c148a3d7f001
Revises: a9b1c2d3e4f7
Create Date: 2026-07-21 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "c148a3d7f001"
down_revision = "a9b1c2d3e4f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mc_projection_record",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=256), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(), nullable=True),
        sa.Column("projected_at", sa.DateTime(), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(), nullable=False),
        sa.Column("partition_key", sa.String(length=256), nullable=False),
        sa.Column("tombstoned", sa.Boolean(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_type",
            "source_id",
            name="uq_mc_projection_record_source",
        ),
    )
    op.create_index(
        op.f("ix_mc_projection_record_source_type"),
        "mc_projection_record",
        ["source_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_mc_projection_record_source_id"),
        "mc_projection_record",
        ["source_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_mc_projection_record_partition_key"),
        "mc_projection_record",
        ["partition_key"],
        unique=False,
    )

    op.create_table(
        "mc_quarantine",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=True),
        sa.Column("source_id", sa.String(length=256), nullable=True),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(), nullable=True),
        sa.Column("projected_at", sa.DateTime(), nullable=False),
        sa.Column("message", sa.String(length=2048), nullable=False),
        sa.Column("diagnostic", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_mc_quarantine_reason_code"),
        "mc_quarantine",
        ["reason_code"],
        unique=False,
    )

    op.create_table(
        "mc_sync_state",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("adapter_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_started_at", sa.DateTime(), nullable=True),
        sa.Column("last_finished_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=2048), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("adapter_key", name="uq_mc_sync_state_adapter_key"),
    )


def downgrade() -> None:
    op.drop_table("mc_sync_state")
    op.drop_index(op.f("ix_mc_quarantine_reason_code"), table_name="mc_quarantine")
    op.drop_table("mc_quarantine")
    op.drop_index(
        op.f("ix_mc_projection_record_partition_key"),
        table_name="mc_projection_record",
    )
    op.drop_index(
        op.f("ix_mc_projection_record_source_id"),
        table_name="mc_projection_record",
    )
    op.drop_index(
        op.f("ix_mc_projection_record_source_type"),
        table_name="mc_projection_record",
    )
    op.drop_table("mc_projection_record")
