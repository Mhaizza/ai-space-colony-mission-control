import { describe, expect, it } from "vitest";

import { isTerminalStatus, orderApprovals, statusBadgeVariant } from "./governanceStatus";
import type { ApprovalListItem } from "@/api/generated/model";

function makeItem(overrides: Partial<ApprovalListItem>): ApprovalListItem {
  return {
    request_id: "req-default",
    status: "pending",
    mission_source_repo: "org/repo",
    mission_card_kind: "issue",
    mission_card_number: 1,
    action_key: "action",
    policy_key: "policy",
    policy_version: 1,
    created_at: "2026-08-01T00:00:00Z",
    expires_at: null,
    ...overrides,
  };
}

describe("isTerminalStatus", () => {
  it("returns true for approved/rejected/expired/superseded", () => {
    expect(isTerminalStatus("approved")).toBe(true);
    expect(isTerminalStatus("rejected")).toBe(true);
    expect(isTerminalStatus("expired")).toBe(true);
    expect(isTerminalStatus("superseded")).toBe(true);
  });

  it("returns false for pending", () => {
    expect(isTerminalStatus("pending")).toBe(false);
  });
});

describe("statusBadgeVariant", () => {
  it("maps each known status to a Badge variant", () => {
    expect(statusBadgeVariant("approved")).toBe("success");
    expect(statusBadgeVariant("rejected")).toBe("danger");
    expect(statusBadgeVariant("expired")).toBe("warning");
    expect(statusBadgeVariant("superseded")).toBe("outline");
    expect(statusBadgeVariant("pending")).toBe("accent");
    expect(statusBadgeVariant("some-unrecognized-status")).toBe("accent");
  });
});

describe("orderApprovals", () => {
  it("places all pending items before all terminal items", () => {
    const terminal = makeItem({
      request_id: "terminal-1",
      status: "approved",
      created_at: "2026-08-05T00:00:00Z",
    });
    const pending = makeItem({
      request_id: "pending-1",
      status: "pending",
      created_at: "2026-08-01T00:00:00Z",
    });

    const ordered = orderApprovals([terminal, pending]);

    expect(ordered.map((item) => item.request_id)).toEqual(["pending-1", "terminal-1"]);
  });

  it("orders pending items newest-first by created_at", () => {
    const older = makeItem({
      request_id: "pending-older",
      status: "pending",
      created_at: "2026-08-01T00:00:00Z",
    });
    const newer = makeItem({
      request_id: "pending-newer",
      status: "pending",
      created_at: "2026-08-10T00:00:00Z",
    });

    const ordered = orderApprovals([older, newer]);

    expect(ordered.map((item) => item.request_id)).toEqual(["pending-newer", "pending-older"]);
  });

  it("orders terminal items newest-first by created_at, after all pending", () => {
    const pending = makeItem({
      request_id: "pending-1",
      status: "pending",
      created_at: "2026-08-01T00:00:00Z",
    });
    const olderTerminal = makeItem({
      request_id: "terminal-older",
      status: "rejected",
      created_at: "2026-07-01T00:00:00Z",
    });
    const newerTerminal = makeItem({
      request_id: "terminal-newer",
      status: "approved",
      created_at: "2026-07-15T00:00:00Z",
    });

    const ordered = orderApprovals([olderTerminal, pending, newerTerminal]);

    expect(ordered.map((item) => item.request_id)).toEqual([
      "pending-1",
      "terminal-newer",
      "terminal-older",
    ]);
  });

  it("does not mutate the input array", () => {
    const first = makeItem({
      request_id: "first",
      status: "approved",
      created_at: "2026-08-01T00:00:00Z",
    });
    const second = makeItem({
      request_id: "second",
      status: "pending",
      created_at: "2026-08-02T00:00:00Z",
    });
    const input = [first, second];
    const inputCopy = [...input];

    orderApprovals(input);

    expect(input).toEqual(inputCopy);
    expect(input[0]).toBe(first);
    expect(input[1]).toBe(second);
  });
});
