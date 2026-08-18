"""Approval command service (Slice 5A Checkpoint C).

This module owns every write path against the Checkpoint B approval domain
(`app/models/mc_approval.py`): creating a request, submitting a decision, and
superseding a decision. It is the only place those tables are ever mutated —
Checkpoint D's routes will call these functions rather than touching the
models directly.

Session ownership
------------------
Every public function here opens its own, freshly-sourced session from
`app.db.session.async_session_maker` and owns that session's transaction
boundary (`session.begin()` / commit on success / rollback on exception) for
its whole lifetime. It never accepts or reuses the caller's request-scoped
session: `get_auth_context()` may already have called `session.commit()` on
that session (see `app/core/auth.py`'s `_get_or_sync_user` /
`_get_or_create_local_user`), so a nested `begin()` on it would either be a
no-op inside an already-closed transaction or silently share unrelated
uncommitted state. `principal_resolver.resolve_principal` is a pure read and
participates in whatever session it is handed; the functions below hand it
their own fresh session, resolved after that session's transaction has
started.

Concurrency and idempotency
----------------------------
`create_approval_request` has no parent row to lock: the atomic
insert-into-`mc_approval_operation` (via a SAVEPOINT + `IntegrityError`
fallback, see `_reserve_or_get_operation`) *is* its serialization point.
`submit_decision` and `supersede_decision` are request-bound: they acquire a
`SELECT ... FOR UPDATE` lock on the target `mc_approval_request` row first,
and only then perform the idempotency reservation and the write — so two
concurrent calls racing on the same request serialize on that row lock, and
the idempotency check inside the critical section sees a consistent view.

Idempotency scope is a closed, internal enum (`IdempotencyScope`), never a
caller-supplied string: callers only ever supply the idempotency key itself.
`response_snapshot` stores the plain JSON-compatible mapping directly;
`json.dumps` is used only to compute `payload_hash`, never for the stored
snapshot (avoiding double-serialization).

Human-only manual path
-----------------------
Every function here is the *manual* path: an authenticated `AuthContext`
resolved to a principal via `resolve_principal`. Regardless of a policy's
`allowed_approver_principal_types` configuration, and regardless of whether a
misconfigured `mc_principal` row links a `system`/`ai`-typed principal to a
resolvable identity, this path structurally rejects any principal whose
`principal_type != "human"` (`_require_human_manual_actor`). System-created
approvals and AI participation are out of scope for this checkpoint entirely
(system creation is deferred to Checkpoint E's internal trigger mechanism;
AI approval is deferred to a future slice per the Human's Option B ruling)
and never route through this module's public functions on the actor side.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import AuthContext
from app.core.time import utcnow
from app.db.session import async_session_maker
from app.mission.approval_evaluator import EffectiveDecision, evaluate_approval
from app.mission.approval_policy import ApprovalPolicyDefinition
from app.mission.approval_system_principal import resolve_system_principal
from app.mission.principal_resolver import ResolvedPrincipal, resolve_principal
from app.models.mc_approval import (
    McApprovalDecision,
    McApprovalEvent,
    McApprovalOperation,
    McApprovalPolicy,
    McApprovalPolicyActivation,
    McApprovalRequest,
)

ApprovalServiceErrorCode = Literal[
    "principal_not_human",
    "policy_not_found",
    "policy_invalid",
    "principal_not_authorized",
    "principal_trust_insufficient",
    "request_not_found",
    "request_not_open",
    "decision_not_found",
    "approval_decision_exists",
    "invalid_supersede",
    "idempotency_key_reused_with_different_payload",
]


class ApprovalServiceError(Exception):
    """Raised for every rejected approval command, with a closed error-code taxonomy."""

    def __init__(self, code: ApprovalServiceErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class IdempotencyScope(str, Enum):
    """Closed set of idempotency namespaces. Never caller-supplied."""

    CREATE_REQUEST = "create_request"
    SUBMIT_DECISION = "submit_decision"
    SUPERSEDE_DECISION = "supersede_decision"


_TERMINAL_STATUSES = frozenset({"approved", "rejected", "expired", "superseded"})


@dataclass(frozen=True, slots=True)
class RequestResult:
    """Frozen, replay-stable result of `create_approval_request`."""

    request_id: UUID
    policy_key: str
    policy_version: int
    status: str
    created_by_principal_id: UUID
    created_at: datetime
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class DecisionResult:
    """Frozen, replay-stable result of `submit_decision` / `supersede_decision`."""

    request_id: UUID
    decision_id: UUID
    principal_id: UUID
    decision: str
    reason: str | None
    status: str
    quorum_satisfied: bool
    mission_effect: str | None
    created_at: datetime


def _to_request_result(request: McApprovalRequest, policy: McApprovalPolicy) -> RequestResult:
    """Build a `RequestResult` from a persisted request + its (pinned) policy row.

    Shared by the human-manual path (`create_approval_request`) and the
    system-trigger path (`create_system_approval_request` /
    `_create_system_approval_request_in_session`) so the two never drift on
    what fields a "created request" response reports.
    """
    return RequestResult(
        request_id=request.id,
        policy_key=policy.policy_key,
        policy_version=policy.version,
        status=request.status,
        created_by_principal_id=request.created_by_principal_id,
        created_at=request.created_at,
        expires_at=request.expires_at,
    )


def _request_result_to_snapshot(result: RequestResult) -> dict[str, Any]:
    return {
        "request_id": str(result.request_id),
        "policy_key": result.policy_key,
        "policy_version": result.policy_version,
        "status": result.status,
        "created_by_principal_id": str(result.created_by_principal_id),
        "created_at": result.created_at.isoformat(),
        "expires_at": result.expires_at.isoformat() if result.expires_at else None,
    }


def _request_result_from_snapshot(snapshot: dict[str, Any]) -> RequestResult:
    return RequestResult(
        request_id=UUID(snapshot["request_id"]),
        policy_key=snapshot["policy_key"],
        policy_version=snapshot["policy_version"],
        status=snapshot["status"],
        created_by_principal_id=UUID(snapshot["created_by_principal_id"]),
        created_at=datetime.fromisoformat(snapshot["created_at"]),
        expires_at=(
            datetime.fromisoformat(snapshot["expires_at"]) if snapshot["expires_at"] else None
        ),
    )


def _decision_result_to_snapshot(result: DecisionResult) -> dict[str, Any]:
    return {
        "request_id": str(result.request_id),
        "decision_id": str(result.decision_id),
        "principal_id": str(result.principal_id),
        "decision": result.decision,
        "reason": result.reason,
        "status": result.status,
        "quorum_satisfied": result.quorum_satisfied,
        "mission_effect": result.mission_effect,
        "created_at": result.created_at.isoformat(),
    }


def _decision_result_from_snapshot(snapshot: dict[str, Any]) -> DecisionResult:
    return DecisionResult(
        request_id=UUID(snapshot["request_id"]),
        decision_id=UUID(snapshot["decision_id"]),
        principal_id=UUID(snapshot["principal_id"]),
        decision=snapshot["decision"],
        reason=snapshot["reason"],
        status=snapshot["status"],
        quorum_satisfied=snapshot["quorum_satisfied"],
        mission_effect=snapshot["mission_effect"],
        created_at=datetime.fromisoformat(snapshot["created_at"]),
    )


def _canonical_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_human_manual_actor(principal: ResolvedPrincipal) -> None:
    # Structural, policy-independent gate: the manual/API-authenticated path
    # is human-only regardless of what any policy's
    # allowed_approver_principal_types configures, and regardless of whether
    # a misconfigured mc_principal row links a non-human principal_type to a
    # resolvable AuthContext identity.
    if principal.principal_type != "human":
        raise ApprovalServiceError(
            "principal_not_human",
            f"principal {principal.id} has principal_type={principal.principal_type!r}; "
            "the manual approval path accepts human principals only",
        )


async def _reserve_or_get_operation(
    session: AsyncSession,
    *,
    idempotency_key: str,
    principal_id: UUID,
    scope: IdempotencyScope,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Reserve an idempotency slot, or return the prior response snapshot if replayed.

    Returns `None` if this call reserved the slot (caller must proceed and
    later call `_finalize_operation`), or the stored `response_snapshot` dict
    if an identical operation was already recorded. Raises
    `ApprovalServiceError` if the same key+principal+scope was already used
    with a *different* payload.
    """
    payload_hash = _canonical_hash(payload)
    try:
        async with session.begin_nested():
            session.add(
                McApprovalOperation(
                    idempotency_key=idempotency_key,
                    principal_id=principal_id,
                    endpoint=scope.value,
                    payload_hash=payload_hash,
                    response_snapshot={},
                    created_at=utcnow(),
                )
            )
            await session.flush()
        return None
    except IntegrityError:
        existing = (
            await session.exec(
                select(McApprovalOperation).where(
                    McApprovalOperation.idempotency_key == idempotency_key,
                    McApprovalOperation.principal_id == principal_id,
                    McApprovalOperation.endpoint == scope.value,
                )
            )
        ).first()
        if (
            existing is None
        ):  # pragma: no cover - lost race with a delete, not reachable in practice
            raise
        if existing.payload_hash != payload_hash:
            raise ApprovalServiceError(
                "idempotency_key_reused_with_different_payload",
                f"idempotency key {idempotency_key!r} was already used for {scope.value!r} "
                "with a different request payload",
            ) from None
        return existing.response_snapshot


