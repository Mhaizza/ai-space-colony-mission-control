"use client";

import type { SelectedMissionCard } from "./types";

const cardKindLabel = (kind: SelectedMissionCard["kind"]): string => {
  switch (kind) {
    case "issue":
      return "Issue";
    case "pull_request":
      return "Pull request";
    default: {
      const _exhaustive: never = kind;
      return _exhaustive;
    }
  }
};

export function MissionGovernanceDrawer({
  card,
}: {
  card: SelectedMissionCard;
  onClose: () => void;
}) {
  return (
    <div data-testid="mission-governance-drawer">
      <h2>
        {card.source_repo} · {cardKindLabel(card.kind)} · #{card.number}
        {card.title ? ` — ${card.title}` : ""}
      </h2>
    </div>
  );
}
