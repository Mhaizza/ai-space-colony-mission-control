# ruff: noqa: INP001
"""Closed source-identity contract tests (commit status + workflow run)."""

from __future__ import annotations

from typing import Any

import pytest

from app.mission.source_identity import (
    commit_status_identity,
    valid_node_id,
    workflow_run_identity,
)

_HEAD = "d" * 40


# --------------------------------------------------------------------------- #
# node_id helper
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "payload",
    [
        {"node_id": ""},
        {"node_id": None},
        {"node_id": 12345},
        {"node_id": True},
        {"node_id": {"x": 1}},
        {},
    ],
)
def test_valid_node_id_rejects_missing_or_wrong_type(payload: dict[str, Any]) -> None:
    assert valid_node_id(payload) is None


def test_valid_node_id_accepts_non_empty_string() -> None:
    assert valid_node_id({"node_id": "MDE=abc"}) == "MDE=abc"


# --------------------------------------------------------------------------- #
# Commit status identity
# --------------------------------------------------------------------------- #


def test_commit_status_prefers_node_id() -> None:
    assert (
        commit_status_identity({"node_id": "S1", "context": "ci", "id": 7}, head_sha=_HEAD) == "S1"
    )


def test_commit_status_fallback_when_fully_defined() -> None:
    assert (
        commit_status_identity({"context": "ci", "id": 7}, head_sha=_HEAD) == f"status:{_HEAD}:ci:7"
    )


def test_commit_status_fallback_accepts_string_id() -> None:
    assert (
        commit_status_identity({"context": "ci", "id": "42"}, head_sha=_HEAD)
        == f"status:{_HEAD}:ci:42"
    )


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"context": "ci"}, id="missing-node_id-missing-id"),
        pytest.param({"context": "ci", "id": None}, id="missing-node_id-invalid-id-none"),
        pytest.param({"context": "ci", "id": ""}, id="missing-node_id-invalid-id-empty"),
        pytest.param({"context": "ci", "id": True}, id="missing-node_id-invalid-id-bool"),
        pytest.param({"id": 7}, id="missing-context"),
        pytest.param({"context": "", "id": 7}, id="empty-context"),
        pytest.param({"context": None, "id": 7}, id="none-context"),
        pytest.param({}, id="empty-identity"),
        pytest.param({"node_id": 12345}, id="malformed-node_id-type"),
        pytest.param({"node_id": "", "context": "ci"}, id="empty-node_id-missing-id"),
    ],
)
def test_commit_status_malformed_returns_none(payload: dict[str, Any]) -> None:
    assert commit_status_identity(payload, head_sha=_HEAD) is None


@pytest.mark.parametrize("head_sha", ["", None, 123])
def test_commit_status_requires_valid_head_for_fallback(head_sha: Any) -> None:
    # node_id still wins even with a bad head.
    assert commit_status_identity({"node_id": "S1"}, head_sha=head_sha) == "S1"
    # But the fallback cannot form without a valid head.
    assert commit_status_identity({"context": "ci", "id": 7}, head_sha=head_sha) is None


def test_commit_status_never_emits_none_component() -> None:
    ident = commit_status_identity({"context": "ci", "id": 7}, head_sha=_HEAD)
    assert ident is not None
    assert "None" not in ident
    assert ":None" not in ident


# --------------------------------------------------------------------------- #
# Workflow run identity
# --------------------------------------------------------------------------- #


def test_workflow_run_prefers_node_id() -> None:
    assert workflow_run_identity({"node_id": "WR1", "id": 9}) == "WR1"


def test_workflow_run_fallback_numeric_id() -> None:
    assert workflow_run_identity({"id": 9}) == "workflow_run:9"


def test_workflow_run_fallback_string_id() -> None:
    assert workflow_run_identity({"id": "abc"}) == "workflow_run:abc"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"foo": "bar"}, id="missing-node_id-missing-id"),
        pytest.param({"id": None}, id="invalid-id-none"),
        pytest.param({"id": ""}, id="invalid-id-empty"),
        pytest.param({"id": True}, id="invalid-id-bool"),
        pytest.param({}, id="empty-identity"),
        pytest.param({"node_id": 999}, id="malformed-node_id-type"),
        pytest.param({"node_id": ""}, id="empty-node_id"),
    ],
)
def test_workflow_run_malformed_returns_none(payload: dict[str, Any]) -> None:
    assert workflow_run_identity(payload) is None


def test_workflow_run_never_emits_none_component() -> None:
    ident = workflow_run_identity({"id": 9})
    assert ident is not None
    assert "None" not in ident