async def _finalize_operation(
    session: AsyncSession,
    *,
    idempotency_key: str,
    principal_id: UUID,
    scope: IdempotencyScope,
    response_snapshot: dict[str, Any],
) -> None:
    op = (
        await session.exec(
            select(McApprovalOperation).where(
                McApprovalOperation.idempotency_key == idempotency_key,
                McApprovalOperation.principal_id == principal_id,
                McApprovalOperation.endpoint == scope.value,
            )
        )
    ).first()
    assert op is not None  # reserved by _reserve_or_get_operation earlier in this transaction
    op.response_snapshot = response_snapshot
    session.add(op)


def validate_policy_definition(policy: McApprovalPolicy) -> ApprovalPolicyDefinition:
    try:
        return ApprovalPolicyDefinition.model_validate(policy.definition)
    except ValidationError as exc:
        raise ApprovalServiceError(
            "policy_invalid",
            f"stored policy {policy.id} ({policy.policy_key!r} v{policy.version}) "
            f"failed schema validation: {exc}",
        ) from exc


async def _load_active_policy(
    session: AsyncSession, policy_key: str
) -> tuple[McApprovalPolicy, ApprovalPolicyDefinition]:
    activation = await session.get(McApprovalPolicyActivation, policy_key)
    if activation is None:
        raise ApprovalServiceError(
            "policy_not_found", f"no active policy for policy_key={policy_key!r}"
        )
    policy = await session.get(McApprovalPolicy, activation.active_policy_id)
    if policy is None:  # pragma: no cover - guarded by the composite FK at the DB level
        raise ApprovalServiceError(
            "policy_not_found", f"no active policy for policy_key={policy_key!r}"
        )
    return policy, validate_policy_definition(policy)


