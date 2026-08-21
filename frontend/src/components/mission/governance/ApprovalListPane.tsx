"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useListApprovalsApiV1MissionApprovalsGet } from "@/api/generated/mission-approvals/mission-approvals";
import { formatTimestamp } from "@/lib/formatters";

import { orderApprovals, statusBadgeVariant } from "./governanceStatus";
import type { SelectedMissionCard } from "./types";

interface ApprovalListPaneProps {
  card: SelectedMissionCard;
  selectedApprovalRequestId: string | null;
  onSelect: (requestId: string) => void;
}

export function ApprovalListPane({
  card,
  selectedApprovalRequestId,
  onSelect,
}: ApprovalListPaneProps) {
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

  const ordered = orderApprovals(items);

  return (
    <div className="divide-y divide-slate-200" aria-label="Approvals">
      {ordered.map((item) => {
        const isSelected = item.request_id === selectedApprovalRequestId;
        return (
          <button
            key={item.request_id}
            type="button"
            data-testid="approval-list-row"
            data-request-id={item.request_id}
            aria-current={isSelected}
            onClick={() => onSelect(item.request_id)}
            className={`flex min-h-12 w-full flex-col gap-1 px-4 py-2 text-left hover:bg-slate-50 ${
              isSelected ? "bg-slate-100" : ""
            }`}
          >
            <div className="flex items-center gap-2">
              <Badge variant={statusBadgeVariant(item.status)}>
                {item.status}
              </Badge>
              <span className="text-sm font-medium text-slate-900">
                {item.action_key ?? "—"}
              </span>
            </div>
            <div className="text-xs text-slate-500">
              {item.policy_key} · v{item.policy_version}
            </div>
            <div className="text-xs text-slate-500">
              Created {formatTimestamp(item.created_at)} · Expires{" "}
              {formatTimestamp(item.expires_at)}
            </div>
          </button>
        );
      })}
    </div>
  );
}
