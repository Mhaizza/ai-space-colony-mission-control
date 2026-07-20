#!/usr/bin/env python3
"""Generate a local-auth token into ignored .env files (ADR-23 D7 / Issue #144 AC7).

When Clerk is unconfigured / AUTH_MODE=local, this script ensures LOCAL_AUTH_TOKEN
is a random value of at least 50 characters in the repo-root and backend ignored
.env files. Example env files keep placeholders only.

Usage:
  python scripts/ensure_local_auth_env.py
  python scripts/ensure_local_auth_env.py --force
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT_ENV = REPO_ROOT / ".env"
BACKEND_ENV = REPO_ROOT / "backend" / ".env"
MIN_TOKEN_LENGTH = 50
PLACEHOLDERS = frozenset(
    {
        "",
        "change-me",
        "changeme",
        "replace-me",
        "replace-with-strong-random-token",
    }
)


def generate_token(min_length: int = MIN_TOKEN_LENGTH) -> str:
    """Return a URL-safe random token of at least min_length characters."""
    # token_urlsafe(48) yields ~64 chars; grow if needed.
    nbytes = 48
    while True:
        token = secrets.token_urlsafe(nbytes)
        if len(token) >= min_length:
            return token
        nbytes += 8


def _parse_env_lines(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _read_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return _parse_env_lines(path.read_text(encoding="utf-8"))


def _upsert_env_value(path: Path, key: str, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()

    prefix = f"{key}="
    replaced = False
    new_lines: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#") or "=" not in line:
            new_lines.append(line)
            continue
        current_key = line.split("=", 1)[0].strip()
        if current_key == key:
            new_lines.append(f"{prefix}{value}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(f"{prefix}{value}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _token_is_usable(token: str) -> bool:
    normalized = token.strip()
    if not normalized or normalized.lower() in PLACEHOLDERS:
        return False
    return len(normalized) >= MIN_TOKEN_LENGTH


def ensure_local_auth_env(*, force: bool = False) -> str:
    """Ensure AUTH_MODE=local and a strong LOCAL_AUTH_TOKEN in ignored .env files."""
    existing = {**_read_env(ROOT_ENV), **_read_env(BACKEND_ENV)}
    auth_mode = existing.get("AUTH_MODE", "local").strip().lower() or "local"
    clerk_key = existing.get("CLERK_SECRET_KEY", "").strip()

    # Generation path applies when Clerk is unconfigured / local mode.
    if auth_mode not in {"", "local"} and clerk_key:
        raise SystemExit(
            "Skipping token generation: AUTH_MODE is not local and CLERK_SECRET_KEY is set. "
            "Unset Clerk or set AUTH_MODE=local to use this path."
        )

    current_token = existing.get("LOCAL_AUTH_TOKEN", "")
    if force or not _token_is_usable(current_token):
        token = generate_token()
    else:
        token = current_token.strip()

    for env_path in (ROOT_ENV, BACKEND_ENV):
        _upsert_env_value(env_path, "AUTH_MODE", "local")
        _upsert_env_value(env_path, "LOCAL_AUTH_TOKEN", token)
        if "BASE_URL" not in _read_env(env_path):
            _upsert_env_value(env_path, "BASE_URL", "http://localhost:8000")
        if "MUTATIONS_HARD_DISABLED" not in _read_env(env_path):
            _upsert_env_value(env_path, "MUTATIONS_HARD_DISABLED", "true")

    return token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing usable LOCAL_AUTH_TOKEN with a newly generated one.",
    )
    args = parser.parse_args(argv)
    token = ensure_local_auth_env(force=args.force)
    print(f"LOCAL_AUTH_TOKEN ready ({len(token)} chars) in {ROOT_ENV} and {BACKEND_ENV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
