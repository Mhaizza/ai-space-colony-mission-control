"""Slice 4 retention: purge aged tombstoned projection records.

Deletes only records that are already tombstoned and whose last projection is
older than the configured TTL. Reconciliation owns tombstoning; this only reaps
the tail. Idempotent: a second run over the same cutoff deletes nothing.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import utcnow
from app.models.mc_projection import McProjectionRecord


async def purge_tombstoned(session: AsyncSession, ttl_days: int) -> int:
    """Delete tombstoned records older than ``ttl_days``; return the count deleted.

    A non-positive ``ttl_days`` disables the purge and returns 0 without touching
    the database.
    """
    if ttl_days <= 0:
        return 0

    cutoff = utcnow() - timedelta(days=ttl_days)
    stmt = delete(McProjectionRecord).where(
        col(McProjectionRecord.tombstoned).is_(True),
        col(McProjectionRecord.projected_at) < cutoff,
    )
    result = await session.exec(stmt)
    await session.commit()
    return int(result.rowcount or 0)
