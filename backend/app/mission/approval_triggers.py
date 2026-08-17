"""Automatic system-created approval-request triggers (Slice 5A Checkpoint E).

Hooked into `GitHubSyncService`'s existing result path (`_poll_tick`, after
`service.run(session)` commits) -- reads only already-committed
`McProjectionRecord` rows, never issues a new GitHub call.

Human-approved production trigger (Checkpoint E plan, final revision):
`policy_key="implementation_review"`, `action_key="implementation_review"`,
condition = a projected Pull Request has a new head SHA for that action.

Five trigger cases, dispatched by `_evaluate_one_pr`:
1. same head + pending predecessor -> no-op.
2. same head + terminal predecessor -> dedup replay (trigger_key collides,
   `create_system_approval_request`'s own IntegrityError fallback returns
   the existing row; no new row).
3. new head + pending predecessor -> atomic supersede (pending -> superseded)
   + successor, one transaction.
4. new head + terminal predecessor (or no prior request at all) -> plain
   successor, no predecessor mutation.
5. concurrent duplicate head observations -> covered by the same lock+
   recheck (case 3) and trigger_key-unique-index dedup (cases 1/2/4)
   primitives already in place; no separate machinery.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.mission.approval_service import (
    _create_system_approval_request_in_session,
    _load_active_policy,
    create_system_approval_request,
)
from app.mission.approval_system_principal import resolve_system_principal
from app.mission.types import SourceType
from app.models.mc_approval import McApprovalRequest
from app.models.mc_projection import McProjectionRecord

logger = get_logger(__name__)

TRIGGER_POLICY_KEY = "implementation_review"
TRIGGER_ACTION_KEY = "implementation_review"
TRIGGER_SCOPE_TYPE = "action"
TRIGGER_MISSION_CARD_KIND = "pull_request"

_TRIGGER_KEY_MAX_LENGTH = 512


@dataclass(frozen=True, slots=True)
class TriggerEvaluationResult:
    """Bounded-tick outcome for one automatic-trigger evaluation pass."""

    observed: int = 0
    created: int = 0
    superseded: int = 0


def _build_mission_trigger_key(
    *, owner: str, repo: str, pr_number: int, action_key: str, head_sha: str
) -> str:
    key = f"mission:{owner}/{repo}#{pr_number}|action:{action_key}|head:{head_sha}"
    if len(key) > _TRIGGER_KEY_MAX_LENGTH:  # pragma: no cover - defensive, not reachable
        # with real GitHub owner/repo/sha lengths
        msg = f"trigger_key exceeds {_TRIGGER_KEY_MAX_LENGTH} chars: {key!r}"
        raise ValueError(msg)
    return key


def _default_expires_at() -> datetime:
    return utcnow() + timedelta(seconds=settings.mc_approval_default_expiration_seconds)


async def evaluate_triggers(
    session: AsyncSession, *, owner: str, repo: str
) -> TriggerEvaluationResult:
    """Observe already-committed PR projection state and create/supersede
    system approval requests per the deterministic trigger-key rules."""
    mission_source_repo = f"{owner}/{repo}"
    pr_records = (
        await session.exec(
            select(McProjectionRecord).where(
                col(McProjectionRecord.source_type) == SourceType.GITHUB_PULL_REQUEST.value,
                col(McProjectionRecord.tombstoned).is_(False),
            )
        )
    ).all()
    # The read-only query above auto-begins a transaction on this session
    # (SQLAlchemy's autobegin). Close it out explicitly before delegating
    # into per-PR handling, some of which opens a *separate* fresh session
    # via create_system_approval_request -- two open transactions can't
    # share one physical connection (this matters for the shared-connection
    # SQLite test setup; a real Postgres deployment gives each session its
    # own connection, but closing this cleanly is correct either way).
    await session.commit()

    observed = 0
    created = 0
    superseded = 0
    for record in pr_records:
        payload = record.payload or {}
        pr_number = payload.get("number")
        head_sha = payload.get("_head_sha")
        if not isinstance(pr_number, int) or not isinstance(head_sha, str) or not head_sha:
            continue
        observed += 1
        outcome = await _evaluate_one_pr(
            session,
            owner=owner,
            repo=repo,
            mission_source_repo=mission_source_repo,
            pr_number=pr_number,
            head_sha=head_sha,
        )
        if outcome == "created":
            created += 1
        elif outcome == "superseded":
            superseded += 1

    return TriggerEvaluationResult(observed=observed, created=created, superseded=superseded)


async def _create_trigger_request(
    *,
    mission_source_repo: str,
    pr_number: int,
    trigger_key: str,
    supersedes_request_id: UUID | None,
) -> None:
    await create_system_approval_request(
        policy_key=TRIGGER_POLICY_KEY,
        scope_type=TRIGGER_SCOPE_TYPE,
        mission_source_repo=mission_source_repo,
        mission_card_kind=TRIGGER_MISSION_CARD_KIND,
        mission_card_number=pr_number,
        action_key=TRIGGER_ACTION_KEY,
        expires_at=_default_expires_at(),
        trigger_key=trigger_key,
        supersedes_request_id=supersedes_request_id,
    )


async def _evaluate_one_pr(
    session: AsyncSession,
    *,
    owner: str,
    repo: str,
    mission_source_repo: str,
    pr_number: int,
    head_sha: str,
) -> str | None:
    new_trigger_key = _build_mission_trigger_key(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        action_key=TRIGGER_ACTION_KEY,
        head_sha=head_sha,
    )

    latest = (
        await session.exec(
            select(McApprovalRequest)
            .where(
                col(McApprovalRequest.mission_source_repo) == mission_source_repo,
                col(McApprovalRequest.mission_card_number) == pr_number,
                col(McApprovalRequest.action_key) == TRIGGER_ACTION_KEY,
                col(McApprovalRequest.trigger_key).is_not(None),
            )
            .order_by(col(McApprovalRequest.created_at).desc())
            .limit(1)
        )
    ).first()
    # Same reasoning as evaluate_triggers' own top-level query: close this
    # read-only autobegun transaction before any branch below that may
    # delegate into a fresh-session call.
    await session.commit()

    if latest is None:
        # Case 4 (no prior request at all).
        await _create_trigger_request(
            mission_source_repo=mission_source_repo,
            pr_number=pr_number,
            trigger_key=new_trigger_key,
            supersedes_request_id=None,
        )
        return "created"

    if latest.trigger_key == new_trigger_key:
        if latest.status == "pending":
            # Case 1: same head, still pending -- no-op.
            return None
        # Case 2: same head, terminal -- dedup replay. The trigger_key
        # collides exactly, so create_system_approval_request's own
        # IntegrityError fallback returns the existing row; no new row.
        await _create_trigger_request(
            mission_source_repo=mission_source_repo,
            pr_number=pr_number,
            trigger_key=new_trigger_key,
            supersedes_request_id=latest.supersedes_request_id,
        )
        return None

    # New head vs. the most recent request for this triple.
    if latest.status != "pending":
        # Case 4 (terminal predecessor): plain successor, no predecessor mutation.
        await _create_trigger_request(
            mission_source_repo=mission_source_repo,
            pr_number=pr_number,
            trigger_key=new_trigger_key,
            supersedes_request_id=latest.id,
        )
        return "created"

    # Case 3 (and, under a race, case 5 -> falls into case 4's handling
    # after losing the lock): new head + pending predecessor.
    return await _handle_new_head_pending_predecessor(
        session,
        predecessor_id=latest.id,
        mission_source_repo=mission_source_repo,
        pr_number=pr_number,
        new_trigger_key=new_trigger_key,
    )


async def _handle_new_head_pending_predecessor(
    session: AsyncSession,
    *,
    predecessor_id: UUID,
    mission_source_repo: str,
    pr_number: int,
    new_trigger_key: str,
) -> str | None:
    """Atomically supersede a still-pending predecessor with a new-head successor.

    Locks the predecessor row first and rechecks its status under the lock.
    If it's still pending, the supersede transition and the successor
    insert happen together in this one transaction. If a race already
    resolved it (case 5: a concurrent trigger evaluation or a human
    decision won first), the lock is released with nothing mutated, and the
    fallback -- a plain successor with no supersede transition -- is
    performed afterward via the fresh-session wrapper, outside this
    function's own transaction, rather than nesting a second session/
    connection while still holding this one's row lock.
    """
    still_pending = False
    async with session.begin():
        predecessor = (
            await session.exec(
                select(McApprovalRequest)
                .where(col(McApprovalRequest.id) == predecessor_id)
                .with_for_update()
            )
        ).first()
        if predecessor is None:
            return None
        still_pending = predecessor.status == "pending"
        if still_pending:
            principal = await resolve_system_principal(session)
            policy, definition = await _load_active_policy(session, TRIGGER_POLICY_KEY)
            await _create_system_approval_request_in_session(
                session,
                principal=principal,
                policy=policy,
                definition=definition,
                scope_type=TRIGGER_SCOPE_TYPE,
                mission_source_repo=mission_source_repo,
                mission_card_kind=TRIGGER_MISSION_CARD_KIND,
                mission_card_number=pr_number,
                action_key=TRIGGER_ACTION_KEY,
                expires_at=_default_expires_at(),
                trigger_key=new_trigger_key,
                supersedes_request_id=predecessor.id,
                predecessor_to_supersede=predecessor,
                auto_retry_count=0,
            )

    if still_pending:
        return "superseded"

    # Race lost: predecessor is no longer pending. Treat as the
    # terminal-predecessor case (case 4), in a fresh transaction.
    await _create_trigger_request(
        mission_source_repo=mission_source_repo,
        pr_number=pr_number,
        trigger_key=new_trigger_key,
        supersedes_request_id=predecessor_id,
    )
    return "created"
