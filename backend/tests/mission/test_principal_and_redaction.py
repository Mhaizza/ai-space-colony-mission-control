# ruff: noqa: INP001
"""Tests for principal registry parsing and redaction."""

from __future__ import annotations

import pytest

from app.mission.principal_registry import parse_principal_registry_json
from app.mission.redaction import redact_secrets, scrub_mapping


def test_principal_registry_rejects_unknown_role() -> None:
    raw = """
    {
      "principals": [{
        "github_login": "x",
        "trust_class": "worker",
        "worker_identity": "cursor",
        "allowed_roles": ["not-a-real-role"],
        "declarable_identities": ["cursor"]
      }]
    }
    """
    with pytest.raises(ValueError, match="unknown RoleSlugs"):
        parse_principal_registry_json(raw)


def test_principal_registry_rejects_overlapping_duplicate_login() -> None:
    raw = """
    {
      "principals": [
        {"github_login": "Same", "trust_class": "human"},
        {"github_login": "same", "trust_class": "reviewer"}
      ]
    }
    """
    with pytest.raises(ValueError, match="Duplicate github_login"):
        parse_principal_registry_json(raw)


def test_redaction_scrubs_token_material() -> None:
    text = "Authorization: Bearer ghp_secret_value and token=ghp_secret_value"
    redacted = redact_secrets(text, secrets=["ghp_secret_value"])
    assert "ghp_secret_value" not in redacted
    assert "[REDACTED]" in redacted

    payload = scrub_mapping(
        {"authorization": "Bearer ghp_secret_value", "nested": {"token": "x"}},
        secrets=["ghp_secret_value"],
    )
    assert payload["authorization"] == "[REDACTED]"
    assert payload["nested"]["token"] == "[REDACTED]"
