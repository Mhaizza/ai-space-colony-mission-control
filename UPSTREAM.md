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

## Initial Divergence

The repository is seeded from the exact pinned upstream commit above.

Authorized initial divergence for Checkpoint 1 is limited to this `UPSTREAM.md` file and repository metadata required to establish the project-owned public fork. No runtime, UI, API, authentication, Docker, GitHub adapter, workflow-record, exporter, database, or host-integration behavior is changed in this checkpoint.

## Compatibility Result

- Result: `BOOTSTRAP_PIN_VERIFIED`
- Verified exact upstream commit: `75eb8b0894803e48891a8a92b564c25fb126f2ea`
- Verification method:
  - `git rev-parse main`
  - `git merge-base --is-ancestor 75eb8b0894803e48891a8a92b564c25fb126f2ea main`
  - remote URL inspection for `origin` and `upstream`
- Runtime compatibility testing: not applicable to Checkpoint 1
- Next required work: Issue #144 Checkpoint 2, only after Human approval
