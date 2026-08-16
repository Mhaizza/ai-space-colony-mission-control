"""Mission Control's own internal principal identity types (Slice 5A Checkpoint B).

This module holds pure types only — the persisted `PrincipalType` union and a
plain, non-persistent dataclass view of a principal plus its role set. It
does not resolve a principal from an authenticated request (that is
`principal_resolver.py`, Checkpoint C) and does not authorize anything.

`PrincipalType` is deliberately wider than who may currently *approve*
something: `ai` remains a valid persisted principal type (for future data
modeling and for `system`-created records attributed to an AI-authored
source), but Slice 5A's Human ruling (Option B) restricts eligible approvers
to `human` and `system` only — see `approval_policy.ApproverPrincipalType`,
which is the narrower, approver-eligible subset enforced by the policy
schema itself. Authenticated AI approvers are deferred to a later slice,
which will need its own Human-approved scope decision before widening that
narrower type.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

PrincipalType = Literal["human", "ai", "system"]


@dataclass(frozen=True, slots=True)
class PrincipalView:
    """Read-only view of a principal and its current role set.

    Not an ORM model — a plain value type for passing principal identity and
    role membership between the (not-yet-built) resolver, the evaluator, and
    tests, without coupling those to SQLModel/session details.
    """

    id: UUID
    principal_type: PrincipalType
    display_name: str
    trust_level: str
    enabled: bool
    role_slugs: frozenset[str]
