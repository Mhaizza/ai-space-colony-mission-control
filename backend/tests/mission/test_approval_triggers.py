# ruff: noqa: INP001
"""Slice 5A Checkpoint E: evaluate_triggers tests.

Human-approved production trigger: policy_key="implementation_review",
action_key="implementation_review", condition = a projected Pull Request has
a new head SHA for that action. Uses the same in-memory SQLite pattern (plus
the SQLite/aiosqlite SAVEPOINT-rollback workaround) as
test_approval_reconciliation.py, since the stale-head-supersession case
(case 3) needs the same atomicity guarantee tested.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

import app.mission.approval_service as approval_service
from app.core.time import utcnow
from app.mission.approval_triggers import (
    TRIGGER_ACTION_KEY,
    TRIGGER_MISSION_CARD_KIND,
    TRIGGER_POLICY_KEY,
    TRIGGER_SCOPE_TYPE,
    _build_mission_trigger_key,
    evaluate_triggers,
)
from app.mission.types import SourceType
from app.models.mc_approval import (
    McApprovalEvent,
    McApprovalPolicy,
    McApprovalPolicyActivation,
    McApprovalRequest,
)
from app.models.mc_projection import McProjectionRecord

OWNER = "Mhaizza"
REPO = "ai-space-colony-mission-control"
MISSION_REPO = f"{OWNER}/{REPO}"

TRIGGER_POLICY = {
    "decision_rule": "majority",
    "quorum": {"slots": [{"slot": "a", "eligible_roles": ["technical-director"]}]},
    "allowed_approver_principal_types": ["human", "system"],
    "allowed_approver_roles": ["technical-director"],
    "rejection_behavior": "leave_mission_unchanged",
    "expiration": {"behavior": "expire"},
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


@asynccontextmanager
async def _test_env(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """`_engine_and_maker` plus patching `approval_service.async_session_maker`
    to the test engine -- `create_system_approval_request` (the fresh-session
    wrapper `evaluate_triggers` calls) always opens its own session via the
    real `async_session_maker`, so it must be redirected to this test's
    in-memory database, exactly like test_approval_service.py's `maker`
    fixture does."""
    async with _engine_and_maker() as maker:
        monkeypatch.setattr(approval_service, "async_session_maker", maker)
        yield maker


async def _seed_policy(maker: async_sessionmaker[AsyncSession]) -> McApprovalPolicy:
    async with maker() as session:
        policy = McApprovalPolicy(
            policy_key=TRIGGER_POLICY_KEY,
            version=1,
            definition=TRIGGER_POLICY,
            created_at=utcnow(),
        )
        session.add(policy)
        await session.commit()
        await session.refresh(policy)
        session.add(
            McApprovalPolicyActivation(
                policy_key=TRIGGER_POLICY_KEY, active_policy_id=policy.id, updated_at=utcnow()
            )
        )
        await session.commit()
        return policy


async def _seed_pr_projection(
    maker: async_sessionmaker[AsyncSession], *, pr_number: int, head_sha: str
) -> None:
    """Upsert the one projection row for this PR node -- mirrors
    GitHubSyncService's own idempotent-upsert-by-(source_type, source_id)
    convention. A second call for the same pr_number simulates the sync
    observing a new head on the *same* PR, not a second PR."""
    async with maker() as session:
        existing = (
            await session.exec(
                select(McProjectionRecord).where(
                    McProjectionRecord.source_type == SourceType.GITHUB_PULL_REQUEST.value,
                    McProjectionRecord.source_id == f"pr-node-{pr_number}",
                )
            )
        ).first()
        if existing is not None:
            existing.payload = {"number": pr_number, "_head_sha": head_sha}
            session.add(existing)
        else:
            session.add(
                McProjectionRecord(
                    source_type=SourceType.GITHUB_PULL_REQUEST.value,
                    source_id=f"pr-node-{pr_number}",
                    partition_key=f"repo:{OWNER}/{REPO}:pull",
                    payload={"number": pr_number, "_head_sha": head_sha},
                    tombstoned=False,
                )
            )
        await session.commit()


async def _requests_for(
    maker: async_sessionmaker[AsyncSession], *, pr_number: int
) -> list[McApprovalRequest]:
    async with maker() as session:
        return list(
            await session.exec(
                select(McApprovalRequest).where(
                    McApprovalRequest.mission_source_repo == MISSION_REPO,
                    McApprovalRequest.mission_card_number == pr_number,
                )
            )
        )


async def _seed_human_request(
    maker: async_sessionmaker[AsyncSession],
    policy: McApprovalPolicy,
    *,
    pr_number: int,
    status: str = "pending",
) -> McApprovalRequest:
    """A Human-created implementation_review request -- creation_source
    "human", trigger_key NULL -- the exact shape a real
    create_approval_request() call would leave behind. Constructed directly
    against the model (mirroring test_approval_reconciliation.py's
    _seed_request helper) rather than via the full AuthContext/principal-
    resolution plumbing, since this test module is only exercising
    evaluate_triggers' predecessor lookup, not the human creation path
    itself (already covered by test_approval_service.py)."""
    from app.models.mc_approval import McPrincipal

    async with maker() as session:
        creator = McPrincipal(
            principal_type="human",
            display_name=f"human-creator-{pr_number}",
            trust_level="standard",
            enabled=True,
            external_provider="local",
            external_subject=f"human-creator-{pr_number}",
        )
        session.add(creator)
        await session.commit()
        await session.refresh(creator)

        request = McApprovalRequest(
            policy_id=policy.id,
            scope_type=TRIGGER_SCOPE_TYPE,
            mission_source_repo=MISSION_REPO,
            mission_card_kind=TRIGGER_MISSION_CARD_KIND,
            mission_card_number=pr_number,
            action_key=TRIGGER_ACTION_KEY,
            created_by_principal_id=creator.id,
            creation_source="human",
            status=status,
            created_at=utcnow(),
            expires_at=None,
            trigger_key=None,
        )
        session.add(request)
        await session.commit()
        await session.refresh(request)
        if status != "pending":
            request.resolved_at = utcnow()
            session.add(request)
            await session.commit()
            await session.refresh(request)
        return request


class TestTriggerKeyConstruction:
    def test_builds_expected_format(self) -> None:
        key = _build_mission_trigger_key(
            owner=OWNER, repo=REPO, pr_number=7, action_key=TRIGGER_ACTION_KEY, head_sha="abc123"
        )
        assert key == f"mission:{OWNER}/{REPO}#7|action:{TRIGGER_ACTION_KEY}|head:abc123"


class TestFirstObservation:
    @pytest.mark.asyncio
    async def test_no_prior_request_creates_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async with _test_env(monkeypatch) as maker:
            await _seed_policy(maker)
            await _seed_pr_projection(maker, pr_number=1, head_sha="a" * 40)

            async with maker() as session:
                result = await evaluate_triggers(session, owner=OWNER, repo=REPO)

            assert result.observed == 1
            assert result.created == 1
            requests = await _requests_for(maker, pr_number=1)
            assert len(requests) == 1
            request = requests[0]
            assert request.creation_source == "system"
            assert request.action_key == TRIGGER_ACTION_KEY
            assert request.trigger_key == _build_mission_trigger_key(
                owner=OWNER,
                repo=REPO,
                pr_number=1,
                action_key=TRIGGER_ACTION_KEY,
                head_sha="a" * 40,
            )
            assert request.supersedes_request_id is None
            assert request.expires_at is not None
            assert request.expires_at > request.created_at

    @pytest.mark.asyncio
    async def test_ignores_pr_record_without_head_sha(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with _test_env(monkeypatch) as maker:
            await _seed_policy(maker)
            async with maker() as session:
                session.add(
                    McProjectionRecord(
                        source_type=SourceType.GITHUB_PULL_REQUEST.value,
                        source_id="pr-node-broken",
                        partition_key=f"repo:{OWNER}/{REPO}:pull",
                        payload={"number": 2},  # no _head_sha
                        tombstoned=False,
                    )
                )
                await session.commit()

            async with maker() as session:
                result = await evaluate_triggers(session, owner=OWNER, repo=REPO)
            assert result.observed == 0
            assert await _requests_for(maker, pr_number=2) == []


class TestSameHead:
    @pytest.mark.asyncio
    async def test_same_head_pending_predecessor_is_no_op(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with _test_env(monkeypatch) as maker:
            await _seed_policy(maker)
            await _seed_pr_projection(maker, pr_number=3, head_sha="b" * 40)
            async with maker() as session:
                await evaluate_triggers(session, owner=OWNER, repo=REPO)

            async with maker() as session:
                result = await evaluate_triggers(session, owner=OWNER, repo=REPO)

            assert result.created == 0
            assert result.superseded == 0
            requests = await _requests_for(maker, pr_number=3)
            assert len(requests) == 1

    @pytest.mark.asyncio
    async def test_same_head_terminal_predecessor_is_dedup_replay(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with _test_env(monkeypatch) as maker:
            await _seed_policy(maker)
            await _seed_pr_projection(maker, pr_number=4, head_sha="c" * 40)
            async with maker() as session:
                await evaluate_triggers(session, owner=OWNER, repo=REPO)

            requests = await _requests_for(maker, pr_number=4)
            async with maker() as session:
                row = await session.get(McApprovalRequest, requests[0].id)
                assert row is not None
                row.status = "approved"
                row.resolved_at = utcnow()
                session.add(row)
                await session.commit()

            async with maker() as session:
                result = await evaluate_triggers(session, owner=OWNER, repo=REPO)

            assert result.created == 0
            requests_after = await _requests_for(maker, pr_number=4)
            assert len(requests_after) == 1  # no new row


class TestNewHead:
    @pytest.mark.asyncio
    async def test_new_head_terminal_predecessor_creates_plain_successor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with _test_env(monkeypatch) as maker:
            await _seed_policy(maker)
            await _seed_pr_projection(maker, pr_number=5, head_sha="d" * 40)
            async with maker() as session:
                await evaluate_triggers(session, owner=OWNER, repo=REPO)
            requests = await _requests_for(maker, pr_number=5)
            predecessor_id = requests[0].id
            async with maker() as session:
                row = await session.get(McApprovalRequest, predecessor_id)
                assert row is not None
                row.status = "rejected"
                row.resolved_at = utcnow()
                session.add(row)
                await session.commit()

            await _seed_pr_projection(maker, pr_number=5, head_sha="e" * 40)
            async with maker() as session:
                result = await evaluate_triggers(session, owner=OWNER, repo=REPO)

            assert result.created == 1
            assert result.superseded == 0
            requests_after = await _requests_for(maker, pr_number=5)
            assert len(requests_after) == 2
            successor = next(r for r in requests_after if r.id != predecessor_id)
            assert successor.status == "pending"
            assert successor.supersedes_request_id == predecessor_id
            # Predecessor untouched.
            async with maker() as session:
                predecessor = await session.get(McApprovalRequest, predecessor_id)
            assert predecessor is not None
            assert predecessor.status == "rejected"
            events = await _events_for(maker, predecessor_id)
            # Only its own original request_created event -- no
            # request_superseded event, since it was already terminal.
            assert [e.event_type for e in events] == ["request_created"]

    @pytest.mark.asyncio
    async def test_new_head_pending_predecessor_atomically_supersedes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with _test_env(monkeypatch) as maker:
            await _seed_policy(maker)
            await _seed_pr_projection(maker, pr_number=6, head_sha="f" * 40)
            async with maker() as session:
                await evaluate_triggers(session, owner=OWNER, repo=REPO)
            requests = await _requests_for(maker, pr_number=6)
            predecessor_id = requests[0].id

            await _seed_pr_projection(maker, pr_number=6, head_sha="0" * 40)
            async with maker() as session:
                result = await evaluate_triggers(session, owner=OWNER, repo=REPO)

            assert result.superseded == 1
            requests_after = await _requests_for(maker, pr_number=6)
            assert len(requests_after) == 2
            async with maker() as session:
                predecessor = await session.get(McApprovalRequest, predecessor_id)
            assert predecessor is not None
            assert predecessor.status == "superseded"
            assert predecessor.resolved_at is not None
            successor = next(r for r in requests_after if r.id != predecessor_id)
            assert successor.status == "pending"
            assert successor.supersedes_request_id == predecessor_id

            events = await _events_for(maker, predecessor_id)
            # Its own original request_created event, plus exactly one
            # request_superseded event from this atomic supersession.
            event_types = sorted(e.event_type for e in events)
            assert event_types == ["request_created", "request_superseded"]


async def _events_for(maker: async_sessionmaker[AsyncSession], request_id) -> list[McApprovalEvent]:
    async with maker() as session:
        return list(
            await session.exec(
                select(McApprovalEvent).where(McApprovalEvent.request_id == request_id)
            )
        )


class TestConcurrentDuplicateHeadObservations:
    @pytest.mark.asyncio
    async def test_losing_race_replays_winners_successor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deterministically seed the state a losing racer would observe
        after a winner already superseded the predecessor and created the
        head-B successor -- the loser's own evaluate_triggers call must not
        create a second successor or a second request_superseded event."""
        async with _test_env(monkeypatch) as maker:
            await _seed_policy(maker)
            await _seed_pr_projection(maker, pr_number=8, head_sha="1" * 40)
            async with maker() as session:
                await evaluate_triggers(session, owner=OWNER, repo=REPO)

            await _seed_pr_projection(maker, pr_number=8, head_sha="2" * 40)
            async with maker() as session:
                first = await evaluate_triggers(session, owner=OWNER, repo=REPO)
            assert first.superseded == 1

            # A second tick observing the *same* head B again (the "loser"
            # in a concurrent-observation race, replayed sequentially here
            # per the deterministic-seeding convention).
            async with maker() as session:
                second = await evaluate_triggers(session, owner=OWNER, repo=REPO)

            assert second.superseded == 0
            assert second.created == 0
            requests_after = await _requests_for(maker, pr_number=8)
            assert len(requests_after) == 2  # still exactly original + one successor


