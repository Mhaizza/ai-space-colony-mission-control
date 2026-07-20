"""Read-only GitHub REST + Projects GraphQL client (no mutations)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final, Protocol

import httpx

from app.mission.redaction import redact_secrets
from app.mission.types import REQUIRED_OAUTH_SCOPES

_MUTATION_OP_RE = re.compile(r"(?i)(?:^|[\s{])mutation\b")

GITHUB_API_BASE: Final[str] = "https://api.github.com"
GITHUB_GRAPHQL_URL: Final[str] = "https://api.github.com/graphql"

# Closed allowlist of read-only REST path prefixes.
ALLOWED_REST_PREFIXES: Final[tuple[str, ...]] = (
    "/user",
    "/repos/",
    "/rate_limit",
)

FORBIDDEN_METHODS: Final[frozenset[str]] = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class GitHubTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
        params: dict[str, str | int] | None = None,
    ) -> httpx.Response: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class GitHubResponse:
    status_code: int
    headers: dict[str, str]
    json_body: Any
    text: str


class GitHubReadClient:
    """Server-only read client. Rejects mutating HTTP methods."""

    def __init__(
        self,
        *,
        token: str,
        transport: httpx.AsyncClient | None = None,
        api_base: str = GITHUB_API_BASE,
        graphql_url: str = GITHUB_GRAPHQL_URL,
    ) -> None:
        self._token = token
        self._owns_transport = transport is None
        self._transport = transport or httpx.AsyncClient(
            base_url=api_base,
            timeout=httpx.Timeout(30.0),
            headers=self._default_headers(),
        )
        self._api_base = api_base.rstrip("/")
        self._graphql_url = graphql_url

    def _default_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-space-colony-mission-control-slice3",
        }

    async def aclose(self) -> None:
        if self._owns_transport:
            await self._transport.aclose()

    async def rest_get(
        self,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
    ) -> GitHubResponse:
        if not path.startswith("/"):
            raise ValueError("REST path must start with '/'")
        if not any(path == p or path.startswith(p) for p in ALLOWED_REST_PREFIXES):
            raise ValueError(f"REST path not on read allowlist: {path}")
        response = await self._transport.request(
            "GET",
            path if self._owns_transport else f"{self._api_base}{path}",
            params=params,
            headers=self._default_headers(),
        )
        return self._wrap(response)

    async def graphql(self, query: str, variables: dict[str, Any] | None = None) -> GitHubResponse:
        # GraphQL endpoint is POST but queries must be read-only (no mutation operation).
        if _MUTATION_OP_RE.search(query) is not None:
            raise ValueError("GitHub GraphQL mutations are forbidden")
        response = await self._transport.request(
            "POST",
            self._graphql_url if not self._owns_transport else "/graphql",
            json={"query": query, "variables": variables or {}},
            headers=self._default_headers(),
        )
        return self._wrap(response)

    def _wrap(self, response: httpx.Response) -> GitHubResponse:
        try:
            body: Any = response.json()
        except ValueError:
            body = None
        headers = {k.lower(): v for k, v in response.headers.items()}
        text = redact_secrets(response.text, secrets=[self._token])
        return GitHubResponse(
            status_code=response.status_code,
            headers=headers,
            json_body=body,
            text=text,
        )


def normalize_oauth_scopes(header_value: str | None) -> frozenset[str]:
    if not header_value:
        return frozenset()
    parts = [p.strip().lower() for p in header_value.split(",")]
    return frozenset(p for p in parts if p)


def verify_exact_read_project_scope(oauth_scopes_header: str | None) -> None:
    """Fail closed unless X-OAuth-Scopes normalizes to exactly {read:project}."""
    scopes = normalize_oauth_scopes(oauth_scopes_header)
    if scopes != REQUIRED_OAUTH_SCOPES:
        raise RuntimeError(
            "Fail-closed: GitHub token X-OAuth-Scopes must equal exactly "
            f"{sorted(REQUIRED_OAUTH_SCOPES)}; got {sorted(scopes)}"
        )


# Project items query (read-only).
PROJECT_ITEMS_QUERY = """
query($login: String!, $number: Int!, $after: String) {
  user(login: $login) {
    projectV2(number: $number) {
      id
      title
      items(first: 50, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          updatedAt
          content {
            __typename
            ... on Issue {
              id
              number
              title
              url
              updatedAt
              repository { id nameWithOwner }
            }
            ... on PullRequest {
              id
              number
              title
              url
              updatedAt
              headRefOid
              repository { id nameWithOwner }
            }
          }
        }
      }
    }
  }
}
"""
