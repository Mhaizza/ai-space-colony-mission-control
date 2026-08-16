"""Slice 5A Checkpoint B persistence models: Mission Control's own internal
governance (principal/policy/approval) domain.

Bounded, Mission-Control-owned tables distinct from the inherited upstream
board/task `Approval` domain (`app/models/approvals.py`) and from the
GitHub-login `PrincipalRegistry` (`app/mission/principal_registry.py`) — see
`docs/slice-5a-architecture-proposal.md` for why neither of those is reused
here. No route, resolver, or service in this checkpoint reads or writes
these tables yet; they exist so the schema itself can be reviewed, migrated,
and unit-tested ahead of the orchestration that will use it (Checkpoint C).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, ForeignKeyConstraint, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.core.time import utcnow
from app.models.base import QueryModel

# Portable JSON: JSONB on PostgreSQL, generic JSON elsewhere (e.g. the SQLite
# engine used by unit tests), matching app/models/mc_projection.py's convention.
_JSON_PORTABLE = JSON().with_variant(JSONB(), "postgresql")


class McPrincipal(QueryModel, table=True):
    """Mission-Control-owned principal identity (human / ai / system).

    External identity (`external_provider`/`external_subject`) is provenance
    only — it is never trusted for authorization on its own; a caller's
    principal must still be resolved server-side (Checkpoint C) from
    authenticated identity, never from a client-supplied value.
    """

    __tablename__ = "mc_principal"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        UniqueConstraint(
            "external_provider",
            "external_subject",
            name="uq_mc_principal_external_identity",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    principal_type: str = Field(max_length=16)  # human | ai | system
    display_name: str = Field(max_length=256)
    trust_level: str = Field(max_length=32)
    enabled: bool = Field(default=True)
    external_provider: str | None = Field(default=None, max_length=64)
    external_subject: str | None = Field(default=None, max_length=256)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class McPrincipalRole(QueryModel, table=True):
    """One role held by one principal. A principal may hold more than one role."""

    __tablename__ = "mc_principal_role"  # pyright: ignore[reportAssignmentType]

    principal_id: UUID = Field(foreign_key="mc_principal.id", primary_key=True)
    role_slug: str = Field(max_length=64, primary_key=True)


class McApprovalPolicy(QueryModel, table=True):
    """One immutable, versioned approval policy. No column is ever updated post-insert.

    "Which version is currently active" is deliberately not a column on this
    table — that is mutable state about a `policy_key`, not a fact about one
    immutable version row. It lives entirely in `McApprovalPolicyActivation`.
    """

    __tablename__ = "mc_approval_policy"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        UniqueConstraint("policy_key", "version", name="uq_mc_approval_policy_key_version"),
        # Composite-key surface only: `id` alone is already globally unique
        # (primary key), so this adds no new identity. It exists so
        # McApprovalPolicyActivation's foreign key can bind policy_key to
        # the referenced row's own policy_key, not merely its id.
        UniqueConstraint("policy_key", "id", name="uq_mc_approval_policy_key_id"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    policy_key: str = Field(max_length=128)
    version: int
    definition: dict[str, Any] = Field(sa_column=Column(_JSON_PORTABLE, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)


class McApprovalPolicyActivation(QueryModel, table=True):
    """Mutable pointer: which immutable policy version is currently active for a key.

    `(policy_key, active_policy_id)` is bound by a composite foreign key to
    `mc_approval_policy(policy_key, id)` — not a bare `active_policy_id ->
    mc_approval_policy.id` reference. A bare single-column FK would let this
    row's `policy_key` ("implementation_review") point at a policy row whose
    own `policy_key` is something else entirely ("security_review:v2") as
    long as the `id` matched some row; the composite FK makes that
    combination impossible at the database level, not just by convention.
    """

    __tablename__ = "mc_approval_policy_activation"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        ForeignKeyConstraint(
            ["policy_key", "active_policy_id"],
            ["mc_approval_policy.policy_key", "mc_approval_policy.id"],
            name="fk_mc_approval_policy_activation_policy",
        ),
    )

    policy_key: str = Field(max_length=128, primary_key=True)
    active_policy_id: UUID
    updated_at: datetime = Field(default_factory=utcnow)


class McApprovalRequest(QueryModel, table=True):
    """One approval request. `policy_id` pins the exact immutable policy version used at creation."""

    __tablename__ = "mc_approval_request"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        Index(
            "ix_mc_approval_request_mission",
            "mission_source_repo",
            "mission_card_kind",
            "mission_card_number",
        ),
        Index("ix_mc_approval_request_status_expires", "status", "expires_at"),
        Index(
            "ux_mc_approval_request_trigger_key",
            "trigger_key",
            unique=True,
            postgresql_where=text("trigger_key IS NOT NULL"),
            sqlite_where=text("trigger_key IS NOT NULL"),
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    policy_id: UUID = Field(foreign_key="mc_approval_policy.id")
    scope_type: str = Field(max_length=16)  # mission | action
    mission_source_repo: str = Field(max_length=256)
    mission_card_kind: str = Field(max_length=16)  # issue | pull_request
    mission_card_number: int
    action_key: str | None = Field(default=None, max_length=64)
    created_by_principal_id: UUID = Field(foreign_key="mc_principal.id")
    creation_source: str = Field(max_length=16)  # system | human
    status: str = Field(max_length=16)  # pending | approved | rejected | expired | superseded
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime | None = None
    resolved_at: datetime | None = None
    supersedes_request_id: UUID | None = Field(default=None, foreign_key="mc_approval_request.id")
    trigger_key: str | None = Field(default=None, max_length=512)
    auto_retry_count: int = Field(default=0)


class McApprovalDecision(QueryModel, table=True):
    """Append-only, immutable decision row. Snapshots the deciding principal's authorization facts."""

    __tablename__ = "mc_approval_decision"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (Index("ix_mc_approval_decision_request", "request_id"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    request_id: UUID = Field(foreign_key="mc_approval_request.id")
    principal_id: UUID = Field(foreign_key="mc_principal.id")
    decision: str = Field(max_length=16)  # approve | reject
    reason: str | None = None
    role_slugs_at_decision: list[str] = Field(sa_column=Column(_JSON_PORTABLE, nullable=False))
    trust_level_at_decision: str = Field(max_length=32)
    created_at: datetime = Field(default_factory=utcnow)
    supersedes_decision_id: UUID | None = Field(default=None, foreign_key="mc_approval_decision.id")


class McApprovalEvent(QueryModel, table=True):
    """Append-only lifecycle event, distinct from an approver's decision."""

    __tablename__ = "mc_approval_event"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (Index("ix_mc_approval_event_request", "request_id", "created_at"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    request_id: UUID = Field(foreign_key="mc_approval_request.id")
    event_type: str = Field(max_length=32)
    # Nullable: some lifecycle events have no single attributable actor
    # (e.g. a reconciliation-tick expiration is a scheduled system process,
    # not a principal acting through an authenticated request).
    triggered_by_principal_id: UUID | None = Field(default=None, foreign_key="mc_principal.id")
    detail: dict[str, Any] | None = Field(default=None, sa_column=Column(_JSON_PORTABLE))
    created_at: datetime = Field(default_factory=utcnow)


class McApprovalOperation(QueryModel, table=True):
    """Dedicated idempotency-key bookkeeping, kept separate from the domain tables above.

    Identity and replay/reuse comparisons are scoped to
    (idempotency_key, principal_id, endpoint), never the key alone — a
    bare-key uniqueness would let two different principals' independently
    chosen, identical keys collide.
    """

    __tablename__ = "mc_approval_operation"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            "principal_id",
            "endpoint",
            name="uq_mc_approval_operation_key_principal_endpoint",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    idempotency_key: str = Field(max_length=128)
    principal_id: UUID = Field(foreign_key="mc_principal.id")
    endpoint: str = Field(max_length=128)
    payload_hash: str = Field(max_length=128)
    response_snapshot: dict[str, Any] = Field(sa_column=Column(_JSON_PORTABLE, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)