def _authorize_decision(principal: ResolvedPrincipal, definition: ApprovalPolicyDefinition) -> None:
    if principal.principal_type not in definition.allowed_approver_principal_types:
        raise ApprovalServiceError(
            "principal_not_authorized",
            f"principal_type {principal.principal_type!r} is not an allowed approver type for this policy",
        )
    if not principal.role_slugs & set(definition.allowed_approver_roles):
        raise ApprovalServiceError(
            "principal_not_authorized",
            f"principal {principal.id} holds none of this policy's allowed_approver_roles",
        )
    if definition.trust_requirements and principal.trust_level not in definition.trust_requirements:
        raise ApprovalServiceError(
            "principal_trust_insufficient",
            f"principal {principal.id} has trust_level={principal.trust_level!r}, "
            f"not in this policy's trust_requirements {definition.trust_requirements!r}",
        )


def can_principal_decide(
    *,
    principal: ResolvedPrincipal,
    request: McApprovalRequest,
    definition: ApprovalPolicyDefinition,
) -> bool:
    """Shared read/write decision-eligibility signal (Slice 5B Checkpoint A).

    This is the *only* place `can_decide` is computed: it directly reuses
    `_require_human_manual_actor` and `_authorize_decision` -- the exact two
    checks `submit_decision` and `supersede_decision` both call, in the same
    order, before writing -- plus the same request-pending gate those two
    functions apply before authorizing at all. No second/independent
    eligibility algorithm exists anywhere in `approval_read_service.py`.

    `_require_human_manual_actor` must run here too, not just
    `_authorize_decision`: a policy may list `"system"` in
    `allowed_approver_principal_types` (e.g. for Checkpoint E's system
    trigger path), which `_authorize_decision` alone would accept, but the
    *manual* mutation path this read-only signal mirrors unconditionally
    rejects any non-human principal via `_require_human_manual_actor` before
    it ever reaches `_authorize_decision`. Skipping that gate here would let
    a resolved system/ai principal see `can_decide=True` for a decision the
    mutation route would reject with `principal_not_human`.

    This is a UX capability signal only. Every mutation command still calls
    `_require_human_manual_actor` and `_authorize_decision` (and re-checks
    `request.status`) itself at command time under its own row lock; a
    `True` result here is never treated as authorization by any write path.
    """
    if request.status != "pending":
        return False
    try:
        _require_human_manual_actor(principal)
        _authorize_decision(principal, definition)
    except ApprovalServiceError:
        return False
    return True


