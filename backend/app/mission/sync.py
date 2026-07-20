"""Read-only GitHub sync orchestration with partial-read safety."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import utcnow
from app.mission.assignment import derive_effective_assignments
from app.mission.authorization import authorize_record_author
from app.mission.github_client import PROJECT_ITEMS_QUERY, GitHubReadClient
from app.mission.principal_registry import PrincipalRegistry
from app.mission.redaction import scrub_mapping
from app.mission.types import QuarantineReason, SourceType
from app.mission.validation import (
    CandidateRecord,
    CommentSourceMeta,
    ValidationIssue,
    check_card_binding,
    check_edited_comment,
    check_exact_head,
    validate_supersession_graph,
)
from app.mission.workflow_record import parse_workflow_record_from_comment
from app.models.mc_projection import McProjectionRecord, McQuarantine, McSyncState


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
    errors: list[str] = field(default_factory=list)
    effective_assignments: dict[int, dict[str, str]] = field(default_factory=dict)


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
        started = utcnow()
        state = await self._get_or_create_state(session)
        state.status = "running"
        state.last_started_at = started
        state.last_error = None
        await session.commit()

        result = SyncResult(ok=True, partial=False)
        try:
            await self._sync_project_items(session, result)
            await self._sync_linked_issues_and_prs(session, result)
            # Workflow records processed from projected issue comments.
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
            row.tombstoned = False
            row.payload = safe_payload if isinstance(safe_payload, dict) else {}
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

    async def _sync_project_items(self, session: AsyncSession, result: SyncResult) -> None:
        after: str | None = None
        partition = f"project:{self._config.project_owner}:{self._config.project_number}"
        try:
            while True:
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
                    return
                body = response.json_body
                if not isinstance(body, dict) or body.get("errors"):
                    result.errors.append(f"project items errors={body!r}")
                    return
                data = body.get("data") or {}
                user = data.get("user") if isinstance(data, dict) else None
                project = user.get("projectV2") if isinstance(user, dict) else None
                items = (project or {}).get("items") if isinstance(project, dict) else None
                if not isinstance(items, dict):
                    result.errors.append("project items missing")
                    return
                for node in items.get("nodes") or []:
                    if not isinstance(node, dict) or not node.get("id"):
                        continue
                    await self._upsert_projection(
                        session,
                        source_type=SourceType.GITHUB_PROJECT_ITEM,
                        source_id=str(node["id"]),
                        source_url=None,
                        source_updated_at=_parse_gh_time(node.get("updatedAt")),
                        partition_key=partition,
                        payload=node,
                        result=result,
                    )
                page = items.get("pageInfo") or {}
                if not page.get("hasNextPage"):
                    break
                after = page.get("endCursor")
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"project sync failed: {exc}")

    async def _sync_linked_issues_and_prs(
        self, session: AsyncSession, result: SyncResult
    ) -> None:
        """Sync issues/PRs/comments discovered via projected project items."""
        stmt = select(McProjectionRecord).where(
            col(McProjectionRecord.source_type) == SourceType.GITHUB_PROJECT_ITEM.value,
            col(McProjectionRecord.tombstoned).is_(False),
        )
        items = list(await session.exec(stmt))
        owner, repo = self._config.self_owner, self._config.self_repo
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
                await self._sync_issue(session, owner, repo, number, node_id, result)
            elif typename == "PullRequest":
                await self._sync_pull(session, owner, repo, number, node_id, content, result)

    async def _sync_issue(
        self,
        session: AsyncSession,
        owner: str,
        repo: str,
        number: int,
        node_id: str,
        result: SyncResult,
    ) -> None:
        path = f"/repos/{owner}/{repo}/issues/{number}"
        response = await self._client.rest_get(path)
        if response.status_code >= 400:
            result.errors.append(f"issue {number} status={response.status_code}")
            return
        body = response.json_body if isinstance(response.json_body, dict) else {}
        await self._upsert_projection(
            session,
            source_type=SourceType.GITHUB_ISSUE,
            source_id=str(body.get("node_id") or node_id),
            source_url=body.get("html_url"),
            source_updated_at=_parse_gh_time(body.get("updated_at")),
            partition_key=str(body.get("repository", {}).get("node_id") or f"repo:{owner}/{repo}"),
            payload=body,
            result=result,
        )
        await self._sync_issue_comments(session, owner, repo, number, node_id, result)

    async def _sync_pull(
        self,
        session: AsyncSession,
        owner: str,
        repo: str,
        number: int,
        node_id: str,
        content: dict[str, Any],
        result: SyncResult,
    ) -> None:
        path = f"/repos/{owner}/{repo}/pulls/{number}"
        response = await self._client.rest_get(path)
        if response.status_code >= 400:
            result.errors.append(f"pull {number} status={response.status_code}")
            return
        body = response.json_body if isinstance(response.json_body, dict) else {}
        head_sha = (body.get("head") or {}).get("sha") or content.get("headRefOid")
        await self._upsert_projection(
            session,
            source_type=SourceType.GITHUB_PULL_REQUEST,
            source_id=str(body.get("node_id") or node_id),
            source_url=body.get("html_url"),
            source_updated_at=_parse_gh_time(body.get("updated_at")),
            partition_key=str(
                (body.get("base") or {}).get("repo", {}).get("node_id")
                or f"repo:{owner}/{repo}"
            ),
            payload={**body, "_head_sha": head_sha},
            result=result,
        )
        # Conversation comments share issue comments endpoint.
        await self._sync_issue_comments(session, owner, repo, number, node_id, result)
        await self._sync_pr_reviews(session, owner, repo, number, node_id, result)
        if isinstance(head_sha, str) and head_sha:
            await self._sync_checks(session, owner, repo, head_sha, result)

    async def _sync_issue_comments(
        self,
        session: AsyncSession,
        owner: str,
        repo: str,
        number: int,
        parent_node_id: str,
        result: SyncResult,
    ) -> None:
        response = await self._client.rest_get(
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            params={"per_page": 100},
        )
        if response.status_code >= 400:
            result.errors.append(f"issue comments {number} status={response.status_code}")
            return
        comments = response.json_body if isinstance(response.json_body, list) else []
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            node_id = comment.get("node_id")
            if not isinstance(node_id, str):
                continue
            await self._upsert_projection(
                session,
                source_type=SourceType.GITHUB_ISSUE_COMMENT,
                source_id=node_id,
                source_url=comment.get("html_url"),
                source_updated_at=_parse_gh_time(comment.get("updated_at")),
                partition_key=parent_node_id,
                payload={**comment, "_parent_number": number, "_parent_node_id": parent_node_id},
                result=result,
            )

    async def _sync_pr_reviews(
        self,
        session: AsyncSession,
        owner: str,
        repo: str,
        number: int,
        parent_node_id: str,
        result: SyncResult,
    ) -> None:
        reviews = await self._client.rest_get(
            f"/repos/{owner}/{repo}/pulls/{number}/reviews",
            params={"per_page": 100},
        )
        if reviews.status_code >= 400:
            result.errors.append(f"reviews {number} status={reviews.status_code}")
            return
        for review in reviews.json_body if isinstance(reviews.json_body, list) else []:
            if not isinstance(review, dict) or not isinstance(review.get("node_id"), str):
                continue
            await self._upsert_projection(
                session,
                source_type=SourceType.GITHUB_PULL_REQUEST_REVIEW,
                source_id=review["node_id"],
                source_url=review.get("html_url"),
                source_updated_at=_parse_gh_time(review.get("submitted_at")),
                partition_key=parent_node_id,
                payload=review,
                result=result,
            )
        review_comments = await self._client.rest_get(
            f"/repos/{owner}/{repo}/pulls/{number}/comments",
            params={"per_page": 100},
        )
        if review_comments.status_code >= 400:
            result.errors.append(
                f"review comments {number} status={review_comments.status_code}"
            )
            return
        for comment in (
            review_comments.json_body if isinstance(review_comments.json_body, list) else []
        ):
            if not isinstance(comment, dict) or not isinstance(comment.get("node_id"), str):
                continue
            await self._upsert_projection(
                session,
                source_type=SourceType.GITHUB_PULL_REQUEST_REVIEW_COMMENT,
                source_id=comment["node_id"],
                source_url=comment.get("html_url"),
                source_updated_at=_parse_gh_time(comment.get("updated_at")),
                partition_key=parent_node_id,
                payload=comment,
                result=result,
            )

    async def _sync_checks(
        self,
        session: AsyncSession,
        owner: str,
        repo: str,
        head_sha: str,
        result: SyncResult,
    ) -> None:
        status = await self._client.rest_get(f"/repos/{owner}/{repo}/commits/{head_sha}/status")
        if status.status_code < 400 and isinstance(status.json_body, dict):
            for st in status.json_body.get("statuses") or []:
                if not isinstance(st, dict):
                    continue
                sid = st.get("node_id") or f"status:{head_sha}:{st.get('context')}:{st.get('id')}"
                await self._upsert_projection(
                    session,
                    source_type=SourceType.GITHUB_COMMIT_STATUS,
                    source_id=str(sid),
                    source_url=st.get("target_url"),
                    source_updated_at=_parse_gh_time(st.get("updated_at")),
                    partition_key=f"repo:{owner}/{repo}:{head_sha}",
                    payload=st,
                    result=result,
                )
        runs = await self._client.rest_get(
            f"/repos/{owner}/{repo}/commits/{head_sha}/check-runs",
            params={"per_page": 100},
        )
        if runs.status_code < 400 and isinstance(runs.json_body, dict):
            for run in runs.json_body.get("check_runs") or []:
                if not isinstance(run, dict) or not isinstance(run.get("node_id"), str):
                    continue
                await self._upsert_projection(
                    session,
                    source_type=SourceType.GITHUB_CHECK_RUN,
                    source_id=run["node_id"],
                    source_url=run.get("html_url"),
                    source_updated_at=_parse_gh_time(run.get("completed_at") or run.get("started_at")),
                    partition_key=f"repo:{owner}/{repo}:{head_sha}",
                    payload=run,
                    result=result,
                )
        suites = await self._client.rest_get(
            f"/repos/{owner}/{repo}/commits/{head_sha}/check-suites",
            params={"per_page": 100},
        )
        if suites.status_code < 400 and isinstance(suites.json_body, dict):
            for suite in suites.json_body.get("check_suites") or []:
                if not isinstance(suite, dict) or not isinstance(suite.get("node_id"), str):
                    continue
                await self._upsert_projection(
                    session,
                    source_type=SourceType.GITHUB_CHECK_SUITE,
                    source_id=suite["node_id"],
                    source_url=None,
                    source_updated_at=_parse_gh_time(suite.get("updated_at")),
                    partition_key=f"repo:{owner}/{repo}:{head_sha}",
                    payload=suite,
                    result=result,
                )
        workflow = await self._client.rest_get(
            f"/repos/{owner}/{repo}/actions/runs",
            params={"head_sha": head_sha, "per_page": 50},
        )
        if workflow.status_code < 400 and isinstance(workflow.json_body, dict):
            for run in workflow.json_body.get("workflow_runs") or []:
                if not isinstance(run, dict):
                    continue
                sid = run.get("node_id") or f"workflow_run:{run.get('id')}"
                await self._upsert_projection(
                    session,
                    source_type=SourceType.GITHUB_WORKFLOW_RUN,
                    source_id=str(sid),
                    source_url=run.get("html_url"),
                    source_updated_at=_parse_gh_time(run.get("updated_at")),
                    partition_key=f"repo:{owner}/{repo}:{head_sha}",
                    payload=run,
                    result=result,
                )

    async def _derive_workflow_state(self, session: AsyncSession, result: SyncResult) -> None:
        """Parse/authorize/validate workflow records from projected issue comments."""
        # Build set of Project-linked issue numbers from project items.
        linked_issues: set[int] = set()
        issue_node_by_number: dict[int, str] = {}
        pr_head_by_number: dict[int, str] = {}

        items = list(
            await session.exec(
                select(McProjectionRecord).where(
                    col(McProjectionRecord.source_type)
                    == SourceType.GITHUB_PROJECT_ITEM.value
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
                    col(McProjectionRecord.source_type)
                    == SourceType.GITHUB_PULL_REQUEST.value
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
                    col(McProjectionRecord.source_type)
                    == SourceType.GITHUB_ISSUE_COMMENT.value
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
                if any(f.rule_id.endswith("type") or "unknown" in f.message.lower() for f in parsed.findings):
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
                    message=(
                        "Worker-authored start_task may not supersede an existing assignment"
                    ),
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
