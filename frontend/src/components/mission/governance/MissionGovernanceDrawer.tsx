"use client";

import { ArrowLeft, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";

import { ApprovalDetailPane } from "./ApprovalDetailPane";
import { ApprovalListPane } from "./ApprovalListPane";
import type { SelectedMissionCard } from "./types";
import type { MissionDecisionController } from "./useMissionDecision";

function kindLabel(kind: SelectedMissionCard["kind"]): string {
  return kind === "pull_request" ? "Pull request" : "Issue";
}

export function MissionGovernanceDrawer({
  card,
  onClose,
  decisionController,
}: {
  card: SelectedMissionCard;
  onClose: () => void;
  decisionController?: MissionDecisionController;
}) {
  const missionIdentity = `${card.source_repo}:${card.kind}:${card.number}`;
  const [previousMissionIdentity, setPreviousMissionIdentity] =
    useState(missionIdentity);
  const [selectedApprovalRequestId, setSelectedApprovalRequestId] = useState<
    string | null
  >(null);

  if (previousMissionIdentity !== missionIdentity) {
    setPreviousMissionIdentity(missionIdentity);
    setSelectedApprovalRequestId(null);
  }

  return (
    <aside
      aria-label="Mission governance"
      className="fixed inset-y-0 right-0 z-40 flex w-[min(100vw,72rem)] flex-col border-l border-slate-200 bg-white shadow-2xl"
      data-testid="mission-governance-drawer"
    >
      <header className="flex items-start justify-between gap-4 border-b border-slate-200 px-4 py-4 md:px-6">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Mission governance
          </p>
          <h2 className="mt-1 text-lg font-semibold text-slate-900">
            {card.source_repo} · {kindLabel(card.kind)} · #{card.number}
          </h2>
          <p className="mt-1 truncate text-sm text-slate-600">
            {card.title ?? "Untitled"}
          </p>
        </div>
        <Button
          aria-label="Close governance drawer"
          size="sm"
          variant="ghost"
          onClick={onClose}
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </Button>
      </header>

      <div className="grid min-h-0 flex-1 md:grid-cols-[22rem_minmax(0,1fr)]">
        <div
          className={`${selectedApprovalRequestId ? "hidden md:block" : "block"} min-h-0 overflow-y-auto`}
          data-testid="approval-list-pane"
        >
          <ApprovalListPane
            card={card}
            selectedApprovalRequestId={selectedApprovalRequestId}
            onSelect={setSelectedApprovalRequestId}
          />
        </div>
        <div
          className={`${selectedApprovalRequestId ? "block" : "hidden md:block"} min-h-0 overflow-y-auto`}
          data-testid="approval-detail-pane"
        >
          {selectedApprovalRequestId ? (
            <div className="border-b border-slate-200 p-2 md:hidden">
              <Button
                className="md:hidden"
                size="sm"
                variant="ghost"
                onClick={() => setSelectedApprovalRequestId(null)}
              >
                <ArrowLeft className="h-4 w-4" aria-hidden="true" />
                Back
              </Button>
            </div>
          ) : null}
          <ApprovalDetailPane
            key={`${missionIdentity}/${selectedApprovalRequestId ?? "none"}`}
            selectedApprovalRequestId={selectedApprovalRequestId}
            card={card}
            decisionController={decisionController}
          />
        </div>
      </div>
    </aside>
  );
}
