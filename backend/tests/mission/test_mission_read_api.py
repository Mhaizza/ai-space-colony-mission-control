# ruff: noqa: INP001
"""Slice 3.5 read-only mission API endpoint + mutation-safety tests."""

from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi.routing import APIRoute

from app.api import mission as mission_api
from app.core.auth import AuthContext
from app.core.mutation_guard import MUTATION_ALLOWLIST
from app.mission.types import MANUAL_REFRESH_ALLOWLIST_ENTRY


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


def test_mutation_allowlist_unchanged_by_slice_3_5() -> None:
    assert MUTATION_ALLOWLIST == frozenset({MANUAL_REFRESH_ALLOWLIST_ENTRY})
    assert MANUAL_REFRESH_ALLOWLIST_ENTRY == ("POST", "/api/v1/mission/refresh")
