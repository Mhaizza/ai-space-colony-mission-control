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
| `GITHUB_RUN_STARTUP_PROBES` | Mandatory when adapter enabled — must stay `true`. Not a bypass: setting `false` while `GITHUB_PAT` is set fails startup. Fail-closed scope + capability probes always run before any polling. |
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
- Exact `{read:project}` scope + capability probes always run before any polling (no bypass)
- Partial/failed reads never tombstone or infer deletion
- Conflicting assignments quarantine rather than selecting a winner

## Partition reconciliation and tombstoning

Records are grouped into completeness partitions keyed by
`(source_type, partition_key)` — for example one partition per project, per
repository issue/PR set, and per card's comments/reviews/checks. During a sync
the adapter records every observed source id per partition and whether that
partition's reads completed fully.

- REST collection endpoints (issue comments, PR reviews, PR inline review
  comments, commit statuses, check runs, check suites, workflow runs) are fully
  paginated. A first page is never treated as a complete partition.
- A partition is reconciled — records absent from it are tombstoned — only after
  every page and every required read for that exact partition succeeds.
- Any non-success/malformed/interrupted/rate-limited/failed read (including
  check/status/workflow reads) marks that partition partial: the sync result is
  partial and that partition is never tombstoned.
- Reconciliation is isolated per partition and source type; a previously
  tombstoned record is revived when observed again. Repeated identical syncs are
  idempotent.

## Deferred to Slice 3.5

Rich projection schema, retention/GC, audit-store maturity, index tuning,
full board/approvals/agents/PR materialization, frontend orval/UI wiring.
"""
