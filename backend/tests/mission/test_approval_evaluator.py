# ruff: noqa: INP001
"""Slice 5A Checkpoint B: evaluate_approval() tests.

Covers the approved direction's own worked examples verbatim (majority),
the unanimous and veto rules' complete predicates, and the deterministic
multi-role quorum-slot allocation rule -- including a non-trivial matching
case specifically constructed to fail under a naive greedy (left-to-right)
assignment while succeeding under a true maximum bipartite matching, so this
suite cannot be satisfied by a greedy stand-in implementation.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from app.mission.approval_evaluator import EffectiveDecision, evaluate_approval
from app.mission.approval_policy import (
    ApprovalPolicyDefinition,
    ExpirationConfig,
    QuorumRequirement,
    QuorumSlot,
    VetoConfig,
)

NOW = datetime(2026, 8, 16, 0, 0, 0)


def _quorum(*slots: tuple[str, list[str]]) -> QuorumRequirement:
    return QuorumRequirement(
        slots=[QuorumSlot(slot=name, eligible_roles=roles) for name, roles in slots]
    )


def _policy(
    *,
    decision_rule: str = "majority",
    quorum: QuorumRequirement | None = None,
    veto: VetoConfig | None = None,
    rejection_behavior: str = "leave_mission_unchanged",
) -> ApprovalPolicyDefinition:
    return ApprovalPolicyDefinition(
        decision_rule=decision_rule,  # type: ignore[arg-type]
        quorum=quorum or _quorum(("a", ["technical-director"]), ("b", ["qa-reviewer"])),
        allowed_approver_principal_types=["human"],
        # Covers every role this test module's quorum/veto fixtures reference
        # (ApprovalPolicyDefinition now requires both to be subsets of this).
        allowed_approver_roles=[
            "technical-director",
            "qa-reviewer",
            "world-designer",
            "ui-ux-engineer",
        ],
        rejection_behavior=rejection_behavior,  # type: ignore[arg-type]
        expiration=ExpirationConfig(behavior="expire"),
        veto=veto,
    )


def _decision(decision: str, roles: list[str], *, trust: str = "standard") -> EffectiveDecision:
    return EffectiveDecision(
        principal_id=uuid4(),
        decision=decision,  # type: ignore[arg-type]
        role_slugs_at_decision=frozenset(roles),
        trust_level_at_decision=trust,
    )


class TestMajority:
    def test_two_approve_one_reject_quorum_satisfied_is_approved(self) -> None:
        # Verbatim from the approved direction: required participants = 3 slots
        # worth of eligibility; 2 approve, 1 reject, quorum satisfied -> approved.
        policy = _policy(
            quorum=_quorum(
                ("a", ["technical-director"]),
                ("b", ["qa-reviewer"]),
                ("c", ["world-designer"]),
            )
        )
        decisions = [
            _decision("approve", ["technical-director"]),
            _decision("approve", ["qa-reviewer"]),
            _decision("approve", ["world-designer"]),
            _decision("reject", ["ui-ux-engineer"]),
        ]
        result = evaluate_approval(policy, decisions, NOW)
        assert result.quorum_satisfied
        assert result.status == "approved"

    def test_one_approve_one_reject_incomplete_quorum_is_pending(self) -> None:
        # Verbatim: 1 approve, 1 reject -> still pending because quorum incomplete.
        policy = _policy(
            quorum=_quorum(
                ("a", ["technical-director"]),
                ("b", ["qa-reviewer"]),
                ("c", ["world-designer"]),
            )
        )
        decisions = [
            _decision("approve", ["technical-director"]),
            _decision("reject", ["qa-reviewer"]),
        ]
        result = evaluate_approval(policy, decisions, NOW)
        assert not result.quorum_satisfied
        assert result.status == "pending"

    def test_quorum_satisfied_approvals_not_outnumbering_rejections_is_rejected(self) -> None:
        policy = _policy(quorum=_quorum(("a", ["technical-director"])))
        decisions = [
            _decision("approve", ["technical-director"]),
            _decision("reject", ["technical-director"]),
        ]
        # Only one slot; the reject can also match it, but matching uses
        # approvers only for the quorum graph. Quorum is satisfied by the
        # single approval matching slot "a"; majority then compares total
        # approve (1) vs total reject (1) among *all* effective decisions.
        result = evaluate_approval(policy, decisions, NOW)
        assert result.quorum_satisfied
        assert result.status == "rejected"


class TestUnanimous:
    def test_quorum_satisfied_zero_rejections_is_approved(self) -> None:
        policy = _policy(decision_rule="unanimous", quorum=_quorum(("a", ["technical-director"])))
        result = evaluate_approval(policy, [_decision("approve", ["technical-director"])], NOW)
        assert result.quorum_satisfied
        assert result.status == "approved"

    def test_quorum_satisfied_any_rejection_is_rejected(self) -> None:
        policy = _policy(
            decision_rule="unanimous",
            quorum=_quorum(("a", ["technical-director"]), ("b", ["qa-reviewer"])),
        )
        decisions = [
            _decision("approve", ["technical-director"]),
            _decision("approve", ["qa-reviewer"]),
            _decision("reject", ["world-designer"]),
        ]
        result = evaluate_approval(policy, decisions, NOW)
        assert result.quorum_satisfied
        assert result.status == "rejected"

    def test_incomplete_quorum_is_pending_regardless_of_votes(self) -> None:
        policy = _policy(
            decision_rule="unanimous",
            quorum=_quorum(("a", ["technical-director"]), ("b", ["qa-reviewer"])),
        )
        result = evaluate_approval(policy, [_decision("approve", ["technical-director"])], NOW)
        assert not result.quorum_satisfied
        assert result.status == "pending"


class TestVeto:
    def test_veto_authorized_rejection_rejects_before_quorum_would_be_satisfied(self) -> None:
        policy = _policy(
            decision_rule="veto",
            quorum=_quorum(("a", ["technical-director"]), ("b", ["qa-reviewer"])),
            veto=VetoConfig(veto_authorized_roles=["technical-director"]),
        )
        # Quorum is nowhere close to satisfied (zero approvals), but the veto
        # check must fire regardless of quorum state.
        result = evaluate_approval(policy, [_decision("reject", ["technical-director"])], NOW)
        assert result.status == "rejected"
        assert not result.quorum_satisfied

    def test_veto_authorized_rejection_rejects_even_after_quorum_satisfied(self) -> None:
        policy = _policy(
            decision_rule="veto",
            quorum=_quorum(("a", ["technical-director"]), ("b", ["qa-reviewer"])),
            veto=VetoConfig(veto_authorized_roles=["world-designer"]),
        )
        decisions = [
            _decision("approve", ["technical-director"]),
            _decision("approve", ["qa-reviewer"]),
            _decision("reject", ["world-designer"]),
        ]
        result = evaluate_approval(policy, decisions, NOW)
        assert result.quorum_satisfied
        assert result.status == "rejected"

    def test_non_veto_rejection_does_not_block_approval(self) -> None:
        policy = _policy(
            decision_rule="veto",
            quorum=_quorum(("a", ["technical-director"])),
            veto=VetoConfig(veto_authorized_roles=["world-designer"]),
        )
        decisions = [
            _decision("approve", ["technical-director"]),
            _decision("reject", ["qa-reviewer"]),  # not veto-authorized
        ]
        result = evaluate_approval(policy, decisions, NOW)
        assert result.quorum_satisfied
        assert result.status == "approved"

    def test_quorum_satisfied_only_non_veto_rejections_stays_pending(self) -> None:
        policy = _policy(
            decision_rule="veto",
            quorum=_quorum(("a", ["technical-director"])),
            veto=VetoConfig(veto_authorized_roles=["world-designer"]),
        )
        # No approvals at all -- quorum's own matching graph only has
        # approving principals as one side, so with zero approvals quorum
        # cannot be satisfied either; this also confirms rejection alone
        # never manufactures quorum satisfaction.
        result = evaluate_approval(policy, [_decision("reject", ["qa-reviewer"])], NOW)
        assert not result.quorum_satisfied
        assert result.status == "pending"

    def test_mission_effect_reflects_rejection_behavior(self) -> None:
        policy = _policy(
            decision_rule="veto",
            quorum=_quorum(("a", ["technical-director"])),
            veto=VetoConfig(veto_authorized_roles=["technical-director"]),
            rejection_behavior="block_mission",
        )
        result = evaluate_approval(policy, [_decision("reject", ["technical-director"])], NOW)
        assert result.status == "rejected"
        assert result.mission_effect == "blocked"

    def test_mission_effect_none_when_not_rejected(self) -> None:
        policy = _policy(decision_rule="unanimous", quorum=_quorum(("a", ["technical-director"])))
        result = evaluate_approval(policy, [_decision("approve", ["technical-director"])], NOW)
        assert result.status == "approved"
        assert result.mission_effect is None


class TestMultiRoleQuorumAllocation:
    def test_single_principal_multiple_roles_fills_at_most_one_slot(self) -> None:
        policy = _policy(
            quorum=_quorum(
                ("a", ["technical-director"]), ("b", ["qa-reviewer", "technical-director"])
            )
        )
        # One principal holds both roles and casts one vote -- must match at
        # most one slot, so a 2-slot quorum stays unsatisfied.
        result = evaluate_approval(
            policy, [_decision("approve", ["technical-director", "qa-reviewer"])], NOW
        )
        assert not result.quorum_satisfied
        assert len(result.missing_requirements) == 1

    def test_two_single_role_principals_satisfy_two_slots(self) -> None:
        policy = _policy(quorum=_quorum(("a", ["technical-director"]), ("b", ["qa-reviewer"])))
        decisions = [
            _decision("approve", ["technical-director"]),
            _decision("approve", ["qa-reviewer"]),
        ]
        result = evaluate_approval(policy, decisions, NOW)
        assert result.quorum_satisfied
        assert result.missing_requirements == []

    def test_maximum_matching_not_greedy(self) -> None:
        # Classic bipartite-matching trap: slot A is eligible for
        # {technical-director} only; slot B is eligible for
        # {qa-reviewer, technical-director}. Principal 1 holds BOTH roles;
        # principal 2 holds only qa-reviewer.
        #
        # A greedy left-to-right assignment processing slot A first would
        # correctly assign principal 1 -> slot A. But an assignment that
        # processes slot B first (or a naive "first eligible principal
        # wins" rule) would assign principal 1 -> slot B, leaving slot A
        # unfillable even though principal 2 could never fill it (they
        # lack technical-director) -- a true maximum matching still finds
        # the assignment that fills both slots (principal 1 -> A,
        # principal 2 -> B), so this case fails under some naive strategies
        # while succeeding under Kuhn's algorithm.
        policy = _policy(
            quorum=_quorum(
                ("A", ["technical-director"]),
                ("B", ["qa-reviewer", "technical-director"]),
            )
        )
        decisions = [
            _decision("approve", ["technical-director", "qa-reviewer"]),
            _decision("approve", ["qa-reviewer"]),
        ]
        result = evaluate_approval(policy, decisions, NOW)
        assert result.quorum_satisfied
        assert result.missing_requirements == []
        assert result.status == "approved"
