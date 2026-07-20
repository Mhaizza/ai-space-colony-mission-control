# Upstream Pin

## Source

- Upstream repository: `https://github.com/abhi1693/openclaw-mission-control`
- Upstream branch at adoption: `master`
- Pinned upstream commit: `75eb8b0894803e48891a8a92b564c25fb126f2ea`
- Adoption date: `2026-07-19`
- Local repository: `Mhaizza/ai-space-colony-mission-control`
- Governing implementation card: `Mhaizza/ai-space-colony-sim#144`
- Governing architecture: ADR-23, `ai-studio/adr/0023-mission-control-projection-and-control-boundary.md`

## Remote Policy

- `origin` points to `https://github.com/Mhaizza/ai-space-colony-mission-control.git`.
- `upstream` points to `https://github.com/abhi1693/openclaw-mission-control.git`.
- Upstream movement is never automatic.
- Any upstream update requires a new implementation card, an exact candidate SHA, compatibility validation, and Human approval before adoption.

## Divergence Summary

### Checkpoint 1

Authorized initial divergence was limited to this `UPSTREAM.md` file and repository metadata required to establish the project-owned public fork. No runtime, UI, API, authentication, Docker, GitHub adapter, workflow-record, exporter, database, or host-integration behavior was changed in Checkpoint 1.

### Checkpoint 2 (Safety and Runtime Boundary)

Authorized divergence for Checkpoint 2 (Issue #144 ACs 5–9) is limited to safety/runtime boundary work:

- Hard-disable inherited mutation/write HTTP routes with fail-closed startup (`MUTATIONS_HARD_DISABLED`, ADR-23 D8)
- Compose loopback publish for frontend/backend; remove default PostgreSQL/Redis host ports; optional `compose.loopback-db.yml` (127.0.0.1 only) for hybrid `install.sh --db-mode docker`; in-container listen on `0.0.0.0` (ADR-23 D7 / design D9)
- Local-auth ≥50-character token generation path into ignored `.env` files when Clerk is unconfigured
- Isolation proofs for bring-up paths (no GitHub API client; no sim / `~/.openclaw` mounts or bring-up references)
- Tests and compose smoke checks for the above

No GitHub adapter, workflow-record, projection schema, UI feature, or host-exporter work is included. Upstream pin SHA is unchanged.

## Compatibility Result

- Result: `CP2_SAFETY_RUNTIME_BOUNDARY`
- Verified exact upstream pin (unchanged): `75eb8b0894803e48891a8a92b564c25fb126f2ea`
- Checkpoint 2 validation focus:
  - mutation/write route hard-disable + fail-closed configuration
  - Compose loopback FE/BE and internal-only PG/Redis
  - local-auth token generation (≥50 chars)
  - isolation proofs on bring-up paths
- Next required work: Issue #144 review/closeout for Slice 1 remaining ACs, then separate cards for Slice 2–5
