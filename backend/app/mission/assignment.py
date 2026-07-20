"""Effective assignment derivation and conflict quarantine."""

from __future__ import annotations

from dataclasses import dataclass

from app.mission.types import QuarantineReason
from app.mission.validation import CandidateRecord, ValidationIssue, superseded_comment_ids


@dataclass(frozen=True, slots=True)
class EffectiveAssignment:
    card: int
    worker: str
    role: str
    comment_id: int
    record_type: str


@dataclass(frozen=True, slots=True)
class AssignmentDerivation:
    effective: dict[int, EffectiveAssignment]
    conflicts: dict[int, ValidationIssue]


def derive_effective_assignments(
    candidates: list[CandidateRecord],
    *,
    quarantined_ids: set[int],
) -> AssignmentDerivation:
    """Derive per-card effective assignment; never choose a winner on conflict.

    Precedence: latest valid unsuperseded start_task|handoff by (created_at, comment_id).
    If multiple unsuperseded assignment records remain with different worker/role,
    quarantine the card's derived assignment.
    """
    superseded = superseded_comment_ids(candidates, quarantined_ids=quarantined_ids)
    by_card: dict[int, list[CandidateRecord]] = {}
    for candidate in candidates:
        cid = candidate.source.comment_id
        if cid in quarantined_ids or cid in superseded:
            continue
        if candidate.record.type not in {"start_task", "handoff"}:
            continue
        by_card.setdefault(candidate.record.card, []).append(candidate)

    effective: dict[int, EffectiveAssignment] = {}
    conflicts: dict[int, ValidationIssue] = {}

    for card, rows in by_card.items():
        ordered = sorted(rows, key=lambda c: (c.source.created_at, c.source.comment_id))
        # Collect all unsuperseded; if >1 with disagreeing worker/role → conflict.
        if not ordered:
            continue
        # After supersession filtering, remaining are unsuperseded. If more than one
        # remains, that is a conflict regardless of ordering — ADR-23 never picks.
        unique_pairs = {(r.record.worker, r.record.role) for r in ordered}
        if len(ordered) > 1 and len(unique_pairs) > 1:
            conflicts[card] = ValidationIssue(
                reason=QuarantineReason.CONFLICTING_ASSIGNMENT,
                message=(
                    f"card {card} has conflicting effective assignment records; "
                    "no winner is selected"
                ),
            )
            continue
        if len(ordered) > 1 and len(unique_pairs) == 1:
            # Identical worker/role from multiple records — still take latest as identity.
            latest = ordered[-1]
        else:
            latest = ordered[-1]
        assert latest.record.worker is not None
        assert latest.record.role is not None
        effective[card] = EffectiveAssignment(
            card=card,
            worker=latest.record.worker,
            role=latest.record.role,
            comment_id=latest.source.comment_id,
            record_type=latest.record.type,
        )

    return AssignmentDerivation(effective=effective, conflicts=conflicts)
