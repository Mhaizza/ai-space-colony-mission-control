# ruff: noqa: INP001
"""Slice 5A Checkpoint B: ApprovalPolicyDefinition schema validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.mission.approval_policy import (
    ApprovalPolicyDefinition,
    ExpirationConfig,
    QuorumRequirement,
    QuorumSlot,
    VetoConfig,
)


def _quorum(*slots: tuple[str, list[str]]) -> QuorumRequirement:
    return QuorumRequirement(
        slots=[QuorumSlot(slot=name, eligible_roles=roles) for name, roles in slots]
    )


def _base_kwargs(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "decision_rule": "majority",
        "quorum": _quorum(("architecture", ["technical-director"])),
        "allowed_approver_principal_types": ["human"],
        "allowed_approver_roles": ["technical-director"],
        "rejection_behavior": "leave_mission_unchanged",
        "expiration": ExpirationConfig(behavior="expire"),
    }
    defaults.update(overrides)
    return defaults


class TestValidPolicies:
    def test_majority_policy_parses(self) -> None:
        ApprovalPolicyDefinition(**_base_kwargs())

    def test_unanimous_policy_parses(self) -> None:
        ApprovalPolicyDefinition(**_base_kwargs(decision_rule="unanimous"))

    def test_veto_policy_parses(self) -> None:
        ApprovalPolicyDefinition(
            **_base_kwargs(
                decision_rule="veto",
                veto=VetoConfig(veto_authorized_roles=["technical-director"]),
            )
        )

    def test_recreate_expiration_with_max_retries_parses(self) -> None:
        # "system" must be present in allowed_approver_principal_types for a
        # "recreate" policy to parse at all -- see
        # TestRecreateRequiresSystemApprover below (Checkpoint E).
        ApprovalPolicyDefinition(
            **_base_kwargs(
                allowed_approver_principal_types=["human", "system"],
                expiration=ExpirationConfig(behavior="recreate", max_auto_retries=3),
            )
        )


class TestVetoConsistency:
    def test_veto_config_without_veto_rule_rejected(self) -> None:
        with pytest.raises(ValidationError, match="veto config is only valid"):
            ApprovalPolicyDefinition(
                **_base_kwargs(
                    decision_rule="majority",
                    veto=VetoConfig(veto_authorized_roles=["technical-director"]),
                )
            )

    def test_veto_rule_without_veto_config_rejected(self) -> None:
        with pytest.raises(ValidationError, match="veto config is required"):
            ApprovalPolicyDefinition(**_base_kwargs(decision_rule="veto"))


class TestExpirationConsistency:
    def test_recreate_without_max_retries_rejected(self) -> None:
        with pytest.raises(ValidationError, match="max_auto_retries is required"):
            ExpirationConfig(behavior="recreate")

    def test_max_retries_without_recreate_rejected(self) -> None:
        with pytest.raises(ValidationError, match="max_auto_retries is only valid"):
            ExpirationConfig(behavior="expire", max_auto_retries=2)


class TestApproverPrincipalTypeExclusion:
    def test_ai_principal_type_rejected(self) -> None:
        # "ai" is not a member of ApproverPrincipalType at all (Human ruling,
        # Option B) -- Pydantic surfaces this as a clean validation error on
        # the literal, not a hand-written runtime check.
        with pytest.raises(ValidationError):
            ApprovalPolicyDefinition(**_base_kwargs(allowed_approver_principal_types=["ai"]))

    def test_human_and_system_principal_types_accepted(self) -> None:
        ApprovalPolicyDefinition(
            **_base_kwargs(allowed_approver_principal_types=["human", "system"])
        )


class TestRoleValidation:
    def test_unknown_role_in_quorum_slot_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unknown role slug"):
            ApprovalPolicyDefinition(
                **_base_kwargs(quorum=_quorum(("architecture", ["not-a-real-role"])))
            )

    def test_unknown_role_in_allowed_approver_roles_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unknown role slug"):
            ApprovalPolicyDefinition(**_base_kwargs(allowed_approver_roles=["not-a-real-role"]))

    def test_unknown_role_in_veto_authorized_roles_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unknown role slug"):
            ApprovalPolicyDefinition(
                **_base_kwargs(
                    decision_rule="veto",
                    veto=VetoConfig(veto_authorized_roles=["not-a-real-role"]),
                )
            )


class TestQuorumStructure:
    def test_duplicate_slot_names_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unique"):
            _quorum(
                ("architecture", ["technical-director"]),
                ("architecture", ["qa-reviewer"]),
            )

    def test_empty_quorum_slots_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QuorumRequirement(slots=[])

    def test_empty_eligible_roles_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QuorumSlot(slot="architecture", eligible_roles=[])


class TestQuorumAndVetoRolesSubsetOfAllowedApprovers:
    """A quorum slot or veto role that isn't itself an allowed approver role
    would require or empower a vote from a role that can never legally cast
    one under the same policy -- an unreachable requirement or an ungranted
    veto power. Both must be subsets of allowed_approver_roles."""

    def test_valid_quorum_subset_accepted(self) -> None:
        ApprovalPolicyDefinition(
            **_base_kwargs(
                allowed_approver_roles=["technical-director", "qa-reviewer"],
                quorum=_quorum(("architecture", ["technical-director"])),
            )
        )

    def test_quorum_role_outside_allowlist_rejected(self) -> None:
        with pytest.raises(ValidationError, match="outside allowed_approver_roles"):
            ApprovalPolicyDefinition(
                **_base_kwargs(
                    allowed_approver_roles=["technical-director"],
                    quorum=_quorum(("architecture", ["qa-reviewer"])),
                )
            )

    def test_valid_veto_subset_accepted(self) -> None:
        ApprovalPolicyDefinition(
            **_base_kwargs(
                decision_rule="veto",
                allowed_approver_roles=["technical-director", "qa-reviewer"],
                quorum=_quorum(("architecture", ["technical-director"])),
                veto=VetoConfig(veto_authorized_roles=["technical-director"]),
            )
        )

    def test_veto_role_outside_allowlist_rejected(self) -> None:
        with pytest.raises(ValidationError, match="outside allowed_approver_roles"):
            ApprovalPolicyDefinition(
                **_base_kwargs(
                    decision_rule="veto",
                    allowed_approver_roles=["technical-director"],
                    quorum=_quorum(("architecture", ["technical-director"])),
                    veto=VetoConfig(veto_authorized_roles=["qa-reviewer"]),
                )
            )


class TestRecreateRequiresSystemApprover:
    """Checkpoint E: bounded auto-retry recreate is always performed by a
    system-typed principal (reconciliation's trusted server automation),
    never a human -- so a policy naming "recreate" must permit "system" as
    an allowed approver/creator type, or the resulting successor could
    never legally be created under it."""

    def test_recreate_without_system_approver_type_rejected(self) -> None:
        with pytest.raises(
            ValidationError, match="requires 'system' in allowed_approver_principal_types"
        ):
            ApprovalPolicyDefinition(
                **_base_kwargs(
                    allowed_approver_principal_types=["human"],
                    expiration=ExpirationConfig(behavior="recreate", max_auto_retries=3),
                )
            )

    def test_recreate_with_system_approver_type_parses(self) -> None:
        ApprovalPolicyDefinition(
            **_base_kwargs(
                allowed_approver_principal_types=["human", "system"],
                expiration=ExpirationConfig(behavior="recreate", max_auto_retries=3),
            )
        )

    def test_recreate_with_system_only_approver_type_parses(self) -> None:
        ApprovalPolicyDefinition(
            **_base_kwargs(
                allowed_approver_principal_types=["system"],
                expiration=ExpirationConfig(behavior="recreate", max_auto_retries=3),
            )
        )

    def test_expire_behavior_does_not_require_system_approver(self) -> None:
        # The new rule is scoped to behavior="recreate" only -- plain expire
        # and block_mission never require "system", human-only policies
        # remain fully valid for those.
        ApprovalPolicyDefinition(
            **_base_kwargs(
                allowed_approver_principal_types=["human"],
                expiration=ExpirationConfig(behavior="expire"),
            )
        )
        ApprovalPolicyDefinition(
            **_base_kwargs(
                allowed_approver_principal_types=["human"],
                expiration=ExpirationConfig(behavior="block_mission"),
            )
        )