async def effective_decisions(session: AsyncSession, request_id: UUID) -> list[McApprovalDecision]:
    all_decisions = (
        await session.exec(
            select(McApprovalDecision).where(McApprovalDecision.request_id == request_id)
        )
    ).all()
    superseded_ids = {
        d.supersedes_decision_id for d in all_decisions if d.supersedes_decision_id is not None
    }
    return [d for d in all_decisions if d.id not in superseded_ids]


async def _finalize_decision(
    session: AsyncSession,
    *,
    request: McApprovalRequest,
    definition: ApprovalPolicyDefinition,
    new_decision: McApprovalDecision,
) -> DecisionResult:
    """Persist a new decision row, re-evaluate the request, and append lifecycle events.

    Shared by `submit_decision` (a new principal voting) and
    `supersede_decision` (replacing a principal's own prior vote): both
    insert one new `McApprovalDecision` row and then re-derive the request's
    full effective decision set from scratch, rather than maintaining
    separate incremental-update code paths.
    """
    session.add(new_decision)
    await session.flush()

    effective = await effective_decisions(session, request.id)
    evaluation = evaluate_approval(
        definition,
        [
            EffectiveDecision(
                principal_id=d.principal_id,
                decision=d.decision,  # type: ignore[arg-type]
                role_slugs_at_decision=frozenset(d.role_slugs_at_decision),
                trust_level_at_decision=d.trust_level_at_decision,
            )
            for d in effective
        ],
        utcnow(),
    )

    if evaluation.status != "pending" and request.status == "pending":
        request.status = evaluation.status
        request.resolved_at = utcnow()
        session.add(request)
        session.add(
            McApprovalEvent(
                request_id=request.id,
                event_type=f"request_{evaluation.status}",
                triggered_by_principal_id=new_decision.principal_id,
                detail={
                    "quorum_satisfied": evaluation.quorum_satisfied,
                    "mission_effect": evaluation.mission_effect,
                    "reason": evaluation.reason,
                },
                created_at=utcnow(),
            )
        )
    elif evaluation.quorum_satisfied:
        session.add(
            McApprovalEvent(
                request_id=request.id,
                event_type="quorum_reached",
                triggered_by_principal_id=new_decision.principal_id,
                detail={"reason": evaluation.reason},
                created_at=utcnow(),
            )
        )

    await session.flush()
    return DecisionResult(
        request_id=request.id,
        decision_id=new_decision.id,
        principal_id=new_decision.principal_id,
        decision=new_decision.decision,
        reason=new_decision.reason,
        status=request.status,
        quorum_satisfied=evaluation.quorum_satisfied,
        mission_effect=evaluation.mission_effect,
        created_at=new_decision.created_at,
    )


