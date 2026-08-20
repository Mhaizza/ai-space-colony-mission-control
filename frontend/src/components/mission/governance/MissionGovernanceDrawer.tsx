"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";

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

const missionCardKey = (card: SelectedMissionCard): string =>
  `${card.source_repo}::${card.kind}::${card.number}`;

export function MissionGovernanceDrawer({
  card,
  onClose,
}: {
  card: SelectedMissionCard;
  onClose: () => void;
}) {
  const cardKey = missionCardKey(card);
  const [selection, setSelection] = useState({
    cardKey,
    selectedApprovalRequestId: null as string | null,
  });
  // Reset the approval selection whenever the Mission card identity changes,
  // using React's "store info from previous renders" pattern (see
  // DashboardShell's sidebarState) so the reset lands in the same commit as
  // the prop change rather than a follow-up effect render.
  if (selection.cardKey !== cardKey) {
    setSelection({ cardKey, selectedApprovalRequestId: null });
  }
  const selectedApprovalRequestId = selection.selectedApprovalRequestId;

  const listPaneVisible = selectedApprovalRequestId ? "hidden md:block" : "block";
  const detailPaneVisible = selectedApprovalRequestId ? "block" : "hidden md:block";

  return (
    <div
      data-testid="mission-governance-drawer"
      className="fixed inset-y-0 right-0 z-40 flex w-full flex-col border-l border-slate-200 bg-white shadow-lg md:w-[720px]"
    >
      <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3">
        <h2 className="min-w-0 truncate text-sm font-semibold text-slate-900">
          {card.source_repo} · {cardKindLabel(card.kind)} · #{card.number}
          {card.title ? ` — ${card.title}` : ""}
        </h2>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onClose}
          aria-label="Close governance drawer"
        >
          Close
        </Button>
      </div>
      <div className="flex min-h-0 flex-1">
        <div data-testid="approval-list-pane-mount" className={listPaneVisible}>
          {/* Task 4/5 wires the real ApprovalListPane here. */}
        </div>
        <div
          data-testid="approval-detail-pane-mount"
          data-selected-request-id={selectedApprovalRequestId ?? ""}
          className={detailPaneVisible}
        >
          {/* Task 6/7 wires the real ApprovalDetailPane here. */}
        </div>
      </div>
    </div>
  );
}
