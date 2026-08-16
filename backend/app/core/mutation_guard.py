"""Hard-disable inherited mutation/write HTTP routes (ADR-23 D8/D8a)."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Final

from fastapi.routing import APIRoute
from starlette.responses import Response

from app.mission.types import (
    CREATE_APPROVAL_ALLOWLIST_ENTRY,
    MANUAL_REFRESH_ALLOWLIST_ENTRY,
    SUBMIT_DECISION_ALLOWLIST_PATH_TEMPLATE,
    SUPERSEDE_DECISION_ALLOWLIST_PATH_TEMPLATE,
)

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import FastAPI
    from starlette.types import ASGIApp, Receive, Scope, Send

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
MUTATIONS_DISABLED_CODE = "mutations_hard_disabled"
MUTATIONS_DISABLED_MESSAGE = (
    "Write/action routes are hard-disabled (ADR-23 D8). " "No action capability is available."
)

# D3's manual refresh, plus D8a's non-parameterized approval-creation route:
# both are exact literal (method, path) pairs, matched by plain membership.
MUTATION_ALLOWLIST: Final[frozenset[tuple[str, str]]] = frozenset(
    {MANUAL_REFRESH_ALLOWLIST_ENTRY, CREATE_APPROVAL_ALLOWLIST_ENTRY}
)

# D8a's two remaining routes are parameterized by `{request_id}`, so a literal
# string can never match the real runtime path (which contains an actual
# UUID). Each entry here requires a *strict* single UUID-shaped path segment
# between fixed literal components -- never a generic `[^/]+`, which would
# also match `foo`, `delete`, or any other single segment, and never a
# multi-segment/prefix wildcard. `fullmatch`, not `search`, so no additional
# leading/trailing segment can slip through either. This closed pair is the
# entire parameterized surface ADR-23 D8a authorizes -- adding a fourth
# mutation route (of either kind) requires an ADR-23 revision, not a code
# change here.
_UUID_SEGMENT: Final[str] = (
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _compile_parameterized_path(template: str) -> re.Pattern[str]:
    # `types.py`'s `{request_id}`-templated constants are the single source
    # of truth for these two paths; substitute the strict UUID pattern for
    # the placeholder rather than duplicating the literal path here.
    pattern = re.escape(template).replace(re.escape("{request_id}"), _UUID_SEGMENT)
    return re.compile(f"^{pattern}$")


_MUTATION_ALLOWLIST_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("POST", _compile_parameterized_path(SUBMIT_DECISION_ALLOWLIST_PATH_TEMPLATE)),
    ("POST", _compile_parameterized_path(SUPERSEDE_DECISION_ALLOWLIST_PATH_TEMPLATE)),
)


def _is_allowlisted(method: str, path: str) -> bool:
    if (method, path) in MUTATION_ALLOWLIST:
        return True
    return any(
        pattern_method == method and pattern.fullmatch(path) is not None
        for pattern_method, pattern in _MUTATION_ALLOWLIST_PATTERNS
    )


def inventory_mutating_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Return sorted (METHOD, path) pairs for registered mutating HTTP routes."""
    found: list[tuple[str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = (route.methods or set()) & MUTATING_METHODS
        for method in sorted(methods):
            found.append((method, route.path))
    return sorted(found)


def enforce_mutations_hard_disabled(app: FastAPI, *, hard_disabled: bool) -> None:
    """Fail closed at startup when mutating routes exist but are not hard-disabled."""
    mutating = inventory_mutating_routes(app)
    if not mutating:
        return
    if not hard_disabled:
        sample = ", ".join(f"{method} {path}" for method, path in mutating[:8])
        more = "" if len(mutating) <= 8 else f" (+{len(mutating) - 8} more)"
        raise RuntimeError(
            "Fail-closed startup: inherited mutation/write routes are registered "
            f"({len(mutating)} total) but MUTATIONS_HARD_DISABLED is not true. "
            f"Examples: {sample}{more}. "
            "Set MUTATIONS_HARD_DISABLED=true or remove the routes (ADR-23 D8)."
        )


def _normalize_path(path: str) -> str:
    if not path:
        return "/"
    if path != "/" and path.endswith("/"):
        return path.rstrip("/")
    return path


class MutationHardDisableMiddleware:
    """Reject mutating HTTP methods when write/action routes are hard-disabled."""

    def __init__(self, app: ASGIApp, *, enabled: bool) -> None:
        self._app = app
        self._enabled = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            self._enabled
            and scope["type"] == "http"
            and str(scope.get("method", "")).upper() in MUTATING_METHODS
        ):
            method = str(scope.get("method", "")).upper()
            path = _normalize_path(str(scope.get("path", "")))
            if _is_allowlisted(method, path):
                await self._app(scope, receive, send)
                return
            response = Response(
                content=json.dumps(
                    {
                        "detail": {
                            "code": MUTATIONS_DISABLED_CODE,
                            "message": MUTATIONS_DISABLED_MESSAGE,
                        },
                        "code": MUTATIONS_DISABLED_CODE,
                        "retryable": False,
                    }
                ),
                status_code=405,
                media_type="application/json",
                headers={"Allow": "GET, HEAD, OPTIONS"},
            )
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)
