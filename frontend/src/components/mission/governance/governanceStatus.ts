import type { ApprovalListItem } from "@/api/generated/model";
import type { BadgeProps } from "@/components/ui/badge";

const TERMINAL_STATUSES = new Set(["approved", "rejected", "expired", "superseded"]);

export function isTerminalStatus(status: string): boolean {
  return TERMINAL_STATUSES.has(status);
}

export function statusBadgeVariant(status: string): NonNullable<BadgeProps["variant"]> {
  switch (status) {
    case "approved":
      return "success";
    case "rejected":
      return "danger";
    case "expired":
      return "warning";
    case "superseded":
      return "outline";
    default:
      return "accent"; // pending and any unrecognized status
  }
}

export function orderApprovals(items: ApprovalListItem[]): ApprovalListItem[] {
  return [...items].sort((a, b) => {
    const aTerminal = isTerminalStatus(a.status);
    const bTerminal = isTerminalStatus(b.status);
    if (aTerminal !== bTerminal) return aTerminal ? 1 : -1;
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });
}
