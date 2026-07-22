"""add mc sync audit and retention index

Revision ID: 500feb3775e9
Revises: c148a3d7f001
Create Date: 2026-07-22 03:50:57.146445

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '500feb3775e9'
down_revision = 'c148a3d7f001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mc_sync_audit",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("adapter_key", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("is_partial", sa.Boolean(), nullable=False),
        sa.Column("projected", sa.Integer(), nullable=False),
        sa.Column("quarantined", sa.Integer(), nullable=False),
        sa.Column("tombstoned", sa.Integer(), nullable=False),
        sa.Column("error_summary", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_mc_sync_audit_key_started",
        "mc_sync_audit",
        ["adapter_key", "started_at"],
    )

    op.create_index(
        "ix_mc_proj_tombstoned_projected_at",
        "mc_projection_record",
        ["tombstoned", "projected_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mc_proj_tombstoned_projected_at",
        table_name="mc_projection_record",
    )

    op.drop_index(
        "ix_mc_sync_audit_key_started",
        table_name="mc_sync_audit",
    )

    op.drop_table("mc_sync_audit")
