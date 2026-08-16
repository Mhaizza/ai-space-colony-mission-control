# ruff: noqa: INP001
"""Slice 5A Checkpoint B persistence model tests.

Uses an in-memory SQLite engine, matching tests/mission/test_mission_read_api.py's
existing pattern. SQLite does not enforce foreign keys by default (neither
plain sqlite3 nor aiosqlite turn `PRAGMA foreign_keys=ON` on automatically,
and nothing elsewhere in this codebase's db/session.py or test fixtures does
either) — the composite-FK integrity test below would silently pass without
proving anything if that were left unaddressed, so this module explicitly
enables FK enforcement on every new DBAPI connection it creates.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import utcnow
from app.models.mc_approval import (
    McApprovalDecision,
    McApprovalEvent,
    McApprovalOperation,
    McApprovalPolicy,
    McApprovalPolicyActivation,
    McApprovalRequest,
    McPrincipal,
    McPrincipalRole,
)


@asynccontextmanager
async def _in_memory_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fk(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.connect() as conn, conn.begin():
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            yield session
    finally:
        await engine.dispose()


async def _make_principal(session: AsyncSession, **overrides: object) -> McPrincipal:
    defaults: dict[str, object] = {
        "principal_type": "human",
        "display_name": "Test Principal",
        "trust_level": "standard",
        "enabled": True,
    }
    defaults.update(overrides)
    principal = McPrincipal(**defaults)  # type: ignore[arg-type]
    session.add(principal)
    await session.commit()
    await session.refresh(principal)
    return principal


async def _make_policy(session: AsyncSession, *, policy_key: str, version: int) -> McApprovalPolicy:
    policy = McApprovalPolicy(
        policy_key=policy_key,
        version=version,
        definition={"decision_rule": "majority"},
        created_at=utcnow(),
    )
    session.add(policy)
    await session.commit()
    await session.refresh(policy)
    return policy


class TestMcPrincipalRole:
    @pytest.mark.asyncio
    async def test_duplicate_principal_role_rejected(self) -> None:
        async with _in_memory_session() as session:
            principal = await _make_principal(session)
            session.add(McPrincipalRole(principal_id=principal.id, role_slug="qa-reviewer"))
            await session.commit()
            session.add(McPrincipalRole(principal_id=principal.id, role_slug="qa-reviewer"))
            with pytest.raises(IntegrityError):
                await session.commit()

    @pytest.mark.asyncio
    async def test_principal_may_hold_multiple_roles(self) -> None:
        async with _in_memory_session() as session:
            principal = await _make_principal(session)
            session.add(McPrincipalRole(principal_id=principal.id, role_slug="qa-reviewer"))
            session.add(McPrincipalRole(principal_id=principal.id, role_slug="technical-director"))
            await session.commit()

            rows = (
                await session.exec(
                    select(McPrincipalRole).where(McPrincipalRole.principal_id == principal.id)
                )
            ).all()
            assert {row.role_slug for row in rows} == {"qa-reviewer", "technical-director"}


class TestMcApprovalPolicy:
    @pytest.mark.asyncio
    async def test_policy_key_version_unique(self) -> None:
        async with _in_memory_session() as session:
            await _make_policy(session, policy_key="implementation_review", version=1)
            session.add(
                McApprovalPolicy(
                    policy_key="implementation_review",
                    version=1,
                    definition={},
                    created_at=utcnow(),
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

    @pytest.mark.asyncio
    async def test_policy_rows_never_updated_by_application_code(self) -> None:
        # No update path exists in this checkpoint (no service layer yet) --
        # this test documents and pins that expectation at the model layer:
        # re-fetching after another write must show byte-identical content.
        async with _in_memory_session() as session:
            policy = await _make_policy(session, policy_key="implementation_review", version=1)
            original_definition = dict(policy.definition)
            original_created_at = policy.created_at

            await _make_policy(session, policy_key="implementation_review", version=2)

            refetched = await session.get(McApprovalPolicy, policy.id)
            assert refetched is not None
            assert refetched.definition == original_definition
            assert refetched.created_at == original_created_at
            assert refetched.version == 1


class TestMcApprovalPolicyActivation:
    @pytest.mark.asyncio
    async def test_activation_may_point_to_a_version_of_the_same_policy_key(self) -> None:
        async with _in_memory_session() as session:
            policy = await _make_policy(session, policy_key="implementation_review", version=1)
            session.add(
                McApprovalPolicyActivation(
                    policy_key="implementation_review",
                    active_policy_id=policy.id,
                    updated_at=utcnow(),
                )
            )
            await session.commit()

            activation = await session.get(McApprovalPolicyActivation, "implementation_review")
            assert activation is not None
            assert activation.active_policy_id == policy.id

    @pytest.mark.asyncio
    async def test_activation_may_be_updated_to_another_version_of_the_same_key(self) -> None:
        async with _in_memory_session() as session:
            v1 = await _make_policy(session, policy_key="implementation_review", version=1)
            v2 = await _make_policy(session, policy_key="implementation_review", version=2)
            session.add(
                McApprovalPolicyActivation(
                    policy_key="implementation_review",
                    active_policy_id=v1.id,
                    updated_at=utcnow(),
                )
            )
            await session.commit()

            activation = await session.get(McApprovalPolicyActivation, "implementation_review")
            assert activation is not None
            activation.active_policy_id = v2.id
            activation.updated_at = utcnow()
            session.add(activation)
            await session.commit()

            refetched = await session.get(McApprovalPolicyActivation, "implementation_review")
            assert refetched is not None
            assert refetched.active_policy_id == v2.id

    @pytest.mark.asyncio
    async def test_activation_pointing_to_a_different_policy_key_row_fails(self) -> None:
        async with _in_memory_session() as session:
            await _make_policy(session, policy_key="implementation_review", version=1)
            other_policy = await _make_policy(session, policy_key="security_review", version=2)

            session.add(
                McApprovalPolicyActivation(
                    policy_key="implementation_review",
                    active_policy_id=other_policy.id,  # belongs to "security_review", not this key
                    updated_at=utcnow(),
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

    @pytest.mark.asyncio
    async def test_changing_activation_pointer_never_mutates_policy_rows(self) -> None:
        async with _in_memory_session() as session:
            v1 = await _make_policy(session, policy_key="implementation_review", version=1)
            v2 = await _make_policy(session, policy_key="implementation_review", version=2)
            v1_definition, v1_created_at = dict(v1.definition), v1.created_at
            v2_definition, v2_created_at = dict(v2.definition), v2.created_at

            session.add(
                McApprovalPolicyActivation(
                    policy_key="implementation_review",
                    active_policy_id=v1.id,
                    updated_at=utcnow(),
                )
            )
            await session.commit()

            activation = await session.get(McApprovalPolicyActivation, "implementation_review")
            assert activation is not None
            activation.active_policy_id = v2.id
            activation.updated_at = utcnow()
            session.add(activation)
            await session.commit()

            refetched_v1 = await session.get(McApprovalPolicy, v1.id)
            refetched_v2 = await session.get(McApprovalPolicy, v2.id)
            assert refetched_v1 is not None
            assert refetched_v2 is not None
            assert refetched_v1.definition == v1_definition
            assert refetched_v1.created_at == v1_created_at
            assert refetched_v1.version == 1
            assert refetched_v2.definition == v2_definition
            assert refetched_v2.created_at == v2_created_at
            assert refetched_v2.version == 2


class TestMcApprovalRequest:
    @pytest.mark.asyncio
    async def test_duplicate_non_null_trigger_key_rejected(self) -> None:
        async with _in_memory_session() as session:
            principal = await _make_principal(session)
            policy = await _make_policy(session, policy_key="implementation_review", version=1)

            def _request(trigger_key: str | None) -> McApprovalRequest:
                return McApprovalRequest(
                    policy_id=policy.id,
                    scope_type="action",
                    mission_source_repo="Mhaizza/ai-space-colony-mission-control",
                    mission_card_kind="issue",
                    mission_card_number=16,
                    created_by_principal_id=principal.id,
                    creation_source="system",
                    status="pending",
                    created_at=utcnow(),
                    trigger_key=trigger_key,
                )

            session.add(_request("mission_ref|action|sha1"))
            await session.commit()
            session.add(_request("mission_ref|action|sha1"))
            with pytest.raises(IntegrityError):
                await session.commit()

    @pytest.mark.asyncio
    async def test_multiple_null_trigger_keys_allowed(self) -> None:
        async with _in_memory_session() as session:
            principal = await _make_principal(session)
            policy = await _make_policy(session, policy_key="implementation_review", version=1)

            for _ in range(2):
                session.add(
                    McApprovalRequest(
                        policy_id=policy.id,
                        scope_type="mission",
                        mission_source_repo="Mhaizza/ai-space-colony-mission-control",
                        mission_card_kind="pull_request",
                        mission_card_number=17,
                        created_by_principal_id=principal.id,
                        creation_source="human",
                        status="pending",
                        created_at=utcnow(),
                        trigger_key=None,
                    )
                )
            await session.commit()  # must not raise

    @pytest.mark.asyncio
    async def test_invalid_policy_fk_rejected(self) -> None:
        async with _in_memory_session() as session:
            principal = await _make_principal(session)
            session.add(
                McApprovalRequest(
                    policy_id=uuid4(),
                    scope_type="action",
                    mission_source_repo="Mhaizza/ai-space-colony-mission-control",
                    mission_card_kind="issue",
                    mission_card_number=16,
                    created_by_principal_id=principal.id,
                    creation_source="system",
                    status="pending",
                    created_at=utcnow(),
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()


class TestMcApprovalDecisionAndEvent:
    @pytest.mark.asyncio
    async def test_decision_insert_and_fk_integrity(self) -> None:
        async with _in_memory_session() as session:
            principal = await _make_principal(session)
            policy = await _make_policy(session, policy_key="implementation_review", version=1)
            request = McApprovalRequest(
                policy_id=policy.id,
                scope_type="action",
                mission_source_repo="Mhaizza/ai-space-colony-mission-control",
                mission_card_kind="issue",
                mission_card_number=16,
                created_by_principal_id=principal.id,
                creation_source="system",
                status="pending",
                created_at=utcnow(),
            )
            session.add(request)
            await session.commit()
            await session.refresh(request)

            session.add(
                McApprovalDecision(
                    request_id=request.id,
                    principal_id=principal.id,
                    decision="approve",
                    role_slugs_at_decision=["qa-reviewer"],
                    trust_level_at_decision="standard",
                    created_at=utcnow(),
                )
            )
            await session.commit()  # must not raise

            session.add(
                McApprovalDecision(
                    request_id=uuid4(),  # invalid FK
                    principal_id=principal.id,
                    decision="approve",
                    role_slugs_at_decision=["qa-reviewer"],
                    trust_level_at_decision="standard",
                    created_at=utcnow(),
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

    @pytest.mark.asyncio
    async def test_event_triggered_by_principal_is_optional(self) -> None:
        async with _in_memory_session() as session:
            principal = await _make_principal(session)
            policy = await _make_policy(session, policy_key="implementation_review", version=1)
            request = McApprovalRequest(
                policy_id=policy.id,
                scope_type="action",
                mission_source_repo="Mhaizza/ai-space-colony-mission-control",
                mission_card_kind="issue",
                mission_card_number=16,
                created_by_principal_id=principal.id,
                creation_source="system",
                status="pending",
                created_at=utcnow(),
            )
            session.add(request)
            await session.commit()
            await session.refresh(request)

            # System-only event: no attributable principal.
            session.add(
                McApprovalEvent(
                    request_id=request.id,
                    event_type="request_expired",
                    triggered_by_principal_id=None,
                    created_at=utcnow(),
                )
            )
            # Principal-attributed event.
            session.add(
                McApprovalEvent(
                    request_id=request.id,
                    event_type="quorum_reached",
                    triggered_by_principal_id=principal.id,
                    created_at=utcnow(),
                )
            )
            await session.commit()  # must not raise


class TestMcApprovalOperation:
    @pytest.mark.asyncio
    async def test_same_key_different_endpoint_coexist(self) -> None:
        async with _in_memory_session() as session:
            principal = await _make_principal(session)

            def _op(endpoint: str) -> McApprovalOperation:
                return McApprovalOperation(
                    idempotency_key="key-1",
                    principal_id=principal.id,
                    endpoint=endpoint,
                    payload_hash="hash-1",
                    response_snapshot={},
                    created_at=utcnow(),
                )

            session.add(_op("POST /api/v1/mission/approvals"))
            session.add(_op("POST /api/v1/mission/approvals/{id}/decisions"))
            await session.commit()  # must not raise

    @pytest.mark.asyncio
    async def test_same_key_different_principal_coexist(self) -> None:
        async with _in_memory_session() as session:
            p1 = await _make_principal(session, display_name="P1")
            p2 = await _make_principal(session, display_name="P2")

            def _op(principal_id: object) -> McApprovalOperation:
                return McApprovalOperation(
                    idempotency_key="key-1",
                    principal_id=principal_id,
                    endpoint="POST /api/v1/mission/approvals",
                    payload_hash="hash-1",
                    response_snapshot={},
                    created_at=utcnow(),
                )

            session.add(_op(p1.id))
            session.add(_op(p2.id))
            await session.commit()  # must not raise -- proves the finding this fixes

    @pytest.mark.asyncio
    async def test_identical_triple_rejected(self) -> None:
        async with _in_memory_session() as session:
            principal = await _make_principal(session)

            def _op() -> McApprovalOperation:
                return McApprovalOperation(
                    idempotency_key="key-1",
                    principal_id=principal.id,
                    endpoint="POST /api/v1/mission/approvals",
                    payload_hash="hash-1",
                    response_snapshot={},
                    created_at=utcnow(),
                )

            session.add(_op())
            await session.commit()
            session.add(_op())
            with pytest.raises(IntegrityError):
                await session.commit()


def test_no_column_reference_leaks_naive_vs_aware_datetime() -> None:
    # Sanity pin: this codebase's utcnow() returns naive datetimes (see
    # app/core/time.py); every default_factory in mc_approval.py uses it, so
    # nothing in this module should require tzinfo.
    assert utcnow().tzinfo is None
    assert isinstance(utcnow(), datetime)
