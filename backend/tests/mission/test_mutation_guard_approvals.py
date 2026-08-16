# ruff: noqa: INP001
"""Slice 5A Checkpoint D: mutation-guard tests for the D8a approval routes.

`MUTATION_ALLOWLIST` (exact literal `(method, path)` tuples) cannot express
the two parameterized D8a routes -- the real runtime path contains an actual
UUID, never the literal string `{request_id}`. These tests exercise the
strict UUID-segment pattern matcher (`app.core.mutation_guard`) against a
probe FastAPI app, using the same pattern as
`test_github_client_and_guard.py::test_middleware_allows_only_manual_refresh`.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.mutation_guard import MUTATIONS_DISABLED_CODE, MutationHardDisableMiddleware

VALID_UUID = str(uuid4())


def _probe_app() -> FastAPI:
    app = FastAPI()

    @app.post("/api/v1/mission/refresh")
    def refresh() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/v1/mission/approvals")
    def create_approval() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/v1/mission/approvals/{request_id}/decisions")
    def submit_decision(request_id: str) -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/v1/mission/approvals/{request_id}/supersede")
    def supersede_decision(request_id: str) -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/v1/mission/approvals/{request_id}/delete")
    def delete_approval(request_id: str) -> dict[str, bool]:
        return {"ok": True}

    @app.patch("/api/v1/mission/approvals/{request_id}")
    def patch_approval(request_id: str) -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/v1/mission/approvals/{request_id}/decisions/extra")
    def extra_segment(request_id: str) -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/v1/boards")
    def boards() -> dict[str, bool]:
        return {"ok": True}

    @app.put("/api/v1/mission/approvals/{request_id}/decisions")
    def put_decisions(request_id: str) -> dict[str, bool]:
        return {"ok": True}

    @app.patch("/api/v1/mission/approvals/{request_id}/decisions")
    def patch_decisions(request_id: str) -> dict[str, bool]:
        return {"ok": True}

    @app.delete("/api/v1/mission/approvals/{request_id}/decisions")
    def delete_decisions(request_id: str) -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(MutationHardDisableMiddleware, enabled=True)
    return app


def _client() -> TestClient:
    return TestClient(_probe_app())


def _assert_blocked(response) -> None:  # type: ignore[no-untyped-def]
    assert response.status_code == 405
    assert response.json()["code"] == MUTATIONS_DISABLED_CODE


class TestApprovalMutationsAllowed:
    def test_create_approval_allowed(self) -> None:
        response = _client().post("/api/v1/mission/approvals", json={})
        assert response.status_code == 200

    def test_submit_decision_allowed(self) -> None:
        response = _client().post(f"/api/v1/mission/approvals/{VALID_UUID}/decisions", json={})
        assert response.status_code == 200

    def test_supersede_decision_allowed(self) -> None:
        response = _client().post(f"/api/v1/mission/approvals/{VALID_UUID}/supersede", json={})
        assert response.status_code == 200

    def test_manual_refresh_still_allowed(self) -> None:
        # Regression: the pre-existing D3 entry is untouched by the D8a extension.
        response = _client().post("/api/v1/mission/refresh", json={})
        assert response.status_code == 200


class TestUnrelatedRoutesRemainBlocked:
    def test_unrelated_post_blocked(self) -> None:
        _assert_blocked(_client().post("/api/v1/boards", json={}))

    def test_unrelated_put_blocked(self) -> None:
        _assert_blocked(_client().put(f"/api/v1/mission/approvals/{VALID_UUID}/decisions", json={}))

    def test_unrelated_patch_blocked(self) -> None:
        _assert_blocked(
            _client().patch(f"/api/v1/mission/approvals/{VALID_UUID}/decisions", json={})
        )

    def test_unrelated_delete_blocked(self) -> None:
        _assert_blocked(_client().delete(f"/api/v1/mission/approvals/{VALID_UUID}/decisions"))


class TestNearMissApprovalRoutesRemainBlocked:
    def test_non_uuid_segment_blocked(self) -> None:
        _assert_blocked(_client().post("/api/v1/mission/approvals/foo", json={}))

    def test_delete_verb_path_blocked(self) -> None:
        _assert_blocked(_client().post(f"/api/v1/mission/approvals/{VALID_UUID}/delete", json={}))

    def test_patch_on_request_id_blocked(self) -> None:
        _assert_blocked(_client().patch(f"/api/v1/mission/approvals/{VALID_UUID}", json={}))

    def test_extra_path_segment_blocked(self) -> None:
        _assert_blocked(
            _client().post(f"/api/v1/mission/approvals/{VALID_UUID}/decisions/extra", json={})
        )

    def test_non_uuid_segment_on_supersede_blocked(self) -> None:
        _assert_blocked(_client().post("/api/v1/mission/approvals/not-a-uuid/supersede", json={}))


def test_mutation_allowlist_cardinality_unchanged_by_pattern_matching() -> None:
    # The literal-tuple allowlist itself must never grow to accommodate the
    # parameterized routes -- it's already asserted exhaustively in
    # test_github_client_and_guard.py; this pins the pattern set's own size
    # so a future accidental fourth route fails a test, not just review.
    from app.core.mutation_guard import _MUTATION_ALLOWLIST_PATTERNS

    assert len(_MUTATION_ALLOWLIST_PATTERNS) == 2
    assert {method for method, _ in _MUTATION_ALLOWLIST_PATTERNS} == {"POST"}
