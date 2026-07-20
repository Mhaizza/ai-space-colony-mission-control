"""Author authorization against the principal registry (ADR-23 D4)."""

from __future__ import annotations

from dataclasses import dataclass

from app.mission.principal_registry import PrincipalEntry, PrincipalRegistry
from app.mission.types import QuarantineReason
from app.mission.workflow_record import ParsedWorkflowRecord


@dataclass(frozen=True, slots=True)
class AuthDecision:
    ok: bool
    reason: QuarantineReason | None
    message: str
    principal: PrincipalEntry | None = None


def authorize_record_author(
    *,
    record: ParsedWorkflowRecord,
    github_login: str,
    registry: PrincipalRegistry,
    effective_worker_identity: str | None = None,
    effective_role: str | None = None,
) -> AuthDecision:
    """Authorize a structurally valid record using authenticated GitHub author identity."""
    principal = registry.get(github_login)
    if principal is None:
        return AuthDecision(
            ok=False,
            reason=QuarantineReason.UNAUTHORIZED_AUTHOR,
            message=f"GitHub login {github_login!r} is not in the principal registry",
        )

    record_type = record.type
    if record_type == "start_task":
        return _authorize_start_task(record, principal, registry)
    if record_type == "handoff":
        return _authorize_handoff(record, principal, registry, effective_worker_identity)
    if record_type == "review_result":
        return _authorize_review_result(principal)
    if record_type == "human_approval":
        return _authorize_human_approval(principal)
    if record_type in {"kanban_update", "completion"}:
        return _authorize_kanban_or_completion(
            record,
            principal,
            effective_worker_identity=effective_worker_identity,
            effective_role=effective_role,
        )
    return AuthDecision(
        ok=False,
        reason=QuarantineReason.MALFORMED_RECORD,
        message=f"unknown record type for authorization: {record_type}",
        principal=principal,
    )


def _authorize_start_task(
    record: ParsedWorkflowRecord,
    principal: PrincipalEntry,
    registry: PrincipalRegistry,
) -> AuthDecision:
    assert record.worker is not None
    assert record.role is not None

    if principal.trust_class == "human":
        # Human may assign any registered worker/role pair.
        if record.worker not in registry.registered_worker_identities():
            return AuthDecision(
                ok=False,
                reason=QuarantineReason.UNAUTHORIZED_AUTHOR,
                message="start_task worker is not a registered Worker-class identity",
                principal=principal,
            )
        target_roles = _roles_for_worker(registry, record.worker)
        if record.role not in target_roles:
            return AuthDecision(
                ok=False,
                reason=QuarantineReason.INVALID_WORKER_ROLE_PAIR,
                message=(
                    f"role {record.role!r} is not allowlisted for worker {record.worker!r}"
                ),
                principal=principal,
            )
        return AuthDecision(ok=True, reason=None, message="ok", principal=principal)

    if principal.trust_class != "worker":
        return AuthDecision(
            ok=False,
            reason=QuarantineReason.UNAUTHORIZED_AUTHOR,
            message="start_task must be authored by a Worker or Human principal",
            principal=principal,
        )

    if record.worker not in principal.declarable_identities:
        return AuthDecision(
            ok=False,
            reason=QuarantineReason.UNAUTHORIZED_AUTHOR,
            message=(
                f"worker principal may not declare identity {record.worker!r} "
                f"(declarable={sorted(principal.declarable_identities)})"
            ),
            principal=principal,
        )
    if record.role not in principal.allowed_roles:
        return AuthDecision(
            ok=False,
            reason=QuarantineReason.INVALID_WORKER_ROLE_PAIR,
            message=(
                f"role {record.role!r} is not allowlisted for worker "
                f"{principal.worker_identity!r}"
            ),
            principal=principal,
        )
    return AuthDecision(ok=True, reason=None, message="ok", principal=principal)


