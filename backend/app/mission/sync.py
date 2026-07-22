"""Read-only GitHub sync orchestration with partial-read safety.

Reconciliation is partition-scoped: each ``(source_type, partition_key)`` pair is
a completeness partition. A partition is reconciled (absent records tombstoned)
only after every page and every required read for it completed successfully.
Partial, malformed, interrupted, rate-limited, or failed reads never tombstone.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import utcnow
from app.mission.assignment import derive_effective_assignments
from app.mission.authorization import authorize_record_author
from app.mission.github_client import PROJECT_ITEMS_QUERY, GitHubReadClient
from app.mission.principal_registry import PrincipalRegistry
from app.mission.reconciliation import PartitionReconciler, select_tombstones
from app.mission.redaction import scrub_mapping
from app.mission.source_identity import commit_status_identity, workflow_run_identity
from app.mission.types import QuarantineReason, SourceType
from app.mission.validation import (
    CandidateRecord,
    CommentSourceMeta,
    check_card_binding,
    check_edited_comment,
    check_exact_head,
    validate_supersession_graph,
)
from app.mission.workflow_record import parse_workflow_record_from_comment
from app.models.mc_projection import McProjectionRecord, McQuarantine, McSyncState

# REST collection pagination limits. Hitting the page cap is treated as a
# partial read (fail-safe: never tombstone from a truncated enumeration).
_PER_PAGE: Final[int] = 100
_MAX_PAGES: Final[int] = 100

# GraphQL pagination cap for the Projects v2 items connection. Exceeding it is a
# partial read: a runaway/looping cursor must never let us tombstone a partition.
_GRAPHQL_MAX_PAGES: Final[int] = 100


@dataclass
class SyncConfig:
    project_owner: str
    project_number: int
    self_owner: str
    self_repo: str
    mission_control_owner: str | None = None
    mission_control_repo: str | None = None
    mission_control_enabled: bool = False


@dataclass
class SyncResult:
    ok: bool
    partial: bool
    projected: int = 0
    quarantined: int = 0
    tombstoned: int = 0
    errors: list[str] = field(default_factory=list)
    effective_assignments: dict[int, dict[str, str]] = field(default_factory=dict)


def _project_partition(owner: str, number: int) -> str:
    return f"project:{owner}:{number}"


def _issue_partition(owner: str, repo: str) -> str:
    return f"repo:{owner}/{repo}:issue"


def _pull_partition(owner: str, repo: str) -> str:
    return f"repo:{owner}/{repo}:pull"


def _issue_comments_partition(owner: str, repo: str, number: int) -> str:
    return f"issue_comments:{owner}/{repo}#{number}"


def _pr_reviews_partition(owner: str, repo: str, number: int) -> str:
    return f"pr_reviews:{owner}/{repo}#{number}"


def _pr_review_comments_partition(owner: str, repo: str, number: int) -> str:
    return f"pr_review_comments:{owner}/{repo}#{number}"


def _commit_status_partition(owner: str, repo: str, head_sha: str) -> str:
    return f"commit_status:{owner}/{repo}@{head_sha}"


def _check_runs_partition(owner: str, repo: str, head_sha: str) -> str:
    return f"check_runs:{owner}/{repo}@{head_sha}"


def _check_suites_partition(owner: str, repo: str, head_sha: str) -> str:
    return f"check_suites:{owner}/{repo}@{head_sha}"


def _workflow_runs_partition(owner: str, repo: str, head_sha: str) -> str:
    return f"workflow_runs:{owner}/{repo}@{head_sha}"


def _array_extractor(body: Any) -> list[Any] | None:
    """Extract a top-level JSON array page; None marks a malformed response."""
    return body if isinstance(body, list) else None


def _wrapped_extractor(key: str) -> Callable[[Any], list[Any] | None]:
    """Build an extractor for ``{key: [...]}`` object-wrapped collection pages."""

    def _extract(body: Any) -> list[Any] | None:
        if isinstance(body, dict):
            value = body.get(key)
            if isinstance(value, list):
                return value
        return None

    return _extract


class GitHubSyncService:
    """Idempotent read-only sync. Partial failures never tombstone."""

    def __init__(
        self,
        *,
        client: GitHubReadClient,
        registry: PrincipalRegistry,
        config: SyncConfig,
        token_for_redaction: str,
    ) -> None:
        self._client = client
        self._registry = registry
        self._config = config
        self._secrets = [token_for_redaction]

    async def run(self, session: AsyncSession) -> SyncResult:
        from app.mission.audit import write_sync_audit  # local: avoid import cycle

        started = utcnow()
        state = await self._get_or_create_state(session)
        state.status = "running"
        state.last_started_at = started
        state.last_error = None
        await session.commit()

        result = SyncResult(ok=True, partial=False)
        reconciler = PartitionReconciler()
        try:
            await self._sync_project_items(session, result, reconciler)
            await self._sync_linked_issues_and_prs(session, result, reconciler)
            # Reconcile before deriving so tombstoned records drop out of state.
            await self._reconcile(session, reconciler, result)
            await self._derive_workflow_state(session, result)
            if result.errors:
                result.partial = True
                result.ok = False
                state.consecutive_failures += 1
                state.status = "degraded"
                state.last_error = "; ".join(result.errors)[:2048]
            else:
                state.consecutive_failures = 0
                state.status = "healthy"
                state.last_success_at = utcnow()
            state.last_finished_at = utcnow()
            await session.commit()
            await write_sync_audit(
                session,
                result,
                adapter_key="github",
                started_at=started,
                finished_at=state.last_finished_at,
                secrets=self._secrets,
            )
            return result
        except Exception as exc:  # noqa: BLE001 — fail closed into sync state
            result.ok = False
            result.partial = True
            result.errors.append(str(exc))
            state.status = "error"
            state.consecutive_failures += 1
            state.last_error = str(exc)[:2048]
            state.last_finished_at = utcnow()
            await session.commit()
            await write_sync_audit(
                session,
                result,
                adapter_key="github",
                started_at=started,
                finished_at=state.last_finished_at,
                secrets=self._secrets,
            )
            return result

    async def _get_or_create_state(self, session: AsyncSession) -> McSyncState:
        stmt = select(McSyncState).where(col(McSyncState.adapter_key) == "github")
        existing = (await session.exec(stmt)).first()
        if existing is not None:
            return existing
        state = McSyncState(adapter_key="github", status="idle", meta={})
        session.add(state)
        await session.flush()
        return state

    async def _paginate_rest(
        self,
        path: str,
        *,
        params: dict[str, str | int] | None,
        extract: Callable[[Any], list[Any] | None],
    ) -> tuple[list[Any], bool]:
        """Fetch every page of a REST collection.

        Returns ``(items, ok)``. ``ok`` is False on any non-success status, any
        malformed page, or exceeding the page cap — signalling a partial read so
        the caller marks the partition incomplete and never tombstones.
        """
        items: list[Any] = []
        base = dict(params or {})
        page = 1
        while True:
            response = await self._client.rest_get(
                path,
                params={**base, "per_page": _PER_PAGE, "page": page},
            )
            if response.status_code >= 400:
                return items, False
            chunk = extract(response.json_body)
            if chunk is None:
                return items, False
            items.extend(chunk)
            if len(chunk) < _PER_PAGE:
                return items, True
            page += 1
            if page > _MAX_PAGES:
                return items, False

    async def _load_partition_rows(
        self,
        session: AsyncSession,
        source_type: str,
        partition_key: str,
    ) -> list[McProjectionRecord]:
        stmt = select(McProjectionRecord).where(
            col(McProjectionRecord.source_type) == source_type,
            col(McProjectionRecord.partition_key) == partition_key,
            col(McProjectionRecord.tombstoned).is_(False),
        )
        return list(await session.exec(stmt))

    async def _reconcile(
        self,
        session: AsyncSession,
        reconciler: PartitionReconciler,
        result: SyncResult,
    ) -> None:
        """Tombstone unobserved records in fully-completed partitions only."""
        now = utcnow()
        for partition in reconciler.reconcilable_partitions():
            rows = await self._load_partition_rows(
                session, partition.source_type, partition.partition_key
            )
            for row in select_tombstones(partition, rows):
                row.tombstoned = True
                row.projected_at = now
                result.tombstoned += 1

    async def _upsert_projection(
        self,
        session: AsyncSession,
        *,
        source_type: SourceType,
        source_id: str,
        source_url: str | None,
        source_updated_at: datetime | None,
        partition_key: str,
        payload: dict[str, Any],
        result: SyncResult,
        reconciler: PartitionReconciler,
    ) -> None:
        safe_payload = scrub_mapping(payload, secrets=self._secrets)
        stmt = select(McProjectionRecord).where(
            col(McProjectionRecord.source_type) == source_type.value,
            col(McProjectionRecord.source_id) == source_id,
        )
        row = (await session.exec(stmt)).first()
        now = utcnow()
        if row is None:
            row = McProjectionRecord(
                source_type=source_type.value,
                source_id=source_id,
                source_url=source_url,
                source_updated_at=source_updated_at,
                projected_at=now,
                last_observed_at=now,
                partition_key=partition_key,
                tombstoned=False,
                payload=safe_payload if isinstance(safe_payload, dict) else {},
            )
            session.add(row)
        else:
            row.source_url = source_url
            row.source_updated_at = source_updated_at
            row.projected_at = now
            row.last_observed_at = now
            row.partition_key = partition_key
            # Revive a previously tombstoned record now that it is observed again.
            row.tombstoned = False
            row.payload = safe_payload if isinstance(safe_payload, dict) else {}
        reconciler.observe(source_type.value, partition_key, source_id)
        result.projected += 1

    async def _quarantine(
        self,
        session: AsyncSession,
        *,
        reason: QuarantineReason,
        message: str,
        source_type: str | None = None,
        source_id: str | None = None,
        source_url: str | None = None,
        source_updated_at: datetime | None = None,
        diagnostic: dict[str, Any] | None = None,
        result: SyncResult,
    ) -> None:
        safe = scrub_mapping(diagnostic or {}, secrets=self._secrets)
        session.add(
            McQuarantine(
                reason_code=reason.value,
                source_type=source_type,
                source_id=source_id,
                source_url=source_url,
                source_updated_at=source_updated_at,
                projected_at=utcnow(),
                message=message[:2048],
                diagnostic=safe if isinstance(safe, dict) else {},
            )
        )
        result.quarantined += 1

    async def _sync_project_items(
        self,
        session: AsyncSession,
        result: SyncResult,
        reconciler: PartitionReconciler,
    ) -> None:
        after: str | None = None
        partition = _project_partition(self._config.project_owner, self._config.project_number)
        source_type = SourceType.GITHUB_PROJECT_ITEM
        reconciler.touch(source_type.value, partition)
        seen_cursors: set[str] = set()
        page_count = 0
        malformed = False
        try:
            while True:
                page_count += 1
                if page_count > _GRAPHQL_MAX_PAGES:
                    result.errors.append("project items exceeded GraphQL page cap")
                    reconciler.mark_partial(source_type.value, partition)
                    return
                response = await self._client.graphql(
                    PROJECT_ITEMS_QUERY,
                    {
                        "login": self._config.project_owner,
                        "number": self._config.project_number,
                        "after": after,
                    },
                )
                if response.status_code >= 400:
                    result.errors.append(f"project items status={response.status_code}")
                    reconciler.mark_partial(source_type.value, partition)
                    return
                body = response.json_body
                if not isinstance(body, dict) or body.get("errors"):
                    result.errors.append(f"project items errors={body!r}")
                    reconciler.mark_partial(source_type.value, partition)
                    return
                data = body.get("data") or {}
                user = data.get("user") if isinstance(data, dict) else None
                project = user.get("projectV2") if isinstance(user, dict) else None
                items = (project or {}).get("items") if isinstance(project, dict) else None
                if not isinstance(items, dict):
                    result.errors.append("project items missing")
                    reconciler.mark_partial(source_type.value, partition)
                    return
                for node in items.get("nodes") or []:
                    if not isinstance(node, dict) or not node.get("id"):
                        # A malformed element makes the whole enumeration untrustworthy;
                        # keep valid siblings but never tombstone from a partial view.
                        malformed = True
                        continue
                    await self._upsert_projection(
                        session,
                        source_type=source_type,
                        source_id=str(node["id"]),
                        source_url=None,
                        source_updated_at=_parse_gh_time(node.get("updatedAt")),
                        partition_key=partition,
                        payload=node,
                        result=result,
                        reconciler=reconciler,
                    )
                page = items.get("pageInfo") or {}
                if not page.get("hasNextPage"):
                    break
                cursor = page.get("endCursor")
                if not isinstance(cursor, str) or not cursor:
                    result.errors.append("project items hasNextPage without a valid endCursor")
                    reconciler.mark_partial(source_type.value, partition)
                    return
                if cursor in seen_cursors:
                    result.errors.append("project items returned a repeated endCursor")
                    reconciler.mark_partial(source_type.value, partition)
                    return
                seen_cursors.add(cursor)
                after = cursor
            if malformed:
                result.errors.append("project items contained malformed element(s)")
                reconciler.mark_partial(source_type.value, partition)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"project sync failed: {exc}")
            reconciler.mark_partial(source_type.value, partition)

    async def _sync_linked_issues_and_prs(
        self,
        session: AsyncSession,
        result: SyncResult,
        reconciler: PartitionReconciler,
    ) -> None:
        """Sync issues/PRs/comments discovered via projected project items."""
        owner, repo = self._config.self_owner, self._config.self_repo
        issue_partition = _issue_partition(owner, repo)
        pull_partition = _pull_partition(owner, repo)
        reconciler.touch(SourceType.GITHUB_ISSUE.value, issue_partition)
        reconciler.touch(SourceType.GITHUB_PULL_REQUEST.value, pull_partition)

        # The set of linked issues/PRs is only complete if the project read was
        # complete; otherwise the entity partitions must not be reconciled.
        project_state = reconciler.touch(
            SourceType.GITHUB_PROJECT_ITEM.value,
            _project_partition(self._config.project_owner, self._config.project_number),
        )
        if not project_state.complete:
            reconciler.mark_partial(SourceType.GITHUB_ISSUE.value, issue_partition)
            reconciler.mark_partial(SourceType.GITHUB_PULL_REQUEST.value, pull_partition)

        stmt = select(McProjectionRecord).where(
            col(McProjectionRecord.source_type) == SourceType.GITHUB_PROJECT_ITEM.value,
            col(McProjectionRecord.tombstoned).is_(False),
        )
        items = list(await session.exec(stmt))
        for item in items:
            content = (item.payload or {}).get("content")
            if not isinstance(content, dict):
                continue
            typename = content.get("__typename")
            number = content.get("number")
            node_id = content.get("id")
            if not isinstance(number, int) or not isinstance(node_id, str):
                continue
            if typename == "Issue":
                await self._sync_issue(session, owner, repo, number, node_id, result, reconciler)
            elif typename == "PullRequest":
                await self._sync_pull(
                    session, owner, repo, number, node_id, content, result, reconciler
                )

    async def _sync_issue(
        self,
        session: AsyncSession,
        owner: str,
        repo: str,
        number: int,
        node_id: str,
        result: SyncResult,
        reconciler: PartitionReconciler,
    ) -> None:
        partition = _issue_partition(owner, repo)
        path = f"/repos/{owner}/{repo}/issues/{number}"
        response = await self._client.rest_get(path)
        if response.status_code >= 400:
            result.errors.append(f"issue {number} status={response.status_code}")
            reconciler.mark_partial(SourceType.GITHUB_ISSUE.value, partition)
            return
        if not isinstance(response.json_body, dict):
            # A malformed element in the reconciled issue partition: keep siblings
            # already observed this cycle live by refusing to reconcile the partition.
            result.errors.append(f"issue {number} malformed body")
            reconciler.mark_partial(SourceType.GITHUB_ISSUE.value, partition)
            return
        body = response.json_body
        await self._upsert_projection(
            session,
            source_type=SourceType.GITHUB_ISSUE,
            source_id=str(body.get("node_id") or node_id),
            source_url=body.get("html_url"),
            source_updated_at=_parse_gh_time(body.get("updated_at")),
            partition_key=partition,
            payload=body,
            result=result,
            reconciler=reconciler,
        )
        await self._sync_issue_comments(session, owner, repo, number, node_id, result, reconciler)

    async def _sync_pull(
        self,
        session: AsyncSession,
        owner: str,
        repo: str,
        number: int,
        node_id: str,
        content: dict[str, Any],
        result: SyncResult,
        reconciler: PartitionReconciler,
    ) -> None:
        partition = _pull_partition(owner, repo)
        path = f"/repos/{owner}/{repo}/pulls/{number}"
        response = await self._client.rest_get(path)
        if response.status_code >= 400:
            result.errors.append(f"pull {number} status={response.status_code}")
            reconciler.mark_partial(SourceType.GITHUB_PULL_REQUEST.value, partition)
            return
        if not isinstance(response.json_body, dict):
            # A malformed element in the reconciled pull partition: keep siblings
            # already observed this cycle live by refusing to reconcile the partition.
            result.errors.append(f"pull {number} malformed body")
            reconciler.mark_partial(SourceType.GITHUB_PULL_REQUEST.value, partition)
            return
        body = response.json_body
        head_sha = (body.get("head") or {}).get("sha") or content.get("headRefOid")
        await self._upsert_projection(
            session,
            source_type=SourceType.GITHUB_PULL_REQUEST,
            source_id=str(body.get("node_id") or node_id),
            source_url=body.get("html_url"),
            source_updated_at=_parse_gh_time(body.get("updated_at")),
            partition_key=partition,
            payload={**body, "_head_sha": head_sha},
            result=result,
            reconciler=reconciler,
        )
        # Conversation comments share the issue comments endpoint.
        await self._sync_issue_comments(session, owner, repo, number, node_id, result, reconciler)
        await self._sync_pr_reviews(session, owner, repo, number, node_id, result, reconciler)
        if isinstance(head_sha, str) and head_sha:
            await self._sync_checks(session, owner, repo, head_sha, result, reconciler)

    async def _sync_issue_comments(
        self,
        session: AsyncSession,
        owner: str,
        repo: str,
        number: int,
        parent_node_id: str,
        result: SyncResult,
        reconciler: PartitionReconciler,
    ) -> None:
        partition = _issue_comments_partition(owner, repo, number)
        source_type = SourceType.GITHUB_ISSUE_COMMENT
        reconciler.touch(source_type.value, partition)
        comments, ok = await self._paginate_rest(
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            params=None,
            extract=_array_extractor,
        )
        if not ok:
            result.errors.append(f"issue comments {number} partial read")
            reconciler.mark_partial(source_type.value, partition)
            return
        malformed = False
        for comment in comments:
            if not isinstance(comment, dict):
                malformed = True
                continue
            comment_node_id = comment.get("node_id")
            if not isinstance(comment_node_id, str):
                malformed = True
                continue
            await self._upsert_projection(
                session,
                source_type=source_type,
                source_id=comment_node_id,
                source_url=comment.get("html_url"),
                source_updated_at=_parse_gh_time(comment.get("updated_at")),
                partition_key=partition,
                payload={**comment, "_parent_number": number, "_parent_node_id": parent_node_id},
                result=result,
                reconciler=reconciler,
            )
        if malformed:
            result.errors.append(f"issue comments {number} contained malformed element(s)")
            reconciler.mark_partial(source_type.value, partition)

    async def _sync_pr_reviews(
        self,
        session: AsyncSession,
        owner: str,
        repo: str,
        number: int,
        parent_node_id: str,
        result: SyncResult,
        reconciler: PartitionReconciler,
    ) -> None:
        reviews_partition = _pr_reviews_partition(owner, repo, number)
        reviews_type = SourceType.GITHUB_PULL_REQUEST_REVIEW
        reconciler.touch(reviews_type.value, reviews_partition)
        reviews, ok = await self._paginate_rest(
            f"/repos/{owner}/{repo}/pulls/{number}/reviews",
            params=None,
            extract=_array_extractor,
        )
        if not ok:
            result.errors.append(f"reviews {number} partial read")
            reconciler.mark_partial(reviews_type.value, reviews_partition)
        else:
            reviews_malformed = False
            for review in reviews:
                if not isinstance(review, dict) or not isinstance(review.get("node_id"), str):
                    reviews_malformed = True
                    continue
                await self._upsert_projection(
                    session,
                    source_type=reviews_type,
                    source_id=review["node_id"],
                    source_url=review.get("html_url"),
                    source_updated_at=_parse_gh_time(review.get("submitted_at")),
                    partition_key=reviews_partition,
                    payload=review,
                    result=result,
                    reconciler=reconciler,
                )
            if reviews_malformed:
                result.errors.append(f"reviews {number} contained malformed element(s)")
                reconciler.mark_partial(reviews_type.value, reviews_partition)

        comments_partition = _pr_review_comments_partition(owner, repo, number)
        comments_type = SourceType.GITHUB_PULL_REQUEST_REVIEW_COMMENT
        reconciler.touch(comments_type.value, comments_partition)
        review_comments, ok = await self._paginate_rest(
            f"/repos/{owner}/{repo}/pulls/{number}/comments",
            params=None,
            extract=_array_extractor,
        )
        if not ok:
            result.errors.append(f"review comments {number} partial read")
            reconciler.mark_partial(comments_type.value, comments_partition)
            return
        comments_malformed = False
        for comment in review_comments:
            if not isinstance(comment, dict) or not isinstance(comment.get("node_id"), str):
                comments_malformed = True
                continue
            await self._upsert_projection(
                session,
                source_type=comments_type,
                source_id=comment["node_id"],
                source_url=comment.get("html_url"),
                source_updated_at=_parse_gh_time(comment.get("updated_at")),
                partition_key=comments_partition,
                payload=comment,
                result=result,
                reconciler=reconciler,
            )
        if comments_malformed:
            result.errors.append(f"review comments {number} contained malformed element(s)")
            reconciler.mark_partial(comments_type.value, comments_partition)

    async def _sync_checks(
        self,
        session: AsyncSession,
        owner: str,
        repo: str,
        head_sha: str,
        result: SyncResult,
        reconciler: PartitionReconciler,
    ) -> None:
        await self._sync_commit_status(session, owner, repo, head_sha, result, reconciler)
        await self._sync_check_runs(session, owner, repo, head_sha, result, reconciler)
        await self._sync_check_suites(session, owner, repo, head_sha, result, reconciler)
        await self._sync_workflow_runs(session, owner, repo, head_sha, result, reconciler)

    async def _sync_commit_status(
        self,
        session: AsyncSession,
        owner: str,
        repo: str,
        head_sha: str,
        result: SyncResult,
        reconciler: PartitionReconciler,
    ) -> None:
        partition = _commit_status_partition(owner, repo, head_sha)
        source_type = SourceType.GITHUB_COMMIT_STATUS
        reconciler.touch(source_type.value, partition)
        statuses, ok = await self._paginate_rest(
            f"/repos/{owner}/{repo}/commits/{head_sha}/status",
            params=None,
            extract=_wrapped_extractor("statuses"),
        )
        if not ok:
            result.errors.append(f"commit status {head_sha[:12]} partial read")
            reconciler.mark_partial(source_type.value, partition)
            return
        malformed = False
        for st in statuses:
            if not isinstance(st, dict):
                malformed = True
                continue
            sid = commit_status_identity(st, head_sha=head_sha)
            if sid is None:
                # Source identity is a closed contract: an element without a
                # fully defined identity is malformed, so mark the partition
                # partial and never upsert a synthetic id.
                malformed = True
                continue
            await self._upsert_projection(
                session,
                source_type=source_type,
                source_id=sid,
                source_url=st.get("target_url"),
                source_updated_at=_parse_gh_time(st.get("updated_at")),
                partition_key=partition,
                payload=st,
                result=result,
                reconciler=reconciler,
            )
        if malformed:
            result.errors.append(f"commit status {head_sha[:12]} contained malformed element(s)")
            reconciler.mark_partial(source_type.value, partition)

    async def _sync_check_runs(
        self,
        session: AsyncSession,
        owner: str,
        repo: str,
        head_sha: str,
        result: SyncResult,
        reconciler: PartitionReconciler,
    ) -> None:
        partition = _check_runs_partition(owner, repo, head_sha)
        source_type = SourceType.GITHUB_CHECK_RUN
        reconciler.touch(source_type.value, partition)
        runs, ok = await self._paginate_rest(
            f"/repos/{owner}/{repo}/commits/{head_sha}/check-runs",
            params=None,
            extract=_wrapped_extractor("check_runs"),
        )
        if not ok:
            result.errors.append(f"check runs {head_sha[:12]} partial read")
            reconciler.mark_partial(source_type.value, partition)
            return
        malformed = False
        for run in runs:
            if not isinstance(run, dict) or not isinstance(run.get("node_id"), str):
                malformed = True
                continue
            await self._upsert_projection(
                session,
                source_type=source_type,
                source_id=run["node_id"],
                source_url=run.get("html_url"),
                source_updated_at=_parse_gh_time(run.get("completed_at") or run.get("started_at")),
                partition_key=partition,
                payload=run,
                result=result,
                reconciler=reconciler,
            )
        if malformed:
            result.errors.append(f"check runs {head_sha[:12]} contained malformed element(s)")
            reconciler.mark_partial(source_type.value, partition)

    async def _sync_check_suites(
        self,
        session: AsyncSession,
        owner: str,
        repo: str,
        head_sha: str,
        result: SyncResult,
        reconciler: PartitionReconciler,
    ) -> None:
        partition = _check_suites_partition(owner, repo, head_sha)
        source_type = SourceType.GITHUB_CHECK_SUITE
        reconciler.touch(source_type.value, partition)
        suites, ok = await self._paginate_rest(
            f"/repos/{owner}/{repo}/commits/{head_sha}/check-suites",
            params=None,
            extract=_wrapped_extractor("check_suites"),
        )
        if not ok:
            result.errors.append(f"check suites {head_sha[:12]} partial read")
            reconciler.mark_partial(source_type.value, partition)
            return
        malformed = False
        for suite in suites:
            if not isinstance(suite, dict) or not isinstance(suite.get("node_id"), str):
                malformed = True
                continue
            await self._upsert_projection(
                session,
                source_type=source_type,
                source_id=suite["node_id"],
                source_url=None,
                source_updated_at=_parse_gh_time(suite.get("updated_at")),
                partition_key=partition,
                payload=suite,
                result=result,
                reconciler=reconciler,
            )
        if malformed:
            result.errors.append(f"check suites {head_sha[:12]} contained malformed element(s)")
            reconciler.mark_partial(source_type.value, partition)

    async def _sync_workflow_runs(
        self,
        session: AsyncSession,
        owner: str,
        repo: str,
        head_sha: str,
        result: SyncResult,
        reconciler: PartitionReconciler,
    ) -> None:
        partition = _workflow_runs_partition(owner, repo, head_sha)
        source_type = SourceType.GITHUB_WORKFLOW_RUN
        reconciler.touch(source_type.value, partition)
        runs, ok = await self._paginate_rest(
            f"/repos/{owner}/{repo}/actions/runs",
            params={"head_sha": head_sha},
            extract=_wrapped_extractor("workflow_runs"),
        )
        if not ok:
            result.errors.append(f"workflow runs {head_sha[:12]} partial read")
            reconciler.mark_partial(source_type.value, partition)
            return
        malformed = False
        for run in runs:
            if not isinstance(run, dict):
                malformed = True
                continue
            sid = workflow_run_identity(run)
            if sid is None:
                # Closed identity contract: no synthetic id from incomplete data.
                malformed = True
                continue
            await self._upsert_projection(
                session,
                source_type=source_type,
                source_id=sid,
                source_url=run.get("html_url"),
                source_updated_at=_parse_gh_time(run.get("updated_at")),
                partition_key=partition,
                payload=run,
                result=result,
                reconciler=reconciler,
            )
        if malformed:
            result.errors.append(f"workflow runs {head_sha[:12]} contained malformed element(s)")
            reconciler.mark_partial(source_type.value, partition)

    async def _derive_workflow_state(self, session: AsyncSession, result: SyncResult) -> None:
        """Parse/authorize/validate workflow records from projected issue comments."""
        # Build set of Project-linked issue numbers from live project items.
        linked_issues: set[int] = set()
        issue_node_by_number: dict[int, str] = {}
        pr_head_by_number: dict[int, str] = {}

        items = list(
            await session.exec(
                select(McProjectionRecord).where(
                    col(McProjectionRecord.source_type) == SourceType.GITHUB_PROJECT_ITEM.value,
                    col(McProjectionRecord.tombstoned).is_(False),
                )
            )
        )
        for item in items:
            content = (item.payload or {}).get("content")
            if not isinstance(content, dict):
                continue
            number = content.get("number")
            node_id = content.get("id")
            if isinstance(number, int) and content.get("__typename") == "Issue":
                linked_issues.add(number)
                if isinstance(node_id, str):
                    issue_node_by_number[number] = node_id
            if isinstance(number, int) and content.get("__typename") == "PullRequest":
                head = content.get("headRefOid")
                if isinstance(head, str):
                    pr_head_by_number[number] = head

        prs = list(
            await session.exec(
                select(McProjectionRecord).where(
                    col(McProjectionRecord.source_type) == SourceType.GITHUB_PULL_REQUEST.value,
                    col(McProjectionRecord.tombstoned).is_(False),
                )
            )
        )
        for pr in prs:
            number = pr.payload.get("number")
            head = pr.payload.get("_head_sha") or (pr.payload.get("head") or {}).get("sha")
            if isinstance(number, int) and isinstance(head, str):
                pr_head_by_number[number] = head

        comments = list(
            await session.exec(
                select(McProjectionRecord).where(
                    col(McProjectionRecord.source_type) == SourceType.GITHUB_ISSUE_COMMENT.value,
                    col(McProjectionRecord.tombstoned).is_(False),
                )
            )
        )

        candidates: list[CandidateRecord] = []
        quarantined_ids: set[int] = set()

        for comment in comments:
            payload = comment.payload or {}
            body = payload.get("body")
            if not isinstance(body, str) or "ai-workflow-record:v1" not in body:
                continue
            comment_id = payload.get("id")
            login = (payload.get("user") or {}).get("login")
            parent_number = payload.get("_parent_number")
            if not isinstance(comment_id, int) or not isinstance(login, str):
                continue
            if not isinstance(parent_number, int):
                continue

            created = _parse_gh_time(payload.get("created_at")) or utcnow()
            updated = _parse_gh_time(payload.get("updated_at")) or created
            source = CommentSourceMeta(
                comment_id=comment_id,
                card=parent_number,
                github_login=login,
                created_at=created,
                updated_at=updated,
                html_url=str(payload.get("html_url") or comment.source_url or ""),
                body=body,
                on_authoritative_issue=parent_number in linked_issues,
                issue_number=parent_number,
            )

            edited = check_edited_comment(source)
            if edited is not None:
                quarantined_ids.add(comment_id)
                await self._quarantine(
                    session,
                    reason=edited.reason,
                    message=edited.message,
                    source_type=SourceType.GITHUB_ISSUE_COMMENT.value,
                    source_id=comment.source_id,
                    source_url=source.html_url,
                    source_updated_at=updated,
                    diagnostic={"comment_id": comment_id},
                    result=result,
                )
                continue

            parsed = parse_workflow_record_from_comment(body)
            if not parsed.ok or parsed.record is None:
                quarantined_ids.add(comment_id)
                reason = QuarantineReason.MALFORMED_RECORD
                if any(
                    f.rule_id.endswith("type") or "unknown" in f.message.lower()
                    for f in parsed.findings
                ):
                    reason = QuarantineReason.UNKNOWN_ENUM
                await self._quarantine(
                    session,
                    reason=reason,
                    message="; ".join(f.message for f in parsed.findings) or "parse failed",
                    source_type=SourceType.GITHUB_ISSUE_COMMENT.value,
                    source_id=comment.source_id,
                    source_url=source.html_url,
                    source_updated_at=updated,
                    diagnostic={"findings": [f.message for f in parsed.findings]},
                    result=result,
                )
                continue

            binding = check_card_binding(source, parsed.record)
            if binding is not None:
                quarantined_ids.add(comment_id)
                await self._quarantine(
                    session,
                    reason=binding.reason,
                    message=binding.message,
                    source_type=SourceType.GITHUB_ISSUE_COMMENT.value,
                    source_id=comment.source_id,
                    source_url=source.html_url,
                    source_updated_at=updated,
                    result=result,
                )
                continue

            candidates.append(CandidateRecord(source=source, record=parsed.record))

        # First pass: supersession structural validation
        supersession_issues = validate_supersession_graph(candidates)
        for comment_id, issue in supersession_issues.items():
            quarantined_ids.add(comment_id)
            await self._quarantine(
                session,
                reason=issue.reason,
                message=issue.message,
                source_type=SourceType.GITHUB_ISSUE_COMMENT.value,
                source_id=str(comment_id),
                result=result,
            )

        surviving = [c for c in candidates if c.source.comment_id not in quarantined_ids]

        # Authorization + exact-head (needs provisional assignment for handoff/kanban).
        # Iterate in order; recompute assignment after each accepted assignment record.
        ordered = sorted(surviving, key=lambda c: (c.source.created_at, c.source.comment_id))
        accepted: list[CandidateRecord] = []
        for candidate in ordered:
            provisional = derive_effective_assignments(
                accepted,
                quarantined_ids=set(),
            )
            eff = provisional.effective.get(candidate.record.card)
            auth = authorize_record_author(
                record=candidate.record,
                github_login=candidate.source.github_login,
                registry=self._registry,
                effective_worker_identity=eff.worker if eff else None,
                effective_role=eff.role if eff else None,
            )
            if not auth.ok and auth.reason is not None:
                quarantined_ids.add(candidate.source.comment_id)
                await self._quarantine(
                    session,
                    reason=auth.reason,
                    message=auth.message,
                    source_type=SourceType.GITHUB_ISSUE_COMMENT.value,
                    source_id=str(candidate.source.comment_id),
                    source_url=candidate.source.html_url,
                    result=result,
                )
                continue

            # Worker start_task may not supersede an existing assignment.
            if (
                candidate.record.type == "start_task"
                and candidate.record.supersedes is not None
                and auth.principal is not None
                and auth.principal.trust_class == "worker"
            ):
                quarantined_ids.add(candidate.source.comment_id)
                await self._quarantine(
                    session,
                    reason=QuarantineReason.UNAUTHORIZED_SUPERSESSION,
                    message=("Worker-authored start_task may not supersede an existing assignment"),
                    source_type=SourceType.GITHUB_ISSUE_COMMENT.value,
                    source_id=str(candidate.source.comment_id),
                    source_url=candidate.source.html_url,
                    result=result,
                )
                continue

            if candidate.record.type in {"review_result", "human_approval"}:
                current_head = _resolve_current_head(candidate.record, pr_head_by_number)
                head_issue = check_exact_head(candidate.record, current_head=current_head)
                if head_issue is not None:
                    quarantined_ids.add(candidate.source.comment_id)
                    await self._quarantine(
                        session,
                        reason=head_issue.reason,
                        message=head_issue.message,
                        source_type=SourceType.GITHUB_ISSUE_COMMENT.value,
                        source_id=str(candidate.source.comment_id),
                        source_url=candidate.source.html_url,
                        result=result,
                    )
                    continue

            accepted.append(candidate)

        derivation = derive_effective_assignments(
            accepted,
            quarantined_ids=set(),
        )
        for card, conflict in derivation.conflicts.items():
            await self._quarantine(
                session,
                reason=conflict.reason,
                message=conflict.message,
                diagnostic={"card": card},
                result=result,
            )
        for card, assignment in derivation.effective.items():
            result.effective_assignments[card] = {
                "worker": assignment.worker,
                "role": assignment.role,
                "comment_id": str(assignment.comment_id),
                "record_type": assignment.record_type,
            }


def _parse_gh_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def _resolve_current_head(
    record: Any,
    pr_head_by_number: dict[int, str],
) -> str | None:
    parsed = record.artifact_parsed
    if parsed is None:
        return None
    if parsed.kind == "pr":
        return pr_head_by_number.get(int(parsed.value))
    # path: head-for-path resolution deferred richness → unresolvable unless provided
    return None
