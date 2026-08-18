# ruff: noqa: INP001
"""Slice 5A Checkpoint D: security tests for the approval routes.

These tests prove the routes correctly *wire* Checkpoint C's already-proven
domain security behavior into the HTTP boundary -- they deliberately don't
re-derive that behavior. Builds and drives the FastAPI test app the same way
tests/mission/test_mission_approvals_api.py does.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from fastapi_pagination import add_pagination
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

import app.mission.approval_service as approval_service
from app.api.deps import require_user_auth
from app.api.mission_approvals import router as mission_approvals_router
from app.core.auth import AuthContext
from app.core.time import utcnow
from app.db.session import get_session
from app.mission.github_client import GitHubReadClient
from app.models.mc_approval import (
    McApprovalPolicy,
    McApprovalPolicyActivation,
    McPrincipal,
    McPrincipalRole,
)
from app.models.users import User

POLICY_DEFINITION = {
    "decision_rule": "majority",
    "quorum": {"slots": [{"slot": "a", "eligible_roles": ["technical-director"]}]},
    "allowed_approver_principal_types": ["human"],
    "allowed_approver_roles": ["technical-director", "qa-reviewer"],
    "rejection_behavior": "leave_mission_unchanged",
    "expiration": {"behavior": "expire"},
}

CREATE_BODY = {
    "policy_key": "implementation_review",
    "scope_type": "action",
    "mission_source_repo": "Mhaizza/ai-space-colony-mission-control",
    "mission_card_kind": "issue",
    "mission_card_number": 16,
}


@asynccontextmanager
async def _engine_and_maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.connect() as conn, conn.begin():
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


def _build_app(
    session_maker: async_sessionmaker[AsyncSession], *, auth: AuthContext | None
) -> FastAPI:
    app = FastAPI()
    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(mission_approvals_router)
    app.include_router(api_v1)

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            yield session

    def _override_require_user_auth() -> AuthContext:
        if auth is None:
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "authentication_required", "message": "authentication required"},
            )
        return auth

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[require_user_auth] = _override_require_user_auth
    add_pagination(app)
    return app


def _auth_for(clerk_user_id: str) -> AuthContext:
    return AuthContext(
        actor_type="user",
        user=User(clerk_user_id=clerk_user_id, email=f"{clerk_user_id}@example.com", name="Test"),
    )


async def _seed_principal(
    maker: async_sessionmaker[AsyncSession],
    *,
    external_subject: str,
    roles: list[str],
    principal_type: str = "human",
    enabled: bool = True,
) -> McPrincipal:
    async with maker() as session:
        principal = McPrincipal(
            principal_type=principal_type,
            display_name=external_subject,
            trust_level="standard",
            enabled=enabled,
            external_provider="local",
            external_subject=external_subject,
        )
        session.add(principal)
        await session.commit()
        await session.refresh(principal)
        for role in roles:
            session.add(McPrincipalRole(principal_id=principal.id, role_slug=role))
        await session.commit()
        return principal


async def _seed_policy(
    maker: async_sessionmaker[AsyncSession], definition: dict[str, object] | None = None
) -> McApprovalPolicy:
    async with maker() as session:
        policy = McApprovalPolicy(
            policy_key="implementation_review",
            version=1,
            definition=definition or POLICY_DEFINITION,
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


@pytest_asyncio.fixture
async def maker(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with _engine_and_maker() as sessionmaker_:
        monkeypatch.setattr(approval_service, "async_session_maker", sessionmaker_)
        yield sessionmaker_


class TestAuthentication:
    @pytest.mark.asyncio
    async def test_unauthenticated_create_returns_401(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_policy(maker)
        app = _build_app(maker, auth=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/mission/approvals", json=CREATE_BODY, headers={"Idempotency-Key": "k1"}
            )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_unauthenticated_list_returns_401(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        app = _build_app(maker, auth=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/mission/approvals")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_unauthenticated_detail_returns_401(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        app = _build_app(maker, auth=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/v1/mission/approvals/{uuid4()}")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_unauthenticated_decision_returns_401(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        app = _build_app(maker, auth=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/mission/approvals/{uuid4()}/decisions",
                json={"decision": "approve", "reason": None},
                headers={"Idempotency-Key": "k1"},
            )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_unauthenticated_supersede_returns_401(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        app = _build_app(maker, auth=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/mission/approvals/{uuid4()}/supersede",
                json={
                    "supersedes_decision_id": str(uuid4()),
                    "decision": "approve",
                    "reason": None,
                },
                headers={"Idempotency-Key": "k1"},
            )
        assert response.status_code == 401


class TestPrincipalAuthorization:
    @pytest.mark.asyncio
    async def test_unregistered_principal_returns_403(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_policy(maker)
        app = _build_app(maker, auth=_auth_for("nobody"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/mission/approvals", json=CREATE_BODY, headers={"Idempotency-Key": "k1"}
            )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "principal_not_registered"

    @pytest.mark.asyncio
    async def test_disabled_principal_returns_403(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(
            maker, external_subject="disabled", roles=["technical-director"], enabled=False
        )
        await _seed_policy(maker)
        app = _build_app(maker, auth=_auth_for("disabled"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/mission/approvals", json=CREATE_BODY, headers={"Idempotency-Key": "k1"}
            )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "principal_disabled"

    @pytest.mark.asyncio
    async def test_system_principal_rejected_on_manual_path(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(
            maker, external_subject="sys", roles=["technical-director"], principal_type="system"
        )
        await _seed_policy(maker)
        app = _build_app(maker, auth=_auth_for("sys"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/mission/approvals", json=CREATE_BODY, headers={"Idempotency-Key": "k1"}
            )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "principal_not_authorized"

    @pytest.mark.asyncio
    async def test_ai_principal_rejected_on_manual_path(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(
            maker, external_subject="ai-actor", roles=["technical-director"], principal_type="ai"
        )
        await _seed_policy(maker)
        app = _build_app(maker, auth=_auth_for("ai-actor"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/mission/approvals", json=CREATE_BODY, headers={"Idempotency-Key": "k1"}
            )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "principal_not_authorized"

    @pytest.mark.asyncio
    async def test_wrong_role_returns_403(self, maker: async_sessionmaker[AsyncSession]) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(maker, external_subject="outsider", roles=[])
        await _seed_policy(maker)

        app = _build_app(maker, auth=_auth_for("creator"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/mission/approvals", json=CREATE_BODY, headers={"Idempotency-Key": "k1"}
            )
            request_id = created.json()["request_id"]

        app2 = _build_app(maker, auth=_auth_for("outsider"))
        async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/mission/approvals/{request_id}/decisions",
                json={"decision": "approve", "reason": None},
                headers={"Idempotency-Key": "d1"},
            )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "principal_not_authorized"

    @pytest.mark.asyncio
    async def test_insufficient_trust_returns_403(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        trust_gated = {**POLICY_DEFINITION, "trust_requirements": ["trusted"]}
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(maker, external_subject="approver", roles=["technical-director"])
        await _seed_policy(maker, definition=trust_gated)

        app = _build_app(maker, auth=_auth_for("creator"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/mission/approvals", json=CREATE_BODY, headers={"Idempotency-Key": "k1"}
            )
            request_id = created.json()["request_id"]

        app2 = _build_app(maker, auth=_auth_for("approver"))
        async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/mission/approvals/{request_id}/decisions",
                json={"decision": "approve", "reason": None},
                headers={"Idempotency-Key": "d1"},
            )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "principal_trust_insufficient"


class TestCallerAwareDetailFailsClosed:
    """`can_decide`/`current_principal_decision` (Slice 5B Checkpoint A) must
    never grant capability for an unregistered/disabled caller -- the detail
    read route fails closed the same way the mutation routes already do,
    never silently defaulting to `can_decide=false` and inventing a 200."""

    @pytest.mark.asyncio
    async def test_unregistered_caller_detail_returns_403(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_policy(maker)
        app = _build_app(maker, auth=_auth_for("creator"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/mission/approvals", json=CREATE_BODY, headers={"Idempotency-Key": "k1"}
            )
            request_id = created.json()["request_id"]

        app2 = _build_app(maker, auth=_auth_for("nobody"))
        async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as client:
            response = await client.get(f"/api/v1/mission/approvals/{request_id}")
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "principal_not_registered"

    @pytest.mark.asyncio
    async def test_disabled_caller_detail_returns_403(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(
            maker, external_subject="disabled", roles=["technical-director"], enabled=False
        )
        await _seed_policy(maker)
        app = _build_app(maker, auth=_auth_for("creator"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/mission/approvals", json=CREATE_BODY, headers={"Idempotency-Key": "k1"}
            )
            request_id = created.json()["request_id"]

        app2 = _build_app(maker, auth=_auth_for("disabled"))
        async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as client:
            response = await client.get(f"/api/v1/mission/approvals/{request_id}")
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "principal_disabled"


class TestClientCannotSpoofServerResolvedFields:
    def test_create_schema_has_no_principal_or_policy_version_fields(self) -> None:
        from app.schemas.mission_approvals import CreateApprovalRequest

        field_names = set(CreateApprovalRequest.model_fields)
        assert "principal_id" not in field_names
        assert "role" not in field_names
        assert "trust_level" not in field_names
        assert "policy_version" not in field_names
        assert "policy_id" not in field_names

    def test_decision_schema_has_no_principal_or_status_fields(self) -> None:
        from app.schemas.mission_approvals import SubmitDecisionRequest

        field_names = set(SubmitDecisionRequest.model_fields)
        assert "principal_id" not in field_names
        assert "role" not in field_names
        assert "trust_level" not in field_names
        assert "status" not in field_names
        assert "quorum_satisfied" not in field_names
        assert "mission_effect" not in field_names


class TestConflictStates:
    @pytest.mark.asyncio
    async def test_decision_on_terminal_request_returns_409(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(maker, external_subject="approver", roles=["technical-director"])
        await _seed_principal(maker, external_subject="late", roles=["qa-reviewer"])
        await _seed_policy(maker)

        app = _build_app(maker, auth=_auth_for("creator"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/mission/approvals", json=CREATE_BODY, headers={"Idempotency-Key": "k1"}
            )
            request_id = created.json()["request_id"]

        app2 = _build_app(maker, auth=_auth_for("approver"))
        async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as client:
            await client.post(
                f"/api/v1/mission/approvals/{request_id}/decisions",
                json={"decision": "approve", "reason": None},
                headers={"Idempotency-Key": "d1"},
            )

        app3 = _build_app(maker, auth=_auth_for("late"))
        async with AsyncClient(transport=ASGITransport(app=app3), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/mission/approvals/{request_id}/decisions",
                json={"decision": "approve", "reason": None},
                headers={"Idempotency-Key": "d2"},
            )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "approval_request_terminal"

    @pytest.mark.asyncio
    async def test_duplicate_effective_decision_returns_409(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        two_slot = {
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
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(maker, external_subject="approver", roles=["technical-director"])
        await _seed_policy(maker, definition=two_slot)

        app = _build_app(maker, auth=_auth_for("creator"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/mission/approvals", json=CREATE_BODY, headers={"Idempotency-Key": "k1"}
            )
            request_id = created.json()["request_id"]

        app2 = _build_app(maker, auth=_auth_for("approver"))
        async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as client:
            await client.post(
                f"/api/v1/mission/approvals/{request_id}/decisions",
                json={"decision": "approve", "reason": None},
                headers={"Idempotency-Key": "d1"},
            )
            response = await client.post(
                f"/api/v1/mission/approvals/{request_id}/decisions",
                json={"decision": "approve", "reason": None},
                headers={"Idempotency-Key": "d2"},
            )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "approval_decision_exists"

    @pytest.mark.asyncio
    async def test_cross_principal_supersede_returns_409(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        two_slot = {
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
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(maker, external_subject="voter", roles=["technical-director"])
        await _seed_principal(maker, external_subject="other", roles=["qa-reviewer"])
        await _seed_policy(maker, definition=two_slot)

        app = _build_app(maker, auth=_auth_for("creator"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/mission/approvals", json=CREATE_BODY, headers={"Idempotency-Key": "k1"}
            )
            request_id = created.json()["request_id"]

        app2 = _build_app(maker, auth=_auth_for("voter"))
        async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as client:
            first_vote = await client.post(
                f"/api/v1/mission/approvals/{request_id}/decisions",
                json={"decision": "reject", "reason": None},
                headers={"Idempotency-Key": "d1"},
            )
            decision_id = first_vote.json()["decision_id"]

        app3 = _build_app(maker, auth=_auth_for("other"))
        async with AsyncClient(transport=ASGITransport(app=app3), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/mission/approvals/{request_id}/supersede",
                json={
                    "supersedes_decision_id": decision_id,
                    "decision": "approve",
                    "reason": None,
                },
                headers={"Idempotency-Key": "s1"},
            )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "invalid_supersede"


class TestGitHubBoundary:
    @pytest.mark.asyncio
    async def test_approval_mutations_never_call_github_client(
        self, maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        graphql_spy = AsyncMock(side_effect=AssertionError("GitHubReadClient.graphql was called"))
        rest_spy = AsyncMock(side_effect=AssertionError("GitHubReadClient.rest_get was called"))
        monkeypatch.setattr(GitHubReadClient, "graphql", graphql_spy)
        monkeypatch.setattr(GitHubReadClient, "rest_get", rest_spy)

        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(maker, external_subject="approver", roles=["technical-director"])
        await _seed_policy(maker)

        app = _build_app(maker, auth=_auth_for("creator"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/mission/approvals", json=CREATE_BODY, headers={"Idempotency-Key": "k1"}
            )
            request_id = created.json()["request_id"]
            await client.get("/api/v1/mission/approvals")
            await client.get(f"/api/v1/mission/approvals/{request_id}")

        app2 = _build_app(maker, auth=_auth_for("approver"))
        async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as client:
            await client.post(
                f"/api/v1/mission/approvals/{request_id}/decisions",
                json={"decision": "approve", "reason": None},
                headers={"Idempotency-Key": "d1"},
            )

        graphql_spy.assert_not_called()
        rest_spy.assert_not_called()
