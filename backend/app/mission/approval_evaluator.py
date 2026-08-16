"""Pure approval policy evaluator (Slice 5A Checkpoint B).

`evaluate_approval` is a deterministic, side-effect-free domain function: no
database access, no clock reads beyond the `now` parameter it is handed, no
knowledge of `mc_approval_request.status` or terminal-state gating (that
orchestration belongs to Checkpoint C's `approval_service.py`, which is
expected to call this only while a request is still open for decisions).

Quorum is never satisfied by counting distinct approving principals. A
principal may hold more than one role (`mc_principal_role`), and a policy's
quorum requirement is a list of *named slots*, each with its own eligible
role set — the same principal can be eligible for several slots at once
without being allowed to fill more than one of them. Quorum satisfaction is
therefore computed as a maximum bipartite matching between effective
approving principals and quorum slots (Kuhn's algorithm: an augmenting-path
search is more than adequate at the graph sizes a governance policy will
ever have, and is simple enough to reason about and test exhaustively). The
`now` parameter is accepted because it is part of the already-accepted
function signature, but it is intentionally unused in Checkpoint B: nothing
here evaluates expiration (Checkpoint E's `approval_reconciliation.py` will
be the caller that actually needs "now" to matter).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.mission.approval_policy import ApprovalPolicyDefinition, QuorumSlot

Decision = Literal["approve", "reject"]
EvaluationStatus = Literal["pending", "approved", "rejected"]
MissionEffect = Literal["unchanged", "blocked"]


@dataclass(frozen=True, slots=True)
class EffectiveDecision:
    """One principal's currently-effective vote on a request, with its authoring snapshot."""

    principal_id: UUID
    decision: Decision
    role_slugs_at_decision: frozenset[str]
    trust_level_at_decision: str


@dataclass(frozen=True, slots=True)
class ApprovalEvaluation:
    """The evaluator's full, typed verdict for one request at one point in time."""

    status: EvaluationStatus
    quorum_satisfied: bool
    approvals: list[UUID] = field(default_factory=list)
    rejections: list[UUID] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)
    mission_effect: MissionEffect | None = None
    reason: str = ""


def _max_bipartite_matching(
    approving_principals: list[EffectiveDecision],
    slots: list[QuorumSlot],
) -> dict[int, int]:
    """Return a maximum matching {slot_index: principal_index} via Kuhn's algorithm.

    One principal is matched to at most one slot, even when their role set
    makes them eligible for several. The specific witness matching returned
    when several maximum matchings exist is not meaningful on its own (see
    the module docstring's note on matching-size invariance); callers should
    only rely on the returned matching's *size*.
    """
    adjacency: list[list[int]] = [
        [
            p_idx
            for p_idx, decision in enumerate(approving_principals)
            if slot.eligible_roles and set(slot.eligible_roles) & decision.role_slugs_at_decision
        ]
        for slot in slots
    ]
    match_for_principal: dict[int, int] = {}

    def _try_assign(slot_idx: int, visited: set[int]) -> bool:
        for p_idx in adjacency[slot_idx]:
            if p_idx in visited:
                continue
            visited.add(p_idx)
            if p_idx not in match_for_principal or _try_assign(match_for_principal[p_idx], visited):
                match_for_principal[p_idx] = slot_idx
                return True
        return False

    for slot_idx in range(len(slots)):
        _try_assign(slot_idx, set())

    return {slot_idx: p_idx for p_idx, slot_idx in match_for_principal.items()}


def evaluate_approval(
    policy: ApprovalPolicyDefinition,
    effective_decisions: list[EffectiveDecision],
    now: datetime,  # noqa: ARG001 - reserved for Checkpoint E's expiration path; unused here
) -> ApprovalEvaluation:
    """Evaluate a policy against a request's current effective decisions."""
    approvals = [d for d in effective_decisions if d.decision == "approve"]
    rejections = [d for d in effective_decisions if d.decision == "reject"]

    slots = policy.quorum.slots
    matching = _max_bipartite_matching(approvals, slots)
    quorum_satisfied = len(matching) == len(slots)
    missing_requirements = [slot.slot for idx, slot in enumerate(slots) if idx not in matching]

    approval_ids = [d.principal_id for d in approvals]
    rejection_ids = [d.principal_id for d in rejections]

    if policy.decision_rule == "veto":
        status, reason = _evaluate_veto(policy, rejections, quorum_satisfied, bool(approvals))
    elif policy.decision_rule == "unanimous":
        status, reason = _evaluate_unanimous(quorum_satisfied, bool(rejections))
    else:
        status, reason = _evaluate_majority(quorum_satisfied, len(approvals), len(rejections))

    mission_effect: MissionEffect | None = None
    if status == "rejected":
        mission_effect = (
            "unchanged" if policy.rejection_behavior == "leave_mission_unchanged" else "blocked"
        )

    return ApprovalEvaluation(
        status=status,
        quorum_satisfied=quorum_satisfied,
        approvals=approval_ids,
        rejections=rejection_ids,
        missing_requirements=missing_requirements,
        mission_effect=mission_effect,
        reason=reason,
    )


def _evaluate_majority(
    quorum_satisfied: bool, approve_count: int, reject_count: int
) -> tuple[EvaluationStatus, str]:
    if not quorum_satisfied:
        return "pending", "quorum not yet satisfied"
    if approve_count > reject_count:
        return "approved", "quorum satisfied and approvals outnumber rejections"
    return "rejected", "quorum satisfied but approvals do not outnumber rejections"


def _evaluate_unanimous(
    quorum_satisfied: bool, has_rejection: bool
) -> tuple[EvaluationStatus, str]:
    if not quorum_satisfied:
        return "pending", "quorum not yet satisfied"
    if has_rejection:
        return "rejected", "quorum satisfied but at least one effective rejection exists"
    return "approved", "quorum satisfied with no effective rejection"


def _evaluate_veto(
    policy: ApprovalPolicyDefinition,
    rejections: list[EffectiveDecision],
    quorum_satisfied: bool,
    has_approval: bool,
) -> tuple[EvaluationStatus, str]:
    assert policy.veto is not None  # enforced by ApprovalPolicyDefinition's own validator
    veto_roles = set(policy.veto.veto_authorized_roles)
    veto_reject = next(
        (d for d in rejections if veto_roles & d.role_slugs_at_decision),
        None,
    )
    if veto_reject is not None:
        return "rejected", "veto-authorized rejection, independent of quorum state"
    if quorum_satisfied and has_approval:
        return "approved", "quorum satisfied, at least one approval, no veto-authorized rejection"
    if quorum_satisfied:
        return (
            "pending",
            "quorum satisfied but no approval yet (non-veto rejections do not approve)",
        )
    return "pending", "quorum not yet satisfied"
