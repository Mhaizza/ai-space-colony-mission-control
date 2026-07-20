"""Closed Slice 3 types for projection identity and quarantine reasons."""

from __future__ import annotations

from enum import StrEnum
from typing import Final, Literal


class SourceType(StrEnum):
    """Closed D3 projection sourceType union (GitHub subset for Slice 3)."""

    GITHUB_PROJECT_ITEM = "github_project_item"
    GITHUB_ISSUE = "github_issue"
    GITHUB_PULL_REQUEST = "github_pull_request"
    GITHUB_ISSUE_COMMENT = "github_issue_comment"
    GITHUB_PULL_REQUEST_REVIEW = "github_pull_request_review"
    GITHUB_PULL_REQUEST_REVIEW_COMMENT = "github_pull_request_review_comment"
    GITHUB_CHECK_SUITE = "github_check_suite"
    GITHUB_CHECK_RUN = "github_check_run"
    GITHUB_WORKFLOW_RUN = "github_workflow_run"
    GITHUB_COMMIT_STATUS = "github_commit_status"


GITHUB_SOURCE_TYPES: Final[frozenset[SourceType]] = frozenset(SourceType)


class QuarantineReason(StrEnum):
    """Closed quarantine reason codes for Slice 3 adapter validation."""

    MALFORMED_RECORD = "malformed_record"
    UNAUTHORIZED_AUTHOR = "unauthorized_author"
    EDITED_COMMENT = "edited_comment"
    STALE_HEAD = "stale_head"
    CYCLIC_SUPERSESSION = "cyclic_supersession"
    DUPLICATE_SUPERSESSION = "duplicate_supersession"
    CROSS_CARD_SUPERSESSION = "cross_card_supersession"
    CROSS_ARTIFACT_SUPERSESSION = "cross_artifact_supersession"
    CONFLICTING_ASSIGNMENT = "conflicting_assignment"
    UNKNOWN_ENUM = "unknown_enum"
    CARD_BINDING_MISMATCH = "card_binding_mismatch"
    UNRESOLVABLE_ARTIFACT = "unresolvable_artifact"
    UNAUTHORIZED_SUPERSESSION = "unauthorized_supersession"
    INVALID_WORKER_ROLE_PAIR = "invalid_worker_role_pair"
    NON_AUTHORITATIVE_SOURCE = "non_authoritative_source"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    PARTIAL_READ = "partial_read"


RecordType = Literal[
    "start_task",
    "handoff",
    "review_result",
    "human_approval",
    "kanban_update",
    "completion",
]

WorkerId = Literal[
    "codex",
    "claude",
    "cursor",
    "openclaw",
    "human",
    "chatgpt-reviewer",
]

AssignmentWorkerId = Literal["codex", "claude", "cursor", "openclaw"]

ReviewResult = Literal["approved", "revisions_required"]

RepoQualifier = Literal["self", "mission-control"]

TrustClass = Literal["worker", "human", "reviewer"]

RECORD_TYPES: Final[frozenset[str]] = frozenset(
    {
        "start_task",
        "handoff",
        "review_result",
        "human_approval",
        "kanban_update",
        "completion",
    }
)

WORKER_IDS: Final[frozenset[str]] = frozenset(
    {
        "codex",
        "claude",
        "cursor",
        "openclaw",
        "human",
        "chatgpt-reviewer",
    }
)

ASSIGNMENT_WORKER_IDS: Final[frozenset[str]] = frozenset(
    {"codex", "claude", "cursor", "openclaw"}
)

REVIEW_RESULTS: Final[frozenset[str]] = frozenset({"approved", "revisions_required"})

REPO_QUALIFIERS: Final[frozenset[str]] = frozenset({"self", "mission-control"})

RECORD_FIELDS: Final[tuple[str, ...]] = (
    "type",
    "card",
    "worker",
    "role",
    "artifact",
    "head",
    "result",
    "supersedes",
)

ROLE_QA_REVIEWER: Final[str] = "qa-reviewer"
ROLE_HUMAN_OWNER: Final[str] = "human-owner"

REQUIRED_OAUTH_SCOPES: Final[frozenset[str]] = frozenset({"read:project"})

MANUAL_REFRESH_ALLOWLIST_ENTRY: Final[tuple[str, str]] = (
    "POST",
    "/api/v1/mission/refresh",
)