async def create_approval_request(
    auth: AuthContext,
    *,
    policy_key: str,
    scope_type: str,
    mission_source_repo: str,
    mission_card_kind: str,
    mission_card_number: int,
    action_key: str | None,
    expires_at: datetime | None,
    idempotency_key: str,
) -> RequestResult:
    """Create a new approval request under the currently active version of `policy_key`.

    Human-only manual creation. There is no parent row to lock: the atomic
    reservation of `idempotency_key` in `mc_approval_operation` is this
    function's sole serialization point, so two concurrent calls with the
    same key race on that insert, and exactly one creates the request.
    """
    payload = {
        "policy_key": policy_key,
        "scope_type": scope_type,
        "mission_source_repo": mission_source_repo,
        "mission_card_kind": mission_card_kind,
        "mission_card_number": mission_card_number,
        "action_key": action_key,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }

    async with async_session_maker() as session, session.begin():
        principal = await resolve_principal(auth, session)
        _require_human_manual_actor(principal)

        replayed = await _reserve_or_get_operation(
            session,
            idempotency_key=idempotency_key,
            principal_id=principal.id,
            scope=IdempotencyScope.CREATE_REQUEST,
            payload=payload,
        )
        if replayed is not None:
            return _request_result_from_snapshot(replayed)

        policy, definition = await _load_active_policy(session, policy_key)
        if principal.principal_type not in definition.allowed_approver_principal_types:
            raise ApprovalServiceError(
                "principal_not_authorized",
                f"principal_type {principal.principal_type!r} may not create requests under this policy",
            )

        request = McApprovalRequest(
            policy_id=policy.id,
            scope_type=scope_type,
            mission_source_repo=mission_source_repo,
            mission_card_kind=mission_card_kind,
            mission_card_number=mission_card_number,
            action_key=action_key,
            created_by_principal_id=principal.id,
            creation_source="human",
            status="pending",
            created_at=utcnow(),
            expires_at=expires_at,
        )
        session.add(request)
        await session.flush()
        session.add(
            McApprovalEvent(
                request_id=request.id,
                event_type="request_created",
                triggered_by_principal_id=principal.id,
                detail=None,
                created_at=utcnow(),
            )
        )

        result = _to_request_result(request, policy)
        await _finalize_operation(
            session,
            idempotency_key=idempotency_key,
            principal_id=principal.id,
            scope=IdempotencyScope.CREATE_REQUEST,
            response_snapshot=_request_result_to_snapshot(result),
        )
        return result


