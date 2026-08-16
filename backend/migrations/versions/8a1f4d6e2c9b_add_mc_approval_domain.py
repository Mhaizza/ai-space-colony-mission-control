"""add mc approval domain (Slice 5A Checkpoint B)

Revision ID: 8a1f4d6e2c9b
Revises: 500feb3775e9
Create Date: 2026-08-16 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "8a1f4d6e2c9b"
down_revision = "500feb3775e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mc_principal",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_type", sa.String(length=16), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("trust_level", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("external_provider", sa.String(length=64), nullable=True),
        sa.Column("external_subject", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "external_provider",
            "external_subject",
            name="uq_mc_principal_external_identity",
        ),
    )

    op.create_table(
        "mc_principal_role",
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("role_slug", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["mc_principal.id"],
            name="fk_mc_principal_role_principal",
        ),
        sa.PrimaryKeyConstraint("principal_id", "role_slug"),
    )

    op.create_table(
        "mc_approval_policy",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("policy_key", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_key", "version", name="uq_mc_approval_policy_key_version"),
        # Composite-key surface only: `id` is already globally unique (PK).
        # This exists so mc_approval_policy_activation's FK can bind
        # policy_key to the referenced row's own policy_key, not just its id.
        sa.UniqueConstraint("policy_key", "id", name="uq_mc_approval_policy_key_id"),
    )

    op.create_table(
        "mc_approval_policy_activation",
        sa.Column("policy_key", sa.String(length=128), nullable=False),
        sa.Column("active_policy_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["policy_key", "active_policy_id"],
            ["mc_approval_policy.policy_key", "mc_approval_policy.id"],
            name="fk_mc_approval_policy_activation_policy",
        ),
        sa.PrimaryKeyConstraint("policy_key"),
    )

    op.create_table(
        "mc_approval_request",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("mission_source_repo", sa.String(length=256), nullable=False),
        sa.Column("mission_card_kind", sa.String(length=16), nullable=False),
        sa.Column("mission_card_number", sa.Integer(), nullable=False),
        sa.Column("action_key", sa.String(length=64), nullable=True),
        sa.Column("created_by_principal_id", sa.Uuid(), nullable=False),
        sa.Column("creation_source", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("supersedes_request_id", sa.Uuid(), nullable=True),
        sa.Column("trigger_key", sa.String(length=512), nullable=True),
        sa.Column("auto_retry_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["mc_approval_policy.id"],
            name="fk_mc_approval_request_policy",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_principal_id"],
            ["mc_principal.id"],
            name="fk_mc_approval_request_created_by_principal",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_request_id"],
            ["mc_approval_request.id"],
            name="fk_mc_approval_request_supersedes",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mc_approval_request_mission",
        "mc_approval_request",
        ["mission_source_repo", "mission_card_kind", "mission_card_number"],
    )
    op.create_index(
        "ix_mc_approval_request_status_expires",
        "mc_approval_request",
        ["status", "expires_at"],
    )
    op.create_index(
        "ux_mc_approval_request_trigger_key",
        "mc_approval_request",
        ["trigger_key"],
        unique=True,
        postgresql_where=sa.text("trigger_key IS NOT NULL"),
        sqlite_where=sa.text("trigger_key IS NOT NULL"),
    )

    op.create_table(
        "mc_approval_decision",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("role_slugs_at_decision", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trust_level_at_decision", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("supersedes_decision_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["mc_approval_request.id"],
            name="fk_mc_approval_decision_request",
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["mc_principal.id"],
            name="fk_mc_approval_decision_principal",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_decision_id"],
            ["mc_approval_decision.id"],
            name="fk_mc_approval_decision_supersedes",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mc_approval_decision_request",
        "mc_approval_decision",
        ["request_id"],
    )

    op.create_table(
        "mc_approval_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("triggered_by_principal_id", sa.Uuid(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["mc_approval_request.id"],
            name="fk_mc_approval_event_request",
        ),
        sa.ForeignKeyConstraint(
            ["triggered_by_principal_id"],
            ["mc_principal.id"],
            name="fk_mc_approval_event_triggered_by_principal",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mc_approval_event_request",
        "mc_approval_event",
        ["request_id", "created_at"],
    )

    op.create_table(
        "mc_approval_operation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint", sa.String(length=128), nullable=False),
        sa.Column("payload_hash", sa.String(length=128), nullable=False),
        sa.Column("response_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["mc_principal.id"],
            name="fk_mc_approval_operation_principal",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            "principal_id",
            "endpoint",
            name="uq_mc_approval_operation_key_principal_endpoint",
        ),
    )


def downgrade() -> None:
    op.drop_table("mc_approval_operation")
    op.drop_index("ix_mc_approval_event_request", table_name="mc_approval_event")
    op.drop_table("mc_approval_event")
    op.drop_index("ix_mc_approval_decision_request", table_name="mc_approval_decision")
    op.drop_table("mc_approval_decision")
    op.drop_index("ux_mc_approval_request_trigger_key", table_name="mc_approval_request")
    op.drop_index("ix_mc_approval_request_status_expires", table_name="mc_approval_request")
    op.drop_index("ix_mc_approval_request_mission", table_name="mc_approval_request")
    op.drop_table("mc_approval_request")
    op.drop_table("mc_approval_policy_activation")
    op.drop_table("mc_approval_policy")
    op.drop_table("mc_principal_role")
    op.drop_table("mc_principal")
