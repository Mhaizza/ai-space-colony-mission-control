# ruff: noqa: INP001
"""Slice 3 GitHub client, mutation allowlist, and partial-read safety tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.mutation_guard import (
    MUTATION_ALLOWLIST,
    MUTATIONS_DISABLED_CODE,
    MutationHardDisableMiddleware,
)
from app.mission.github_client import GitHubReadClient
from app.mission.types import MANUAL_REFRESH_ALLOWLIST_ENTRY, QuarantineReason, SourceType


@pytest.mark.asyncio
async def test_github_client_forbids_graphql_mutation() -> None:
    client = GitHubReadClient(token="test-token", transport=AsyncMock())
    with pytest.raises(ValueError, match="mutations are forbidden"):
        await client.graphql("mutation { updateIssue(input: {}) { issue { id } } }")
    await client.aclose()


@pytest.mark.asyncio
async def test_github_client_forbids_non_allowlisted_rest_path() -> None:
    transport = AsyncMock()
    client = GitHubReadClient(token="test-token", transport=transport)
    with pytest.raises(ValueError, match="allowlist"):
        await client.rest_get("/orgs/foo/repos")
    await client.aclose()


def test_mutation_allowlist_contains_exactly_manual_refresh() -> None:
    assert MUTATION_ALLOWLIST == frozenset({MANUAL_REFRESH_ALLOWLIST_ENTRY})
    assert MANUAL_REFRESH_ALLOWLIST_ENTRY == ("POST", "/api/v1/mission/refresh")


def test_middleware_allows_only_manual_refresh() -> None:
    probe = FastAPI()

    @probe.post("/api/v1/mission/refresh")
    def refresh() -> dict[str, bool]:
        return {"ok": True}

    @probe.post("/api/v1/boards")
    def boards() -> dict[str, bool]:
        return {"ok": True}

    probe.add_middleware(MutationHardDisableMiddleware, enabled=True)
    client = TestClient(probe)

    allowed = client.post("/api/v1/mission/refresh", json={})
    assert allowed.status_code == 200
    assert allowed.json()["ok"] is True

    blocked = client.post("/api/v1/boards", json={})
    assert blocked.status_code == 405
    assert blocked.json()["code"] == MUTATIONS_DISABLED_CODE


def test_quarantine_reason_codes_are_closed() -> None:
    values = {reason.value for reason in QuarantineReason}
    required = {
        "malformed_record",
        "unauthorized_author",
        "edited_comment",
        "stale_head",
        "cyclic_supersession",
        "duplicate_supersession",
        "cross_card_supersession",
        "conflicting_assignment",
        "partial_read",
    }
    assert required <= values


def test_source_types_closed_github_subset() -> None:
    assert SourceType.GITHUB_PROJECT_ITEM.value == "github_project_item"
    assert SourceType.GITHUB_ISSUE_COMMENT.value == "github_issue_comment"
    # Local sources deferred (Slice 4); not present in Slice 3 GitHub adapter enum.
    assert not hasattr(SourceType, "LOCAL_WORKTREE")


@pytest.mark.asyncio
async def test_partial_read_does_not_infer_deletion(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed child read records an error and does not tombstone prior rows."""
    from app.mission.principal_registry import empty_principal_registry
    from app.mission.sync import GitHubSyncService, SyncConfig, SyncResult

    class FakeSession:
        def __init__(self) -> None:
            self.added: list[Any] = []
            self.committed = 0

        def add(self, obj: Any) -> None:
            self.added.append(obj)

        async def commit(self) -> None:
            self.committed += 1

        async def flush(self) -> None:
            return None

        async def exec(self, _stmt: Any) -> Any:
            class Result:
                def first(self) -> None:
                    return None

                def all(self) -> list[Any]:
                    return []

            return Result()

    class FakeClient:
        async def graphql(self, *_args: Any, **_kwargs: Any) -> Any:
            class Resp:
                status_code = 500
                json_body: dict[str, Any] = {}
                headers: dict[str, str] = {}
                text = "error"

            return Resp()

        async def rest_get(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("REST should not run when project sync fails early")

    service = GitHubSyncService(
        client=FakeClient(),  # type: ignore[arg-type]
        registry=empty_principal_registry(),
        config=SyncConfig(
            project_owner="Mhaizza",
            project_number=4,
            self_owner="Mhaizza",
            self_repo="ai-space-colony-sim",
        ),
        token_for_redaction="secret-token",
    )
    session = FakeSession()
    # Patch _get_or_create_state to avoid DB
    state_box: dict[str, Any] = {}

    async def fake_state(_session: Any) -> Any:
        class State:
            status = "idle"
            last_started_at = None
            last_finished_at = None
            last_success_at = None
            last_error = None
            consecutive_failures = 0

        state = State()
        state_box["state"] = state
        return state

    monkeypatch.setattr(service, "_get_or_create_state", fake_state)
    result = await service.run(session)  # type: ignore[arg-type]
    assert isinstance(result, SyncResult)
    assert result.partial is True
    assert result.ok is False
    assert result.errors
    # No tombstones inferred — FakeSession never received tombstone updates.
    assert all(getattr(obj, "tombstoned", False) is False for obj in session.added)