def _authorize_handoff(
    record: ParsedWorkflowRecord,
    principal: PrincipalEntry,
    registry: PrincipalRegistry,
    effective_worker_identity: str | None,
) -> AuthDecision:
    assert record.worker is not None
    assert record.role is not None

    if principal.trust_class == "human":
        if record.worker not in registry.registered_worker_identities():
            return AuthDecision(
                ok=False,
                reason=QuarantineReason.UNAUTHORIZED_AUTHOR,
                message="handoff target is not a registered Worker-class identity",
                principal=principal,
            )
        if record.role not in _roles_for_worker(registry, record.worker):
            return AuthDecision(
                ok=False,
                reason=QuarantineReason.INVALID_WORKER_ROLE_PAIR,
                message=(
                    f"role {record.role!r} is not allowlisted for worker {record.worker!r}"
                ),
                principal=principal,
            )
        return AuthDecision(ok=True, reason=None, message="ok", principal=principal)

    if principal.trust_class != "worker":
        return AuthDecision(
            ok=False,
            reason=QuarantineReason.UNAUTHORIZED_AUTHOR,
            message="handoff must be authored by the effective Worker or Human principal",
            principal=principal,
        )

    if (
        effective_worker_identity is None
        or principal.worker_identity != effective_worker_identity
    ):
        return AuthDecision(
            ok=False,
            reason=QuarantineReason.UNAUTHORIZED_AUTHOR,
            message="handoff must be authored by the currently effective Worker principal",
            principal=principal,
        )

    if record.worker not in registry.registered_worker_identities():
        return AuthDecision(
            ok=False,
            reason=QuarantineReason.UNAUTHORIZED_AUTHOR,
            message="handoff target is not a registered Worker-class identity",
            principal=principal,
        )
    if record.role not in _roles_for_worker(registry, record.worker):
        return AuthDecision(
            ok=False,
            reason=QuarantineReason.INVALID_WORKER_ROLE_PAIR,
            message=f"role {record.role!r} is not allowlisted for worker {record.worker!r}",
            principal=principal,
        )
    return AuthDecision(ok=True, reason=None, message="ok", principal=principal)


def _authorize_review_result(principal: PrincipalEntry) -> AuthDecision:
    if principal.trust_class != "reviewer":
        return AuthDecision(
            ok=False,
            reason=QuarantineReason.UNAUTHORIZED_AUTHOR,
            message="review_result must be authored by a Reviewer principal",
            principal=principal,
        )
    return AuthDecision(ok=True, reason=None, message="ok", principal=principal)


def _authorize_human_approval(principal: PrincipalEntry) -> AuthDecision:
    if principal.trust_class != "human":
        return AuthDecision(
            ok=False,
            reason=QuarantineReason.UNAUTHORIZED_AUTHOR,
            message="human_approval must be authored by a Human principal",
            principal=principal,
        )
    return AuthDecision(ok=True, reason=None, message="ok", principal=principal)


def _authorize_kanban_or_completion(
    record: ParsedWorkflowRecord,
    principal: PrincipalEntry,
    *,
    effective_worker_identity: str | None,
    effective_role: str | None,
) -> AuthDecision:
    assert record.worker is not None
    assert record.role is not None

    if principal.trust_class == "human":
        if record.worker != "human" or record.role != "human-owner":
            return AuthDecision(
                ok=False,
                reason=QuarantineReason.INVALID_WORKER_ROLE_PAIR,
                message=(
                    f"{record.type} authored by Human requires worker=human and "
                    "role=human-owner"
                ),
                principal=principal,
            )
        return AuthDecision(ok=True, reason=None, message="ok", principal=principal)

    if principal.trust_class != "worker":
        return AuthDecision(
            ok=False,
            reason=QuarantineReason.UNAUTHORIZED_AUTHOR,
            message=f"{record.type} must be authored by effective Worker or Human",
            principal=principal,
        )

    if (
        effective_worker_identity is None
        or principal.worker_identity != effective_worker_identity
    ):
        return AuthDecision(
            ok=False,
            reason=QuarantineReason.UNAUTHORIZED_AUTHOR,
            message=f"{record.type} must be authored by the currently effective Worker",
            principal=principal,
        )
    if record.worker != effective_worker_identity or record.role != effective_role:
        return AuthDecision(
            ok=False,
            reason=QuarantineReason.INVALID_WORKER_ROLE_PAIR,
            message=(
                f"{record.type} worker/role must match the effective assignment "
                f"({effective_worker_identity}/{effective_role})"
            ),
            principal=principal,
        )
    return AuthDecision(ok=True, reason=None, message="ok", principal=principal)


def _roles_for_worker(registry: PrincipalRegistry, worker: str) -> frozenset[str]:
    roles: set[str] = set()
    for entry in registry.workers():
        if entry.worker_identity == worker:
            roles |= set(entry.allowed_roles)
    return frozenset(roles)
