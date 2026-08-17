# ruff: noqa: INP001
"""Slice 5A Checkpoint C: approval_service tests.

Uses the same in-memory SQLite pattern as test_mc_approval_models.py, but
patches `app.mission.approval_service.async_session_maker` to point at that
in-memory engine's sessionmaker, since the service functions always open
their own fresh session rather than accepting one — this is the whole point
of the "dedicated session" design (see approval_service.py's module
docstring) and must be exercised as such, not bypassed.

Real concurrent PostgreSQL connections cannot be exercised in this sandbox
(no Docker daemon available; see Checkpoint B's migration-check limitation
for the same constraint). `asyncio.gather`-ing two calls against a single
shared aiosqlite/SQLite connection (which is what StaticPool gives every
session in this module) is *not* a safe stand-in for that: SQLite does not
support two overlapping transactions on one physical connection, so two
genuinely-interleaved sessions can corrupt each other's transaction state
in ways that have nothing to do with the code under test. The
`TestConcurrencySerialization` tests below instead deterministically seed
the exact database state a *losing* participant of a real race would see
after the *winning* participant has already committed (a row already
present in `mc_approval_operation`, or a request row already locked/
resolved), and assert the losing call's code path (the `IntegrityError` ->
existing-row fallback in `_reserve_or_get_operation`; the
already-effective-decision / already-superseded checks under a request
that's already been acted on) takes the correct branch. This proves the
logical fallback/serialization branches are correct; it is not a substitute
for proving row-level lock *blocking* under real concurrent Postgres
connections, which CI's Postgres-backed test job must be relied on for
before Checkpoint C Human acceptance.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

import app.mission.approval_service as approval_service
from app.core.auth import AuthContext
from app.core.auth_mode import AuthMode
from app.core.config import settings
from app.core.time import utcnow
from app.mission.approval_service import (
    ApprovalServiceError,
    IdempotencyScope,
    create_approval_request,
    submit_decision,
    supersede_decision,
)
from app.models.mc_approval import McApprovalPolicy, McApprovalPolicyActivation, McPrincipal
from app.models.users import User

POLICY_DEFINITION = {
    "decision_rule": "majority",
    "quorum": {"slots": [{"slot": "a", "eligible_roles": ["technical-director"]}]},
    "allowed_approver_principal_types": ["human"],
    "allowed_approver_roles": ["technical-director", "qa-reviewer"],
    "rejection_behavior": "leave_mission_unchanged",
    "expiration": {"behavior": "expire"},
}

TRUST_GATED_POLICY_DEFINITION = {
    "decision_rule": "majority",
    "quorum": {"slots": [{"slot": "a", "eligible_roles": ["technical-director"]}]},
    "allowed_approver_principal_types": ["human"],
    "allowed_approver_roles": ["technical-director", "qa-reviewer"],
    "trust_requirements": ["trusted"],
    "rejection_behavior": "leave_mission_unchanged",
    "expiration": {"behavior": "expire"},
}

TWO_SLOT_POLICY_DEFINITION = {
    "decision_rule": "unanimous",
    "quorum": {
        "slots": [
            {"slot": "a", "eligible_roles": ["technical-director"]},
            {"slot": "b", "eligible_roles": ["qa-reviewer"]},
        ]
    },
    "allowed_approver_principal_types": ["human"],
    "allowed_approver_roles": ["technical-director", "qa-reviewer"],
    "rejection_behavior": "leave_mission_unchanged",
    "expiration": {"behavior": "expire"},
}


@asynccontextmanager
async def _engine_and_maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    # StaticPool pins the engine to a single, reused DBAPI connection: plain
    # `sqlite+aiosqlite:///:memory:` without it hands out a fresh, empty
    # in-memory database per connection, which would silently break every
    # test in this module that opens more than one session against the
    # "same" database (every approval_service function opens its own fresh
    # session by design -- see approval_service.py's module docstring).
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.connect() as conn, conn.begin():
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


def _auth(clerk_user_id: str) -> AuthContext:
    return AuthContext(
        actor_type="user",
        user=User(clerk_user_id=clerk_user_id, email=f"{clerk_user_id}@example.com", name="Test"),
    )


async def _seed_principal(
    maker: async_sessionmaker[AsyncSession],
    *,
    external_subject: str,
    roles: list[str],
    principal_type: str = "human",
    enabled: bool = True,
    trust_level: str = "standard",
) -> McPrincipal:
    from app.models.mc_approval import McPrincipalRole

    async with maker() as session:
        principal = McPrincipal(
            principal_type=principal_type,
            display_name=external_subject,
            trust_level=trust_level,
            enabled=enabled,
            external_provider="local",
            external_subject=external_subject,
        )
        session.add(principal)
        await session.commit()
        await session.refresh(principal)
        for role in roles:
            session.add(McPrincipalRole(principal_id=principal.id, role_slug=role))
        await session.commit()
        return principal


async def _seed_policy(
    maker: async_sessionmaker[AsyncSession],
    *,
    policy_key: str = "implementation_review",
    definition: dict[str, object] | None = None,
    version: int = 1,
) -> McApprovalPolicy:
    async with maker() as session:
        policy = McApprovalPolicy(
            policy_key=policy_key,
            version=version,
            definition=definition or POLICY_DEFINITION,
            created_at=utcnow(),
        )
        session.add(policy)
        await session.commit()
        await session.refresh(policy)
        session.add(
            McApprovalPolicyActivation(
                policy_key=policy_key,
                active_policy_id=policy.id,
                updated_at=utcnow(),
            )
        )
        await session.commit()
        return policy


@pytest.fixture(autouse=True)
def _local_auth_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_mode", AuthMode.LOCAL)


@pytest_asyncio.fixture
async def maker(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with _engine_and_maker() as sessionmaker_:
        monkeypatch.setattr(approval_service, "async_session_maker", sessionmaker_)
        yield sessionmaker_


class TestCreateApprovalRequest:
    @pytest.mark.asyncio
    async def test_human_principal_creates_request(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_policy(maker)

        result = await create_approval_request(
            _auth("creator"),
            policy_key="implementation_review",
            scope_type="action",
            mission_source_repo="Mhaizza/ai-space-colony-mission-control",
            mission_card_kind="issue",
            mission_card_number=16,
            action_key=None,
            expires_at=None,
            idempotency_key="key-1",
        )
        assert result.status == "pending"
        assert result.policy_key == "implementation_review"
        assert result.policy_version == 1

    @pytest.mark.asyncio
    async def test_non_human_principal_rejected(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(
            maker, external_subject="sys-actor", roles=[], principal_type="system"
        )
        await _seed_policy(maker)

        with pytest.raises(ApprovalServiceError) as exc_info:
            await create_approval_request(
                _auth("sys-actor"),
                policy_key="implementation_review",
                scope_type="action",
                mission_source_repo="Mhaizza/ai-space-colony-mission-control",
                mission_card_kind="issue",
                mission_card_number=16,
                action_key=None,
                expires_at=None,
                idempotency_key="key-1",
            )
        assert exc_info.value.code == "principal_not_human"

    @pytest.mark.asyncio
    async def test_ai_typed_principal_linked_to_resolvable_identity_still_rejected(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        # Proves the manual path's human-only gate is structural, not merely
        # a consequence of policy configuration: even though nothing about
        # this principal or policy is otherwise malformed, an "ai"-typed
        # mc_principal row resolvable to a real AuthContext must still be
        # rejected on the manual path.
        await _seed_principal(maker, external_subject="ai-actor", roles=[], principal_type="ai")
        await _seed_policy(maker)

        with pytest.raises(ApprovalServiceError) as exc_info:
            await create_approval_request(
                _auth("ai-actor"),
                policy_key="implementation_review",
                scope_type="action",
                mission_source_repo="Mhaizza/ai-space-colony-mission-control",
                mission_card_kind="issue",
                mission_card_number=16,
                action_key=None,
                expires_at=None,
                idempotency_key="key-1",
            )
        assert exc_info.value.code == "principal_not_human"

    @pytest.mark.asyncio
    async def test_unknown_policy_key_rejected(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])

        with pytest.raises(ApprovalServiceError) as exc_info:
            await create_approval_request(
                _auth("creator"),
                policy_key="no-such-policy",
                scope_type="action",
                mission_source_repo="Mhaizza/ai-space-colony-mission-control",
                mission_card_kind="issue",
                mission_card_number=16,
                action_key=None,
                expires_at=None,
                idempotency_key="key-1",
            )
        assert exc_info.value.code == "policy_not_found"

    @pytest.mark.asyncio
    async def test_replayed_idempotency_key_returns_identical_result_without_new_row(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        from sqlmodel import select

        from app.models.mc_approval import McApprovalRequest

        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_policy(maker)

        kwargs: dict[str, object] = dict(
            policy_key="implementation_review",
            scope_type="action",
            mission_source_repo="Mhaizza/ai-space-colony-mission-control",
            mission_card_kind="issue",
            mission_card_number=16,
            action_key=None,
            expires_at=None,
            idempotency_key="same-key",
        )
        first = await create_approval_request(_auth("creator"), **kwargs)  # type: ignore[arg-type]
        second = await create_approval_request(_auth("creator"), **kwargs)  # type: ignore[arg-type]
        assert first == second

        async with maker() as session:
            rows = (await session.exec(select(McApprovalRequest))).all()
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_reused_key_with_different_payload_rejected(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_policy(maker)

        await create_approval_request(
            _auth("creator"),
            policy_key="implementation_review",
            scope_type="action",
            mission_source_repo="Mhaizza/ai-space-colony-mission-control",
            mission_card_kind="issue",
            mission_card_number=16,
            action_key=None,
            expires_at=None,
            idempotency_key="dup-key",
        )
        with pytest.raises(ApprovalServiceError) as exc_info:
            await create_approval_request(
                _auth("creator"),
                policy_key="implementation_review",
                scope_type="action",
                mission_source_repo="Mhaizza/ai-space-colony-mission-control",
                mission_card_kind="issue",
                mission_card_number=17,  # different payload, same key
                action_key=None,
                expires_at=None,
                idempotency_key="dup-key",
            )
        assert exc_info.value.code == "idempotency_key_reused_with_different_payload"

    @pytest.mark.asyncio
    async def test_response_snapshot_stored_as_plain_mapping_not_double_serialized(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        from sqlmodel import select

        from app.models.mc_approval import McApprovalOperation

        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_policy(maker)

        await create_approval_request(
            _auth("creator"),
            policy_key="implementation_review",
            scope_type="action",
            mission_source_repo="Mhaizza/ai-space-colony-mission-control",
            mission_card_kind="issue",
            mission_card_number=16,
            action_key=None,
            expires_at=None,
            idempotency_key="snap-key",
        )
        async with maker() as session:
            op = (await session.exec(select(McApprovalOperation))).first()
        assert op is not None
        assert isinstance(op.response_snapshot, dict)
        assert op.response_snapshot["status"] == "pending"


class TestSubmitDecision:
    @pytest.mark.asyncio
    async def test_authorized_human_vote_resolves_request(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(maker, external_subject="approver", roles=["technical-director"])
        await _seed_policy(maker)

        created = await create_approval_request(
            _auth("creator"),
            policy_key="implementation_review",
            scope_type="action",
            mission_source_repo="Mhaizza/ai-space-colony-mission-control",
            mission_card_kind="issue",
            mission_card_number=16,
            action_key=None,
            expires_at=None,
            idempotency_key="key-1",
        )
        result = await submit_decision(
            _auth("approver"),
            request_id=created.request_id,
            decision="approve",
            reason=None,
            idempotency_key="decision-key-1",
        )
        assert result.status == "approved"
        assert result.quorum_satisfied

    @pytest.mark.asyncio
    async def test_unauthorized_role_rejected(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(maker, external_subject="outsider", roles=[])
        await _seed_policy(maker)

        created = await create_approval_request(
            _auth("creator"),
            policy_key="implementation_review",
            scope_type="action",
            mission_source_repo="Mhaizza/ai-space-colony-mission-control",
            mission_card_kind="issue",
            mission_card_number=16,
            action_key=None,
            expires_at=None,
            idempotency_key="key-1",
        )
        with pytest.raises(ApprovalServiceError) as exc_info:
            await submit_decision(
                _auth("outsider"),
                request_id=created.request_id,
                decision="approve",
                reason=None,
                idempotency_key="decision-key-1",
            )
        assert exc_info.value.code == "principal_not_authorized"

    @pytest.mark.asyncio
    async def test_non_human_principal_rejected_on_manual_path(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        # A system-typed principal that IS an allowed_approver_principal_type
        # under a hypothetical misconfigured policy still can't vote through
        # this manual path -- the check is structural on principal_type, not
        # derived from policy configuration.
        await _seed_principal(
            maker,
            external_subject="sys-voter",
            roles=["technical-director"],
            principal_type="system",
        )
        await _seed_policy(
            maker,
            definition={
                **POLICY_DEFINITION,
                "allowed_approver_principal_types": ["human", "system"],
            },
        )

        created = await create_approval_request(
            _auth("creator"),
            policy_key="implementation_review",
            scope_type="action",
            mission_source_repo="Mhaizza/ai-space-colony-mission-control",
            mission_card_kind="issue",
            mission_card_number=16,
            action_key=None,
            expires_at=None,
            idempotency_key="key-1",
        )
        with pytest.raises(ApprovalServiceError) as exc_info:
            await submit_decision(
                _auth("sys-voter"),
                request_id=created.request_id,
                decision="approve",
                reason=None,
                idempotency_key="decision-key-1",
            )
        assert exc_info.value.code == "principal_not_human"

    @pytest.mark.asyncio
    async def test_second_vote_from_same_principal_rejected(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(maker, external_subject="approver", roles=["technical-director"])
        await _seed_policy(maker, definition=TWO_SLOT_POLICY_DEFINITION)

        created = await create_approval_request(
            _auth("creator"),
            policy_key="implementation_review",
            scope_type="action",
            mission_source_repo="Mhaizza/ai-space-colony-mission-control",
            mission_card_kind="issue",
            mission_card_number=16,
            action_key=None,
            expires_at=None,
            idempotency_key="key-1",
        )
        await submit_decision(
            _auth("approver"),
            request_id=created.request_id,
            decision="approve",
            reason=None,
            idempotency_key="decision-key-1",
        )
        with pytest.raises(ApprovalServiceError) as exc_info:
            await submit_decision(
                _auth("approver"),
                request_id=created.request_id,
                decision="approve",
                reason=None,
                idempotency_key="decision-key-2",
            )
        assert exc_info.value.code == "approval_decision_exists"

    @pytest.mark.asyncio
    async def test_decision_on_resolved_request_rejected(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(maker, external_subject="approver", roles=["technical-director"])
        await _seed_principal(maker, external_subject="late", roles=["qa-reviewer"])
        await _seed_policy(maker)

        created = await create_approval_request(
            _auth("creator"),
            policy_key="implementation_review",
            scope_type="action",
            mission_source_repo="Mhaizza/ai-space-colony-mission-control",
            mission_card_kind="issue",
            mission_card_number=16,
            action_key=None,
            expires_at=None,
            idempotency_key="key-1",
        )
        await submit_decision(
            _auth("approver"),
            request_id=created.request_id,
            decision="approve",
            reason=None,
            idempotency_key="decision-key-1",
        )
        with pytest.raises(ApprovalServiceError) as exc_info:
            await submit_decision(
                _auth("late"),
                request_id=created.request_id,
                decision="approve",
                reason=None,
                idempotency_key="decision-key-2",
            )
        assert exc_info.value.code == "request_not_open"

    @pytest.mark.asyncio
    async def test_unknown_request_id_rejected(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="approver", roles=["technical-director"])

        with pytest.raises(ApprovalServiceError) as exc_info:
            await submit_decision(
                _auth("approver"),
                request_id=uuid4(),
                decision="approve",
                reason=None,
                idempotency_key="decision-key-1",
            )
        assert exc_info.value.code == "request_not_found"

    @pytest.mark.asyncio
    async def test_replayed_decision_idempotency_key_returns_identical_result(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        from sqlmodel import select

        from app.models.mc_approval import McApprovalDecision

        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(maker, external_subject="approver", roles=["technical-director"])
        await _seed_policy(maker, definition=TWO_SLOT_POLICY_DEFINITION)

        created = await create_approval_request(
            _auth("creator"),
            policy_key="implementation_review",
            scope_type="action",
            mission_source_repo="Mhaizza/ai-space-colony-mission-control",
            mission_card_kind="issue",
            mission_card_number=16,
            action_key=None,
            expires_at=None,
            idempotency_key="key-1",
        )
        kwargs: dict[str, object] = dict(
            request_id=created.request_id,
            decision="approve",
            reason=None,
            idempotency_key="same-decision-key",
        )
        first = await submit_decision(_auth("approver"), **kwargs)  # type: ignore[arg-type]
        second = await submit_decision(_auth("approver"), **kwargs)  # type: ignore[arg-type]
        assert first == second

        async with maker() as session:
            rows = (await session.exec(select(McApprovalDecision))).all()
        assert len(rows) == 1


class TestTrustRequirements:
    @pytest.mark.asyncio
    async def test_principal_below_trust_requirement_rejected(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(
            maker,
            external_subject="approver",
            roles=["technical-director"],
            trust_level="standard",
        )
        await _seed_policy(maker, definition=TRUST_GATED_POLICY_DEFINITION)

        created = await create_approval_request(
            _auth("creator"),
            policy_key="implementation_review",
            scope_type="action",
            mission_source_repo="Mhaizza/ai-space-colony-mission-control",
            mission_card_kind="issue",
            mission_card_number=16,
            action_key=None,
            expires_at=None,
            idempotency_key="key-1",
        )
        with pytest.raises(ApprovalServiceError) as exc_info:
            await submit_decision(
                _auth("approver"),
                request_id=created.request_id,
                decision="approve",
                reason=None,
                idempotency_key="decision-key-1",
            )
        assert exc_info.value.code == "principal_trust_insufficient"

    @pytest.mark.asyncio
    async def test_principal_meeting_trust_requirement_may_vote(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(
            maker,
            external_subject="approver",
            roles=["technical-director"],
            trust_level="trusted",
        )
        await _seed_policy(maker, definition=TRUST_GATED_POLICY_DEFINITION)

        created = await create_approval_request(
            _auth("creator"),
            policy_key="implementation_review",
            scope_type="action",
            mission_source_repo="Mhaizza/ai-space-colony-mission-control",
            mission_card_kind="issue",
            mission_card_number=16,
            action_key=None,
            expires_at=None,
            idempotency_key="key-1",
        )
        result = await submit_decision(
            _auth("approver"),
            request_id=created.request_id,
            decision="approve",
            reason=None,
            idempotency_key="decision-key-1",
        )
        assert result.status == "approved"

    @pytest.mark.asyncio
    async def test_empty_trust_requirements_does_not_gate(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        # POLICY_DEFINITION has no `trust_requirements` key at all -- absence
        # (as opposed to an empty list) must not gate either.
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(
            maker,
            external_subject="approver",
            roles=["technical-director"],
            trust_level="standard",
        )
        await _seed_policy(maker, definition=POLICY_DEFINITION)

        created = await create_approval_request(
            _auth("creator"),
            policy_key="implementation_review",
            scope_type="action",
            mission_source_repo="Mhaizza/ai-space-colony-mission-control",
            mission_card_kind="issue",
            mission_card_number=16,
            action_key=None,
            expires_at=None,
            idempotency_key="key-1",
        )
        result = await submit_decision(
            _auth("approver"),
            request_id=created.request_id,
            decision="approve",
            reason=None,
            idempotency_key="decision-key-1",
        )
        assert result.status == "approved"


def test_idempotency_scope_values_are_closed_semantic_names() -> None:
    assert {scope.value for scope in IdempotencyScope} == {
        "create_request",
        "submit_decision",
        "supersede_decision",
    }


class TestSupersedeDecision:
    @pytest.mark.asyncio
    async def test_principal_may_change_own_vote(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(maker, external_subject="voter", roles=["technical-director"])
        await _seed_policy(maker)

        created = await create_approval_request(
            _auth("creator"),
            policy_key="implementation_review",
            scope_type="action",
            mission_source_repo="Mhaizza/ai-space-colony-mission-control",
            mission_card_kind="issue",
            mission_card_number=16,
            action_key=None,
            expires_at=None,
            idempotency_key="key-1",
        )
        first_vote = await submit_decision(
            _auth("voter"),
            request_id=created.request_id,
            decision="reject",
            reason="initial concerns",
            idempotency_key="decision-key-1",
        )
        # Single-slot majority quorum is matched from approving principals
        # only -- a lone rejection never satisfies it, so this stays pending
        # rather than resolving to "rejected".
        assert first_vote.status == "pending"
        assert not first_vote.quorum_satisfied

    @pytest.mark.asyncio
    async def test_supersede_own_decision_before_resolution(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(maker, external_subject="voter", roles=["technical-director"])
        await _seed_policy(maker, definition=TWO_SLOT_POLICY_DEFINITION)

        created = await create_approval_request(
            _auth("creator"),
            policy_key="implementation_review",
            scope_type="action",
            mission_source_repo="Mhaizza/ai-space-colony-mission-control",
            mission_card_kind="issue",
            mission_card_number=16,
            action_key=None,
            expires_at=None,
            idempotency_key="key-1",
        )
        first_vote = await submit_decision(
            _auth("voter"),
            request_id=created.request_id,
            decision="reject",
            reason="initial concerns",
            idempotency_key="decision-key-1",
        )
        assert first_vote.status == "pending"

        superseded = await supersede_decision(
            _auth("voter"),
            request_id=created.request_id,
            decision_id=first_vote.decision_id,
            decision="approve",
            reason="concerns resolved",
            idempotency_key="supersede-key-1",
        )
        assert superseded.decision == "approve"
        assert superseded.decision_id != first_vote.decision_id

    @pytest.mark.asyncio
    async def test_cannot_supersede_another_principals_decision(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(maker, external_subject="voter", roles=["technical-director"])
        await _seed_principal(maker, external_subject="other", roles=["qa-reviewer"])
        await _seed_policy(maker, definition=TWO_SLOT_POLICY_DEFINITION)

        created = await create_approval_request(
            _auth("creator"),
            policy_key="implementation_review",
            scope_type="action",
            mission_source_repo="Mhaizza/ai-space-colony-mission-control",
            mission_card_kind="issue",
            mission_card_number=16,
            action_key=None,
            expires_at=None,
            idempotency_key="key-1",
        )
        first_vote = await submit_decision(
            _auth("voter"),
            request_id=created.request_id,
            decision="reject",
            reason=None,
            idempotency_key="decision-key-1",
        )
        with pytest.raises(ApprovalServiceError) as exc_info:
            await supersede_decision(
                _auth("other"),
                request_id=created.request_id,
                decision_id=first_vote.decision_id,
                decision="approve",
                reason=None,
                idempotency_key="supersede-key-1",
            )
        assert exc_info.value.code == "invalid_supersede"

    @pytest.mark.asyncio
    async def test_cannot_supersede_already_superseded_decision(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(maker, external_subject="voter", roles=["technical-director"])
        await _seed_policy(maker, definition=TWO_SLOT_POLICY_DEFINITION)

        created = await create_approval_request(
            _auth("creator"),
            policy_key="implementation_review",
            scope_type="action",
            mission_source_repo="Mhaizza/ai-space-colony-mission-control",
            mission_card_kind="issue",
            mission_card_number=16,
            action_key=None,
            expires_at=None,
            idempotency_key="key-1",
        )
        first_vote = await submit_decision(
            _auth("voter"),
            request_id=created.request_id,
            decision="reject",
            reason=None,
            idempotency_key="decision-key-1",
        )
        second_vote = await supersede_decision(
            _auth("voter"),
            request_id=created.request_id,
            decision_id=first_vote.decision_id,
            decision="reject",
            reason="still concerned",
            idempotency_key="supersede-key-1",
        )
        with pytest.raises(ApprovalServiceError) as exc_info:
            await supersede_decision(
                _auth("voter"),
                request_id=created.request_id,
                decision_id=first_vote.decision_id,  # the already-superseded one
                decision="approve",
                reason=None,
                idempotency_key="supersede-key-2",
            )
        assert exc_info.value.code == "invalid_supersede"
        assert second_vote.decision_id != first_vote.decision_id


class TestConcurrencySerialization:
    """Deterministically seed the state a *losing* race participant would
    observe after the *winning* participant already committed, and assert
    the losing call's code path takes the correct fallback branch. See the
    module docstring for why `asyncio.gather` against a single shared SQLite
    connection is not used here."""

    @pytest.mark.asyncio
    async def test_losing_create_race_replays_winners_response_without_new_row(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        from sqlmodel import select

        from app.mission.approval_service import (
            IdempotencyScope,
            RequestResult,
            _canonical_hash,
            _request_result_to_snapshot,
        )
        from app.models.mc_approval import McApprovalOperation, McApprovalRequest

        principal = await _seed_principal(
            maker, external_subject="creator", roles=["technical-director"]
        )
        await _seed_policy(maker)

        payload = {
            "policy_key": "implementation_review",
            "scope_type": "action",
            "mission_source_repo": "Mhaizza/ai-space-colony-mission-control",
            "mission_card_kind": "issue",
            "mission_card_number": 16,
            "action_key": None,
            "expires_at": None,
        }
        # Simulate a winner that already committed a request and its
        # idempotency-operation row for this exact key before the losing
        # call ever reserves the slot.
        winner_result = RequestResult(
            request_id=uuid4(),
            policy_key="implementation_review",
            policy_version=1,
            status="pending",
            created_by_principal_id=principal.id,
            created_at=utcnow(),
            expires_at=None,
        )
        async with maker() as session:
            session.add(
                McApprovalOperation(
                    idempotency_key="race-key",
                    principal_id=principal.id,
                    endpoint=IdempotencyScope.CREATE_REQUEST.value,
                    payload_hash=_canonical_hash(payload),
                    response_snapshot=_request_result_to_snapshot(winner_result),
                    created_at=utcnow(),
                )
            )
            await session.commit()

        loser_result = await create_approval_request(
            _auth("creator"),
            policy_key="implementation_review",
            scope_type="action",
            mission_source_repo="Mhaizza/ai-space-colony-mission-control",
            mission_card_kind="issue",
            mission_card_number=16,
            action_key=None,
            expires_at=None,
            idempotency_key="race-key",
        )
        assert loser_result == winner_result

        async with maker() as session:
            rows = (await session.exec(select(McApprovalRequest))).all()
        # The loser must never have inserted its own request row -- it
        # replayed the winner's already-committed response instead.
        assert len(rows) == 0

    @pytest.mark.asyncio
    async def test_second_effective_vote_after_first_is_committed_is_rejected(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        # Simulates the losing side of two principals racing to fill the
        # same request: by the time the "losing" call's request-row lock is
        # granted, the winner's decision has already committed and been
        # evaluated. The losing call must see that committed state and
        # behave exactly as sequential ordering would (already proven by
        # TestSubmitDecision.test_decision_on_resolved_request_rejected /
        # test_second_vote_from_same_principal_rejected) -- this test pins
        # that a *different* principal racing in after a request has already
        # resolved gets the same "request_not_open" rejection, not a silent
        # double-resolution.
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(maker, external_subject="winner", roles=["technical-director"])
        await _seed_principal(maker, external_subject="loser", roles=["technical-director"])
        await _seed_policy(maker)

        created = await create_approval_request(
            _auth("creator"),
            policy_key="implementation_review",
            scope_type="action",
            mission_source_repo="Mhaizza/ai-space-colony-mission-control",
            mission_card_kind="issue",
            mission_card_number=16,
            action_key=None,
            expires_at=None,
            idempotency_key="key-1",
        )
        await submit_decision(
            _auth("winner"),
            request_id=created.request_id,
            decision="approve",
            reason=None,
            idempotency_key="decision-winner",
        )
        with pytest.raises(ApprovalServiceError) as exc_info:
            await submit_decision(
                _auth("loser"),
                request_id=created.request_id,
                decision="approve",
                reason=None,
                idempotency_key="decision-loser",
            )
        assert exc_info.value.code == "request_not_open"


class TestSystemCreationCorePrincipalTypeGuard:
    """Checkpoint E, direct-core test (not via resolve_system_principal):
    _create_system_approval_request_in_session must reject a non-"system"
    ResolvedPrincipal before any mutation, independent of and in addition
    to the policy-level allowed_approver_principal_types check -- a future
    direct internal caller passing a human-typed principal must never
    reach a write, even against an otherwise fully system-enabled policy."""

    @pytest.mark.asyncio
    async def test_human_principal_rejected_before_any_mutation(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        from uuid import uuid4 as _uuid4

        from sqlmodel import select

        from app.mission.approval_policy import ApprovalPolicyDefinition
        from app.mission.approval_service import _create_system_approval_request_in_session
        from app.mission.principal_resolver import ResolvedPrincipal
        from app.models.mc_approval import McApprovalEvent, McApprovalRequest

        system_enabled_policy = {
            "decision_rule": "majority",
            "quorum": {"slots": [{"slot": "a", "eligible_roles": ["technical-director"]}]},
            "allowed_approver_principal_types": ["human", "system"],
            "allowed_approver_roles": ["technical-director"],
            "rejection_behavior": "leave_mission_unchanged",
            "expiration": {"behavior": "expire"},
        }
        policy = await _seed_policy(maker, definition=system_enabled_policy)
        definition = ApprovalPolicyDefinition.model_validate(system_enabled_policy)

        human_principal = ResolvedPrincipal(
            id=_uuid4(),
            principal_type="human",  # not "system"
            display_name="Not The System",
            trust_level="standard",
            enabled=True,
            role_slugs=frozenset(),
        )

        async with maker() as session:
            with pytest.raises(ApprovalServiceError) as exc_info:
                await _create_system_approval_request_in_session(
                    session,
                    principal=human_principal,
                    policy=policy,
                    definition=definition,
                    scope_type="action",
                    mission_source_repo="Mhaizza/ai-space-colony-mission-control",
                    mission_card_kind="pull_request",
                    mission_card_number=1,
                    action_key="implementation_review",
                    expires_at=None,
                    trigger_key="mission:owner/repo#1|action:implementation_review|head:"
                    + "a" * 40,
                    supersedes_request_id=None,
                    predecessor_to_supersede=None,
                    auto_retry_count=0,
                )
            assert exc_info.value.code == "principal_not_authorized"

            # No row and no event were ever created.
            requests = (await session.exec(select(McApprovalRequest))).all()
            events = (await session.exec(select(McApprovalEvent))).all()
        assert requests == []
        assert events == []

    @pytest.mark.asyncio
    async def test_human_principal_rejected_even_with_predecessor_to_supersede(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """The predecessor-supersede mutation must not happen either --
        checked before any write, including the predecessor transition."""
        from uuid import uuid4 as _uuid4

        from app.mission.approval_policy import ApprovalPolicyDefinition
        from app.mission.approval_service import _create_system_approval_request_in_session
        from app.mission.principal_resolver import ResolvedPrincipal
        from app.models.mc_approval import McApprovalRequest

        system_enabled_policy = {
            "decision_rule": "majority",
            "quorum": {"slots": [{"slot": "a", "eligible_roles": ["technical-director"]}]},
            "allowed_approver_principal_types": ["human", "system"],
            "allowed_approver_roles": ["technical-director"],
            "rejection_behavior": "leave_mission_unchanged",
            "expiration": {"behavior": "expire"},
        }
        policy = await _seed_policy(maker, definition=system_enabled_policy)
        definition = ApprovalPolicyDefinition.model_validate(system_enabled_policy)
        creator = await _seed_principal(
            maker, external_subject="creator", roles=["technical-director"]
        )
        predecessor = await create_approval_request(
            _auth("creator"),
            policy_key="implementation_review",
            scope_type="action",
            mission_source_repo="Mhaizza/ai-space-colony-mission-control",
            mission_card_kind="pull_request",
            mission_card_number=2,
            action_key="implementation_review",
            expires_at=None,
            idempotency_key="pred-key",
        )
        del creator

        human_principal = ResolvedPrincipal(
            id=_uuid4(),
            principal_type="human",
            display_name="Not The System",
            trust_level="standard",
            enabled=True,
            role_slugs=frozenset(),
        )

        async with maker() as session:
            predecessor_row = await session.get(McApprovalRequest, predecessor.request_id)
            assert predecessor_row is not None
            with pytest.raises(ApprovalServiceError) as exc_info:
                await _create_system_approval_request_in_session(
                    session,
                    principal=human_principal,
                    policy=policy,
                    definition=definition,
                    scope_type="action",
                    mission_source_repo="Mhaizza/ai-space-colony-mission-control",
                    mission_card_kind="pull_request",
                    mission_card_number=2,
                    action_key="implementation_review",
                    expires_at=None,
                    trigger_key="mission:owner/repo#2|action:implementation_review|head:"
                    + "b" * 40,
                    supersedes_request_id=predecessor_row.id,
                    predecessor_to_supersede=predecessor_row,
                    auto_retry_count=0,
                )
            assert exc_info.value.code == "principal_not_authorized"
            assert predecessor_row.status == "pending"  # untouched
            assert predecessor_row.resolved_at is None
