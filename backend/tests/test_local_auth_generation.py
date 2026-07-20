# ruff: noqa: INP001
"""Local-auth token generation path (Issue #144 AC7)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ensure_local_auth_env.py"


def _load_ensure_module():
    spec = importlib.util.spec_from_file_location("ensure_local_auth_env", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generate_token_meets_minimum_length() -> None:
    module = _load_ensure_module()
    token = module.generate_token()
    assert len(token) >= module.MIN_TOKEN_LENGTH
    assert token.lower() not in module.PLACEHOLDERS


def test_ensure_local_auth_env_writes_ignored_env_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_ensure_module()
    root_env = tmp_path / ".env"
    backend_env = tmp_path / "backend" / ".env"
    monkeypatch.setattr(module, "ROOT_ENV", root_env)
    monkeypatch.setattr(module, "BACKEND_ENV", backend_env)

    token = module.ensure_local_auth_env()
    assert len(token) >= 50

    root_values = module._read_env(root_env)
    backend_values = module._read_env(backend_env)
    assert root_values["AUTH_MODE"] == "local"
    assert backend_values["AUTH_MODE"] == "local"
    assert root_values["LOCAL_AUTH_TOKEN"] == token
    assert backend_values["LOCAL_AUTH_TOKEN"] == token
    assert len(root_values["LOCAL_AUTH_TOKEN"]) >= 50


def test_ensure_local_auth_env_keeps_existing_strong_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_ensure_module()
    root_env = tmp_path / ".env"
    backend_env = tmp_path / "backend" / ".env"
    monkeypatch.setattr(module, "ROOT_ENV", root_env)
    monkeypatch.setattr(module, "BACKEND_ENV", backend_env)

    existing = "b" * 50
    root_env.write_text(
        f"AUTH_MODE=local\nLOCAL_AUTH_TOKEN={existing}\nBASE_URL=http://localhost:8000\n",
        encoding="utf-8",
    )

    token = module.ensure_local_auth_env()
    assert token == existing