class TestNoGitHubMutationSurface:
    def test_module_imports_no_github_write_capable_client(self) -> None:
        import app.mission.approval_triggers as module

        source_names = dir(module)
        assert "GitHubReadClient" not in source_names
        assert not any(
            "github" in name.lower() and "client" in name.lower() for name in source_names
        )


class TestHumanCreatedPredecessorHistory:
    """Re-review blocker: the predecessor/history lookup must not exclude
    Human-created requests just because trigger_key is NULL -- the most
    recent request for the exact (mission_source_repo, mission_card_number,
    action_key) triple is the predecessor regardless of creation_source."""

    @pytest.mark.asyncio
    async def test_human_pending_predecessor_atomically_superseded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with _test_env(monkeypatch) as maker:
            policy = await _seed_policy(maker)
            human_predecessor = await _seed_human_request(maker, policy, pr_number=20)
            await _seed_pr_projection(maker, pr_number=20, head_sha="a" * 40)

            async with maker() as session:
                result = await evaluate_triggers(session, owner=OWNER, repo=REPO)

            assert result.superseded == 1
            assert result.created == 0

            async with maker() as session:
                predecessor = await session.get(McApprovalRequest, human_predecessor.id)
            assert predecessor is not None
            assert predecessor.status == "superseded"
            assert predecessor.resolved_at is not None

            requests_after = await _requests_for(maker, pr_number=20)
            assert len(requests_after) == 2
            successor = next(r for r in requests_after if r.id != human_predecessor.id)
            assert successor.status == "pending"
            assert successor.creation_source == "system"
            assert successor.supersedes_request_id == human_predecessor.id

            # Exactly one request_superseded event on the Human predecessor.
            events = await _events_for(maker, human_predecessor.id)
            assert [e.event_type for e in events] == ["request_superseded"]

            # No two pending review cycles remain for this repo/PR/action.
            pending = [r for r in requests_after if r.status == "pending"]
            assert len(pending) == 1

    @pytest.mark.asyncio
    async def test_human_terminal_predecessor_gets_linked_system_successor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with _test_env(monkeypatch) as maker:
            policy = await _seed_policy(maker)
            human_predecessor = await _seed_human_request(
                maker, policy, pr_number=21, status="rejected"
            )
            await _seed_pr_projection(maker, pr_number=21, head_sha="b" * 40)

            async with maker() as session:
                result = await evaluate_triggers(session, owner=OWNER, repo=REPO)

            assert result.created == 1
            assert result.superseded == 0

            async with maker() as session:
                predecessor = await session.get(McApprovalRequest, human_predecessor.id)
            assert predecessor is not None
            assert predecessor.status == "rejected"  # unchanged

            requests_after = await _requests_for(maker, pr_number=21)
            assert len(requests_after) == 2
            successor = next(r for r in requests_after if r.id != human_predecessor.id)
            assert successor.status == "pending"
            assert successor.creation_source == "system"
            assert successor.supersedes_request_id == human_predecessor.id

            # No two pending review cycles remain for this repo/PR/action.
            pending = [r for r in requests_after if r.status == "pending"]
            assert len(pending) == 1


