# ruff: noqa: INP001
"""Slice 5A Checkpoint D: approval_read_service tests.

Focus: `QuorumRequirementView` (and the rest of `ApprovalDetailResponse`)
must never drift from `approval_evaluator.evaluate_approval()`'s own
canonical output. No test here reimplements matching -- every assertion
either reuses `evaluate_approval()` directly as the source of truth, or
reuses fixtures already proven against it in test_approval_evaluator.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.time import utcnow
from app.mission.approval_evaluator import EffectiveDecision, evaluate_approval
from app.mission.approval_policy import ApprovalPolicyDefinition
from app.mission.approval_read_service import get_approval_detail
from app.models.mc_approval import (
    McApprovalDecision,
    McApprovalPolicy,
    McApprovalPolicyActivation,
    McApprovalRequest,
    McPrincipal,
)


@asynccontextmanager
async def _engine_and_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.connect() as conn, conn.begin():
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            yield session
    finally:
        await engine.dispose()


async def _make_principal(
    session: AsyncSession, roles: list[str], **overrides: object
) -> McPrincipal:
    from app.models.mc_approval import McPrincipalRole

    defaults: dict[str, object] = {
        "principal_type": "human",
        "display_name": "Test Principal",
        "trust_level": "standard",
        "enabled": True,
    }
    defaults.update(overrides)
    principal = McPrincipal(**defaults)  # type: ignore[arg-type]
    session.add(principal)
    await session.commit()
    await session.refresh(principal)
    for role in roles:
        session.add(McPrincipalRole(principal_id=principal.id, role_slug=role))
    await session.commit()
    return principal


async def _make_policy(session: AsyncSession, definition: dict[str, object]) -> McApprovalPolicy:
    policy = McApprovalPolicy(
        policy_key="implementation_review",
        version=1,
        definition=definition,
        created_at=utcnow(),
    )
    session.add(policy)
    await session.commit()
    await session.refresh(policy)
    session.add(
        McApprovalPolicyActivation(
            policy_key="implementation_review",
            active_policy_id=policy.id,
            updated_at=utcnow(),
        )
    )
    await session.commit()
    return policy


async def _make_request(
    session: AsyncSession, policy: McApprovalPolicy, creator: McPrincipal
) -> McApprovalRequest:
    request = McApprovalRequest(
        policy_id=policy.id,
        scope_type="action",
        mission_source_repo="Mhaizza/ai-space-colony-mission-control",
        mission_card_kind="issue",
        mission_card_number=16,
        created_by_principal_id=creator.id,
        creation_source="human",
        status="pending",
        created_at=utcnow(),
    )
    session.add(request)
    await session.commit()
    await session.refresh(request)
    return request


async def _cast_decision(
    session: AsyncSession,
    request: McApprovalRequest,
    principal: McPrincipal,
    decision: str,
    roles: list[str],
) -> McApprovalDecision:
    row = McApprovalDecision(
        request_id=request.id,
        principal_id=principal.id,
        decision=decision,
        role_slugs_at_decision=roles,
        trust_level_at_decision="standard",
        created_at=utcnow(),
    )
    session.add(row)
    await session.commit()
    return row


TWO_SLOT_POLICY: dict[str, object] = {
    "decision_rule": "unanimous",
    "quorum": {
        "slots": [
            {"slot": "a", "eligible_roles": ["technical-director"]},
            {"slot": "b", "eligible_roles": ["qa-reviewer"]},
        ]
    },
    "allowed_approver_principal_types": ["human"],
    "allowed_approver_roles": ["technical-director", "qa-reviewer"],
    "rejection_behavior": "leave_mission_unchanged",
    "expiration": {"behavior": "expire"},
}

# The exact matching-trap fixture from
# test_approval_evaluator.py::TestMultiRoleQuorumAllocation::test_maximum_matching_not_greedy --
# reused verbatim, not reinvented, so the read path is proven against the
# same non-trivial case the write-path evaluator's own tests already cover.
MULTI_ROLE_POLICY: dict[str, object] = {
    "decision_rule": "majority",
    "quorum": {
        "slots": [
            {"slot": "A", "eligible_roles": ["technical-director"]},
            {"slot": "B", "eligible_roles": ["qa-reviewer", "technical-director"]},
        ]
    },
    "allowed_approver_principal_types": ["human"],
    "allowed_approver_roles": ["technical-director", "qa-reviewer"],
    "rejection_behavior": "leave_mission_unchanged",
    "expiration": {"behavior": "expire"},
}


class TestQuorumRequirementsShape:
    @pytest.mark.asyncio
    async def test_every_quorum_slot_returned_with_eligible_roles(self) -> None:
        async with _engine_and_session() as session:
            creator = await _make_principal(session, ["technical-director"])
            policy = await _make_policy(session, TWO_SLOT_POLICY)
            request = await _make_request(session, policy, creator)

            detail = await get_approval_detail(session, request.id)
            assert detail is not None
            assert {q.slot for q in detail.quorum_requirements} == {"a", "b"}
            by_slot = {q.slot: q for q in detail.quorum_requirements}
            assert by_slot["a"].eligible_roles == ["technical-director"]
            assert by_slot["b"].eligible_roles == ["qa-reviewer"]

    @pytest.mark.asyncio
    async def test_slot_satisfied_state_agrees_with_evaluate_approval(self) -> None:
        async with _engine_and_session() as session:
            creator = await _make_principal(session, ["technical-director"])
            voter_a = await _make_principal(session, ["technical-director"], display_name="A")
            policy = await _make_policy(session, TWO_SLOT_POLICY)
            request = await _make_request(session, policy, creator)
            await _cast_decision(session, request, voter_a, "approve", ["technical-director"])

            detail = await get_approval_detail(session, request.id)
            assert detail is not None

            definition = ApprovalPolicyDefinition.model_validate(TWO_SLOT_POLICY)
            evaluation = evaluate_approval(
                definition,
                [
                    EffectiveDecision(
                        principal_id=voter_a.id,
                        decision="approve",
                        role_slugs_at_decision=frozenset({"technical-director"}),
                        trust_level_at_decision="standard",
                    )
                ],
                datetime.now(),  # noqa: DTZ005 - evaluator ignores `now` at this checkpoint
            )

            for q in detail.quorum_requirements:
                assert q.satisfied == (q.slot not in evaluation.missing_requirements)
            assert detail.quorum_satisfied == evaluation.quorum_satisfied
            assert detail.missing_requirements == evaluation.missing_requirements

    @pytest.mark.asyncio
    async def test_multi_role_matching_case_matches_evaluator_semantics(self) -> None:
        async with _engine_and_session() as session:
            creator = await _make_principal(session, ["technical-director"])
            dual_role = await _make_principal(
                session, ["technical-director", "qa-reviewer"], display_name="Dual"
            )
            qa_only = await _make_principal(session, ["qa-reviewer"], display_name="QA")
            policy = await _make_policy(session, MULTI_ROLE_POLICY)
            request = await _make_request(session, policy, creator)
            await _cast_decision(
                session, request, dual_role, "approve", ["technical-director", "qa-reviewer"]
            )
            await _cast_decision(session, request, qa_only, "approve", ["qa-reviewer"])

            detail = await get_approval_detail(session, request.id)
            assert detail is not None

            definition = ApprovalPolicyDefinition.model_validate(MULTI_ROLE_POLICY)
            evaluation = evaluate_approval(
                definition,
                [
                    EffectiveDecision(
                        principal_id=dual_role.id,
                        decision="approve",
                        role_slugs_at_decision=frozenset({"technical-director", "qa-reviewer"}),
                        trust_level_at_decision="standard",
                    ),
                    EffectiveDecision(
                        principal_id=qa_only.id,
                        decision="approve",
                        role_slugs_at_decision=frozenset({"qa-reviewer"}),
                        trust_level_at_decision="standard",
                    ),
                ],
                datetime.now(),  # noqa: DTZ005
            )

            assert evaluation.quorum_satisfied
            assert evaluation.missing_requirements == []
            for q in detail.quorum_requirements:
                assert q.satisfied == (q.slot not in evaluation.missing_requirements)
            assert detail.quorum_satisfied is True
            assert detail.missing_requirements == []
            # No slot->principal assignment is exposed anywhere on the response.
            for q in detail.quorum_requirements:
                assert not hasattr(q, "principal_id")

    @pytest.mark.asyncio
    async def test_detail_response_sufficient_for_frontend_without_recomputation(self) -> None:
        async with _engine_and_session() as session:
            creator = await _make_principal(session, ["technical-director"])
            voter_a = await _make_principal(session, ["technical-director"], display_name="A")
            policy = await _make_policy(session, TWO_SLOT_POLICY)
            request = await _make_request(session, policy, creator)
            await _cast_decision(session, request, voter_a, "approve", ["technical-director"])

            detail = await get_approval_detail(session, request.id)
            assert detail is not None

            # Every slot exposes eligible_roles (who can still approve).
            assert all(q.eligible_roles for q in detail.quorum_requirements)
            # Every slot exposes a satisfied flag (completion state).
            assert {q.satisfied for q in detail.quorum_requirements} == {True, False}
            # missing_requirements names exactly the unsatisfied slots.
            unsatisfied = {q.slot for q in detail.quorum_requirements if not q.satisfied}
            assert set(detail.missing_requirements) == unsatisfied
            # Effective decisions are present without requiring the caller to
            # infer a principal-to-slot binding to render anything above.
            assert len(detail.effective_decisions) == 1

    @pytest.mark.asyncio
    async def test_unknown_request_returns_none(self) -> None:
        from uuid import uuid4

        async with _engine_and_session() as session:
            detail = await get_approval_detail(session, uuid4())
        assert detail is None
