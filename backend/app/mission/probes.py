"""Fail-closed startup capability probes (ADR-23 D5)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.mission.github_client import (
    PROJECT_ITEMS_QUERY,
    GitHubReadClient,
    verify_exact_read_project_scope,
)


@dataclass(frozen=True, slots=True)
class RepoProbeTarget:
    qualifier: str
    owner: str
    repo: str


@dataclass(frozen=True, slots=True)
class ProbeReport:
    ok: bool
    oauth_scopes: frozenset[str]
    project_ok: bool
    repo_results: dict[str, bool]
    details: tuple[str, ...]


async def run_startup_probes(
    client: GitHubReadClient,
    *,
    project_owner: str,
    project_number: int,
    repos: list[RepoProbeTarget],
) -> ProbeReport:
    """Probe scope + Project + required public REST surfaces for each repo."""
    details: list[str] = []
    # Scope probe via /rate_limit (lightweight authenticated GET).
    rate = await client.rest_get("/rate_limit")
    if rate.status_code >= 400:
        raise RuntimeError(
            f"Fail-closed: GitHub auth probe failed status={rate.status_code}"
        )
    oauth_header = rate.headers.get("x-oauth-scopes")
    verify_exact_read_project_scope(oauth_header)
    scopes = frozenset(
        p.strip().lower() for p in (oauth_header or "").split(",") if p.strip()
    )

    project_ok = await _probe_project(client, project_owner, project_number, details)
    if not project_ok:
        raise RuntimeError(
            "Fail-closed: cannot read configured user-owned GitHub Project; "
            + "; ".join(details)
        )

    repo_results: dict[str, bool] = {}
    for target in repos:
        ok = await _probe_repo(client, target, details)
        repo_results[target.qualifier] = ok
        if not ok:
            raise RuntimeError(
                f"Fail-closed: repository probe failed for {target.qualifier} "
                f"({target.owner}/{target.repo}); " + "; ".join(details)
            )

    return ProbeReport(
        ok=True,
        oauth_scopes=scopes,
        project_ok=project_ok,
        repo_results=repo_results,
        details=tuple(details),
    )


async def _probe_project(
    client: GitHubReadClient,
    owner: str,
    number: int,
    details: list[str],
) -> bool:
    response = await client.graphql(
        PROJECT_ITEMS_QUERY,
        {"login": owner, "number": number, "after": None},
    )
    if response.status_code >= 400:
        details.append(f"project graphql status={response.status_code}")
        return False
    body = response.json_body
    if not isinstance(body, dict):
        details.append("project graphql returned non-object")
        return False
    if body.get("errors"):
        details.append(f"project graphql errors={body.get('errors')!r}")
        return False
    data: Any = body.get("data") or {}
    user = data.get("user") if isinstance(data, dict) else None
    project = user.get("projectV2") if isinstance(user, dict) else None
    if not isinstance(project, dict) or not project.get("id"):
        details.append("project V2 not accessible")
        return False
    details.append(f"project ok id={project['id']}")
    return True


async def _probe_repo(
    client: GitHubReadClient,
    target: RepoProbeTarget,
    details: list[str],
) -> bool:
    owner, repo = target.owner, target.repo
    paths = [
        f"/repos/{owner}/{repo}",
        f"/repos/{owner}/{repo}/issues?state=open&per_page=1",
        f"/repos/{owner}/{repo}/pulls?state=open&per_page=1",
        f"/repos/{owner}/{repo}/commits?per_page=1",
        f"/repos/{owner}/{repo}/actions/runs?per_page=1",
    ]
    for path in paths:
        response = await client.rest_get(path.split("?", 1)[0], params=_params(path))
        if response.status_code >= 400:
            details.append(f"{path} status={response.status_code}")
            return False

    # PR reviews / review comments / checks / statuses — probe against latest PR if any.
    pulls = await client.rest_get(
        f"/repos/{owner}/{repo}/pulls",
        params={"state": "open", "per_page": 1},
    )
    if pulls.status_code >= 400:
        details.append(f"pulls status={pulls.status_code}")
        return False
    pull_items = pulls.json_body if isinstance(pulls.json_body, list) else []
    if pull_items:
        number = pull_items[0].get("number")
        head_sha = (pull_items[0].get("head") or {}).get("sha")
        if isinstance(number, int):
            for suffix in (
                f"/pulls/{number}/comments",
                f"/pulls/{number}/reviews",
                f"/issues/{number}/comments",
            ):
                response = await client.rest_get(f"/repos/{owner}/{repo}{suffix}", params={"per_page": 1})
                if response.status_code >= 400:
                    details.append(f"{suffix} status={response.status_code}")
                    return False
        if isinstance(head_sha, str) and head_sha:
            for suffix in (
                f"/commits/{head_sha}/status",
                f"/commits/{head_sha}/check-runs",
                f"/commits/{head_sha}/check-suites",
            ):
                response = await client.rest_get(f"/repos/{owner}/{repo}{suffix}", params={"per_page": 1})
                if response.status_code >= 400:
                    details.append(f"{suffix} status={response.status_code}")
                    return False

    details.append(f"repo ok {owner}/{repo}")
    return True


def _params(path_with_query: str) -> dict[str, str | int] | None:
    if "?" not in path_with_query:
        return None
    query = path_with_query.split("?", 1)[1]
    out: dict[str, str | int] = {}
    for part in query.split("&"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        out[key] = int(value) if value.isdigit() else value
    return out
