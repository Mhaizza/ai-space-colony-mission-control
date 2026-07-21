# ruff: noqa: INP001
"""Slice 3 startup-probe enforcement (no bypass) tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

import app.main as main_module
from app.core.auth_mode import AuthMode
from app.core.config import Settings
from app.mission.probes import RepoProbeTarget, run_startup_probes


@dataclass
class FakeResp:
    status_code: int
    json_body: Any
    headers: dict[str, str] = field(default_factory=dict)
    text: str = ""


class ScriptedProbeClient:
    """rest_get/graphql delegate to handlers; records aclose()."""

    def __init__(self, *, rest_handler: Any, graphql_handler: Any) -> None:
        self._rest = rest_handler
        self._graphql = graphql_handler
        self.closed = False

    async def rest_get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self._rest(path, dict(params or {}))

    async def graphql(self, query: str, variables: dict[str, Any] | None = None) -> Any:
        return self._graphql(query, dict(variables or {}))

    async def aclose(self) -> None:
        self.closed = True


_SELF = RepoProbeTarget(qualifier="self", owner="Mhaizza", repo="ai-space-colony-sim")


def _graphql_ok(_query: str, _vars: dict[str, Any]) -> FakeResp:
    return FakeResp(200, {"data": {"user": {"projectV2": {"id": "PVT_1"}}}})


# --------------------------------------------------------------------------- #
# Configuration: no bypass
# --------------------------------------------------------------------------- #


def test_disabling_probes_with_adapter_enabled_fails_startup() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(
            auth_mode=AuthMode.LOCAL,
            local_auth_token="x" * 60,
            base_url="http://localhost:8000",
            github_pat="ghp_example_token",
            github_run_startup_probes=False,
        )
    assert "GITHUB_RUN_STARTUP_PROBES" in str(exc.value)


def test_probes_enabled_config_is_accepted() -> None:
    settings = Settings(
        auth_mode=AuthMode.LOCAL,
        local_auth_token="x" * 60,
        base_url="http://localhost:8000",
        github_pat="ghp_example_token",
        github_run_startup_probes=True,
    )
    assert settings.github_adapter_enabled is True
    assert settings.github_run_startup_probes is True


# --------------------------------------------------------------------------- #
# Probe failures prevent startup (and therefore polling)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_failed_scope_probe_raises() -> None:
    def rest_handler(path: str, _params: dict[str, Any]) -> FakeResp:
        if path == "/rate_limit":
            return FakeResp(200, {}, headers={"x-oauth-scopes": "read:project, repo"})
        return FakeResp(200, {})

    client = ScriptedProbeClient(rest_handler=rest_handler, graphql_handler=_graphql_ok)
    with pytest.raises(RuntimeError, match="exactly"):
        await run_startup_probes(
            client,  # type: ignore[arg-type]
            project_owner="Mhaizza",
            project_number=4,
            repos=[_SELF],
        )


@pytest.mark.asyncio
async def test_failed_auth_probe_raises() -> None:
    def rest_handler(path: str, _params: dict[str, Any]) -> FakeResp:
        if path == "/rate_limit":
            return FakeResp(401, {})
        return FakeResp(200, {})

    client = ScriptedProbeClient(rest_handler=rest_handler, graphql_handler=_graphql_ok)
    with pytest.raises(RuntimeError, match="auth probe failed"):
        await run_startup_probes(
            client,  # type: ignore[arg-type]
            project_owner="Mhaizza",
            project_number=4,
            repos=[_SELF],
        )


@pytest.mark.asyncio
async def test_failed_project_probe_raises() -> None:
    def rest_handler(path: str, _params: dict[str, Any]) -> FakeResp:
        if path == "/rate_limit":
            return FakeResp(200, {}, headers={"x-oauth-scopes": "read:project"})
        return FakeResp(200, {})

    def graphql_handler(_query: str, _vars: dict[str, Any]) -> FakeResp:
        return FakeResp(200, {"data": {"user": {"projectV2": None}}})

    client = ScriptedProbeClient(rest_handler=rest_handler, graphql_handler=graphql_handler)
    with pytest.raises(RuntimeError, match="cannot read configured user-owned GitHub Project"):
        await run_startup_probes(
            client,  # type: ignore[arg-type]
            project_owner="Mhaizza",
            project_number=4,
            repos=[_SELF],
        )


@pytest.mark.asyncio
async def test_failed_repository_probe_raises() -> None:
    def rest_handler(path: str, _params: dict[str, Any]) -> FakeResp:
        if path == "/rate_limit":
            return FakeResp(200, {}, headers={"x-oauth-scopes": "read:project"})
        if path == "/repos/Mhaizza/ai-space-colony-sim":
            return FakeResp(404, {})
        return FakeResp(200, [])

    client = ScriptedProbeClient(rest_handler=rest_handler, graphql_handler=_graphql_ok)
    with pytest.raises(RuntimeError, match="repository probe failed"):
        await run_startup_probes(
            client,  # type: ignore[arg-type]
            project_owner="Mhaizza",
            project_number=4,
            repos=[_SELF],
        )


# --------------------------------------------------------------------------- #
# _start_github_adapter always runs probes; failure closes client, no polling
# --------------------------------------------------------------------------- #


class _FakePoller:
    instances: list[_FakePoller] = []

    def __init__(self, *, interval_seconds: int, tick: Any) -> None:
        self.interval_seconds = interval_seconds
        self.tick = tick
        self.started = False
        _FakePoller.instances.append(self)

    def start(self) -> None:
        self.started = True


class _FakeAdapterClient:
    instances: list[_FakeAdapterClient] = []

    def __init__(self, *, token: str) -> None:
        self.token = token
        self.closed = False
        _FakeAdapterClient.instances.append(self)

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_fakes() -> None:
    _FakePoller.instances.clear()
    _FakeAdapterClient.instances.clear()


@pytest.mark.asyncio
async def test_start_adapter_always_invokes_probes_then_polls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe_calls: list[bool] = []

    async def fake_probes(_client: Any, **_kwargs: Any) -> None:
        probe_calls.append(True)

    monkeypatch.setattr(main_module, "GitHubReadClient", _FakeAdapterClient)
    monkeypatch.setattr(main_module, "PollingScheduler", _FakePoller)
    monkeypatch.setattr(main_module, "run_startup_probes", fake_probes)
    monkeypatch.setattr(main_module.settings, "github_pat", "ghp_example_token")

    app = FastAPI()
    await main_module._start_github_adapter(app)

    assert probe_calls == [True]  # probes always run before polling
    assert _FakePoller.instances and _FakePoller.instances[0].started is True
    assert app.state.github_poller is _FakePoller.instances[0]
    assert _FakeAdapterClient.instances[0].closed is False


@pytest.mark.asyncio
async def test_start_adapter_failed_probe_closes_client_and_skips_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_probes(_client: Any, **_kwargs: Any) -> None:
        raise RuntimeError("Fail-closed: repository probe failed")

    monkeypatch.setattr(main_module, "GitHubReadClient", _FakeAdapterClient)
    monkeypatch.setattr(main_module, "PollingScheduler", _FakePoller)
    monkeypatch.setattr(main_module, "run_startup_probes", failing_probes)
    monkeypatch.setattr(main_module.settings, "github_pat", "ghp_example_token")

    app = FastAPI()
    with pytest.raises(RuntimeError, match="repository probe failed"):
        await main_module._start_github_adapter(app)

    assert _FakeAdapterClient.instances[0].closed is True  # resources closed
    assert _FakePoller.instances == []  # polling never started
    assert getattr(app.state, "github_poller", None) is None