class TestTriggerCreatedTTLExactness:
    """Re-review blocker: expires_at - created_at must equal exactly
    mc_approval_default_expiration_seconds (86400s), not slightly less due
    to two separate utcnow() calls."""

    @pytest.mark.asyncio
    async def test_first_observation_ttl_is_exact(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from datetime import timedelta

        from app.core.config import settings

        async with _test_env(monkeypatch) as maker:
            await _seed_policy(maker)
            await _seed_pr_projection(maker, pr_number=22, head_sha="c" * 40)

            async with maker() as session:
                await evaluate_triggers(session, owner=OWNER, repo=REPO)

            requests = await _requests_for(maker, pr_number=22)
            assert len(requests) == 1
            request = requests[0]
            assert request.expires_at is not None
            assert request.expires_at - request.created_at == timedelta(
                seconds=settings.mc_approval_default_expiration_seconds
            )

    @pytest.mark.asyncio
    async def test_new_head_pending_predecessor_successor_ttl_is_exact(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datetime import timedelta

        from app.core.config import settings

        async with _test_env(monkeypatch) as maker:
            await _seed_policy(maker)
            await _seed_pr_projection(maker, pr_number=23, head_sha="d" * 40)
            async with maker() as session:
                await evaluate_triggers(session, owner=OWNER, repo=REPO)

            await _seed_pr_projection(maker, pr_number=23, head_sha="e" * 40)
            async with maker() as session:
                await evaluate_triggers(session, owner=OWNER, repo=REPO)

            requests = await _requests_for(maker, pr_number=23)
            successor = max(requests, key=lambda r: r.created_at)
            assert successor.expires_at is not None
            assert successor.expires_at - successor.created_at == timedelta(
                seconds=settings.mc_approval_default_expiration_seconds
            )
