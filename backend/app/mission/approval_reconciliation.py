"""Expiration reconciliation tick (Slice 5A Checkpoint E).

Server-side-only expiration: the frontend never authoritatively changes
approval state. `run_reconciliation_tick` is a bounded, idempotent sweep of
pending requests whose `expires_at` has passed -- mirrors
`app.mission.retention.purge_tombstoned`'s shape (session-in, result-out,
safe to call repeatedly; a second run over already-processed rows changes
nothing).

Each candidate row is locked, rechecked, and committed in its own
micro-transaction -- never one batch-wide transaction -- so a crash mid-sweep
leaves only fully-committed or fully-untouched rows, and two concurrent
reconciliation ticks (or a reconciliation tick racing a human decision on the
same request) serialize on that per-row lock exactly like `submit_decision`/
`supersede_decision` already do.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.mission.approval_service import (
    _create_system_approval_request_in_session,
    _next_recreate_trigger_key,
    validate_policy_definition,
)
from app.mission.approval_system_principal import resolve_system_principal
from app.models.mc_approval import McApprovalEvent, McApprovalPolicy, McApprovalRequest

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """Bounded-sweep outcome for one reconciliation tick."""

    processed: int = 0
    recreated: int = 0


async def run_reconciliation_tick(session: AsyncSession) -> ReconciliationResult:
    """Expire every pending request whose `expires_at` has passed, bounded by batch size."""
    now = utcnow()
    candidate_ids = (
        await session.exec(
            select(McApprovalRequest.id)
            .where(
                col(McApprovalRequest.status) == "pending",
                col(McApprovalRequest.expires_at).is_not(None),
                col(McApprovalRequest.expires_at) <= now,
            )
            .order_by(col(McApprovalRequest.expires_at).asc())
            .limit(settings.mc_approval_reconciliation_batch_size)
        )
    ).all()
    # The read-only candidate query above auto-begins a transaction on this
    # session (SQLAlchemy's autobegin). Close it out explicitly (nothing
    # was written, so commit vs. rollback is equivalent) so each row below
    # can open its own genuine, independent `session.begin()` micro-
    # transaction rather than raising "a transaction is already begun."
    await session.commit()

    processed = 0
    recreated = 0
    for request_id in candidate_ids:
        outcome = await _reconcile_one(session, request_id)
        if outcome is None:
            continue
        processed += 1
        if outcome == "recreated":
            recreated += 1

    return ReconciliationResult(processed=processed, recreated=recreated)


async def _reconcile_one(session: AsyncSession, request_id: UUID) -> str | None:
    """Lock, recheck, and (if still eligible) expire exactly one request.

    Returns `None` if the recheck under the lock found the row no longer
    eligible (another process already resolved it -- this is what makes
    repeated/concurrent reconciliation idempotent), else "expired" or
    "recreated". Commits its own micro-transaction on success.
    """
    async with session.begin():
        request = (
            await session.exec(
                select(McApprovalRequest)
                .where(col(McApprovalRequest.id) == request_id)
                .with_for_update()
            )
        ).first()
        if request is None:
            return None

        now = utcnow()
        if request.status != "pending" or request.expires_at is None or request.expires_at > now:
            # Recheck-inside-lock: another tick, replica, or human decision
            # already changed this row since the unlocked candidate select.
            return None

        policy = await session.get(McApprovalPolicy, request.policy_id)
        assert policy is not None  # guarded by FK
        definition = validate_policy_definition(policy)

        request.status = "expired"
        request.resolved_at = now
        session.add(request)

        event_detail: dict[str, object] = {"reason": "expiration"}
        outcome = "expired"

        if definition.expiration.behavior == "block_mission":
            event_detail["mission_effect"] = "blocked"
        elif definition.expiration.behavior == "recreate":
            assert definition.expiration.max_auto_retries is not None  # schema-enforced
            if request.auto_retry_count < definition.expiration.max_auto_retries:
                original_ttl = request.expires_at - request.created_at
                if original_ttl <= timedelta(0):
                    # Anomalous/degenerate TTL (nothing today validates
                    # expires_at > created_at at original creation time).
                    # Defensive, single-row fallback to plain expire -- no
                    # successor, so a garbage TTL cannot burn through
                    # max_auto_retries in a rapid-fire loop. Not a general
                    # recreate-degradation rule: a healthy sibling request
                    # under the same policy is unaffected.
                    logger.warning(
                        "mission.approval_reconciliation.non_positive_ttl request_id=%s",
                        request.id,
                    )
                    event_detail["non_positive_ttl"] = True
                else:
                    principal = await resolve_system_principal(session)
                    new_trigger_key = _next_recreate_trigger_key(request)
                    new_auto_retry_count = request.auto_retry_count + 1
                    successor_expires_at = now + original_ttl
                    await _create_system_approval_request_in_session(
                        session,
                        principal=principal,
                        policy=policy,
                        definition=definition,
                        scope_type=request.scope_type,
                        mission_source_repo=request.mission_source_repo,
                        mission_card_kind=request.mission_card_kind,
                        mission_card_number=request.mission_card_number,
                        action_key=request.action_key,
                        expires_at=successor_expires_at,
                        trigger_key=new_trigger_key,
                        supersedes_request_id=request.id,
                        predecessor_to_supersede=None,
                        auto_retry_count=new_auto_retry_count,
                        created_at=now,
                    )
                    outcome = "recreated"
            else:
                event_detail["max_auto_retries_reached"] = True

        session.add(
            McApprovalEvent(
                request_id=request.id,
                event_type="request_expired",
                triggered_by_principal_id=None,
                detail=event_detail,
                created_at=now,
            )
        )
        await session.flush()

    return outcome
