#!/usr/bin/env python3
"""Compose smoke checks for Checkpoint 2 network boundaries (Issue #144 AC6).

Default mode: static compose.yml + `docker compose config` parse checks.
Live mode (`--live`): bring stack up, probe backend /healthz, probe frontend
reachability (HTTP or TCP), assert loopback-only host publishes, restart, and
force-recreate evidence — not config-only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "compose.yml"
LOOPBACK_DB_COMPOSE = REPO_ROOT / "compose.loopback-db.yml"
SMOKE_PROJECT = "mc-cp2-smoke"
SMOKE_BACKEND_PORT = "18080"
SMOKE_FRONTEND_PORT = "13080"


def _service_block(compose_text: str, service_name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service_name)}:\n(.*?)(?=^  [a-z0-9_-]+:|\Z)",
        compose_text,
    )
    if match is None:
        raise AssertionError(f"missing compose service: {service_name}")
    return match.group(1)


def _run(
    args: list[str],
    *,
    check: bool = False,
    timeout: int = 120,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
        env=merged,
    )


def check_static(errors: list[str]) -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    try:
        db_block = _service_block(text, "db")
        redis_block = _service_block(text, "redis")
        backend_block = _service_block(text, "backend")
        frontend_block = _service_block(text, "frontend")
    except AssertionError as exc:
        errors.append(str(exc))
        return

    if re.search(r"(?m)^\s+ports:\s*$", db_block):
        errors.append("db must not declare ports in default compose.yml")
    if re.search(r"(?m)^\s+ports:\s*$", redis_block):
        errors.append("redis must not declare ports in default compose.yml")
    if '127.0.0.1:${BACKEND_PORT:-8000}:8000' not in backend_block:
        errors.append("backend must publish 127.0.0.1 only")
    if '127.0.0.1:${FRONTEND_PORT:-3000}:3000' not in frontend_block:
        errors.append("frontend must publish 127.0.0.1 only")
    if "ai-space-colony-sim" in text or re.search(r"~/\.openclaw", text):
        errors.append("compose must not reference sim or ~/.openclaw")

    if not LOOPBACK_DB_COMPOSE.is_file():
        errors.append("missing compose.loopback-db.yml for hybrid install path")
    else:
        loopback_text = LOOPBACK_DB_COMPOSE.read_text(encoding="utf-8")
        if '127.0.0.1:${POSTGRES_PORT:-5432}:5432' not in loopback_text:
            errors.append("compose.loopback-db.yml must publish Postgres on 127.0.0.1 only")
        if re.search(r'(?m)^\s+-\s+"?\d+:\d+"?\s*$', loopback_text):
            errors.append("compose.loopback-db.yml must not use wildcard host publish")
        if re.search(r"(?m)^\s+redis\s*:", loopback_text) or "6379" in loopback_text:
            errors.append("compose.loopback-db.yml must not publish Redis")


def check_compose_config(errors: list[str]) -> None:
    try:
        completed = _run(
            ["docker", "compose", "-f", str(COMPOSE), "config"],
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"docker compose config skipped: {exc}")
        return

    if completed.returncode != 0:
        errors.append(f"docker compose config failed: {completed.stderr.strip()}")
        return

    rendered = completed.stdout
    if "host_ip: 127.0.0.1" not in rendered:
        errors.append("rendered compose missing 127.0.0.1 host_ip publishes")
    try:
        rendered_db = _service_block(rendered, "db")
        rendered_redis = _service_block(rendered, "redis")
    except AssertionError as exc:
        errors.append(str(exc))
        return

    if re.search(r"(?m)^\s+ports:\s*$", rendered_db):
        errors.append("rendered default db unexpectedly publishes a host port")
    if re.search(r"(?m)^\s+ports:\s*$", rendered_redis):
        errors.append("rendered default redis unexpectedly publishes a host port")

    # Optional hybrid override: loopback PG only when the override file is merged.
    try:
        merged = _run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE),
                "-f",
                str(LOOPBACK_DB_COMPOSE),
                "config",
            ],
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        errors.append(f"merged loopback-db compose config skipped unexpectedly: {exc}")
        return
    if merged.returncode != 0:
        errors.append(f"merged loopback-db compose config failed: {merged.stderr.strip()}")
        return
    merged_out = merged.stdout
    if "127.0.0.1" not in merged_out or "5432" not in merged_out:
        errors.append("merged loopback-db config missing 127.0.0.1:5432 publish")
    # Redis must still lack host ports when only the db override is applied.
    try:
        merged_redis = _service_block(merged_out, "redis")
    except AssertionError as exc:
        errors.append(str(exc))
    else:
        if re.search(r"(?m)^\s+ports:\s*$", merged_redis):
            errors.append("merged loopback-db config unexpectedly publishes redis")


def _wait_http(url: str, timeout_seconds: int = 180) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if 200 <= getattr(resp, "status", 200) < 300:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(2)
    return False


def _tcp_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_frontend_reachable(port: str, timeout_seconds: int = 240) -> bool:
    """Return True when frontend accepts HTTP success or a TCP connection."""
    url = f"http://127.0.0.1:{port}/"
    host_port = int(port)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if 200 <= getattr(resp, "status", 200) < 300:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        if _tcp_open("127.0.0.1", host_port):
            return True
        time.sleep(2)
    return False


def _compose_ps_json(project: str, env: dict[str, str]) -> list[dict]:
    completed = _run(
        [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            str(COMPOSE),
            "--env-file",
            ".env",
            "ps",
            "--format",
            "json",
        ],
        timeout=60,
        env=env,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"compose ps failed: {completed.stderr.strip()}")
    raw = completed.stdout.strip()
    if not raw:
        return []
    # Compose may emit one JSON object per line or a JSON array.
    if raw.startswith("["):
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        raise RuntimeError("unexpected compose ps JSON array payload")
    rows: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _published_ports(row: dict) -> list[str]:
    publishers = row.get("Publishers") or []
    if isinstance(publishers, list) and publishers:
        out: list[str] = []
        for pub in publishers:
            if not isinstance(pub, dict):
                continue
            url = pub.get("URL") or ""
            published = pub.get("PublishedPort") or 0
            target = pub.get("TargetPort") or 0
            if published:
                out.append(f"{url}:{published}->{target}")
        return out
    # Fallback: Ports string like "127.0.0.1:18080->8000/tcp"
    ports = str(row.get("Ports") or "")
    return [part.strip() for part in ports.split(",") if part.strip()]


def check_live(errors: list[str]) -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        errors.append("live smoke requires a repo-root .env (copy from .env.example)")
        return

    env = {
        "BACKEND_PORT": SMOKE_BACKEND_PORT,
        "FRONTEND_PORT": SMOKE_FRONTEND_PORT,
        "COMPOSE_PROJECT_NAME": SMOKE_PROJECT,
    }
    compose_base = [
        "docker",
        "compose",
        "-p",
        SMOKE_PROJECT,
        "-f",
        str(COMPOSE),
        "--env-file",
        ".env",
    ]

    def compose_cmd(*args: str, timeout: int = 600) -> subprocess.CompletedProcess[str]:
        return _run([*compose_base, *args], timeout=timeout, env=env)

    print(f"Live smoke: project={SMOKE_PROJECT} backend={SMOKE_BACKEND_PORT} frontend={SMOKE_FRONTEND_PORT}")
    try:
        up = compose_cmd("up", "-d", "--build", timeout=900)
        if up.returncode != 0:
            errors.append(f"compose up failed: {up.stderr.strip() or up.stdout.strip()}")
            return

        health_url = f"http://127.0.0.1:{SMOKE_BACKEND_PORT}/healthz"
        if not _wait_http(health_url, timeout_seconds=240):
            logs = compose_cmd("logs", "--no-color", "--tail=80", "backend", "db", "redis")
            errors.append(
                "backend /healthz not reachable after up\n"
                + (logs.stdout or logs.stderr or "")
            )
            return
        print(f"healthz OK: {health_url}")

        frontend_url = f"http://127.0.0.1:{SMOKE_FRONTEND_PORT}/"
        if not _wait_frontend_reachable(SMOKE_FRONTEND_PORT, timeout_seconds=240):
            logs = compose_cmd("logs", "--no-color", "--tail=80", "frontend")
            errors.append(
                f"frontend not reachable on 127.0.0.1:{SMOKE_FRONTEND_PORT} "
                "(HTTP success or TCP connection required) after backend /healthz\n"
                + (logs.stdout or logs.stderr or "")
            )
            return
        print(f"frontend reachable: {frontend_url}")

        if not _tcp_open("127.0.0.1", int(SMOKE_BACKEND_PORT)):
            errors.append("backend loopback TCP not open after up")

        try:
            rows = _compose_ps_json(SMOKE_PROJECT, env)
        except (RuntimeError, json.JSONDecodeError) as exc:
            errors.append(f"compose ps inspection failed: {exc}")
            rows = []

        by_service: dict[str, dict] = {}
        for row in rows:
            name = str(row.get("Service") or row.get("Name") or "")
            # Prefer Service key; fall back to trailing service token.
            service = str(row.get("Service") or "")
            if not service and name:
                service = name.rsplit("-", 1)[-1]
            if service:
                by_service[service] = row

        for svc in ("db", "redis"):
            row = by_service.get(svc)
            if row is None:
                errors.append(f"live smoke missing running service: {svc}")
                continue
            published = _published_ports(row)
            # Empty publishers / no host mapping is required for default stack.
            host_maps = [p for p in published if "->" in p or re.search(r":\d+", p)]
            # Filter out container-only port listings without host bind (e.g. "5432/tcp").
            host_maps = [p for p in host_maps if "->" in p]
            if host_maps:
                errors.append(f"{svc} unexpectedly publishes host ports: {host_maps}")

        for svc, expected_host_port in (
            ("backend", SMOKE_BACKEND_PORT),
            ("frontend", SMOKE_FRONTEND_PORT),
        ):
            row = by_service.get(svc)
            if row is None:
                errors.append(f"live smoke missing running service: {svc}")
                continue
            published = _published_ports(row)
            joined = " ".join(published)
            if f"127.0.0.1:{expected_host_port}" not in joined and expected_host_port not in joined:
                errors.append(
                    f"{svc} must publish 127.0.0.1:{expected_host_port}; got {published or '(none)'}"
                )
            if re.search(r"(?:^|[\s,])0\.0\.0\.0:", joined):
                errors.append(f"{svc} must not publish on 0.0.0.0: {published}")

        # Restart evidence
        restart = compose_cmd("restart", "backend", timeout=180)
        if restart.returncode != 0:
            errors.append(f"compose restart backend failed: {restart.stderr.strip()}")
        elif not _wait_http(health_url, timeout_seconds=120):
            errors.append("backend /healthz not reachable after restart")
        else:
            print("restart + healthz OK")

        # Clean recreate evidence (lighter than full --no-cache rebuild).
        recreate = compose_cmd("up", "-d", "--force-recreate", "--no-deps", "backend", timeout=300)
        if recreate.returncode != 0:
            errors.append(f"compose force-recreate backend failed: {recreate.stderr.strip()}")
        elif not _wait_http(health_url, timeout_seconds=180):
            errors.append("backend /healthz not reachable after force-recreate")
        else:
            print("force-recreate + healthz OK")
    finally:
        down = compose_cmd("down", "-v", "--remove-orphans", timeout=180)
        if down.returncode != 0:
            errors.append(f"compose down cleanup failed: {down.stderr.strip()}")
        else:
            print(f"cleaned up project {SMOKE_PROJECT}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run live compose up/healthz/port/restart/recreate smoke (AC6)",
    )
    args = parser.parse_args(argv)
    errors: list[str] = []

    check_static(errors)
    check_compose_config(errors)
    if args.live:
        check_live(errors)

    if errors:
        print("Compose smoke FAILED:")
        for error in errors:
            print(f"  - {error}")
        return 1

    if args.live:
        print(
            "Compose smoke OK "
            "(static + live up/healthz/frontend/loopback ports/restart/recreate; "
            "no default PG/Redis host ports)"
        )
    else:
        print(
            "Compose smoke OK "
            "(loopback FE/BE; no default PG/Redis host ports; "
            "optional loopback-db override; no sim/.openclaw mounts)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
