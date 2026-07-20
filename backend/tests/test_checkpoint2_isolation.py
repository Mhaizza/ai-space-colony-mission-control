# ruff: noqa: INP001
"""Isolation and Compose boundary proofs for Issue #144 Checkpoint 2 (ACs 6/8/9)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "compose.yml"
LOOPBACK_DB_COMPOSE_PATH = REPO_ROOT / "compose.loopback-db.yml"

# Paths exercised by local Compose / token bring-up (not inherited UI or gateway packages).
BRING_UP_FILES = (
    REPO_ROOT / "compose.yml",
    REPO_ROOT / "compose.loopback-db.yml",
    REPO_ROOT / "backend" / "Dockerfile",
    REPO_ROOT / "frontend" / "Dockerfile",
    REPO_ROOT / "scripts" / "ensure_local_auth_env.py",
    REPO_ROOT / "scripts" / "rq-docker",
    REPO_ROOT / "Makefile",
    REPO_ROOT / "backend" / "pyproject.toml",
    REPO_ROOT / "frontend" / "package.json",
)

GITHUB_CLIENT_PATTERNS = (
    re.compile(r"\bPyGithub\b"),
    re.compile(r"\boctokit\b", re.IGNORECASE),
    re.compile(r"from\s+github\s+import\b"),
    re.compile(r"import\s+github\b"),
    re.compile(r"api\.github\.com"),
    re.compile(r"@octokit/"),
)

HOST_OPENCLAW_PATH = re.compile(
    r"(~/\.openclaw|Users[/\\][^/\\]+[/\\]\.openclaw|\$HOME/\.openclaw|%USERPROFILE%\\\.openclaw)",
    re.IGNORECASE,
)

DOCKER = shutil.which("docker")


def _service_block(compose_text: str, service_name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service_name)}:\n(.*?)(?=^  [a-z0-9_-]+:|\Z)",
        compose_text,
    )
    assert match is not None, f"missing compose service: {service_name}"
    return match.group(1)


def _docker_compose_config(*extra_files: Path) -> str:
    assert DOCKER is not None
    cmd = [DOCKER, "compose", "-f", str(COMPOSE_PATH)]
    for path in extra_files:
        cmd.extend(["-f", str(path)])
    cmd.append("config")
    completed = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr.strip()
    return completed.stdout


def test_compose_publishes_only_loopback_frontend_and_backend() -> None:
    text = COMPOSE_PATH.read_text(encoding="utf-8")
    db_block = _service_block(text, "db")
    redis_block = _service_block(text, "redis")
    backend_block = _service_block(text, "backend")
    frontend_block = _service_block(text, "frontend")

    assert re.search(r"(?m)^\s+ports:\s*$", db_block) is None
    assert re.search(r"(?m)^\s+ports:\s*$", redis_block) is None
    assert "127.0.0.1:${BACKEND_PORT:-8000}:8000" in backend_block
    assert "127.0.0.1:${FRONTEND_PORT:-3000}:3000" in frontend_block
    assert re.search(r'(?m)^\s+-\s+"?\d+:\d+"?\s*$', backend_block) is None
    assert re.search(r'(?m)^\s+-\s+"?\d+:\d+"?\s*$', frontend_block) is None


def test_loopback_db_override_is_loopback_only() -> None:
    assert LOOPBACK_DB_COMPOSE_PATH.is_file()
    text = LOOPBACK_DB_COMPOSE_PATH.read_text(encoding="utf-8")
    assert "127.0.0.1:${POSTGRES_PORT:-5432}:5432" in text
    assert re.search(r'(?m)^\s+-\s+"?\d+:\d+"?\s*$', text) is None
    assert "6379" not in text


def test_compose_has_no_sim_or_openclaw_host_mounts() -> None:
    text = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "ai-space-colony-sim" not in text
    assert HOST_OPENCLAW_PATH.search(text) is None
    assert "../" not in text
    # Only named volume for postgres data — no bind mounts of host source trees.
    assert "postgres_data:" in text
    assert re.search(r"(?m)^\s+-\s+\./", text) is None
    assert re.search(r"(?m)^\s+type:\s*bind\b", text) is None


def test_backend_listens_on_all_interfaces_in_dockerfile() -> None:
    dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "--host" in dockerfile and "0.0.0.0" in dockerfile


def test_frontend_listens_on_all_interfaces_in_dockerfile() -> None:
    dockerfile = (REPO_ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    assert "0.0.0.0" in dockerfile


@pytest.mark.skipif(DOCKER is None, reason="docker CLI not available")
def test_rendered_default_compose_keeps_db_redis_internal() -> None:
    """Runtime compose config: default stack must not publish PG/Redis host ports."""
    rendered = _docker_compose_config()
    assert "host_ip: 127.0.0.1" in rendered
    db_block = _service_block(rendered, "db")
    redis_block = _service_block(rendered, "redis")
    assert re.search(r"(?m)^\s+ports:\s*$", db_block) is None
    assert re.search(r"(?m)^\s+ports:\s*$", redis_block) is None


@pytest.mark.skipif(DOCKER is None, reason="docker CLI not available")
def test_rendered_loopback_db_override_publishes_postgres_on_loopback_only() -> None:
    """Runtime compose config: hybrid override publishes PG on 127.0.0.1 only."""
    rendered = _docker_compose_config(LOOPBACK_DB_COMPOSE_PATH)
    db_block = _service_block(rendered, "db")
    redis_block = _service_block(rendered, "redis")
    assert "host_ip: 127.0.0.1" in db_block
    assert re.search(r"(?m)^\s+published:\s*[\"']?5432[\"']?\s*$", db_block) is not None
    assert re.search(r"(?m)^\s+ports:\s*$", redis_block) is None


@pytest.mark.skipif(DOCKER is None, reason="docker CLI not available")
def test_running_default_stack_publishes_only_loopback_app_ports() -> None:
    """If a default-project stack is already up, inspect live published ports."""
    assert DOCKER is not None
    completed = subprocess.run(
        [
            DOCKER,
            "compose",
            "-f",
            str(COMPOSE_PATH),
            "ps",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        pytest.skip("default compose stack not running")

    raw = completed.stdout.strip()
    rows: list[dict]
    if raw.startswith("["):
        parsed = json.loads(raw)
        assert isinstance(parsed, list)
        rows = parsed
    else:
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]

    if not rows:
        pytest.skip("default compose stack not running")

    by_service: dict[str, dict] = {}
    for row in rows:
        service = str(row.get("Service") or "")
        if service:
            by_service[service] = row

    # Require the core services; otherwise this is not a full default stack.
    if not {"db", "redis", "backend", "frontend"} <= set(by_service):
        pytest.skip("default compose stack incomplete")

    def published(row: dict) -> str:
        pubs = row.get("Publishers") or []
        if isinstance(pubs, list) and pubs:
            parts: list[str] = []
            for pub in pubs:
                if not isinstance(pub, dict):
                    continue
                url = pub.get("URL") or ""
                port = pub.get("PublishedPort") or 0
                target = pub.get("TargetPort") or 0
                if port:
                    parts.append(f"{url}:{port}->{target}")
            return " ".join(parts)
        return str(row.get("Ports") or "")

    for svc in ("db", "redis"):
        mapping = published(by_service[svc])
        assert "->" not in mapping, f"{svc} must not publish host ports; got {mapping!r}"

    for svc in ("backend", "frontend"):
        mapping = published(by_service[svc])
        assert "127.0.0.1" in mapping, f"{svc} must publish on 127.0.0.1; got {mapping!r}"
        assert "0.0.0.0" not in mapping, f"{svc} must not publish on 0.0.0.0; got {mapping!r}"


def test_no_github_api_client_in_bring_up_paths() -> None:
    violations: list[str] = []
    for path in BRING_UP_FILES:
        assert path.is_file(), f"missing bring-up file: {path}"
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in GITHUB_CLIENT_PATTERNS:
            if pattern.search(text):
                violations.append(f"{path.relative_to(REPO_ROOT)} matches {pattern.pattern}")
    assert not violations, "GitHub API client surface must remain absent:\n" + "\n".join(violations)


def test_no_sim_or_openclaw_runtime_refs_in_bring_up_paths() -> None:
    """AC8/AC9: bring-up tooling must not reference sim or host ~/.openclaw paths."""
    violations: list[str] = []
    for path in BRING_UP_FILES:
        rel = path.relative_to(REPO_ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "ai-space-colony-sim" in line:
                violations.append(f"{rel}:{lineno}: ai-space-colony-sim reference")
            if HOST_OPENCLAW_PATH.search(line):
                violations.append(f"{rel}:{lineno}: host .openclaw path reference")
    assert not violations, "isolation violations:\n" + "\n".join(violations)
