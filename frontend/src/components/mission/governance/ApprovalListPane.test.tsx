import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import type { ApprovalListItem } from "@/api/generated/model";
import { useListApprovalsApiV1MissionApprovalsGet } from "@/api/generated/mission-approvals/mission-approvals";
import { ApprovalListPane } from "./ApprovalListPane";
import type { SelectedMissionCard } from "./types";

vi.mock("@/api/generated/mission-approvals/mission-approvals", () => ({
  useListApprovalsApiV1MissionApprovalsGet: vi.fn(),
}));

const mockedUseListApprovals = vi.mocked(
  useListApprovalsApiV1MissionApprovalsGet,
);
type ApprovalListQueryResult = ReturnType<
  typeof useListApprovalsApiV1MissionApprovalsGet
>;

const missionCard: SelectedMissionCard = {
  source_repo: "acme/mission-control",
  kind: "issue",
  number: 101,
  title: "Fix flaky projection test",
};

const approvalOne: ApprovalListItem = {
  request_id: "request-2",
  status: "pending",
  mission_source_repo: "acme/mission-control",
  mission_card_kind: "issue",
  mission_card_number: 101,
  action_key: "deploy.preview",
  policy_key: "human-review",
  policy_version: 3,
  created_at: "2026-08-19T10:00:00Z",
  expires_at: null,
};

const approvalTwo: ApprovalListItem = {
  ...approvalOne,
  request_id: "request-1",
  created_at: "2026-08-18T10:00:00Z",
};

function renderPane() {
  return render(
    <ApprovalListPane
      card={missionCard}
      selectedApprovalRequestId={null}
      onSelect={vi.fn()}
    />,
  );
}

function setQueryResult(result: Partial<ApprovalListQueryResult>) {
  mockedUseListApprovals.mockReturnValue(result as ApprovalListQueryResult);
}

describe("ApprovalListPane", () => {
  beforeEach(() => {
    mockedUseListApprovals.mockReset();
  });

  it("calls the generated list hook with the exact Mission filter tuple", () => {
    setQueryResult({
      data: { status: 200, data: { items: [], total: 0, limit: 200, offset: 0 } },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderPane();

    expect(mockedUseListApprovals).toHaveBeenCalledWith({
      mission_source_repo: missionCard.source_repo,
      mission_card_kind: missionCard.kind,
      mission_card_number: missionCard.number,
    });
  });

  it("renders an animate-pulse skeleton while loading", () => {
    setQueryResult({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderPane();

    expect(screen.getByTestId("approval-list-loading")).toHaveClass(
      "animate-pulse",
    );
  });

  it("renders the empty state when the Mission has no approvals", () => {
    setQueryResult({
      data: { status: 200, data: { items: [], total: 0, limit: 200, offset: 0 } },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderPane();

    expect(
      screen.getByText("No approvals for this Mission"),
    ).toBeInTheDocument();
  });

  it("renders an error state with a Retry button that refetches", () => {
    const refetch = vi.fn();
    setQueryResult({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("boom"),
      refetch,
    });

    renderPane();

    expect(
      screen.getByText("Unable to load approvals for this Mission"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("renders one row container per approval item", () => {
    setQueryResult({
      data: {
        status: 200,
        data: {
          items: [approvalOne, approvalTwo],
          total: 2,
          limit: 200,
          offset: 0,
        },
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderPane();

    expect(screen.getAllByTestId("approval-list-row")).toHaveLength(2);
  });
});
