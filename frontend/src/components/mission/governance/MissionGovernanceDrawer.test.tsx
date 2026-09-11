import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApprovalListItem } from "@/api/generated/model";

import { MissionGovernanceDrawer } from "./MissionGovernanceDrawer";
import type { SelectedMissionCard } from "./types";

const {
  useApprovalList,
  useApprovalDetail,
  useCreateApproval,
  useSubmitDecision,
  useSupersedeDecision,
} = vi.hoisted(() => ({
  useApprovalList: vi.fn(),
  useApprovalDetail: vi.fn(),
  useCreateApproval: vi.fn(),
  useSubmitDecision: vi.fn(),
  useSupersedeDecision: vi.fn(),
}));

vi.mock("@/api/generated/mission-approvals/mission-approvals", () => ({
  useListApprovalsApiV1MissionApprovalsGet: useApprovalList,
  useGetApprovalDetailApiV1MissionApprovalsRequestIdGet: useApprovalDetail,
  useCreateApprovalApiV1MissionApprovalsPost: useCreateApproval,
  useSubmitApprovalDecisionApiV1MissionApprovalsRequestIdDecisionsPost:
    useSubmitDecision,
  useSupersedeApprovalDecisionApiV1MissionApprovalsRequestIdSupersedePost:
    useSupersedeDecision,
}));

const card: SelectedMissionCard = {
  source_repo: "acme/mission-control",
  kind: "pull_request",
  number: 73,
  title: "Ship governance drawer",
};

const approval: ApprovalListItem = {
  request_id: "req-1",
  status: "pending",
  mission_source_repo: card.source_repo,
  mission_card_kind: card.kind,
  mission_card_number: card.number,
  action_key: "deploy",
  policy_key: "production-deploy",
  policy_version: 1,
  created_at: "2026-08-29T10:00:00Z",
  expires_at: null,
};

