"""Slice 3 — Read-Only GitHub Adapter (Issue #148)

## Authority

- Issue: https://github.com/Mhaizza/ai-space-colony-sim/issues/148
- ADR-23 (Accepted)
- Mission Control design v0.4.0

## Server-only configuration

Configure these in ignored `.env` / `backend/.env` (never commit real secrets):

| Variable | Purpose |
| --- | --- |
| `GITHUB_PAT` | Classic PAT with **exactly** `{read:project}`. Empty disables the adapter. |
| `GITHUB_PROJECT_OWNER` / `GITHUB_PROJECT_NUMBER` | User-owned Project (default `Mhaizza` / `4`) |
| `GITHUB_SELF_OWNER` / `GITHUB_SELF_REPO` | Authoritative workflow repo |
| `GITHUB_MISSION_CONTROL_OWNER` / `GITHUB_MISSION_CONTROL_REPO` | Fork mapping for `mission-control` qualifier probes |
| `GITHUB_POLL_INTERVAL_SECONDS` | 15–300 (default 15) |
| `GITHUB_RUN_STARTUP_PROBES` | Fail-closed scope + capability probes when adapter enabled |
| `MC_PRINCIPAL_REGISTRY_JSON` | Server-only principal registry JSON |
| `MUTATIONS_HARD_DISABLED` | Must remain `true` |

### Principal registry example

```json
{
  "principals": [
    {"github_login": "Mhaizza", "trust_class": "human"},
    {"github_login": "reviewer-bot", "trust_class": "reviewer"},
    {
      "github_login": "cursor-bot",
      "trust_class": "worker",
      "worker_identity": "cursor",
      "allowed_roles": ["technical-director"],
      "declarable_identities": ["cursor"]
    }
  ]
}
```

## Manual refresh

`POST /api/v1/mission/refresh` is the sole mutation-middleware allowlist exception.
It re-invokes the same outbound read-only sync path as polling and never writes to GitHub.

## Safety

- No GitHub mutations, no inbound webhooks
- Credentials never enter browser bundles, API responses, logs, projection rows, or quarantine diagnostics
- Partial/failed reads never tombstone or infer deletion
- Conflicting assignments quarantine rather than selecting a winner

## Deferred to Slice 3.5

Rich projection schema, retention/GC, audit-store maturity, index tuning,
full board/approvals/agents/PR materialization, frontend orval/UI wiring.
"""
