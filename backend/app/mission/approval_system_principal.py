"""Trusted internal system principal (Slice 5A Checkpoint E).

The one reserved `mc_principal` row that Checkpoint E's reconciliation tick
and automatic-trigger evaluator act as. `resolve_system_principal` is
structurally unreachable from any HTTP-authenticated path:
`principal_resolver._external_identity` (the only function that ever turns
an `AuthContext` into an `mc_principal` lookup key) hardcodes its provider to
`"clerk"` or `"local"` and its subject to `auth.user.clerk_user_id` -- it can
never produce `(SYSTEM_PROVIDER, SYSTEM_SUBJECT)`. No new check is required
to prevent HTTP impersonation of the system principal; there is simply no
code path that could ever construct that lookup key from an `AuthContext`.

Participates in whatever transaction the caller already has open -- never
begins or commits one of its own, exactly like `principal_resolver.
resolve_principal`. Safe to call from inside reconciliation's per-row
transaction, a trigger's own transaction, or a fresh-session wrapper's
transaction alike.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.mission.principal_resolver import ResolvedPrincipal
from app.models.mc_approval import McPrincipal

SYSTEM_PROVIDER = "system"
SYSTEM_SUBJECT = "mission-control-lifecycle-automation"


def _to_resolved(principal: McPrincipal) -> ResolvedPrincipal:
    return ResolvedPrincipal(
        id=principal.id,
        principal_type=principal.principal_type,
        display_name=principal.display_name,
        trust_level=principal.trust_level,
        enabled=principal.enabled,
        role_slugs=frozenset(),
    )


async def resolve_system_principal(session: AsyncSession) -> ResolvedPrincipal:
    """Get-or-create the one reserved system principal.

    Mirrors `approval_service._reserve_or_get_operation`'s established
    shape exactly: an optimistic `SELECT` first, then a `begin_nested()`
    (SAVEPOINT)-guarded insert attempt, catching `IntegrityError` from that
    nested transaction only (never the caller's outer transaction) before
    re-selecting the row a concurrent racer in a *different* transaction
    won. Never commits.
    """
    existing = (
        await session.exec(
            select(McPrincipal).where(
                McPrincipal.external_provider == SYSTEM_PROVIDER,
                McPrincipal.external_subject == SYSTEM_SUBJECT,
            )
        )
    ).first()
    if existing is not None:
        return _to_resolved(existing)

    try:
        async with session.begin_nested():
            principal = McPrincipal(
                principal_type="system",
                display_name="Mission Control Lifecycle Automation",
                trust_level="trusted",
                enabled=True,
                external_provider=SYSTEM_PROVIDER,
                external_subject=SYSTEM_SUBJECT,
            )
            session.add(principal)
            await session.flush()
        return _to_resolved(principal)
    except IntegrityError:
        existing = (
            await session.exec(
                select(McPrincipal).where(
                    McPrincipal.external_provider == SYSTEM_PROVIDER,
                    McPrincipal.external_subject == SYSTEM_SUBJECT,
                )
            )
        ).first()
        if (
            existing is None
        ):  # pragma: no cover - lost race with a delete, not reachable in practice
            raise
        return _to_resolved(existing)
