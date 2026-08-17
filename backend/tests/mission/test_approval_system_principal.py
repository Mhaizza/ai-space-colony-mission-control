# ruff: noqa: INP001
"""Slice 5A Checkpoint E: resolve_system_principal tests.

Uses the same in-memory SQLite pattern as test_approval_service.py. Proves
get-or-create idempotency, SAVEPOINT-based race safety (the outer
transaction must remain usable after a resolved race), and -- the key
security invariant -- that no `AuthContext`-authenticated caller can ever
resolve to the system principal.
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

from app.core.auth import AuthContext
from app.mission.approval_system_principal import (
    SYSTEM_PROVIDER,
    SYSTEM_SUBJECT,
    resolve_system_principal,
)
from app.mission.principal_resolver import resolve_principal
from app.models.mc_approval import McPrincipal
from app.models.users import User


@asynccontextmanager
async def _engine_and_maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    # pysqlite's default implicit-transaction handling silently issues a
    # real COMMIT around SAVEPOINT release unless disabled -- without this,
    # a `session.begin_nested()` insert (as `resolve_system_principal` uses)
    # would appear to survive even when the *outer* transaction that
    # contains it is rolled back, which is exactly the scenario
    # TestGetOrCreate.test_rolled_back_bootstrap_leaves_no_row below must
    # exercise correctly. Postgres has no equivalent quirk; this is a
    # SQLite/aiosqlite test-harness-only workaround, not a production
    # behavior difference. See SQLAlchemy's own documented pysqlite
    # serializable/savepoint workaround.
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


class TestGetOrCreate:
    @pytest.mark.asyncio
    async def test_creates_on_first_call(self) -> None:
        async with _engine_and_maker() as maker, maker() as session:
            principal = await resolve_system_principal(session)
            await session.commit()
        assert principal.principal_type == "system"
        assert principal.enabled is True

    @pytest.mark.asyncio
    async def test_returns_same_principal_on_repeated_calls(self) -> None:
        async with _engine_and_maker() as maker:
            async with maker() as session:
                first = await resolve_system_principal(session)
                await session.commit()
            async with maker() as session:
                second = await resolve_system_principal(session)
                await session.commit()
        assert first.id == second.id

    @pytest.mark.asyncio
    async def test_rolled_back_bootstrap_leaves_no_row(self) -> None:
        """resolve_system_principal must never call session.commit() itself
        -- if the caller's own transaction later rolls back, the bootstrap
        row must roll back with it, not persist independently."""
        async with _engine_and_maker() as maker:
            async with maker() as session:
                try:
                    async with session.begin():
                        await resolve_system_principal(session)
                        raise RuntimeError("simulated failure")
                except RuntimeError:
                    pass
            async with maker() as session:
                existing = (
                    await session.exec(
                        select(McPrincipal).where(
                            McPrincipal.external_provider == SYSTEM_PROVIDER,
                            McPrincipal.external_subject == SYSTEM_SUBJECT,
                        )
                    )
                ).first()
        assert existing is None


class TestRaceSafety:
    @pytest.mark.asyncio
    async def test_losing_race_returns_winners_row_and_leaves_outer_transaction_usable(
        self,
    ) -> None:
        """Deterministically seed the state a losing racer would observe
        after a winner already committed the system-principal row in a
        *separate* transaction, then prove the loser's IntegrityError
        fallback returns that row *and* that its own outer transaction
        remains usable afterward -- the direct regression test for catching
        IntegrityError on a SAVEPOINT, not the outer transaction."""
        async with _engine_and_maker() as maker:
            async with maker() as winner_session:
                winner = await resolve_system_principal(winner_session)
                await winner_session.commit()

            async with maker() as session:
                async with session.begin():
                    loser = await resolve_system_principal(session)
                    assert loser.id == winner.id

                    # The outer transaction must still be usable after the
                    # resolved SAVEPOINT conflict: a follow-up write in the
                    # same session must succeed, not raise on a poisoned
                    # transaction.
                    from app.models.mc_approval import McPrincipalRole

                    session.add(
                        McPrincipalRole(principal_id=loser.id, role_slug="technical-director")
                    )
                await session.commit()

            async with maker() as session:
                from app.models.mc_approval import McPrincipalRole

                roles = (
                    await session.exec(
                        select(McPrincipalRole).where(McPrincipalRole.principal_id == winner.id)
                    )
                ).all()
        assert len(roles) == 1


class TestHttpImpersonationImpossible:
    @pytest.mark.asyncio
    async def test_authcontext_can_never_resolve_to_system_principal(self) -> None:
        """resolve_principal (the HTTP/AuthContext path) can never resolve
        to the system principal's id -- not even for an AuthContext whose
        clerk_user_id happens to equal the reserved subject string."""
        async with _engine_and_maker() as maker:
            async with maker() as session:
                system_principal = await resolve_system_principal(session)
                await session.commit()

            # An AuthContext whose user id is literally the reserved
            # subject string -- the worst-case coincidence.
            auth = AuthContext(
                actor_type="user",
                user=User(
                    clerk_user_id=SYSTEM_SUBJECT,
                    email="coincidence@example.com",
                    name="Coincidence",
                ),
            )
            async with maker() as session:
                from app.mission.principal_resolver import PrincipalResolutionError

                with pytest.raises(PrincipalResolutionError) as exc_info:
                    await resolve_principal(auth, session)
                assert exc_info.value.code == "principal_not_registered"

        # resolve_principal never even queries for provider="system" -- it
        # always derives provider from settings.auth_mode ("clerk"/"local"),
        # so it structurally cannot match the system principal's row
        # regardless of what identity string the caller supplies.
        assert system_principal.principal_type == "system"