function listSuccess(items: ApprovalListItem[]) {
  return {
    data: {
      status: 200 as const,
      data: { items, total: items.length, limit: 200, offset: 0 },
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
}

describe("MissionGovernanceDrawer shell", () => {
  beforeEach(() => {
    useApprovalList.mockReset();
    useApprovalDetail.mockReset();
    useCreateApproval.mockReset();
    useSubmitDecision.mockReset();
    useSupersedeDecision.mockReset();
    useApprovalList.mockReturnValue(listSuccess([]));
    useApprovalDetail.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
  });

  it("renders the trusted Mission identity and both responsive pane mounts", () => {
    render(<MissionGovernanceDrawer card={card} onClose={vi.fn()} />);

    const drawer = screen.getByTestId("mission-governance-drawer");
    expect(drawer).toHaveTextContent("acme/mission-control");
    expect(drawer).toHaveTextContent("Pull request");
    expect(drawer).toHaveTextContent("#73");
    expect(drawer).toHaveTextContent("Ship governance drawer");

    expect(screen.getByTestId("approval-list-pane")).toHaveClass(
      "block",
      "min-h-0",
      "overflow-y-auto",
    );
    expect(screen.getByTestId("approval-detail-pane")).toHaveClass(
      "hidden",
      "md:block",
      "min-h-0",
      "overflow-y-auto",
    );
  });

  it("dismisses through the provided close callback", () => {
    const onClose = vi.fn();
    render(<MissionGovernanceDrawer card={card} onClose={onClose} />);

    fireEvent.click(
      screen.getByRole("button", { name: "Close governance drawer" }),
    );

    expect(onClose).toHaveBeenCalledOnce();
  });

  it("stays open and resets approval selection when the Mission changes", () => {
    const nextApproval = {
      ...approval,
      request_id: "req-next",
      mission_source_repo: "acme/other-mission",
      mission_card_kind: "issue",
      mission_card_number: 99,
      action_key: "release",
    };
    useApprovalList.mockImplementation(
      (params: { mission_source_repo: string }) =>
        params.mission_source_repo === "acme/other-mission"
          ? listSuccess([nextApproval])
          : listSuccess([approval]),
    );
    const { rerender } = render(
      <MissionGovernanceDrawer card={card} onClose={vi.fn()} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^deploy\b/i }));
    expect(useApprovalDetail).toHaveBeenLastCalledWith("req-1", {
      query: { enabled: true },
    });

    const nextCard: SelectedMissionCard = {
      source_repo: "acme/other-mission",
      kind: "issue",
      number: 99,
      title: "Other mission",
    };
    rerender(<MissionGovernanceDrawer card={nextCard} onClose={vi.fn()} />);

    expect(screen.getByTestId("mission-governance-drawer")).toBeInTheDocument();
    expect(useApprovalList).toHaveBeenLastCalledWith({
      mission_source_repo: "acme/other-mission",
      mission_card_kind: "issue",
      mission_card_number: 99,
    });
    expect(useApprovalDetail).toHaveBeenLastCalledWith("", {
      query: { enabled: false },
    });
    expect(
      screen.getByText("Select an approval to view details"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^deploy\b/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /^release\b/i }),
    ).toBeInTheDocument();
  });

  it("preserves the list, Mission, and approval selection through detail error Retry", () => {
    const detailRefetch = vi.fn();
    useApprovalList.mockReturnValue(
      listSuccess([
        approval,
        { ...approval, request_id: "req-2", action_key: "merge" },
      ]),
    );
    useApprovalDetail.mockImplementation((requestId: string) => ({
      data: undefined,
      isLoading: false,
      isError: requestId === "req-1",
      error: requestId === "req-1" ? new Error("boom") : null,
      refetch: detailRefetch,
    }));

    render(<MissionGovernanceDrawer card={card} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /^deploy\b/i }));

    expect(screen.getAllByTestId("approval-list-row")).toHaveLength(2);
    expect(screen.getByRole("button", { name: /^deploy\b/i })).toHaveAttribute(
      "aria-current",
      "true",
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Unable to load approval details",
    );

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(detailRefetch).toHaveBeenCalledOnce();
    expect(useApprovalDetail).toHaveBeenLastCalledWith("req-1", {
      query: { enabled: true },
    });
    expect(screen.getAllByTestId("approval-list-row")).toHaveLength(2);
    expect(screen.getByRole("button", { name: /^deploy\b/i })).toHaveAttribute(
      "aria-current",
      "true",
    );
    expect(screen.getByTestId("mission-governance-drawer")).toHaveTextContent(
      "acme/mission-control",
    );
  });

  it("drills into detail and returns to the same Mission list with Back", () => {
    useApprovalList.mockReturnValue(listSuccess([approval]));
    render(<MissionGovernanceDrawer card={card} onClose={vi.fn()} />);

    const listPane = screen.getByTestId("approval-list-pane");
    const detailPane = screen.getByTestId("approval-detail-pane");
    expect(listPane).toHaveClass("block");
    expect(detailPane).toHaveClass("hidden", "md:block");

    fireEvent.click(screen.getByRole("button", { name: /^deploy\b/i }));

    expect(listPane).toHaveClass("hidden", "md:block");
    expect(detailPane).toHaveClass("block");
    expect(screen.getByRole("button", { name: "Back" })).toHaveClass(
      "md:hidden",
    );

    fireEvent.click(screen.getByRole("button", { name: "Back" }));

    expect(useApprovalDetail).toHaveBeenLastCalledWith("", {
      query: { enabled: false },
    });
    expect(listPane).toHaveClass("block");
    expect(detailPane).toHaveClass("hidden", "md:block");
    expect(
      screen.queryByRole("button", { name: "Back" }),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("mission-governance-drawer")).toHaveTextContent(
      "acme/mission-control",
    );
  });

  it("never initializes a Mission approval mutation hook", () => {
    useApprovalList.mockReturnValue(listSuccess([approval]));

    render(<MissionGovernanceDrawer card={card} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /^deploy\b/i }));

    expect(useCreateApproval).not.toHaveBeenCalled();
    expect(useSubmitDecision).not.toHaveBeenCalled();
    expect(useSupersedeDecision).not.toHaveBeenCalled();
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
});
