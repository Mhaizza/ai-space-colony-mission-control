"""Canonical RoleSlug registry (ADR-23 D4), checked in for Slice 3."""

from __future__ import annotations

from typing import Final

# Mirrors ai-space-colony-sim ai-studio/roles/role-slugs.json at Slice 2 merge.
# Slice 3 embeds the closed set so Mission Control does not mount the sim repo.
ROLE_SLUGS: Final[frozenset[str]] = frozenset(
    {
        "ai-simulation-designer",
        "creative-director",
        "gameplay-engineer",
        "game-systems-designer",
        "human-owner",
        "qa-reviewer",
        "technical-director",
        "ui-ux-engineer",
        "world-designer",
    }
)