async def submit_decision(
    auth: AuthContext,
    *,
    request_id: UUID,
    decision: Literal["approve", "reject"],
    reason: str | None,
    idempotency_key: str,
) -> DecisionResult:
    """Cast a new decision on an open request. One principal may hold only one effective vote.

    Locks the target request row (`SELECT ... FOR UPDATE`) before the
    idempotency reservation, so concurrent calls on the same request
    serialize on that lock and the idempotency check runs inside a
    consistent view of the request.
    """
    payload = {"request_id": str(request_id), "decision": decision, "reason": reason}

    async with async_session_maker() as session, session.begin():
        principal = await resolve_principal(auth, session)
        _require_human_manual_actor(principal)

        request = (
            await session.exec(
                select(McApprovalRequest)
                .where(col(McApprovalRequest.id) == request_id)
                .with_for_update()
            )
        ).first()
        if request is None:
            raise ApprovalServiceError("request_not_found", f"no approval request {request_id}")

        replayed = await _reserve_or_get_operation(
            session,
            idempotency_key=idempotency_key,
            principal_id=principal.id,
            scope=IdempotencyScope.SUBMIT_DECISION,
            payload=payload,
        )
        if replayed is not None:
            return _decision_result_from_snapshot(replayed)

        if request.status != "pending":
            raise ApprovalServiceError(
                "request_not_open",
                f"request {request_id} is not open for decisions (status={request.status!r})",
            )

        policy = await session.get(McApprovalPolicy, request.policy_id)
        assert policy is not None  # guarded by FK
        definition = validate_policy_definition(policy)
        _authorize_decision(principal, definition)

        existing_effective = await effective_decisions(session, request_id)
        if any(d.principal_id == principal.id for d in existing_effective):
            raise ApprovalServiceError(
                "approval_decision_exists",
                f"principal {principal.id} already has an effective decision on request {request_id}; "
                "use supersede_decision to change it",
            )

        new_decision = McApprovalDecision(
            request_id=request_id,
            principal_id=principal.id,
            decision=decision,
            reason=reason,
            role_slugs_at_decision=sorted(principal.role_slugs),
            trust_level_at_decision=principal.trust_level,
            created_at=utcnow(),
        )
        result = await _finalize_decision(
            session, request=request, definition=definition, new_decision=new_decision
        )
        await _finalize_operation(
            session,
            idempotency_key=idempotency_key,
            principal_id=principal.id,
            scope=IdempotencyScope.SUBMIT_DECISION,
            response_snapshot=_decision_result_to_snapshot(result),
        )
        return result


async def supersede_decision(
    auth: AuthContext,
    *,
    request_id: UUID,
    decision_id: UUID,
    decision: Literal["approve", "reject"],
    reason: str | None,
    idempotency_key: str,
) -> DecisionResult:
    """Replace the caller's own prior effective decision on an open request with a new one."""
    payload = {
        "request_id": str(request_id),
        "decision_id": str(decision_id),
        "decision": decision,
        "reason": reason,
    }

    async with async_session_maker() as session, session.begin():
        principal = await resolve_principal(auth, session)
        _require_human_manual_actor(principal)

        request = (
            await session.exec(
                select(McApprovalRequest)
                .where(col(McApprovalRequest.id) == request_id)
                .with_for_update()
            )
        ).first()
        if request is None:
            raise ApprovalServiceError("request_not_found", f"no approval request {request_id}")

        replayed = await _reserve_or_get_operation(
            session,
            idempotency_key=idempotency_key,
            principal_id=principal.id,
            scope=IdempotencyScope.SUPERSEDE_DECISION,
            payload=payload,
        )
        if replayed is not None:
            return _decision_result_from_snapshot(replayed)

        if request.status != "pending":
            raise ApprovalServiceError(
                "request_not_open",
                f"request {request_id} is not open for decisions (status={request.status!r})",
            )

        prior = await session.get(McApprovalDecision, decision_id)
        if prior is None or prior.request_id != request_id:
            raise ApprovalServiceError(
                "decision_not_found", f"no decision {decision_id} on request {request_id}"
            )
        if prior.principal_id != principal.id:
            raise ApprovalServiceError(
                "invalid_supersede", "a principal may only supersede its own decision"
            )

        effective = await effective_decisions(session, request_id)
        if prior.id not in {d.id for d in effective}:
            raise ApprovalServiceError(
                "invalid_supersede", f"decision {decision_id} has already been superseded"
            )

        policy = await session.get(McApprovalPolicy, request.policy_id)
        assert policy is not None  # guarded by FK
        definition = validate_policy_definition(policy)
        _authorize_decision(principal, definition)

        new_decision = McApprovalDecision(
            request_id=request_id,
            principal_id=principal.id,
            decision=decision,
            reason=reason,
            role_slugs_at_decision=sorted(principal.role_slugs),
            trust_level_at_decision=principal.trust_level,
            created_at=utcnow(),
            supersedes_decision_id=prior.id,
        )
        result = await _finalize_decision(
            session, request=request, definition=definition, new_decision=new_decision
        )
        await _finalize_operation(
            session,
            idempotency_key=idempotency_key,
            principal_id=principal.id,
            scope=IdempotencyScope.SUPERSEDE_DECISION,
            response_snapshot=_decision_result_to_snapshot(result),
        )
        return result


