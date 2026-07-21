"""Edited-comment, exact-head, and supersession graph validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.mission.types import QuarantineReason
from app.mission.workflow_record import ParsedWorkflowRecord


@dataclass(frozen=True, slots=True)
class CommentSourceMeta:
    comment_id: int
    card: int
    github_login: str
    created_at: datetime
    updated_at: datetime
    html_url: str
    body: str
    on_authoritative_issue: bool
    issue_number: int


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    source: CommentSourceMeta
    record: ParsedWorkflowRecord


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    reason: QuarantineReason
    message: str


def is_edited_comment(created_at: datetime, updated_at: datetime) -> bool:
    """Edited comments are invalid when updatedAt != createdAt."""
    return updated_at != created_at


def check_edited_comment(source: CommentSourceMeta) -> ValidationIssue | None:
    if is_edited_comment(source.created_at, source.updated_at):
        return ValidationIssue(
            reason=QuarantineReason.EDITED_COMMENT,
            message=(
                f"comment {source.comment_id} was edited "
                f"(created_at={source.created_at.isoformat()} "
                f"updated_at={source.updated_at.isoformat()})"
            ),
        )
    return None


def check_card_binding(
    source: CommentSourceMeta, record: ParsedWorkflowRecord
) -> ValidationIssue | None:
    if not source.on_authoritative_issue:
        return ValidationIssue(
            reason=QuarantineReason.NON_AUTHORITATIVE_SOURCE,
            message="workflow record is not on an authoritative Project-linked Issue",
        )
    if record.card != source.issue_number:
        return ValidationIssue(
            reason=QuarantineReason.CARD_BINDING_MISMATCH,
            message=(
                f"record.card={record.card} does not match Issue number " f"{source.issue_number}"
            ),
        )
    return None


def check_exact_head(
    record: ParsedWorkflowRecord,
    *,
    current_head: str | None,
) -> ValidationIssue | None:
    """Accept review/human_approval only when head equals current artifact head."""
    if record.type not in {"review_result", "human_approval"}:
        return None
    if current_head is None:
        return ValidationIssue(
            reason=QuarantineReason.UNRESOLVABLE_ARTIFACT,
            message="artifact current head is unresolvable",
        )
    if record.head != current_head:
        return ValidationIssue(
            reason=QuarantineReason.STALE_HEAD,
            message=(
                f"record head {record.head} does not match current artifact head " f"{current_head}"
            ),
        )
    return None


def validate_supersession_graph(
    candidates: list[CandidateRecord],
) -> dict[int, ValidationIssue]:
    """Validate supersession edges; return comment_id → quarantine issue.

    Rules:
    - target must exist, be earlier, same card
    - review_result/human_approval additionally require same artifact
    - a record may be superseded at most once
    - cycles are rejected
    """
    by_id = {c.source.comment_id: c for c in candidates}
    issues: dict[int, ValidationIssue] = {}
    superseded_by: dict[int, int] = {}

    ordered = sorted(
        candidates,
        key=lambda c: (c.source.created_at, c.source.comment_id),
    )

    for candidate in ordered:
        record = candidate.record
        comment_id = candidate.source.comment_id
        supersedes = record.supersedes
        if supersedes is None:
            continue

        target = by_id.get(supersedes)
        if target is None:
            issues[comment_id] = ValidationIssue(
                reason=QuarantineReason.MALFORMED_RECORD,
                message=f"supersedes target comment {supersedes} is not present",
            )
            continue

        if target.record.card != record.card:
            issues[comment_id] = ValidationIssue(
                reason=QuarantineReason.CROSS_CARD_SUPERSESSION,
                message="supersession must target an earlier record on the same card",
            )
            continue

        if (target.source.created_at, target.source.comment_id) >= (
            candidate.source.created_at,
            comment_id,
        ):
            issues[comment_id] = ValidationIssue(
                reason=QuarantineReason.MALFORMED_RECORD,
                message="supersession must target an earlier record",
            )
            continue

        if record.type in {"review_result", "human_approval"}:
            if target.record.type != record.type:
                issues[comment_id] = ValidationIssue(
                    reason=QuarantineReason.MALFORMED_RECORD,
                    message=f"{record.type} may only supersede the same type",
                )
                continue
            if target.record.artifact != record.artifact:
                issues[comment_id] = ValidationIssue(
                    reason=QuarantineReason.CROSS_ARTIFACT_SUPERSESSION,
                    message=(f"{record.type} supersession must target the same artifact"),
                )
                continue

        if supersedes in superseded_by:
            issues[comment_id] = ValidationIssue(
                reason=QuarantineReason.DUPLICATE_SUPERSESSION,
                message=(
                    f"comment {supersedes} is already superseded by " f"{superseded_by[supersedes]}"
                ),
            )
            continue

        # Cycle detection: walk from target through supersedes edges including this edge.
        if _would_create_cycle(by_id, comment_id, supersedes):
            issues[comment_id] = ValidationIssue(
                reason=QuarantineReason.CYCLIC_SUPERSESSION,
                message=f"supersession edge {comment_id}->{supersedes} creates a cycle",
            )
            continue

        superseded_by[supersedes] = comment_id

    return issues


def _would_create_cycle(
    by_id: dict[int, CandidateRecord],
    source_id: int,
    target_id: int,
) -> bool:
    """Return True if adding source->target would create a cycle."""
    seen: set[int] = set()
    current: int | None = target_id
    while current is not None:
        if current == source_id:
            return True
        if current in seen:
            return True
        seen.add(current)
        node = by_id.get(current)
        if node is None or node.record.supersedes is None:
            return False
        current = node.record.supersedes
    return False


def superseded_comment_ids(
    candidates: list[CandidateRecord],
    *,
    quarantined_ids: set[int],
) -> set[int]:
    """Return IDs superseded by a non-quarantined successor."""
    superseded: set[int] = set()
    for candidate in candidates:
        if candidate.source.comment_id in quarantined_ids:
            continue
        target = candidate.record.supersedes
        if target is not None and target not in quarantined_ids:
            superseded.add(target)
    return superseded
