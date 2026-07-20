# ruff: noqa: INP001
"""Tests for ADR-23 D8 mutation/write hard-disable and fail-closed startup."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.auth_mode import AuthMode
from app.core.config import Settings
from app.core.mutation_guard import (
    MUTATIONS_DISABLED_CODE,
    MutationHardDisableMiddleware,
    enforce_mutations_hard_disabled,
    inventory_mutating_routes,
)
from app.main import app

BASE_URL = "http://localhost:8000"
VALID_LOCAL_TOKEN = "a" * 50


@pytest.fixture
def main_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """HTTP client against the real app without requiring a live database."""
    monkeypatch.setattr("app.main.init_db", AsyncMock())
    with TestClient(app) as client:
        yield client


def test_settings_reject_mutations_hard_disabled_false() -> None:
    with pytest.raises(ValidationError, match="MUTATIONS_HARD_DISABLED must be true"):
        Settings(
            _env_file=None,
            auth_mode=AuthMode.LOCAL,
            local_auth_token=VALID_LOCAL_TOKEN,
            base_url=BASE_URL,
            mutations_hard_disabled=False,
        )


def test_inventory_mutating_routes_finds_post_put_patch_delete() -> None:
    probe = FastAPI()
    router = APIRouter()

    @router.get("/items")
    def list_items() -> dict[str, str]:
        return {"ok": "yes"}

    @router.post("/items")
    def create_item() -> dict[str, str]:
        return {"ok": "created"}

    @router.delete("/items/{item_id}")
    def delete_item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    probe.include_router(router)
    assert inventory_mutating_routes(probe) == [
        ("DELETE", "/items/{item_id}"),
        ("POST", "/items"),
    ]


def test_enforce_fail_closed_when_mutations_not_hard_disabled() -> None:
    probe = FastAPI()

    @probe.post("/write")
    def write() -> dict[str, bool]:
        return {"ok": True}

    with pytest.raises(RuntimeError, match="Fail-closed startup"):
        enforce_mutations_hard_disabled(probe, hard_disabled=False)


def test_enforce_passes_when_hard_disabled() -> None:
    probe = FastAPI()

    @probe.post("/write")
    def write() -> dict[str, bool]:
        return {"ok": True}

    enforce_mutations_hard_disabled(probe, hard_disabled=True)


def test_middleware_rejects_mutating_methods() -> None:
    probe = FastAPI()

    @probe.get("/read")
    def read() -> dict[str, bool]:
        return {"ok": True}

    @probe.post("/write")
    def write() -> dict[str, bool]:
        return {"ok": True}

    probe.add_middleware(MutationHardDisableMiddleware, enabled=True)
    client = TestClient(probe)

    assert client.get("/read").status_code == 200
    blocked = client.post("/write", json={})
    assert blocked.status_code == 405
    body = blocked.json()
    assert body["code"] == MUTATIONS_DISABLED_CODE
    assert body["detail"]["code"] == MUTATIONS_DISABLED_CODE


def test_main_app_inventories_mutating_routes_and_blocks_them(
    main_client: TestClient,
) -> None:
    mutating = inventory_mutating_routes(app)
    assert len(mutating) >= 1
    enforce_mutations_hard_disabled(app, hard_disabled=True)

    assert main_client.get("/healthz").status_code == 200

    method, path = mutating[0]
    request_path = path
    for part in path.split("/"):
        if part.startswith("{") and part.endswith("}"):
            request_path = request_path.replace(part, "00000000-0000-0000-0000-000000000000")

    response = main_client.request(method, request_path)
    assert response.status_code == 405
    assert response.json()["code"] == MUTATIONS_DISABLED_CODE


def test_every_inherited_mutating_route_is_inert(main_client: TestClient) -> None:
    """AC5: every inherited mutation/write route returns no action capability."""
    mutating = inventory_mutating_routes(app)
    assert mutating, "expected inherited mutating routes to still be registered"

    failures: list[str] = []
    for method, path in mutating:
        request_path = path
        for part in path.split("/"):
            if part.startswith("{") and part.endswith("}"):
                request_path = request_path.replace(
                    part,
                    "00000000-0000-0000-0000-000000000000",
                )
        response = main_client.request(method, request_path)
        if response.status_code != 405:
            failures.append(f"{method} {path} -> {response.status_code}")
            continue
        payload: dict[str, Any] = response.json()
        if payload.get("code") != MUTATIONS_DISABLED_CODE:
            failures.append(f"{method} {path} -> unexpected body {payload!r}")

    assert not failures, "mutating routes must be hard-disabled:\n" + "\n".join(failures)
