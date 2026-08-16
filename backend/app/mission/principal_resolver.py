"""Principal identity resolution from authenticated identity (Slice 5A Checkpoint C).

`resolve_principal` is a pure identity lookup: it maps the caller's
authenticated `AuthContext` to a registered `mc_principal` row and its
current role set. It never accepts, trusts, or infers a principal identity,
role, or trust level from anything the client supplies — those values are
always read from the database row matched by server-verified authenticated
identity alone.

This module participates in whatever transaction the caller already has
open (a dedicated, freshly-opened `AsyncSession` per approval command — see
`approval_service.py`'s module docstring for why that must not be the
request-scoped session `get_auth_context()` may have already committed on).
It never calls `session.begin()` or `session.commit()` itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import AuthContext
from app.core.auth_mode import AuthMode
from app.core.config import settings
from app.models.mc_approval import McPrincipal, McPrincipalRole

PrincipalErrorCode = Literal["principal_not_registered", "principal_disabled"]


class PrincipalResolutionError(Exception):
    """Raised when the authenticated caller cannot be resolved to an active principal."""

    def __init__(self, code: PrincipalErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ResolvedPrincipal:
    """A registered principal and its current role set, as of resolution time."""

    id: UUID
    principal_type: str
    display_name: str
    trust_level: str
    enabled: bool
    role_slugs: frozenset[str]


def _external_identity(auth: AuthContext) -> tuple[str, str]:
    """Derive the (provider, subject) tuple mc_principal is keyed on, from AuthContext alone."""
    if auth.user is None:
        raise PrincipalResolutionError(
            "principal_not_registered", "no authenticated user on AuthContext"
        )
    provider = "clerk" if settings.auth_mode == AuthMode.CLERK else "local"
    return provider, auth.user.clerk_user_id


async def resolve_principal(auth: AuthContext, session: AsyncSession) -> ResolvedPrincipal:
    """Resolve the authenticated caller to a registered, enabled mc_principal + role set.

    Raises `PrincipalResolutionError` with code `principal_not_registered` if no
    mc_principal row matches the authenticated identity, or `principal_disabled`
    if a matching row exists but `enabled=False`.
    """
    provider, subject = _external_identity(auth)
    principal = (
        await session.exec(
            select(McPrincipal).where(
                McPrincipal.external_provider == provider,
                McPrincipal.external_subject == subject,
            )
        )
    ).first()
    if principal is None:
        raise PrincipalResolutionError(
            "principal_not_registered",
            f"no mc_principal registered for external identity {provider}:{subject}",
        )
    if not principal.enabled:
        raise PrincipalResolutionError(
            "principal_disabled", f"principal {principal.id} is disabled"
        )

    role_rows = (
        await session.exec(
            select(McPrincipalRole.role_slug).where(McPrincipalRole.principal_id == principal.id)
        )
    ).all()

    return ResolvedPrincipal(
        id=principal.id,
        principal_type=principal.principal_type,
        display_name=principal.display_name,
        trust_level=principal.trust_level,
        enabled=principal.enabled,
        role_slugs=frozenset(role_rows),
    )
