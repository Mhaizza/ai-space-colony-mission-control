"""Domain-exception -> API error mapping for the Slice 5A approval routes.

Maps `PrincipalResolutionError`/`ApprovalServiceError` codes to
`(HTTP status, stable API error code)` pairs. No message-text sniffing:
every mapping keys off the domain exception's own closed `.code`, never its
free-text message. The router raises `HTTPException(status_code=...,
detail={"code": ..., "message": ...})` from these mappings; no DB error
string, stack trace, credential, or raw SQL detail is ever included.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.mission.approval_service import ApprovalServiceError
from app.mission.principal_resolver import PrincipalResolutionError

_PRINCIPAL_RESOLUTION_STATUS: dict[str, int] = {
    "principal_not_registered": status.HTTP_403_FORBIDDEN,
    "principal_disabled": status.HTTP_403_FORBIDDEN,
}

_APPROVAL_SERVICE_MAPPING: dict[str, tuple[int, str]] = {
    "principal_not_human": (status.HTTP_403_FORBIDDEN, "principal_not_authorized"),
    "principal_not_authorized": (status.HTTP_403_FORBIDDEN, "principal_not_authorized"),
    "principal_trust_insufficient": (status.HTTP_403_FORBIDDEN, "principal_trust_insufficient"),
    "policy_not_found": (status.HTTP_400_BAD_REQUEST, "approval_policy_invalid"),
    "policy_invalid": (status.HTTP_400_BAD_REQUEST, "approval_policy_invalid"),
    "request_not_found": (status.HTTP_404_NOT_FOUND, "approval_request_not_found"),
    "request_not_open": (status.HTTP_409_CONFLICT, "approval_request_terminal"),
    "decision_not_found": (status.HTTP_404_NOT_FOUND, "decision_not_found"),
    "approval_decision_exists": (status.HTTP_409_CONFLICT, "approval_decision_exists"),
    "invalid_supersede": (status.HTTP_409_CONFLICT, "invalid_supersede"),
    "idempotency_key_reused_with_different_payload": (
        status.HTTP_409_CONFLICT,
        "idempotency_key_reused",
    ),
}


def to_http_exception(exc: PrincipalResolutionError | ApprovalServiceError) -> HTTPException:
    """Map a resolved domain exception to the closed API error taxonomy."""
    status_code: int
    code: str
    if isinstance(exc, PrincipalResolutionError):
        status_code = _PRINCIPAL_RESOLUTION_STATUS[exc.code]
        code = exc.code
    else:
        status_code, code = _APPROVAL_SERVICE_MAPPING[exc.code]
    return HTTPException(status_code=status_code, detail={"code": code, "message": str(exc)})