# ---------------------------------------------------------------------------
# Checkpoint E: trusted system-trigger creation path.
#
# Distinct from the idempotency mechanism above in every respect: there is
# no HTTP caller, no Idempotency-Key, and no `mc_approval_operation` row
# involved here. Duplicate/replay suppression is instead the
# `trigger_key`/`recreate:` deterministic-key + database-unique-index
# mechanism (see `_create_system_approval_request_in_session`'s
# IntegrityError fallback below) -- a wholly separate idempotency
# mechanism, per design, never touching Checkpoint C/D's.
# ---------------------------------------------------------------------------


def _next_recreate_trigger_key(predecessor: McApprovalRequest) -> str:
    """Deterministic trigger_key for a bounded-recreate successor.

    Never stacks `|retry:` suffixes. `n` here must always be identical to
    the `auto_retry_count` the caller separately assigns to the successor
    row (both are `predecessor.auto_retry_count + 1`, read once from the
    same locked row within the same critical section -- see
    `approval_reconciliation.py`'s recreate branch and the cross-check test
    that proves the two never drift apart).
    """
    n = predecessor.auto_retry_count + 1
    if predecessor.trigger_key is None:
        # Human-created original: this predecessor itself is the chain's root.
        return f"recreate:{predecessor.id}|retry:{n}"
    if predecessor.trigger_key.startswith("recreate:"):
        # A prior retry of a human-origin chain: carry the same root
        # forward -- never re-derive it from this retry's own id.
        root = predecessor.trigger_key.removeprefix("recreate:").split("|", 1)[0]
        return f"recreate:{root}|retry:{n}"
    # System/head-origin request (trigger_key starts with "mission:"):
    # strip any existing "|retry:<k>" suffix to recover the base
    # mission-key, then append exactly one fresh suffix.
    base = predecessor.trigger_key.split("|retry:", 1)[0]
    return f"{base}|retry:{n}"


