import type { ApprovalListItem } from "@/api/generated/model";
import type { BadgeProps } from "@/components/ui/badge";

const TERMINAL_STATUSES = new Set([
  "approved",
  "rejected",
  "expired",
  "superseded",
]);

export function isTerminalStatus(status: string): boolean {
  return TERMINAL_STATUSES.has(status);
}

export function statusBadgeVariant(
  status: string,
): NonNullable<BadgeProps["variant"]> {
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
      return "accent";
  }
}

export function orderApprovals(items: ApprovalListItem[]): ApprovalListItem[] {
  return [...items].sort((left, right) => {
    const leftTerminal = isTerminalStatus(left.status);
    const rightTerminal = isTerminalStatus(right.status);

    if (leftTerminal !== rightTerminal) {
      return leftTerminal ? 1 : -1;
    }

    return (
      new Date(right.created_at).getTime() - new Date(left.created_at).getTime()
    );
  });
}
