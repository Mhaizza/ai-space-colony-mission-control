# ruff: noqa: INP001
"""Slice 5A Checkpoint C: resolve_principal() tests.

Uses the same in-memory SQLite pattern as
tests/mission/test_mc_approval_models.py. resolve_principal is a pure read:
these tests only ever assert what it returns/raises, never that it commits
anything (it must not).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import AuthContext
from app.core.auth_mode import AuthMode
from app.core.config import settings
from app.mission.principal_resolver import PrincipalResolutionError, resolve_principal
from app.models.mc_approval import McPrincipal, McPrincipalRole
from app.models.users import User


@asynccontextmanager
async def _in_memory_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.connect() as conn, conn.begin():
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            yield session
    finally:
        await engine.dispose()


def _user(clerk_user_id: str) -> User:
    return User(
        clerk_user_id=clerk_user_id,
        email=f"{clerk_user_id}@example.com",
        name="Test User",
    )


def _auth(user: User | None) -> AuthContext:
    return AuthContext(actor_type="user", user=user)


class TestExternalIdentityDerivation:
    @pytest.mark.asyncio
    async def test_clerk_mode_uses_clerk_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "auth_mode", AuthMode.CLERK)
        async with _in_memory_session() as session:
            principal = McPrincipal(
                principal_type="human",
                display_name="Ada",
                trust_level="standard",
                enabled=True,
                external_provider="clerk",
                external_subject="user_abc",
            )
            session.add(principal)
            await session.commit()

            resolved = await resolve_principal(_auth(_user("user_abc")), session)
            assert resolved.id == principal.id
            assert resolved.principal_type == "human"

    @pytest.mark.asyncio
    async def test_local_mode_uses_local_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "auth_mode", AuthMode.LOCAL)
        async with _in_memory_session() as session:
            principal = McPrincipal(
                principal_type="human",
                display_name="Local Admin",
                trust_level="standard",
                enabled=True,
                external_provider="local",
                external_subject="local-auth-user",
            )
            session.add(principal)
            await session.commit()

            resolved = await resolve_principal(_auth(_user("local-auth-user")), session)
            assert resolved.id == principal.id

    @pytest.mark.asyncio
    async def test_no_authenticated_user_raises_not_registered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "auth_mode", AuthMode.LOCAL)
        async with _in_memory_session() as session:
            with pytest.raises(PrincipalResolutionError) as exc_info:
                await resolve_principal(_auth(None), session)
            assert exc_info.value.code == "principal_not_registered"


class TestResolution:
    @pytest.mark.asyncio
    async def test_unregistered_identity_raises_not_registered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "auth_mode", AuthMode.LOCAL)
        async with _in_memory_session() as session:
            with pytest.raises(PrincipalResolutionError) as exc_info:
                await resolve_principal(_auth(_user("nobody")), session)
            assert exc_info.value.code == "principal_not_registered"

    @pytest.mark.asyncio
    async def test_disabled_principal_raises_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "auth_mode", AuthMode.LOCAL)
        async with _in_memory_session() as session:
            principal = McPrincipal(
                principal_type="human",
                display_name="Disabled",
                trust_level="standard",
                enabled=False,
                external_provider="local",
                external_subject="disabled-user",
            )
            session.add(principal)
            await session.commit()

            with pytest.raises(PrincipalResolutionError) as exc_info:
                await resolve_principal(_auth(_user("disabled-user")), session)
            assert exc_info.value.code == "principal_disabled"

    @pytest.mark.asyncio
    async def test_role_slugs_loaded_as_frozenset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "auth_mode", AuthMode.LOCAL)
        async with _in_memory_session() as session:
            principal = McPrincipal(
                principal_type="human",
                display_name="Multi Role",
                trust_level="standard",
                enabled=True,
                external_provider="local",
                external_subject="multi-role-user",
            )
            session.add(principal)
            await session.commit()
            await session.refresh(principal)
            session.add(McPrincipalRole(principal_id=principal.id, role_slug="qa-reviewer"))
            session.add(McPrincipalRole(principal_id=principal.id, role_slug="technical-director"))
            await session.commit()

            resolved = await resolve_principal(_auth(_user("multi-role-user")), session)
            assert resolved.role_slugs == frozenset({"qa-reviewer", "technical-director"})

    @pytest.mark.asyncio
    async def test_no_roles_yields_empty_frozenset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "auth_mode", AuthMode.LOCAL)
        async with _in_memory_session() as session:
            principal = McPrincipal(
                principal_type="human",
                display_name="No Roles",
                trust_level="standard",
                enabled=True,
                external_provider="local",
                external_subject="no-role-user",
            )
            session.add(principal)
            await session.commit()

            resolved = await resolve_principal(_auth(_user("no-role-user")), session)
            assert resolved.role_slugs == frozenset()

    @pytest.mark.asyncio
    async def test_resolution_never_commits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # resolve_principal must be a pure read: it must not call session.commit()
        # or session.begin() itself. Proven by wrapping the session's commit
        # method and asserting it is never invoked during resolution.
        monkeypatch.setattr(settings, "auth_mode", AuthMode.LOCAL)
        async with _in_memory_session() as session:
            principal = McPrincipal(
                principal_type="human",
                display_name="Read Only",
                trust_level="standard",
                enabled=True,
                external_provider="local",
                external_subject="read-only-user",
            )
            session.add(principal)
            await session.commit()

            commit_calls = 0
            original_commit = session.commit

            async def _tracking_commit() -> None:
                nonlocal commit_calls
                commit_calls += 1
                await original_commit()

            monkeypatch.setattr(session, "commit", _tracking_commit)

            await resolve_principal(_auth(_user("read-only-user")), session)
            assert commit_calls == 0

    @pytest.mark.asyncio
    async def test_system_and_ai_principal_types_resolve_like_any_other(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # resolve_principal itself is type-agnostic -- it is a pure identity
        # lookup and does not enforce the human-only manual-path rule; that
        # structural rejection is approval_service._require_human_manual_actor's
        # job (tested in test_approval_service.py), applied AFTER resolution.
        monkeypatch.setattr(settings, "auth_mode", AuthMode.LOCAL)
        async with _in_memory_session() as session:
            principal = McPrincipal(
                principal_type="system",
                display_name="System Actor",
                trust_level="trusted",
                enabled=True,
                external_provider="local",
                external_subject="system-actor",
            )
            session.add(principal)
            await session.commit()

            resolved = await resolve_principal(_auth(_user("system-actor")), session)
            assert resolved.principal_type == "system"
