"""Typed, Pydantic-validated approval policy schema (Slice 5A Checkpoint B).

This module defines the closed shape a policy's `definition` JSONB payload
must satisfy. It does not persist anything and is not yet wired to any
insert path — `mc_approval_policy.definition` is a plain JSONB column at
this checkpoint (see `app/models/mc_approval.py`); validating a definition
through `ApprovalPolicyDefinition` before it is written is Checkpoint C's
`approval_service.py` responsibility. Shipping the schema now, ahead of that
wiring, is intentional: it lets the schema and the evaluator that consumes
it be reviewed and tested together, independent of the transactional
plumbing that will call them.

Role values everywhere in this module are validated against the existing
closed `RoleSlug` registry (`app/mission/role_slugs.py`) rather than being
freeform strings — Mission Control does not invent a second role vocabulary.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.mission.role_slugs import ROLE_SLUGS

DecisionRule = Literal["majority", "unanimous", "veto"]

# Slice 5A Human ruling (Option B): eligible approvers are Human and system
# principals only. `ai` remains a valid persisted `mc_principal.principal_type`
# (see principals.PrincipalType) but is never a member of this narrower,
# approver-eligible type — the Literal itself has no "ai" member, so a
# policy naming one fails at the type/schema level, not as an ad hoc runtime
# check bolted on afterward.
ApproverPrincipalType = Literal["human", "system"]

RejectionBehavior = Literal["leave_mission_unchanged", "block_mission"]
ExpirationBehaviorKind = Literal["expire", "block_mission", "recreate"]


def _validate_roles(roles: list[str], *, field_name: str) -> list[str]:
    unknown = [role for role in roles if role not in ROLE_SLUGS]
    if unknown:
        msg = f"{field_name} contains unknown role slug(s): {sorted(set(unknown))}"
        raise ValueError(msg)
    return roles


class QuorumSlot(BaseModel):
    """One named quorum requirement, satisfied by any principal holding an eligible role."""

    slot: str = Field(min_length=1)
    eligible_roles: list[str] = Field(min_length=1)

    @field_validator("eligible_roles")
    @classmethod
    def _eligible_roles_known(cls, value: list[str]) -> list[str]:
        return _validate_roles(value, field_name="eligible_roles")


class QuorumRequirement(BaseModel):
    """A policy's full quorum requirement: an ordered list of named, per-role-eligible slots."""

    slots: list[QuorumSlot] = Field(min_length=1)

    @field_validator("slots")
    @classmethod
    def _slot_names_unique(cls, value: list[QuorumSlot]) -> list[QuorumSlot]:
        names = [slot.slot for slot in value]
        if len(names) != len(set(names)):
            msg = f"quorum slot names must be unique, got: {names}"
            raise ValueError(msg)
        return value


class ExpirationConfig(BaseModel):
    """How a pending request is resolved once `expires_at` has passed."""

    behavior: ExpirationBehaviorKind
    max_auto_retries: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _max_auto_retries_matches_behavior(self) -> ExpirationConfig:
        if self.behavior == "recreate" and self.max_auto_retries is None:
            msg = "max_auto_retries is required when behavior is 'recreate'"
            raise ValueError(msg)
        if self.behavior != "recreate" and self.max_auto_retries is not None:
            msg = "max_auto_retries is only valid when behavior is 'recreate'"
            raise ValueError(msg)
        return self


class VetoConfig(BaseModel):
    """Roles authorized to immediately reject a `veto`-rule request."""

    veto_authorized_roles: list[str] = Field(min_length=1)

    @field_validator("veto_authorized_roles")
    @classmethod
    def _veto_roles_known(cls, value: list[str]) -> list[str]:
        return _validate_roles(value, field_name="veto_authorized_roles")


class ApprovalPolicyDefinition(BaseModel):
    """The full, closed shape of one `mc_approval_policy.definition` payload."""

    decision_rule: DecisionRule
    quorum: QuorumRequirement
    allowed_approver_principal_types: list[ApproverPrincipalType] = Field(min_length=1)
    allowed_approver_roles: list[str] = Field(min_length=1)
    trust_requirements: list[str] | None = None
    rejection_behavior: RejectionBehavior
    expiration: ExpirationConfig
    veto: VetoConfig | None = None

    @field_validator("allowed_approver_roles")
    @classmethod
    def _approver_roles_known(cls, value: list[str]) -> list[str]:
        return _validate_roles(value, field_name="allowed_approver_roles")

    @model_validator(mode="after")
    def _veto_config_matches_rule(self) -> ApprovalPolicyDefinition:
        if self.decision_rule == "veto" and self.veto is None:
            msg = "veto config is required when decision_rule is 'veto'"
            raise ValueError(msg)
        if self.decision_rule != "veto" and self.veto is not None:
            msg = "veto config is only valid when decision_rule is 'veto'"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _quorum_and_veto_roles_subset_of_allowed_approvers(self) -> ApprovalPolicyDefinition:
        # A quorum slot or a veto-authorized role that isn't itself an
        # allowed approver role would let a policy require or empower a
        # vote from a role that can never legally cast one under this same
        # policy -- an unreachable quorum requirement or a veto grant to a
        # role that was never authorized to approve in the first place.
        allowed = set(self.allowed_approver_roles)
        for slot in self.quorum.slots:
            outside = sorted(set(slot.eligible_roles) - allowed)
            if outside:
                msg = (
                    f"quorum slot {slot.slot!r} eligible_roles {outside} "
                    "outside allowed_approver_roles"
                )
                raise ValueError(msg)
        if self.veto is not None:
            outside = sorted(set(self.veto.veto_authorized_roles) - allowed)
            if outside:
                msg = f"veto.veto_authorized_roles {outside} outside allowed_approver_roles"
                raise ValueError(msg)
        return self
