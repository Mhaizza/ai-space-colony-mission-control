"""Hard-disable inherited mutation/write HTTP routes (ADR-23 D8)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from fastapi.routing import APIRoute
from starlette.responses import Response

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import FastAPI
    from starlette.types import ASGIApp, Receive, Scope, Send

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
MUTATIONS_DISABLED_CODE = "mutations_hard_disabled"
MUTATIONS_DISABLED_MESSAGE = (
    "Write/action routes are hard-disabled (ADR-23 D8). "
    "No action capability is available."
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
