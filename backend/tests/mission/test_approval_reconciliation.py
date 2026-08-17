# ruff: noqa: INP001
"""Slice 5A Checkpoint E: run_reconciliation_tick tests.

Uses the same in-memory SQLite pattern as test_approval_service.py, plus the
SQLite/aiosqlite SAVEPOINT-rollback workaround also used in
test_approval_system_principal.py -- required here because the recreate
atomicity test (below) asserts a mid-transaction failure rolls back both the
predecessor's expiry and the successor's insert together.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

import app.mission.approval_reconciliation as approval_reconciliation
from app.core.time import utcnow
from app.mission.approval_reconciliation import run_reconciliation_tick
from app.models.mc_approval import (
    McApprovalEvent,
    McApprovalPolicy,
    McApprovalPolicyActivation,
    McApprovalRequest,
    McPrincipal,
)

MISSION_REPO = "Mhaizza/ai-space-colony-mission-control"

EXPIRE_POLICY = {
    "decision_rule": "majority",
    "quorum": {"slots": [{"slot": "a", "eligible_roles": ["technical-director"]}]},
    "allowed_approver_principal_types": ["human"],
    "allowed_approver_roles": ["technical-director"],
    "rejection_behavior": "leave_mission_unchanged",
    "expiration": {"behavior": "expire"},
}

BLOCK_MISSION_POLICY = {
    **EXPIRE_POLICY,
    "expiration": {"behavior": "block_mission"},
}

RECREATE_POLICY = {
    "decision_rule": "majority",
    "quorum": {"slots": [{"slot": "a", "eligible_roles": ["technical-director"]}]},
    "allowed_approver_principal_types": ["human", "system"],
    "allowed_approver_roles": ["technical-director"],
    "rejection_behavior": "leave_mission_unchanged",
    "expiration": {"behavior": "recreate", "max_auto_retries": 3},
}


@asynccontextmanager
async def _engine_and_maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _do_connect(dbapi_connection: object, connection_record: object) -> None:
        dbapi_connection.isolation_level = None  # type: ignore[attr-defined]

    @event.listens_for(engine.sync_engine, "begin")
    def _do_begin(conn: object) -> None:
        conn.exec_driver_sql("BEGIN")  # type: ignore[attr-defined]

    async with engine.connect() as conn, conn.begin():
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


async def _seed_policy(
    maker: async_sessionmaker[AsyncSession],
    *,
    policy_key: str = "implementation_review",
    definition: dict[str, object] | None = None,
) -> McApprovalPolicy:
    async with maker() as session:
        policy = McApprovalPolicy(
            policy_key=policy_key,
            version=1,
            definition=definition or EXPIRE_POLICY,
            created_at=utcnow(),
        )
        session.add(policy)
        await session.commit()
        await session.refresh(policy)
        session.add(
            McApprovalPolicyActivation(
                policy_key=policy_key, active_policy_id=policy.id, updated_at=utcnow()
            )
        )
        await session.commit()
        return policy


async def _seed_human_creator(maker: async_sessionmaker[AsyncSession]) -> McPrincipal:
    async with maker() as session:
        principal = McPrincipal(
            principal_type="human",
            display_name="creator",
            trust_level="standard",
            enabled=True,
            external_provider="local",
            external_subject="creator",
        )
        session.add(principal)
        await session.commit()
        await session.refresh(principal)
        return principal


async def _seed_request(
    maker: async_sessionmaker[AsyncSession],
    *,
    policy: McApprovalPolicy,
    creator: McPrincipal,
    expires_at,
    created_at=None,
    status: str = "pending",
    trigger_key: str | None = None,
    auto_retry_count: int = 0,
    mission_card_number: int = 42,
    action_key: str | None = None,
) -> McApprovalRequest:
    # Default created_at to one hour before expires_at (a sane positive TTL)
    # rather than "now" -- callers that pass an already-past expires_at
    # without an explicit created_at would otherwise get a *negative* TTL
    # by construction, silently exercising the non-positive-TTL fallback
    # branch instead of whatever the test actually intends.
    if created_at is None:
        created_at = expires_at - timedelta(hours=1) if expires_at is not None else utcnow()
    async with maker() as session:
        request = McApprovalRequest(
            policy_id=policy.id,
            scope_type="action",
            mission_source_repo=MISSION_REPO,
            mission_card_kind="pull_request",
            mission_card_number=mission_card_number,
            action_key=action_key,
            created_by_principal_id=creator.id,
            creation_source="human" if trigger_key is None else "system",
            status=status,
            created_at=created_at,
            expires_at=expires_at,
            trigger_key=trigger_key,
            auto_retry_count=auto_retry_count,
        )
        session.add(request)
        await session.commit()
        await session.refresh(request)
        return request


async def _events_for(maker: async_sessionmaker[AsyncSession], request_id) -> list[McApprovalEvent]:
    async with maker() as session:
        return list(
            await session.exec(
                select(McApprovalEvent).where(McApprovalEvent.request_id == request_id)
            )
        )


class TestPlainExpire:
    @pytest.mark.asyncio
    async def test_expiration_occurs_server_side_with_one_event(self) -> None:
        async with _engine_and_maker() as maker:
            policy = await _seed_policy(maker, definition=EXPIRE_POLICY)
            creator = await _seed_human_creator(maker)
            past = utcnow() - timedelta(hours=1)
            request = await _seed_request(maker, policy=policy, creator=creator, expires_at=past)

            async with maker() as session:
                result = await run_reconciliation_tick(session)

            assert result.processed == 1
            async with maker() as session:
                refreshed = await session.get(McApprovalRequest, request.id)
            assert refreshed is not None
            assert refreshed.status == "expired"
            assert refreshed.resolved_at is not None

            events = await _events_for(maker, request.id)
            assert len(events) == 1
            assert events[0].event_type == "request_expired"
            assert events[0].triggered_by_principal_id is None

    @pytest.mark.asyncio
    async def test_pending_not_yet_expired_untouched(self) -> None:
        async with _engine_and_maker() as maker:
            policy = await _seed_policy(maker, definition=EXPIRE_POLICY)
            creator = await _seed_human_creator(maker)
            future = utcnow() + timedelta(hours=1)
            request = await _seed_request(maker, policy=policy, creator=creator, expires_at=future)

            async with maker() as session:
                result = await run_reconciliation_tick(session)

            assert result.processed == 0
            async with maker() as session:
                refreshed = await session.get(McApprovalRequest, request.id)
            assert refreshed is not None
            assert refreshed.status == "pending"

    @pytest.mark.asyncio
    async def test_terminal_request_never_reopened_even_if_expires_at_passed(self) -> None:
        async with _engine_and_maker() as maker:
            policy = await _seed_policy(maker, definition=EXPIRE_POLICY)
            creator = await _seed_human_creator(maker)
            past = utcnow() - timedelta(hours=1)
            request = await _seed_request(
                maker, policy=policy, creator=creator, expires_at=past, status="approved"
            )

            async with maker() as session:
                result = await run_reconciliation_tick(session)

            assert result.processed == 0
            events = await _events_for(maker, request.id)
            assert events == []
            async with maker() as session:
                refreshed = await session.get(McApprovalRequest, request.id)
            assert refreshed is not None
            assert refreshed.status == "approved"

    @pytest.mark.asyncio
    async def test_pinned_policy_version_unchanged_after_reconciliation(self) -> None:
        async with _engine_and_maker() as maker:
            policy = await _seed_policy(maker, definition=EXPIRE_POLICY)
            creator = await _seed_human_creator(maker)
            past = utcnow() - timedelta(hours=1)
            await _seed_request(maker, policy=policy, creator=creator, expires_at=past)

            async with maker() as session:
                await run_reconciliation_tick(session)

            async with maker() as session:
                refreshed_policy = await session.get(McApprovalPolicy, policy.id)
            assert refreshed_policy is not None
            assert refreshed_policy.definition == EXPIRE_POLICY

    @pytest.mark.asyncio
    async def test_repeated_reconciliation_is_idempotent(self) -> None:
        async with _engine_and_maker() as maker:
            policy = await _seed_policy(maker, definition=EXPIRE_POLICY)
            creator = await _seed_human_creator(maker)
            past = utcnow() - timedelta(hours=1)
            request = await _seed_request(maker, policy=policy, creator=creator, expires_at=past)

            async with maker() as session:
                first = await run_reconciliation_tick(session)
            async with maker() as session:
                second = await run_reconciliation_tick(session)

            assert first.processed == 1
            assert second.processed == 0
            events = await _events_for(maker, request.id)
            assert len(events) == 1


class TestBlockMission:
    @pytest.mark.asyncio
    async def test_block_mission_records_mission_effect_in_single_event(self) -> None:
        async with _engine_and_maker() as maker:
            policy = await _seed_policy(maker, definition=BLOCK_MISSION_POLICY)
            creator = await _seed_human_creator(maker)
            past = utcnow() - timedelta(hours=1)
            request = await _seed_request(maker, policy=policy, creator=creator, expires_at=past)

            async with maker() as session:
                await run_reconciliation_tick(session)

            events = await _events_for(maker, request.id)
            assert len(events) == 1
            assert events[0].detail is not None
            assert events[0].detail["mission_effect"] == "blocked"


class TestRecreate:
    @pytest.mark.asyncio
    async def test_recreate_under_bound_creates_successor_with_preserved_ttl(self) -> None:
        async with _engine_and_maker() as maker:
            policy = await _seed_policy(maker, definition=RECREATE_POLICY)
            creator = await _seed_human_creator(maker)
            created_at = utcnow() - timedelta(hours=3)
            expires_at = created_at + timedelta(hours=2)  # already passed
            predecessor = await _seed_request(
                maker,
                policy=policy,
                creator=creator,
                expires_at=expires_at,
                created_at=created_at,
            )

            async with maker() as session:
                result = await run_reconciliation_tick(session)

            assert result.processed == 1
            assert result.recreated == 1

            async with maker() as session:
                refreshed_predecessor = await session.get(McApprovalRequest, predecessor.id)
                successors = list(
                    await session.exec(
                        select(McApprovalRequest).where(
                            McApprovalRequest.supersedes_request_id == predecessor.id
                        )
                    )
                )
            assert refreshed_predecessor is not None
            assert refreshed_predecessor.status == "expired"
            assert len(successors) == 1
            successor = successors[0]
            assert successor.status == "pending"
            assert successor.auto_retry_count == 1
            assert successor.creation_source == "system"
            assert successor.trigger_key == f"recreate:{predecessor.id}|retry:1"
            # TTL preserved exactly (2h), clock restarted from recreation time.
            assert successor.expires_at - successor.created_at == timedelta(hours=2)

    @pytest.mark.asyncio
    async def test_auto_retry_count_sequence_and_bounded_stop(self) -> None:
        async with _engine_and_maker() as maker:
            policy = await _seed_policy(maker, definition=RECREATE_POLICY)
            creator = await _seed_human_creator(maker)
            created_at = utcnow() - timedelta(days=1, hours=1)
            expires_at = created_at + timedelta(hours=1)
            current = await _seed_request(
                maker,
                policy=policy,
                creator=creator,
                expires_at=expires_at,
                created_at=created_at,
            )
            root_id = current.id

            observed_auto_retry_counts = []
            for _ in range(4):  # original expires 3 times (retries 1,2,3), 4th hits the bound
                async with maker() as session:
                    await run_reconciliation_tick(session)
                async with maker() as session:
                    successors = list(
                        await session.exec(
                            select(McApprovalRequest).where(
                                McApprovalRequest.supersedes_request_id == current.id
                            )
                        )
                    )
                if not successors:
                    break
                current = successors[0]
                observed_auto_retry_counts.append(current.auto_retry_count)
                # Force the new successor to already be expired so the next
                # tick can process it too -- push created_at back as well,
                # preserving a positive TTL (never just move expires_at
                # before created_at, which would trip the non-positive-TTL
                # fallback instead of continuing the retry chain).
                async with maker() as session:
                    row = await session.get(McApprovalRequest, current.id)
                    assert row is not None
                    row.created_at = utcnow() - timedelta(hours=2)
                    row.expires_at = utcnow() - timedelta(hours=1)
                    session.add(row)
                    await session.commit()

            assert observed_auto_retry_counts == [1, 2, 3]

            # Retry 3 (auto_retry_count=3 == max_auto_retries) must NOT
            # produce a 4th successor -- bounded stop.
            async with maker() as session:
                final = await session.get(McApprovalRequest, current.id)
            assert final is not None
            assert final.status == "expired"

            async with maker() as session:
                await run_reconciliation_tick(session)
                no_successor = list(
                    await session.exec(
                        select(McApprovalRequest).where(
                            McApprovalRequest.supersedes_request_id == current.id
                        )
                    )
                )
            assert no_successor == []

            # Cross-check: every successor's auto_retry_count exactly
            # matches the retry number embedded in its own trigger_key, and
            # every trigger_key shares the same root (the original request).
            async with maker() as session:
                chain = list(
                    await session.exec(
                        select(McApprovalRequest).where(McApprovalRequest.id != root_id)
                    )
                )
            for row in chain:
                assert row.trigger_key is not None
                assert row.trigger_key == f"recreate:{root_id}|retry:{row.auto_retry_count}"

    @pytest.mark.asyncio
    async def test_recreate_at_bound_falls_back_to_plain_expire(self) -> None:
        async with _engine_and_maker() as maker:
            policy = await _seed_policy(maker, definition=RECREATE_POLICY)
            creator = await _seed_human_creator(maker)
            past = utcnow() - timedelta(hours=1)
            request = await _seed_request(
                maker,
                policy=policy,
                creator=creator,
                expires_at=past,
                auto_retry_count=3,  # already == max_auto_retries
                trigger_key="recreate:00000000-0000-0000-0000-000000000000|retry:3",
            )

            async with maker() as session:
                result = await run_reconciliation_tick(session)

            assert result.processed == 1
            assert result.recreated == 0
            async with maker() as session:
                successors = list(
                    await session.exec(
                        select(McApprovalRequest).where(
                            McApprovalRequest.supersedes_request_id == request.id
                        )
                    )
                )
            assert successors == []
            events = await _events_for(maker, request.id)
            assert len(events) == 1
            assert events[0].detail is not None
            assert events[0].detail.get("max_auto_retries_reached") is True

    @pytest.mark.asyncio
    async def test_non_positive_ttl_falls_back_to_single_row_expire(self) -> None:
        async with _engine_and_maker() as maker:
            policy = await _seed_policy(maker, definition=RECREATE_POLICY)
            creator = await _seed_human_creator(maker)
            now = utcnow()
            # Degenerate: expires_at <= created_at.
            request = await _seed_request(
                maker,
                policy=policy,
                creator=creator,
                created_at=now - timedelta(hours=1),
                expires_at=now - timedelta(hours=2),
            )

            async with maker() as session:
                result = await run_reconciliation_tick(session)

            assert result.processed == 1
            assert result.recreated == 0
            async with maker() as session:
                refreshed = await session.get(McApprovalRequest, request.id)
                successors = list(
                    await session.exec(
                        select(McApprovalRequest).where(
                            McApprovalRequest.supersedes_request_id == request.id
                        )
                    )
                )
            assert refreshed is not None
            assert refreshed.status == "expired"
            assert successors == []
            events = await _events_for(maker, request.id)
            assert events[0].detail is not None
            assert events[0].detail.get("non_positive_ttl") is True

    @pytest.mark.asyncio
    async def test_healthy_sibling_request_unaffected_by_anomalous_ttl_row(self) -> None:
        async with _engine_and_maker() as maker:
            policy = await _seed_policy(maker, definition=RECREATE_POLICY)
            creator = await _seed_human_creator(maker)
            now = utcnow()
            anomalous = await _seed_request(
                maker,
                policy=policy,
                creator=creator,
                mission_card_number=1,
                created_at=now - timedelta(hours=1),
                expires_at=now - timedelta(hours=2),
            )
            healthy = await _seed_request(
                maker,
                policy=policy,
                creator=creator,
                mission_card_number=2,
                created_at=now - timedelta(hours=3),
                expires_at=now - timedelta(hours=1),
            )

            async with maker() as session:
                result = await run_reconciliation_tick(session)

            assert result.processed == 2
            assert result.recreated == 1
            async with maker() as session:
                healthy_successors = list(
                    await session.exec(
                        select(McApprovalRequest).where(
                            McApprovalRequest.supersedes_request_id == healthy.id
                        )
                    )
                )
            assert len(healthy_successors) == 1
            del anomalous  # seeded only to occupy the anomalous half of the pair

    @pytest.mark.asyncio
    async def test_recreate_atomicity_full_rollback_on_mid_sequence_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Force an exception between the predecessor's expiry and the
        successor's insert; assert the whole per-row transaction rolls back
        -- predecessor is still pending afterward, no successor exists."""
        async with _engine_and_maker() as maker:
            policy = await _seed_policy(maker, definition=RECREATE_POLICY)
            creator = await _seed_human_creator(maker)
            past = utcnow() - timedelta(hours=1)
            request = await _seed_request(maker, policy=policy, creator=creator, expires_at=past)

            async def _boom(*args: object, **kwargs: object) -> None:
                raise RuntimeError("simulated failure mid-recreate")

            monkeypatch.setattr(approval_reconciliation, "resolve_system_principal", _boom)

            async with maker() as session:
                with pytest.raises(RuntimeError, match="simulated failure mid-recreate"):
                    await run_reconciliation_tick(session)

            async with maker() as session:
                refreshed = await session.get(McApprovalRequest, request.id)
                successors = list(
                    await session.exec(
                        select(McApprovalRequest).where(
                            McApprovalRequest.supersedes_request_id == request.id
                        )
                    )
                )
            assert refreshed is not None
            assert refreshed.status == "pending"  # rolled back, not left "expired"
            assert successors == []
            assert await _events_for(maker, request.id) == []


class TestConcurrencySerialization:
    """Deterministically seed the state a losing race participant would
    observe after a winning participant already committed -- see
    test_approval_service.py's module docstring for why asyncio.gather
    against shared SQLite is not used here."""

    @pytest.mark.asyncio
    async def test_losing_reconciliation_race_skips_already_resolved_row(self) -> None:
        async with _engine_and_maker() as maker:
            policy = await _seed_policy(maker, definition=EXPIRE_POLICY)
            creator = await _seed_human_creator(maker)
            past = utcnow() - timedelta(hours=1)
            request = await _seed_request(maker, policy=policy, creator=creator, expires_at=past)

            # Winner already resolved it.
            async with maker() as session:
                row = await session.get(McApprovalRequest, request.id)
                assert row is not None
                row.status = "expired"
                row.resolved_at = utcnow()
                session.add(row)
                await session.commit()

            # Loser's tick must recheck-inside-lock and skip cleanly.
            async with maker() as session:
                result = await run_reconciliation_tick(session)
            assert result.processed == 0
            events = await _events_for(maker, request.id)
            assert events == []
