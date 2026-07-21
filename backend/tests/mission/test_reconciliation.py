# ruff: noqa: INP001
"""Slice 3 partition reconciliation, tombstoning, and pagination tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.mission.principal_registry import empty_principal_registry
from app.mission.reconciliation import PartitionReconciler, select_tombstones
from app.mission.sync import (
    GitHubSyncService,
    SyncConfig,
    SyncResult,
    _array_extractor,
    _commit_status_partition,
    _issue_comments_partition,
    _wrapped_extractor,
)
from app.mission.types import SourceType


@dataclass
class FakeRow:
    """Stand-in for McProjectionRecord for reconciliation decisions."""

    source_id: str
    tombstoned: bool = False
    partition_key: str = ""
    source_type: str = ""
    projected_at: Any = None


@dataclass
class FakeResp:
    status_code: int
    json_body: Any
    headers: dict[str, str] = field(default_factory=dict)
    text: str = ""


class FakeSession:
    """Minimal async session capturing added rows; exec returns preset rows."""

    def __init__(self, exec_rows: list[Any] | None = None) -> None:
        self.added: list[Any] = []
        self.committed = 0
        self._exec_rows = exec_rows or []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed += 1

    async def flush(self) -> None:
        return None

    async def exec(self, _stmt: Any) -> Any:
        rows = self._exec_rows

        class Result:
            def first(self) -> Any:
                return rows[0] if rows else None

            def all(self) -> list[Any]:
                return list(rows)

            def __iter__(self) -> Any:
                return iter(rows)

        return Result()


class FakeClient:
    """Scripted read client: rest_get/graphql delegate to provided handlers."""

    def __init__(
        self,
        *,
        rest_handler: Any = None,
        graphql_handler: Any = None,
    ) -> None:
        self._rest = rest_handler
        self._graphql = graphql_handler
        self.rest_calls: list[tuple[str, dict[str, Any]]] = []
        self.graphql_calls: list[tuple[str, dict[str, Any]]] = []

    async def rest_get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        self.rest_calls.append((path, dict(params or {})))
        assert self._rest is not None
        return self._rest(path, dict(params or {}))

    async def graphql(self, query: str, variables: dict[str, Any] | None = None) -> Any:
        self.graphql_calls.append((query, dict(variables or {})))
        assert self._graphql is not None
        return self._graphql(query, dict(variables or {}))

    async def aclose(self) -> None:
        return None


def _service(client: Any) -> GitHubSyncService:
    return GitHubSyncService(
        client=client,
        registry=empty_principal_registry(),
        config=SyncConfig(
            project_owner="Mhaizza",
            project_number=4,
            self_owner="Mhaizza",
            self_repo="ai-space-colony-sim",
        ),
        token_for_redaction="secret-token",
    )


# --------------------------------------------------------------------------- #
# Pure reconciler logic
# --------------------------------------------------------------------------- #


def test_complete_partition_selects_absent_records() -> None:
    reconciler = PartitionReconciler()
    reconciler.observe("t", "p", "a")
    reconciler.observe("t", "p", "b")
    partition = reconciler.touch("t", "p")
    rows = [FakeRow("a"), FakeRow("b"), FakeRow("c"), FakeRow("d")]
    targets = select_tombstones(partition, rows)
    assert {r.source_id for r in targets} == {"c", "d"}


def test_partial_partition_is_not_reconcilable() -> None:
    reconciler = PartitionReconciler()
    reconciler.observe("t", "p", "a")
    reconciler.mark_partial("t", "p")
    assert reconciler.reconcilable_partitions() == []


def test_rebuild_empty_complete_partition_tombstones_all() -> None:
    """A completed read that observed nothing tombstones every prior record."""
    reconciler = PartitionReconciler()
    partition = reconciler.touch("t", "p")  # completed, observed nothing
    rows = [FakeRow("a"), FakeRow("b")]
    targets = select_tombstones(partition, rows)
    assert {r.source_id for r in targets} == {"a", "b"}


def test_already_tombstoned_rows_are_skipped_idempotent() -> None:
    reconciler = PartitionReconciler()
    reconciler.observe("t", "p", "a")
    partition = reconciler.touch("t", "p")
    rows = [FakeRow("a"), FakeRow("b", tombstoned=True), FakeRow("c")]
    first = select_tombstones(partition, rows)
    second = select_tombstones(partition, rows)
    assert {r.source_id for r in first} == {"c"}
    assert {r.source_id for r in first} == {r.source_id for r in second}


# --------------------------------------------------------------------------- #
# _reconcile isolation across partitions and source types
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_reconcile_isolated_by_partition_and_source_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(FakeClient())
    reconciler = PartitionReconciler()

    # Complete partition A (issues, repo1): observed a1 only → a2 tombstoned.
    reconciler.observe(SourceType.GITHUB_ISSUE.value, "repoA:issue", "a1")
    # Complete partition B (issues, repo2): observed b1 → b2 tombstoned (isolated).
    reconciler.observe(SourceType.GITHUB_ISSUE.value, "repoB:issue", "b1")
    # Complete partition C (pulls, repoA — same key prefix, different source type).
    reconciler.observe(SourceType.GITHUB_PULL_REQUEST.value, "repoA:pull", "c1")
    # Partial partition D (issue comments): must NOT tombstone.
    reconciler.observe(SourceType.GITHUB_ISSUE_COMMENT.value, "comments#1", "d1")
    reconciler.mark_partial(SourceType.GITHUB_ISSUE_COMMENT.value, "comments#1")

    rows_by_key = {
        (SourceType.GITHUB_ISSUE.value, "repoA:issue"): [FakeRow("a1"), FakeRow("a2")],
        (SourceType.GITHUB_ISSUE.value, "repoB:issue"): [FakeRow("b1"), FakeRow("b2")],
        (SourceType.GITHUB_PULL_REQUEST.value, "repoA:pull"): [FakeRow("c1"), FakeRow("c2")],
        (SourceType.GITHUB_ISSUE_COMMENT.value, "comments#1"): [FakeRow("d1"), FakeRow("d2")],
    }

    async def fake_load(_session: Any, source_type: str, partition_key: str) -> list[Any]:
        return rows_by_key[(source_type, partition_key)]

    monkeypatch.setattr(service, "_load_partition_rows", fake_load)

    result = SyncResult(ok=True, partial=False)
    await service._reconcile(FakeSession(), reconciler, result)  # type: ignore[arg-type]

    def tombstoned(key: tuple[str, str]) -> set[str]:
        return {r.source_id for r in rows_by_key[key] if r.tombstoned}

    assert tombstoned((SourceType.GITHUB_ISSUE.value, "repoA:issue")) == {"a2"}
    assert tombstoned((SourceType.GITHUB_ISSUE.value, "repoB:issue")) == {"b2"}
    assert tombstoned((SourceType.GITHUB_PULL_REQUEST.value, "repoA:pull")) == {"c2"}
    # Partial partition untouched.
    assert tombstoned((SourceType.GITHUB_ISSUE_COMMENT.value, "comments#1")) == set()
    assert result.tombstoned == 3


# --------------------------------------------------------------------------- #
# Revival + idempotency at the upsert layer
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_upsert_revives_previously_tombstoned_record() -> None:
    existing = FakeRow("N1", tombstoned=True)
    session = FakeSession(exec_rows=[existing])
    service = _service(FakeClient())
    reconciler = PartitionReconciler()
    result = SyncResult(ok=True, partial=False)

    await service._upsert_projection(
        session,  # type: ignore[arg-type]
        source_type=SourceType.GITHUB_ISSUE_COMMENT,
        source_id="N1",
        source_url="https://example.com/1",
        source_updated_at=None,
        partition_key="comments#5",
        payload={"body": "hi"},
        result=result,
        reconciler=reconciler,
    )

    assert existing.tombstoned is False  # revived
    partition = reconciler.touch(SourceType.GITHUB_ISSUE_COMMENT.value, "comments#5")
    assert "N1" not in {r.source_id for r in select_tombstones(partition, [existing])}


@pytest.mark.asyncio
async def test_repeated_observation_is_idempotent() -> None:
    reconciler = PartitionReconciler()
    for _ in range(3):
        reconciler.observe("t", "p", "same")
    partition = reconciler.touch("t", "p")
    assert partition.observed_ids == {"same"}
    rows = [FakeRow("same"), FakeRow("other")]
    assert {r.source_id for r in select_tombstones(partition, rows)} == {"other"}


# --------------------------------------------------------------------------- #
# REST pagination
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_paginate_rest_fetches_all_pages() -> None:
    def rest_handler(_path: str, params: dict[str, Any]) -> FakeResp:
        page = params.get("page", 1)
        if page == 1:
            return FakeResp(200, [{"i": n} for n in range(100)])
        if page == 2:
            return FakeResp(200, [{"i": 100}, {"i": 101}])
        return FakeResp(200, [])

    service = _service(FakeClient(rest_handler=rest_handler))
    items, ok = await service._paginate_rest("/repos/o/r/x", params=None, extract=_array_extractor)
    assert ok is True
    assert len(items) == 102


@pytest.mark.asyncio
async def test_paginate_rest_full_first_page_is_not_complete() -> None:
    """100 results on page 1 must trigger a page-2 fetch (not treated as done)."""
    calls: list[int] = []

    def rest_handler(_path: str, params: dict[str, Any]) -> FakeResp:
        page = params.get("page", 1)
        calls.append(page)
        if page == 1:
            return FakeResp(200, [{"i": n} for n in range(100)])
        return FakeResp(200, [])

    service = _service(FakeClient(rest_handler=rest_handler))
    items, ok = await service._paginate_rest("/repos/o/r/x", params=None, extract=_array_extractor)
    assert ok is True
    assert calls == [1, 2]
    assert len(items) == 100


@pytest.mark.asyncio
async def test_paginate_rest_failure_marks_partial() -> None:
    def rest_handler(_path: str, params: dict[str, Any]) -> FakeResp:
        page = params.get("page", 1)
        if page == 1:
            return FakeResp(200, [{"i": n} for n in range(100)])
        return FakeResp(502, None)

    service = _service(FakeClient(rest_handler=rest_handler))
    items, ok = await service._paginate_rest("/repos/o/r/x", params=None, extract=_array_extractor)
    assert ok is False
    assert len(items) == 100  # partial page data retained but caller must not tombstone


@pytest.mark.asyncio
async def test_paginate_rest_malformed_body_marks_partial() -> None:
    def rest_handler(_path: str, _params: dict[str, Any]) -> FakeResp:
        return FakeResp(200, {"not": "a list"})

    service = _service(FakeClient(rest_handler=rest_handler))
    items, ok = await service._paginate_rest("/repos/o/r/x", params=None, extract=_array_extractor)
    assert ok is False
    assert items == []


# --------------------------------------------------------------------------- #
# Issue-comment pagination failure never tombstones
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_issue_comment_pagination_failure_never_tombstones() -> None:
    def rest_handler(_path: str, _params: dict[str, Any]) -> FakeResp:
        return FakeResp(503, None)

    service = _service(FakeClient(rest_handler=rest_handler))
    reconciler = PartitionReconciler()
    result = SyncResult(ok=True, partial=False)
    await service._sync_issue_comments(
        FakeSession(),  # type: ignore[arg-type]
        "Mhaizza",
        "ai-space-colony-sim",
        5,
        "PARENT",
        result,
        reconciler,
    )
    assert result.errors
    partition_key = _issue_comments_partition("Mhaizza", "ai-space-colony-sim", 5)
    reconcilable = {(p.source_type, p.partition_key) for p in reconciler.reconcilable_partitions()}
    assert (SourceType.GITHUB_ISSUE_COMMENT.value, partition_key) not in reconcilable


# --------------------------------------------------------------------------- #
# Check/status/workflow read-failure propagation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_commit_status_failure_propagates_and_never_tombstones() -> None:
    def rest_handler(_path: str, _params: dict[str, Any]) -> FakeResp:
        return FakeResp(500, None)

    service = _service(FakeClient(rest_handler=rest_handler))
    reconciler = PartitionReconciler()
    result = SyncResult(ok=True, partial=False)
    await service._sync_commit_status(
        FakeSession(),  # type: ignore[arg-type]
        "Mhaizza",
        "ai-space-colony-sim",
        "d" * 40,
        result,
        reconciler,
    )
    assert result.errors  # not silently healthy
    partition_key = _commit_status_partition("Mhaizza", "ai-space-colony-sim", "d" * 40)
    reconcilable = {(p.source_type, p.partition_key) for p in reconciler.reconcilable_partitions()}
    assert (SourceType.GITHUB_COMMIT_STATUS.value, partition_key) not in reconcilable


@pytest.mark.asyncio
async def test_commit_status_success_observes_and_completes() -> None:
    def rest_handler(_path: str, params: dict[str, Any]) -> FakeResp:
        page = params.get("page", 1)
        if page == 1:
            return FakeResp(200, {"statuses": [{"node_id": "S1", "context": "ci"}]})
        return FakeResp(200, {"statuses": []})

    service = _service(FakeClient(rest_handler=rest_handler))
    reconciler = PartitionReconciler()
    result = SyncResult(ok=True, partial=False)
    await service._sync_commit_status(
        FakeSession(),  # type: ignore[arg-type]
        "Mhaizza",
        "ai-space-colony-sim",
        "d" * 40,
        result,
        reconciler,
    )
    assert not result.errors
    partition_key = _commit_status_partition("Mhaizza", "ai-space-colony-sim", "d" * 40)
    reconcilable = {(p.source_type, p.partition_key) for p in reconciler.reconcilable_partitions()}
    assert (SourceType.GITHUB_COMMIT_STATUS.value, partition_key) in reconcilable
    partition = reconciler.touch(SourceType.GITHUB_COMMIT_STATUS.value, partition_key)
    assert "S1" in partition.observed_ids


def test_wrapped_extractor_reads_nested_array() -> None:
    extract = _wrapped_extractor("check_runs")
    assert extract({"check_runs": [{"a": 1}]}) == [{"a": 1}]
    assert extract({"check_runs": "bad"}) is None
    assert extract(["not", "wrapped"]) is None
