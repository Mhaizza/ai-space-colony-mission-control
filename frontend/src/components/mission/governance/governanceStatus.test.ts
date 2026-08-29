import type { ApprovalListItem } from "@/api/generated/model";
import { describe, expect, it } from "vitest";

import {
  isTerminalStatus,
  orderApprovals,
  statusBadgeVariant,
} from "./governanceStatus";

function approval(
  requestId: string,
  status: string,
  createdAt: string,
): ApprovalListItem {
  return {
    request_id: requestId,
    status,
    mission_source_repo: "acme/mission-control",
    mission_card_kind: "issue",
    mission_card_number: 42,
    action_key: "deploy",
    policy_key: "production-deploy",
    policy_version: 1,
    created_at: createdAt,
    expires_at: null,
  };
}

describe("isTerminalStatus", () => {
  it.each(["approved", "rejected", "expired", "superseded"])(
    "classifies %s as terminal",
    (status) => {
      expect(isTerminalStatus(status)).toBe(true);
    },
  );

  it("classifies pending as non-terminal", () => {
    expect(isTerminalStatus("pending")).toBe(false);
  });
});

describe("statusBadgeVariant", () => {
  it.each([
    ["approved", "success"],
    ["rejected", "danger"],
    ["expired", "warning"],
    ["superseded", "outline"],
    ["pending", "accent"],
    ["unknown", "accent"],
  ] as const)("maps %s to %s", (status, expectedVariant) => {
    expect(statusBadgeVariant(status)).toBe(expectedVariant);
  });
});

describe("orderApprovals", () => {
  it("places pending approvals before terminal approvals", () => {
    const items = [
      approval("terminal", "approved", "2026-08-29T12:00:00Z"),
      approval("pending", "pending", "2026-08-29T10:00:00Z"),
    ];

    expect(orderApprovals(items).map((item) => item.request_id)).toEqual([
      "pending",
      "terminal",
    ]);
  });

  it("orders pending approvals newest-first", () => {
    const items = [
      approval("older", "pending", "2026-08-29T10:00:00Z"),
      approval("newer", "pending", "2026-08-29T11:00:00Z"),
    ];

    expect(orderApprovals(items).map((item) => item.request_id)).toEqual([
      "newer",
      "older",
    ]);
  });

  it("orders terminal approvals newest-first after pending approvals", () => {
    const items = [
      approval("terminal-older", "rejected", "2026-08-29T09:00:00Z"),
      approval("terminal-newer", "expired", "2026-08-29T11:00:00Z"),
      approval("pending", "pending", "2026-08-29T08:00:00Z"),
    ];

    expect(orderApprovals(items).map((item) => item.request_id)).toEqual([
      "pending",
      "terminal-newer",
      "terminal-older",
    ]);
  });

  it("does not mutate its input array", () => {
    const items = [
      approval("terminal", "approved", "2026-08-29T12:00:00Z"),
      approval("pending", "pending", "2026-08-29T10:00:00Z"),
    ];
    const originalOrder = [...items];

    const ordered = orderApprovals(items);

    expect(items).toEqual(originalOrder);
    expect(ordered).not.toBe(items);
  });
});
