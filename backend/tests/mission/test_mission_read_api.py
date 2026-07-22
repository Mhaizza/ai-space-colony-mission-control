# ruff: noqa: INP001
"""Slice 3.5 read-only mission API endpoint + mutation-safety tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import mission as mission_api
from app.core.auth import AuthContext
from app.core.mutation_guard import MUTATION_ALLOWLIST
from app.core.time import utcnow
from app.mission.types import MANUAL_REFRESH_ALLOWLIST_ENTRY
from app.models.mc_projection import McProjectionRecord


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def __iter__(self) -> Any:
        return iter(self._rows)


class SequentialSession:
    def __init__(self, results: list[list[Any]]) -> None:
        self._results = results
        self.calls = 0

    async def exec(self, _statement: Any) -> FakeResult:
        rows = self._results[self.calls]
        self.calls += 1
        return FakeResult(rows)


def _auth() -> AuthContext:
    return cast(AuthContext, object())


@pytest.mark.asyncio
async def test_mission_overview_composes_all_sections() -> None:
    # Order follows get_overview: sync, projections, quarantine(reasons, recent),
    # workflow(items, issues, pulls, comments).
    session = SequentialSession([[], [], [], [], [], [], [], []])
    overview = await mission_api.mission_overview(
        quarantine_limit=50,
        card_limit=100,
        record_limit=100,
        auth=_auth(),
        session=cast(Any, session),
    )
    assert overview.sync is None
    assert overview.projections.total == 0
    assert overview.quarantine.total == 0
    assert overview.workflow.cards_total == 0
    assert overview.adapter.self_repo  # non-secret adapter summary present


@pytest.mark.asyncio
async def test_mission_quarantine_endpoint_returns_summary() -> None:
    session = SequentialSession([[("malformed_record", 1)], []])
    summary = await mission_api.mission_quarantine(
        limit=25,
        auth=_auth(),
        session=cast(Any, session),
    )
    assert summary.total == 1
    assert summary.by_reason[0].reason_code == "malformed_record"


@pytest.mark.asyncio
async def test_mission_workflow_endpoint_returns_summary() -> None:
    session = SequentialSession([[], [], [], []])
    summary = await mission_api.mission_workflow(
        card_limit=100,
        record_limit=100,
        auth=_auth(),
        session=cast(Any, session),
    )
    assert summary.cards_total == 0
    assert summary.records == []


@asynccontextmanager
async def _in_memory_session(*rows: Any) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.connect() as conn, conn.begin():
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            for row in rows:
                session.add(row)
            if rows:
                await session.commit()
            yield session
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mission_audit_endpoint_empty_safe() -> None:
    async with _in_memory_session() as session:
        summary = await mission_api.mission_audit(auth=_auth(), session=session)
    assert summary.total == 0
    assert summary.recent == []


@pytest.mark.asyncio
async def test_mission_pr_status_endpoint_empty_safe() -> None:
    async with _in_memory_session() as session:
        summary = await mission_api.mission_pr_status(auth=_auth(), session=session)
    assert summary.total == 0
    assert summary.items == []


@pytest.mark.asyncio
async def test_mission_pr_status_endpoint_never_leaks_payload() -> None:
    # Route-layer regression guard for the ADR-23 read-model invariant.
    row = McProjectionRecord(
        source_type="github_check_run",
        source_id="cr1",
        partition_key="p",
        tombstoned=False,
        projected_at=utcnow(),
        payload={"status": "completed", "token": "ghp_leak", "secret": "s3cr3t"},
    )
    async with _in_memory_session(row) as session:
        summary = await mission_api.mission_pr_status(auth=_auth(), session=session)

    assert summary.total == 1
    dumped = str(summary.model_dump())
    assert "ghp_" not in dumped
    assert "s3cr3t" not in dumped


def test_mission_router_read_endpoints_are_get_only() -> None:
    mutating = {"POST", "PUT", "PATCH", "DELETE"}
    for route in mission_api.router.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = (route.methods or set()) & mutating
        if not methods:
            continue
        # The only mutating mission route remains the Slice 3 manual refresh.
        assert methods == {"POST"}
        assert route.path == "/mission/refresh"


def test_mission_slice4_read_routes_registered_as_get() -> None:
    get_paths = {
        route.path: (route.methods or set())
        for route in mission_api.router.routes
        if isinstance(route, APIRoute)
    }
    assert get_paths.get("/mission/audit") == {"GET"}
    assert get_paths.get("/mission/pr-status") == {"GET"}


def test_mutation_allowlist_unchanged_by_slice_3_5() -> None:
    assert MUTATION_ALLOWLIST == frozenset({MANUAL_REFRESH_ALLOWLIST_ENTRY})
    assert MANUAL_REFRESH_ALLOWLIST_ENTRY == ("POST", "/api/v1/mission/refresh")
