# ruff: noqa: INP001
"""Slice 5A Checkpoint D: API-level tests for the approval routes.

Builds an isolated FastAPI test app (mirrors
tests/test_skills_marketplace_api.py's `_build_test_app` pattern):
`get_session` is overridden for the read routes, `require_user_auth` is
overridden to return a fixed principal's identity, and
`app.mission.approval_service.async_session_maker` is monkeypatched so the
mutation routes' internally-opened sessions hit the same in-memory database
as the read routes (mirrors tests/mission/test_approval_service.py's
`maker` fixture).

These tests exercise the HTTP-level contract only -- request/response
shapes, status codes, header handling -- and deliberately reuse
Checkpoint C's already-proven domain behavior (idempotency replay, quorum
evaluation) rather than re-testing it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
from app.models.mc_approval import McApprovalPolicy, McApprovalPolicyActivation, McPrincipal
from app.models.users import User

POLICY_DEFINITION = {
    "decision_rule": "majority",
    "quorum": {"slots": [{"slot": "a", "eligible_roles": ["technical-director"]}]},
    "allowed_approver_principal_types": ["human"],
    "allowed_approver_roles": ["technical-director", "qa-reviewer"],
    "rejection_behavior": "leave_mission_unchanged",
    "expiration": {"behavior": "expire"},
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


def _build_app(session_maker: async_sessionmaker[AsyncSession], *, auth: AuthContext) -> FastAPI:
    app = FastAPI()
    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(mission_approvals_router)
    app.include_router(api_v1)

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            yield session

    def _override_require_user_auth() -> AuthContext:
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
) -> McPrincipal:
    from app.models.mc_approval import McPrincipalRole

    async with maker() as session:
        principal = McPrincipal(
            principal_type="human",
            display_name=external_subject,
            trust_level="standard",
            enabled=True,
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


async def _seed_policy(maker: async_sessionmaker[AsyncSession]) -> McApprovalPolicy:
    async with maker() as session:
        policy = McApprovalPolicy(
            policy_key="implementation_review",
            version=1,
            definition=POLICY_DEFINITION,
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


CREATE_BODY = {
    "policy_key": "implementation_review",
    "scope_type": "action",
    "mission_source_repo": "Mhaizza/ai-space-colony-mission-control",
    "mission_card_kind": "issue",
    "mission_card_number": 16,
}


class TestCreateApprovalApi:
    @pytest.mark.asyncio
    async def test_create_returns_expected_shape(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_policy(maker)
        app = _build_app(maker, auth=_auth_for("creator"))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/mission/approvals",
                json=CREATE_BODY,
                headers={"Idempotency-Key": "key-1"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["policy_key"] == "implementation_review"
        assert body["policy_version"] == 1
        assert body["status"] == "pending"
        assert "request_id" in body
        assert "created_by_principal_id" in body

    @pytest.mark.asyncio
    async def test_create_missing_idempotency_key_rejected(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_policy(maker)
        app = _build_app(maker, auth=_auth_for("creator"))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/mission/approvals", json=CREATE_BODY)
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "idempotency_key_required"

    @pytest.mark.asyncio
    async def test_create_empty_idempotency_key_rejected(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_policy(maker)
        app = _build_app(maker, auth=_auth_for("creator"))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/mission/approvals", json=CREATE_BODY, headers={"Idempotency-Key": "  "}
            )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "idempotency_key_required"

    @pytest.mark.asyncio
    async def test_create_replay_same_key_same_payload(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_policy(maker)
        app = _build_app(maker, auth=_auth_for("creator"))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post(
                "/api/v1/mission/approvals", json=CREATE_BODY, headers={"Idempotency-Key": "dup"}
            )
            second = await client.post(
                "/api/v1/mission/approvals", json=CREATE_BODY, headers={"Idempotency-Key": "dup"}
            )
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json() == second.json()

    @pytest.mark.asyncio
    async def test_create_reused_key_different_payload_conflicts(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_policy(maker)
        app = _build_app(maker, auth=_auth_for("creator"))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/v1/mission/approvals", json=CREATE_BODY, headers={"Idempotency-Key": "dup2"}
            )
            different = {**CREATE_BODY, "mission_card_number": 17}
            response = await client.post(
                "/api/v1/mission/approvals", json=different, headers={"Idempotency-Key": "dup2"}
            )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "idempotency_key_reused"


class TestDecisionAndSupersedeApi:
    @pytest.mark.asyncio
    async def test_submit_decision_returns_expected_shape(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(maker, external_subject="approver", roles=["technical-director"])
        await _seed_policy(maker)

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
                json={"decision": "approve", "reason": "looks good"},
                headers={"Idempotency-Key": "d1"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "approved"
        assert body["quorum_satisfied"] is True
        assert body["decision"] == "approve"

    @pytest.mark.asyncio
    async def test_decision_on_unknown_request_returns_404(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="approver", roles=["technical-director"])
        app = _build_app(maker, auth=_auth_for("approver"))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/mission/approvals/{uuid4()}/decisions",
                json={"decision": "approve", "reason": None},
                headers={"Idempotency-Key": "d1"},
            )
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "approval_request_not_found"

    @pytest.mark.asyncio
    async def test_supersede_returns_expected_shape(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_principal(maker, external_subject="voter", roles=["technical-director"])
        await _seed_policy(maker)

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
                json={"decision": "reject", "reason": "initial"},
                headers={"Idempotency-Key": "d1"},
            )
            decision_id = first_vote.json()["decision_id"]

            response = await client.post(
                f"/api/v1/mission/approvals/{request_id}/supersede",
                json={
                    "supersedes_decision_id": decision_id,
                    "decision": "approve",
                    "reason": "resolved",
                },
                headers={"Idempotency-Key": "s1"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["decision"] == "approve"
        assert body["decision_id"] != decision_id


class TestReadApi:
    @pytest.mark.asyncio
    async def test_list_returns_created_request(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        await _seed_policy(maker)
        app = _build_app(maker, auth=_auth_for("creator"))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/v1/mission/approvals", json=CREATE_BODY, headers={"Idempotency-Key": "k1"}
            )
            response = await client.get("/api/v1/mission/approvals")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["limit"] == 200
        assert body["offset"] == 0
        assert body["items"][0]["policy_key"] == "implementation_review"

    @pytest.mark.asyncio
    async def test_detail_returns_quorum_requirements(
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
            response = await client.get(f"/api/v1/mission/approvals/{request_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["decision_rule"] == "majority"
        assert body["quorum_requirements"] == [
            {"slot": "a", "eligible_roles": ["technical-director"], "satisfied": False}
        ]
        assert body["quorum_satisfied"] is False
        assert body["missing_requirements"] == ["a"]

    @pytest.mark.asyncio
    async def test_detail_unknown_request_returns_404(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_principal(maker, external_subject="creator", roles=["technical-director"])
        app = _build_app(maker, auth=_auth_for("creator"))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/v1/mission/approvals/{uuid4()}")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "approval_request_not_found"
