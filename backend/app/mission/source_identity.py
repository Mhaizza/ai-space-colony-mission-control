"""Closed source-identity contract for projection elements.

Source identity is *mandatory*. An element is projected only when a fully
defined, non-synthetic identity can be derived from its raw payload. This module
is the single home for identity rules — parser, validator, and sync all reuse
these functions instead of re-implementing the ``node_id``/fallback logic.

Contract guarantees:

- A returned identity is always a non-empty string with no ``None`` / ``null`` /
  empty-string / missing components.
- ``None`` means the element is malformed: the caller must mark the exact
  ``(source_type, partition_key)`` PARTIAL and must not upsert the element.
- Synthetic identities are only ever generated from fully validated components.
"""

from __future__ import annotations

from typing import Any


def valid_node_id(payload: dict[str, Any]) -> str | None:
    """Return a non-empty ``node_id`` string, else ``None``."""
    node_id = payload.get("node_id")
    if isinstance(node_id, str) and node_id:
        return node_id
    return None


def _valid_rest_id(value: Any) -> str | None:
    """Return the canonical string form of a valid REST numeric/string id.

    Valid ids are a non-boolean ``int`` or a non-empty ``str``. Everything else
    (``None``, ``bool``, empty string, floats, containers) is invalid.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value:
        return value
    return None


def commit_status_identity(payload: dict[str, Any], *, head_sha: str) -> str | None:
    """Closed identity for a commit-status element, or ``None`` if malformed.

    Accept a non-empty ``node_id``. Otherwise accept the fallback identity
    ``status:<sha>:<context>:<id>`` only when *every* component (head sha,
    context, and id) is fully defined and valid. Any missing/invalid component
    makes the element malformed.
    """
    node_id = valid_node_id(payload)
    if node_id is not None:
        return node_id
    if not isinstance(head_sha, str) or not head_sha:
        return None
    context = payload.get("context")
    if not isinstance(context, str) or not context:
        return None
    rest_id = _valid_rest_id(payload.get("id"))
    if rest_id is None:
        return None
    return f"status:{head_sha}:{context}:{rest_id}"


def workflow_run_identity(payload: dict[str, Any]) -> str | None:
    """Closed identity for a workflow-run element, or ``None`` if malformed.

    Accept a non-empty ``node_id``, otherwise the fallback identity
    ``workflow_run:<id>`` only when ``id`` is a valid numeric/string value.
    """
    node_id = valid_node_id(payload)
    if node_id is not None:
        return node_id
    rest_id = _valid_rest_id(payload.get("id"))
    if rest_id is None:
        return None
    return f"workflow_run:{rest_id}"
