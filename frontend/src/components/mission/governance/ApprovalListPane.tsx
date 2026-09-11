"use client";

import { useListApprovalsApiV1MissionApprovalsGet } from "@/api/generated/mission-approvals/mission-approvals";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
      <div className="space-y-3 p-4" aria-label="Loading approvals">
        {[0, 1, 2].map((row) => (
          <div
            className="h-16 animate-pulse rounded-lg bg-slate-100"
            data-testid="approval-list-skeleton"
            key={row}
          />
        ))}
      </div>
    );
  }

  if (query.isError && !query.data) {
    return (
      <div
        className="m-4 rounded-lg border border-rose-200 bg-rose-50 p-4"
        role="alert"
      >
        <p className="text-sm text-rose-700">Unable to load approvals</p>
        <Button
          className="mt-3"
          size="sm"
          variant="outline"
          onClick={() => query.refetch()}
        >
          Retry
        </Button>
      </div>
    );
  }

  const items = query.data?.status === 200 ? query.data.data.items : [];

  const orderedItems = orderApprovals(items);

  return (
    <div className="space-y-2 p-4">
      {query.isError ? (
        <div
          role="alert"
          className="rounded-lg border border-rose-200 p-3 text-sm"
        >
          Unable to refresh approvals. Previously loaded information is shown.
          <Button
            className="mt-2"
            variant="outline"
            size="sm"
            onClick={() => void query.refetch()}
          >
            Retry
          </Button>
        </div>
      ) : null}
      {items.length === 0 ? (
        <p className="text-sm text-slate-500">No approvals for this Mission</p>
      ) : null}
      {orderedItems.map((item) => (
        <button
          aria-current={
            item.request_id === selectedApprovalRequestId ? "true" : undefined
          }
          className="w-full rounded-lg border border-slate-200 p-3 text-left transition-colors hover:border-slate-300 aria-[current=true]:border-sky-500 aria-[current=true]:bg-sky-50"
          data-request-id={item.request_id}
          data-testid="approval-list-row"
          key={item.request_id}
          type="button"
          onClick={() => onSelect(item.request_id)}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-slate-900">
                {item.action_key ?? "Approval request"}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                {item.policy_key} · v{item.policy_version}
              </p>
            </div>
            <Badge variant={statusBadgeVariant(item.status)}>
              {item.status}
            </Badge>
          </div>
          <p className="mt-2 text-xs text-slate-500">
            Created {formatTimestamp(item.created_at)} · Expires{" "}
            {formatTimestamp(item.expires_at, "Never")}
          </p>
        </button>
      ))}
    </div>
  );
}
