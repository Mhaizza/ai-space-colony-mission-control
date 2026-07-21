"""Server-only principal registry for workflow-record authorization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final, Literal, cast

from app.mission.role_slugs import ROLE_SLUGS
from app.mission.types import ASSIGNMENT_WORKER_IDS, TrustClass

WorkerIdentity = Literal["codex", "claude", "cursor", "openclaw"]


@dataclass(frozen=True, slots=True)
class PrincipalEntry:
    """One GitHub-login principal mapped to a trust class."""

    github_login: str
    trust_class: TrustClass
    # Worker-class: own identity + roles that identity may hold + identities it may declare.
    worker_identity: WorkerIdentity | None = None
    allowed_roles: frozenset[str] = frozenset()
    declarable_identities: frozenset[WorkerIdentity] = frozenset()


@dataclass(frozen=True, slots=True)
class PrincipalRegistry:
    """Closed login → trust-class map used for author authorization."""

    entries_by_login: dict[str, PrincipalEntry]

    def get(self, github_login: str) -> PrincipalEntry | None:
        return self.entries_by_login.get(github_login.lower())

    def workers(self) -> list[PrincipalEntry]:
        return [e for e in self.entries_by_login.values() if e.trust_class == "worker"]

    def humans(self) -> list[PrincipalEntry]:
        return [e for e in self.entries_by_login.values() if e.trust_class == "human"]

    def reviewers(self) -> list[PrincipalEntry]:
        return [e for e in self.entries_by_login.values() if e.trust_class == "reviewer"]

    def registered_worker_identities(self) -> frozenset[str]:
        return frozenset(e.worker_identity for e in self.workers() if e.worker_identity is not None)


_EMPTY: Final[PrincipalRegistry] = PrincipalRegistry(entries_by_login={})


def empty_principal_registry() -> PrincipalRegistry:
    """Return an empty registry (adapter inactive / tests)."""
    return _EMPTY


def parse_principal_registry_json(raw: str) -> PrincipalRegistry:
    """Parse server-only principal registry JSON.

    Expected shape:
    {
      "principals": [
        {
          "github_login": "alice",
          "trust_class": "worker",
          "worker_identity": "cursor",
          "allowed_roles": ["implementation-engineer", "technical-director"],
          "declarable_identities": ["cursor"]
        },
        {"github_login": "bob", "trust_class": "human"},
        {"github_login": "reviewbot", "trust_class": "reviewer"}
      ]
    }
    """
    text = raw.strip()
    if not text:
        return empty_principal_registry()

    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"MC_PRINCIPAL_REGISTRY_JSON is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("MC_PRINCIPAL_REGISTRY_JSON must be a JSON object")

    principals = payload.get("principals")
    if not isinstance(principals, list):
        raise ValueError("MC_PRINCIPAL_REGISTRY_JSON.principals must be an array")

    entries: dict[str, PrincipalEntry] = {}
    seen_logins: set[str] = set()
    for item in principals:
        entry = _parse_entry(item)
        login_key = entry.github_login.lower()
        if login_key in seen_logins:
            raise ValueError(f"Duplicate github_login in principal registry: {entry.github_login}")
        seen_logins.add(login_key)
        entries[login_key] = entry

    # Enforce disjoint trust classes per login (already one entry each).
    return PrincipalRegistry(entries_by_login=entries)


def _parse_entry(item: object) -> PrincipalEntry:
    if not isinstance(item, dict):
        raise ValueError("Each principal must be a JSON object")

    login = item.get("github_login")
    trust = item.get("trust_class")
    if not isinstance(login, str) or not login.strip():
        raise ValueError("principal.github_login must be a non-empty string")
    if trust not in {"worker", "human", "reviewer"}:
        raise ValueError(f"principal.trust_class must be worker|human|reviewer; got {trust!r}")

    if trust == "worker":
        identity = item.get("worker_identity")
        if identity not in ASSIGNMENT_WORKER_IDS:
            raise ValueError(
                "worker principal.worker_identity must be one of "
                f"{sorted(ASSIGNMENT_WORKER_IDS)}; got {identity!r}"
            )
        roles_raw = item.get("allowed_roles", [])
        if not isinstance(roles_raw, list) or not all(isinstance(r, str) for r in roles_raw):
            raise ValueError("worker principal.allowed_roles must be an array of strings")
        roles = frozenset(roles_raw)
        unknown_roles = roles - ROLE_SLUGS
        if unknown_roles:
            raise ValueError(
                f"worker principal.allowed_roles contains unknown RoleSlugs: "
                f"{sorted(unknown_roles)}"
            )
        if "qa-reviewer" in roles:
            raise ValueError("worker principal.allowed_roles may not include qa-reviewer")
        if "human-owner" in roles:
            raise ValueError("worker principal.allowed_roles may not include human-owner")

        declarable_raw = item.get("declarable_identities", [identity])
        if not isinstance(declarable_raw, list) or not all(
            isinstance(d, str) for d in declarable_raw
        ):
            raise ValueError("worker principal.declarable_identities must be an array of strings")
        declarable: set[WorkerIdentity] = set()
        for d in declarable_raw:
            if d not in ASSIGNMENT_WORKER_IDS:
                raise ValueError(f"declarable_identities entry must be Worker-class; got {d!r}")
            declarable.add(cast(WorkerIdentity, d))
        worker_identity = cast(WorkerIdentity, identity)
        return PrincipalEntry(
            github_login=login.strip(),
            trust_class="worker",
            worker_identity=worker_identity,
            allowed_roles=roles,
            declarable_identities=frozenset(declarable),
        )

    # human / reviewer: no worker fields allowed
    for forbidden in ("worker_identity", "allowed_roles", "declarable_identities"):
        if forbidden in item and item[forbidden] not in (None, [], ""):
            raise ValueError(f"{trust} principal must not set {forbidden}")

    return PrincipalEntry(
        github_login=login.strip(),
        trust_class=cast(TrustClass, trust),
    )
