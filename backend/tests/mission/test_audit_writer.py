# ruff: noqa: INP001
"""Slice 4 T3 sync audit writer tests (ADR-23: audit failure never fails sync)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import pytest

from app.mission.audit import write_sync_audit
from app.mission.sync import SyncResult
from app.models.mc_sync_audit import McSyncAudit

STARTED = datetime(2026, 7, 22, 10, 0, 0)
FINISHED = datetime(2026, 7, 22, 10, 5, 0)


class RecordingSession:
    """Async session stand-in that records added rows and commit calls."""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1


class FailingCommitSession(RecordingSession):
    """Session whose commit() simulates a DB error."""

    async def commit(self) -> None:
        raise RuntimeError("db connection lost")


@pytest.mark.asyncio
async def test_write_sync_audit_creates_row() -> None:
    session = RecordingSession()
    result = SyncResult(ok=True, partial=False, projected=5, quarantined=1, tombstoned=2)

    await write_sync_audit(
        session,  # type: ignore[arg-type]
        result,
        adapter_key="github",
        started_at=STARTED,
        finished_at=FINISHED,
    )

    assert session.commits == 1
    assert len(session.added) == 1
    row = session.added[0]
    assert isinstance(row, McSyncAudit)
    assert row.adapter_key == "github"
    assert row.started_at == STARTED
    assert row.finished_at == FINISHED
    assert row.is_partial is False
    assert row.projected == 5
    assert row.quarantined == 1
    assert row.tombstoned == 2
    assert row.error_summary is None


@pytest.mark.asyncio
async def test_write_sync_audit_stores_partial_run() -> None:
    session = RecordingSession()
    result = SyncResult(ok=False, partial=True, errors=["project items status=502"])

    await write_sync_audit(
        session,  # type: ignore[arg-type]
        result,
        adapter_key="github",
        started_at=STARTED,
    )

    row = session.added[0]
    assert row.is_partial is True
    assert row.error_summary == "project items status=502"


@pytest.mark.asyncio
async def test_write_sync_audit_error_summary_truncated_and_secret_free() -> None:
    session = RecordingSession()
    secret = "ghp_secret_value"
    errors = [f"issue 1 failed token={secret}"] + ["x" * 100 for _ in range(10)]
    result = SyncResult(ok=False, partial=True, errors=errors)

    await write_sync_audit(
        session,  # type: ignore[arg-type]
        result,
        adapter_key="github",
        started_at=STARTED,
        secrets=[secret],
    )

    summary = session.added[0].error_summary
    assert summary is not None
    assert len(summary) <= 512
    assert secret not in summary


@pytest.mark.asyncio
async def test_write_sync_audit_failure_is_swallowed_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = FailingCommitSession()
    result = SyncResult(ok=True, partial=False)

    with caplog.at_level(logging.ERROR):
        await write_sync_audit(
            session,  # type: ignore[arg-type]
            result,
            adapter_key="github",
            started_at=STARTED,
        )

    assert any(
        record.levelno >= logging.ERROR and "audit_write_failed" in record.getMessage()
        for record in caplog.records
    )
