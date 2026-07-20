"""Credential and secret redaction helpers (ADR-23 D5 / D7)."""

from __future__ import annotations

import re
from typing import Any

_BEARER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)(\S+)")
_TOKEN_QUERY_RE = re.compile(r"(?i)([?&](?:access_)?token=)([^&\s]+)")


def redact_secrets(text: str, *, secrets: list[str] | None = None) -> str:
    """Redact known secret values and common credential patterns from text."""
    redacted = _BEARER_RE.sub(r"\1[REDACTED]", text)
    redacted = _TOKEN_QUERY_RE.sub(r"\1[REDACTED]", redacted)
    for secret in secrets or []:
        if secret and secret in redacted:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def scrub_mapping(value: Any, *, secrets: list[str] | None = None) -> Any:
    """Recursively scrub mappings/lists/strings for secret material."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_l = str(key).lower()
            if key_l in {
                "authorization",
                "token",
                "access_token",
                "github_pat",
                "github_token",
                "password",
                "secret",
                "local_auth_token",
            }:
                out[str(key)] = "[REDACTED]"
            else:
                out[str(key)] = scrub_mapping(item, secrets=secrets)
        return out
    if isinstance(value, list):
        return [scrub_mapping(item, secrets=secrets) for item in value]
    if isinstance(value, str):
        return redact_secrets(value, secrets=secrets)
    return value
