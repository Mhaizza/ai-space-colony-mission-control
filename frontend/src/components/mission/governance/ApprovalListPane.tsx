"use client";

import { Button } from "@/components/ui/button";
import { useListApprovalsApiV1MissionApprovalsGet } from "@/api/generated/mission-approvals/mission-approvals";

import type { SelectedMissionCard } from "./types";

interface ApprovalListPaneProps {
  card: SelectedMissionCard;
  selectedApprovalRequestId: string | null;
  onSelect: (requestId: string) => void;
}

export function ApprovalListPane({ card }: ApprovalListPaneProps) {
  const query = useListApprovalsApiV1MissionApprovalsGet({
    mission_source_repo: card.source_repo,
    mission_card_kind: card.kind,
    mission_card_number: card.number,
  });

  if (query.isLoading) {
    return (
      <div
        data-testid="approval-list-loading"
        className="space-y-3 p-4 animate-pulse"
        aria-label="Loading approvals"
      >
        <div className="h-4 w-32 rounded-full bg-slate-200" />
        <div className="h-12 rounded-lg bg-slate-100" />
        <div className="h-12 rounded-lg bg-slate-100" />
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="space-y-3 p-4">
        <p className="text-sm text-slate-600">
          Unable to load approvals for this Mission
        </p>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={() => query.refetch()}
        >
          Retry
        </Button>
      </div>
    );
  }

  const items = query.data?.status === 200 ? query.data.data.items : [];

  if (items.length === 0) {
    return (
      <p className="p-4 text-sm text-slate-600">
        No approvals for this Mission
      </p>
    );
  }

  return (
    <div
      className="divide-y divide-slate-200"
      role="list"
      aria-label="Approvals"
    >
      {items.map((item) => (
        <div
          key={item.request_id}
          data-testid="approval-list-row"
          data-request-id={item.request_id}
          className="min-h-12"
          role="listitem"
        />
      ))}
    </div>
  );
}
