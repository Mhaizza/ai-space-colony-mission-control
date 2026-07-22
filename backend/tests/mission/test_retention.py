# ruff: noqa: INP001
"""Slice 4 T5 tombstone retention purge tests."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import utcnow
from app.mission.retention import purge_tombstoned
from app.models.mc_projection import McProjectionRecord

NOW = utcnow()


def _record(source_id: str, *, tombstoned: bool, age_days: int) -> McProjectionRecord:
    return McProjectionRecord(
        source_type="github_issue",
        source_id=source_id,
        partition_key="repo:x/y:issue",
        tombstoned=tombstoned,
        projected_at=NOW - timedelta(days=age_days),
    )


async def _seeded_session(*records: McProjectionRecord) -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.connect() as conn, conn.begin():
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = maker()
    for record in records:
        session.add(record)
    await session.commit()
    return session


async def _live_source_ids(s: AsyncSession) -> set[str]:
    rows = await s.exec(select(McProjectionRecord))
    return {r.source_id for r in rows}


@pytest.mark.asyncio
async def test_purge_deletes_old_tombstones() -> None:
    session = await _seeded_session(_record("old", tombstoned=True, age_days=40))

    deleted = await purge_tombstoned(session, 30)

    assert deleted == 1
    assert await _live_source_ids(session) == set()


@pytest.mark.asyncio
async def test_purge_keeps_live_rows() -> None:
    # Live row is untouched even when much older than the TTL.
    session = await _seeded_session(_record("live", tombstoned=False, age_days=99))

    deleted = await purge_tombstoned(session, 30)

    assert deleted == 0
    assert await _live_source_ids(session) == {"live"}


@pytest.mark.asyncio
async def test_purge_keeps_recent_tombstones() -> None:
    session = await _seeded_session(_record("recent", tombstoned=True, age_days=1))

    deleted = await purge_tombstoned(session, 30)

    assert deleted == 0
    assert await _live_source_ids(session) == {"recent"}


@pytest.mark.asyncio
async def test_purge_disabled_when_ttl_not_positive() -> None:
    session = await _seeded_session(_record("old", tombstoned=True, age_days=40))

    assert await purge_tombstoned(session, 0) == 0
    assert await purge_tombstoned(session, -5) == 0
    assert await _live_source_ids(session) == {"old"}


@pytest.mark.asyncio
async def test_purge_is_idempotent() -> None:
    session = await _seeded_session(
        _record("old", tombstoned=True, age_days=40),
        _record("live", tombstoned=False, age_days=99),
    )

    first = await purge_tombstoned(session, 30)
    second = await purge_tombstoned(session, 30)

    assert first == 1
    assert second == 0
    assert await _live_source_ids(session) == {"live"}