async def _create_system_approval_request_in_session(
    session: AsyncSession,
    *,
    principal: ResolvedPrincipal,
    policy: McApprovalPolicy,
    definition: ApprovalPolicyDefinition,
    scope_type: str,
    mission_source_repo: str,
    mission_card_kind: str,
    mission_card_number: int,
    action_key: str | None,
    expires_at: datetime | None,
    trigger_key: str,
    supersedes_request_id: UUID | None,
    predecessor_to_supersede: McApprovalRequest | None,
    auto_retry_count: int,
    created_at: datetime | None = None,
) -> RequestResult:
    """Pure in-session core of system-trigger request creation.

    Never opens or commits a transaction -- the caller owns that. This is
    what makes it safe to call from inside reconciliation's per-row
    transaction (recreate: predecessor-expire + successor-insert must be
    atomic) or a trigger's own transaction (stale-head: predecessor-
    supersede + successor-insert must be atomic), rather than only from a
    fresh session.

    `created_at` defaults to a fresh `utcnow()` reading when omitted (the
    ordinary trigger-observation case). Reconciliation's recreate branch
    passes its own already-computed `now` explicitly instead, so the
    successor's `expires_at - created_at` exactly equals the preserved TTL
    window rather than drifting by the microseconds between two separate
    `utcnow()` calls.
    """
    if principal.principal_type != "system":
        # Enforced here, independent of and in addition to the
        # policy-authorization check below -- any future direct internal
        # caller of this function must be rejected for a non-system
        # principal even if the target policy happens to permit "system"
        # creators, since this function unconditionally persists
        # creation_source="system". Checked first, before any mutation.
        raise ApprovalServiceError(
            "principal_not_authorized",
            f"principal {principal.id} has principal_type={principal.principal_type!r}; "
            "system-created requests require a system principal",
        )
    if "system" not in definition.allowed_approver_principal_types:
        raise ApprovalServiceError(
            "principal_not_authorized",
            f"policy {policy.policy_key!r} does not permit system-created requests",
        )

    if predecessor_to_supersede is not None:
        # Caller has already SELECT ... FOR UPDATE'd this row and rechecked
        # it is still "pending" -- this function trusts that lock; it does
        # not re-acquire or re-check it itself.
        predecessor_to_supersede.status = "superseded"
        predecessor_to_supersede.resolved_at = utcnow()
        session.add(predecessor_to_supersede)
        session.add(
            McApprovalEvent(
                request_id=predecessor_to_supersede.id,
                event_type="request_superseded",
                triggered_by_principal_id=principal.id,
                detail={"reason": "newer_trigger_observed", "trigger_key": trigger_key},
                created_at=utcnow(),
            )
        )
        await session.flush()

    request_created_at = created_at if created_at is not None else utcnow()
    try:
        async with session.begin_nested():
            request = McApprovalRequest(
                policy_id=policy.id,
                scope_type=scope_type,
                mission_source_repo=mission_source_repo,
                mission_card_kind=mission_card_kind,
                mission_card_number=mission_card_number,
                action_key=action_key,
                created_by_principal_id=principal.id,
                creation_source="system",
                status="pending",
                created_at=request_created_at,
                expires_at=expires_at,
                supersedes_request_id=supersedes_request_id,
                trigger_key=trigger_key,
                auto_retry_count=auto_retry_count,
            )
            session.add(request)
            await session.flush()
        session.add(
            McApprovalEvent(
                request_id=request.id,
                event_type="request_created",
                triggered_by_principal_id=principal.id,
                detail={"trigger_key": trigger_key},
                created_at=utcnow(),
            )
        )
        await session.flush()
        return _to_request_result(request, policy)
    except IntegrityError:
        # trigger_key already exists -- either a genuine prior request for
        # this exact logical event, or a concurrent racer that just won.
        existing = (
            await session.exec(
                select(McApprovalRequest).where(McApprovalRequest.trigger_key == trigger_key)
            )
        ).first()
        if (
            existing is None
        ):  # pragma: no cover - lost race with a delete, not reachable in practice
            raise
        return _to_request_result(existing, policy)


async def create_system_approval_request(
    *,
    policy_key: str,
    scope_type: str,
    mission_source_repo: str,
    mission_card_kind: str,
    mission_card_number: int,
    action_key: str | None,
    expires_at: datetime | None,
    trigger_key: str,
    supersedes_request_id: UUID | None = None,
    created_at: datetime | None = None,
) -> RequestResult:
    """Fresh-session public wrapper for system-trigger request creation.

    Used only by callers with no already-open transaction of their own --
    first-observation and terminal-predecessor trigger cases, which need no
    cross-row atomicity beyond the single insert itself. Reconciliation's
    recreate branch and a trigger's stale-head-supersession branch call
    `_create_system_approval_request_in_session` directly inside their own
    already-open transaction instead (see `approval_reconciliation.py` /
    `approval_triggers.py`) -- never this wrapper, which would open a
    second, separate transaction and break the atomicity those two cases
    require.

    Always creates with `auto_retry_count=0`: a fresh trigger observation
    is never itself a retry. `created_at` defaults to a fresh `utcnow()`
    reading when omitted; pass it explicitly (paired with an `expires_at`
    computed from that same value) so `expires_at - created_at` equals the
    caller's intended TTL exactly, rather than drifting by the microseconds
    between two separate `utcnow()` calls.
    """
    async with async_session_maker() as session, session.begin():
        principal = await resolve_system_principal(session)
        policy, definition = await _load_active_policy(session, policy_key)
        return await _create_system_approval_request_in_session(
            session,
            principal=principal,
            policy=policy,
            definition=definition,
            scope_type=scope_type,
            mission_source_repo=mission_source_repo,
            mission_card_kind=mission_card_kind,
            mission_card_number=mission_card_number,
            action_key=action_key,
            expires_at=expires_at,
            trigger_key=trigger_key,
            supersedes_request_id=supersedes_request_id,
            predecessor_to_supersede=None,
            auto_retry_count=0,
            created_at=created_at,
        )
