import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApprovalDetailResponse } from "@/api/generated/model";

import { ApprovalDetailPane } from "./ApprovalDetailPane";

const { useApprovalDetail } = vi.hoisted(() => ({
  useApprovalDetail: vi.fn(),
}));

vi.mock("@/api/generated/mission-approvals/mission-approvals", () => ({
  useGetApprovalDetailApiV1MissionApprovalsRequestIdGet: useApprovalDetail,
}));

describe("ApprovalDetailPane query states", () => {
  beforeEach(() => {
    useApprovalDetail.mockReset();
    useApprovalDetail.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
  });

  it("shows the placeholder and disables fetching when no request is selected", () => {
    render(<ApprovalDetailPane selectedApprovalRequestId={null} />);

    expect(
      screen.getByText("Select an approval to view details"),
    ).toBeInTheDocument();
    expect(useApprovalDetail).toHaveBeenCalledWith("", {
      query: { enabled: false },
    });
  });

  it("fetches exactly the selected request id", () => {
    render(<ApprovalDetailPane selectedApprovalRequestId="req-abc" />);

    expect(useApprovalDetail).toHaveBeenCalledWith("req-abc", {
      query: { enabled: true },
    });
  });

  it("renders a localized detail skeleton while loading", () => {
    useApprovalDetail.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<ApprovalDetailPane selectedApprovalRequestId="req-abc" />);

    expect(screen.getByTestId("approval-detail-skeleton")).toHaveClass(
      "animate-pulse",
    );
  });

  it("retries only the same failed detail query", () => {
    const refetch = vi.fn();
    useApprovalDetail.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("boom"),
      refetch,
    });

    render(<ApprovalDetailPane selectedApprovalRequestId="req-abc" />);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Unable to load approval details",
    );
    expect(refetch).toHaveBeenCalledOnce();
    expect(useApprovalDetail).toHaveBeenLastCalledWith("req-abc", {
      query: { enabled: true },
    });
  });
});

const fullDetail: ApprovalDetailResponse = {
  request_id: "req-abc",
  status: "pending",
  mission_source_repo: "acme/mission-control",
  mission_card_kind: "issue",
  mission_card_number: 42,
  action_key: "deploy-production",
  policy_key: "production-deploy",
  policy_version: 7,
  decision_rule: "all",
  quorum_satisfied: false,
  quorum_requirements: [
    {
      slot: "operator",
      eligible_roles: ["maintainer", "owner"],
      satisfied: true,
    },
    {
      slot: "security",
      eligible_roles: ["security-reviewer"],
      satisfied: false,
    },
  ],
  missing_requirements: ["security"],
  effective_decisions: [
    {
      principal_id: "principal-1",
      decision: "approve",
      reason: "Checks passed",
      role_slugs_at_decision: ["maintainer"],
      created_at: "2026-08-29T10:00:00Z",
    },
  ],
  lifecycle: [
    {
      event_type: "created",
      triggered_by_principal_id: "principal-system",
      detail: { source: "mission-refresh" },
      created_at: "2026-08-29T09:00:00Z",
    },
  ],
  created_at: "2026-08-29T09:00:00Z",
  expires_at: "2026-08-30T09:00:00Z",
  resolved_at: null,
  mission_effect: "Deployment remains blocked",
  can_decide: true,
  current_principal_decision: null,
};

function loaded(detail: ApprovalDetailResponse) {
  return {
    data: { status: 200 as const, data: detail },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
}

describe("ApprovalDetailPane backend-authoritative detail", () => {
  beforeEach(() => {
    useApprovalDetail.mockReset();
  });

  it("renders every backend-derived governance field without mutation controls", () => {
    useApprovalDetail.mockReturnValue(loaded(fullDetail));

    render(<ApprovalDetailPane selectedApprovalRequestId="req-abc" />);

    const detail = screen.getByTestId("approval-detail");
    for (const value of [
      "pending",
      "production-deploy",
      "v7",
      "all",
      "Not satisfied",
      "operator",
      "maintainer",
      "owner",
      "security-reviewer",
      "security",
      "principal-1",
      "approve",
      "Checks passed",
      "created",
      "principal-system",
      "mission-refresh",
      "Deployment remains blocked",
      "You can decide on this request",
    ]) {
      expect(detail).toHaveTextContent(value);
    }
    expect(detail).toHaveTextContent("Created");
    expect(detail).toHaveTextContent("Expires");
    expect(detail).toHaveTextContent("Resolved");
    expect(
      screen.queryByRole("button", { name: /approve/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /reject/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /change decision/i }),
    ).not.toBeInTheDocument();
  });

  it("renders the caller's current decision as read-only information", () => {
    useApprovalDetail.mockReturnValue(
      loaded({
        ...fullDetail,
        current_principal_decision: {
          decision_id: "decision-7",
          decision: "reject",
          reason: "Needs another review",
          created_at: "2026-08-29T11:00:00Z",
        },
      }),
    );

    render(<ApprovalDetailPane selectedApprovalRequestId="req-abc" />);

    expect(
      screen.getByText(/Your current decision/i).parentElement,
    ).toHaveTextContent("reject");
    expect(screen.getByTestId("approval-detail")).toHaveTextContent(
      "Needs another review",
    );
    expect(screen.getByTestId("approval-detail")).toHaveTextContent(
      "decision-7",
    );
    expect(
      screen.queryByRole("button", { name: /change decision/i }),
    ).not.toBeInTheDocument();
  });

  it.each([
    ["approved", "bg-[color:rgba(15,118,110,0.14)]"],
    ["rejected", "bg-[color:rgba(180,35,24,0.15)]"],
    ["expired", "bg-[color:rgba(180,83,9,0.15)]"],
    ["superseded", "border"],
  ])(
    "renders complete read-only history for terminal status %s",
    (status, badgeClass) => {
      useApprovalDetail.mockReturnValue(
        loaded({ ...fullDetail, status, can_decide: true }),
      );

      render(<ApprovalDetailPane selectedApprovalRequestId="req-abc" />);

      expect(screen.getByTestId("approval-status")).toHaveTextContent(status);
      expect(screen.getByTestId("approval-status")).toHaveClass(badgeClass);
      expect(screen.getByTestId("approval-detail")).toHaveTextContent(
        "created",
      );
      expect(screen.getByTestId("approval-detail")).toHaveTextContent(
        "Deployment remains blocked",
      );
      expect(
        screen.queryByRole("button", { name: /approve/i }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: /reject/i }),
      ).not.toBeInTheDocument();
    },
  );
});
