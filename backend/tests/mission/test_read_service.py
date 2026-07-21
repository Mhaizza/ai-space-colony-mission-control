# ruff: noqa: INP001
"""Slice 3.5 read-only projection query service tests."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.mission import read_service
from app.models.mc_projection import McProjectionRecord, McQuarantine, McSyncState


class FakeResult:
    """Result wrapper supporting the read-service access patterns."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def __iter__(self) -> Any:
        return iter(self._rows)


class SequentialSession:
    """Async session returning queued result sets in call order."""

    def __init__(self, results: list[list[Any]]) -> None:
        self._results = results
        self.calls = 0

    async def exec(self, _statement: Any) -> FakeResult:
        rows = self._results[self.calls]
        self.calls += 1
        return FakeResult(rows)


VALID_START_TASK = (
    "<!-- ai-workflow-record:v1 "
    '{"type":"start_task","card":148,"worker":"cursor","role":"technical-director",'
    '"artifact":null,"head":null,"result":null,"supersedes":null} -->'
)


@pytest.mark.asyncio
async def test_get_sync_status_returns_none_when_absent() -> None:
    session = SequentialSession([[]])
    assert await read_service.get_sync_status(session) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_sync_status_maps_row() -> None:
    started = datetime(2026, 7, 20, 1, 0, 0)
    state = McSyncState(
        adapter_key="github",
        status="degraded",
        last_started_at=started,
        last_error="boom",
        consecutive_failures=2,
    )
    session = SequentialSession([[state]])
    result = await read_service.get_sync_status(session)  # type: ignore[arg-type]
    assert result is not None
    assert result.status == "degraded"
    assert result.last_started_at == started
    assert result.last_error == "boom"
    assert result.consecutive_failures == 2


@pytest.mark.asyncio
async def test_get_projection_summary_aggregates_live_and_tombstoned() -> None:
    rows = [
        ("github_issue", False, 3),
        ("github_issue", True, 1),
        ("github_project_item", False, 2),
    ]
    session = SequentialSession([rows])
    summary = await read_service.get_projection_summary(session)  # type: ignore[arg-type]
    assert summary.total == 6
    assert summary.live == 5
    assert summary.tombstoned == 1
    by_type = {item.source_type: item for item in summary.by_source_type}
    assert by_type["github_issue"].live == 3
    assert by_type["github_issue"].tombstoned == 1
    assert by_type["github_project_item"].live == 2
    # Sorted by source_type for deterministic output.
    assert [item.source_type for item in summary.by_source_type] == [
        "github_issue",
        "github_project_item",
    ]


@pytest.mark.asyncio
async def test_get_quarantine_summary_counts_and_recent() -> None:
    reason_rows = [("partial_read", 1), ("malformed_record", 2)]
    entry = McQuarantine(
        reason_code="malformed_record",
        source_type="github_issue_comment",
        source_id="C1",
        message="bad record",
    )
    session = SequentialSession([reason_rows, [entry]])
    summary = await read_service.get_quarantine_summary(session, limit=10)  # type: ignore[arg-type]
    assert summary.total == 3
    # Reasons sorted deterministically by code.
    assert [item.reason_code for item in summary.by_reason] == [
        "malformed_record",
        "partial_read",
    ]
    assert len(summary.recent) == 1
    assert summary.recent[0].source_id == "C1"


@pytest.mark.asyncio
async def test_get_workflow_summary_builds_cards_and_records() -> None:
    project_items = [
        McProjectionRecord(
            source_type="github_project_item",
            source_id="PI1",
            payload={
                "content": {
                    "__typename": "Issue",
                    "number": 148,
                    "title": "Slice 3",
                    "url": "https://example.com/148",
                    "updatedAt": "2026-07-20T00:00:00Z",
                }
            },
        ),
        McProjectionRecord(
            source_type="github_project_item",
            source_id="PI2",
            payload={
                "content": {
                    "__typename": "PullRequest",
                    "number": 3,
                    "title": "adapter",
                    "url": "https://example.com/pr/3",
                }
            },
        ),
        McProjectionRecord(
            source_type="github_project_item",
            source_id="PI3",
            payload={"content": {"__typename": "DraftIssue", "title": "draft"}},
        ),
    ]
    issues = [
        McProjectionRecord(
            source_type="github_issue",
            source_id="I1",
            payload={"number": 148, "state": "open"},
        )
    ]
    pulls = [
        McProjectionRecord(
            source_type="github_pull_request",
            source_id="P1",
            payload={"number": 3, "state": "closed"},
        )
    ]
    comments = [
        McProjectionRecord(
            source_type="github_issue_comment",
            source_id="C1",
            source_url="https://example.com/c1",
            source_updated_at=datetime(2026, 7, 20, 2, 0, 0),
            payload={"body": VALID_START_TASK, "user": {"login": "Mhaizza"}},
        ),
        McProjectionRecord(
            source_type="github_issue_comment",
            source_id="C2",
            payload={"body": "just a normal comment", "user": {"login": "someone"}},
        ),
    ]
    session = SequentialSession([project_items, issues, pulls, comments])
    summary = await read_service.get_workflow_summary(session)  # type: ignore[arg-type]

    # DraftIssue is not a card; only the Issue + PullRequest count.
    assert summary.cards_total == 2
    cards = {card.number: card for card in summary.cards}
    assert cards[148].kind == "issue"
    assert cards[148].state == "open"
    assert cards[148].title == "Slice 3"
    assert cards[3].kind == "pull_request"
    assert cards[3].state == "closed"
    # Cards sorted by number descending.
    assert [card.number for card in summary.cards] == [148, 3]

    # Only the marker-bearing comment is a workflow record.
    assert summary.records_total == 1
    record = summary.records[0]
    assert record.comment_source_id == "C1"
    assert record.parsed_ok is True
    assert record.record_type == "start_task"
    assert record.card == 148
    assert record.worker == "cursor"
    assert record.role == "technical-director"
    assert record.author == "Mhaizza"


@pytest.mark.asyncio
async def test_get_workflow_summary_keeps_unparsable_marker_records() -> None:
    comments = [
        McProjectionRecord(
            source_type="github_issue_comment",
            source_id="C9",
            payload={"body": "<!-- ai-workflow-record:v1 {bad json} -->"},
        )
    ]
    session = SequentialSession([[], [], [], comments])
    summary = await read_service.get_workflow_summary(session)  # type: ignore[arg-type]
    assert summary.records_total == 1
    assert summary.records[0].parsed_ok is False
    assert summary.records[0].card is None


def test_parse_iso_handles_bad_input() -> None:
    assert read_service._parse_iso(None) is None
    assert read_service._parse_iso("not-a-date") is None
    parsed = read_service._parse_iso("2026-07-20T00:00:00Z")
    assert parsed == datetime(2026, 7, 20, 0, 0, 0)
