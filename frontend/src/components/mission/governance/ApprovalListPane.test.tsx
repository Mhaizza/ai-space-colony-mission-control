import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApprovalListItem } from "@/api/generated/model";

import { ApprovalListPane } from "./ApprovalListPane";
import type { SelectedMissionCard } from "./types";

const { useListApprovals } = vi.hoisted(() => ({
  useListApprovals: vi.fn(),
}));

vi.mock("@/api/generated/mission-approvals/mission-approvals", () => ({
  useListApprovalsApiV1MissionApprovalsGet: useListApprovals,
}));

const card: SelectedMissionCard = {
  source_repo: "acme/mission-control",
  kind: "issue",
  number: 42,
  title: "Govern launch",
};

const item: ApprovalListItem = {
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

function success(items: ApprovalListItem[]) {
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

function renderPane({
  selectedApprovalRequestId = null,
  onSelect = vi.fn(),
}: {
  selectedApprovalRequestId?: string | null;
  onSelect?: (requestId: string) => void;
} = {}) {
  return render(
    <ApprovalListPane
      card={card}
      selectedApprovalRequestId={selectedApprovalRequestId}
      onSelect={onSelect}
    />,
  );
}

describe("ApprovalListPane query states", () => {
  beforeEach(() => {
    useListApprovals.mockReset();
  });

  it.each([{ items: [] }, { items: [item] }])(
    "retains cached rows or empty state while showing a refresh error",
    ({ items }) => {
      const refetch = vi.fn();
      useListApprovals.mockReturnValue({
        ...success(items),
        isError: true,
        refetch,
      });
      renderPane();
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Unable to refresh approvals",
      );
      if (items.length)
        expect(screen.getByTestId("approval-list-row")).toBeInTheDocument();
      else
        expect(
          screen.getByText("No approvals for this Mission"),
        ).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(refetch).toHaveBeenCalledOnce();
    },
  );

  it("queries with the selected card's exact complete Mission identity", () => {
    useListApprovals.mockReturnValue(success([]));

    renderPane();

    expect(useListApprovals).toHaveBeenCalledWith({
      mission_source_repo: "acme/mission-control",
      mission_card_kind: "issue",
      mission_card_number: 42,
    });
  });

  it("renders a localized skeleton while the list loads", () => {
    useListApprovals.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderPane();

    expect(screen.getAllByTestId("approval-list-skeleton")).toHaveLength(3);
    expect(screen.getAllByTestId("approval-list-skeleton")[0]).toHaveClass(
      "animate-pulse",
    );
  });

  it("renders the approved empty state", () => {
    useListApprovals.mockReturnValue(success([]));

    renderPane();

    expect(
      screen.getByText("No approvals for this Mission"),
    ).toBeInTheDocument();
  });

  it("retries only the failed list query", () => {
    const refetch = vi.fn();
    useListApprovals.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("boom"),
      refetch,
    });

    renderPane();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Unable to load approvals",
    );
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("renders one selectable row container per returned approval", () => {
    useListApprovals.mockReturnValue(
      success([item, { ...item, request_id: "req-2" }]),
    );

    renderPane();

    expect(screen.getAllByTestId("approval-list-row")).toHaveLength(2);
  });

  it("renders pending newest-first before terminal newest-first", () => {
    useListApprovals.mockReturnValue(
      success([
        {
          ...item,
          request_id: "terminal-old",
          status: "approved",
          created_at: "2026-08-29T08:00:00Z",
        },
        {
          ...item,
          request_id: "pending-old",
          created_at: "2026-08-29T09:00:00Z",
        },
        {
          ...item,
          request_id: "terminal-new",
          status: "rejected",
          created_at: "2026-08-29T11:00:00Z",
        },
        {
          ...item,
          request_id: "pending-new",
          created_at: "2026-08-29T12:00:00Z",
        },
      ]),
    );

    renderPane();

    expect(
      screen
        .getAllByTestId("approval-list-row")
        .map((row) => row.dataset.requestId),
    ).toEqual(["pending-new", "pending-old", "terminal-new", "terminal-old"]);
  });

  it("selects the exact request id and marks only that row current", () => {
    const onSelect = vi.fn();
    useListApprovals.mockReturnValue(
      success([item, { ...item, request_id: "req-2", action_key: "merge" }]),
    );

    renderPane({ selectedApprovalRequestId: "req-2", onSelect });

    const selectedRow = screen.getByRole("button", { name: /^merge\b/i });
    const otherRow = screen.getByRole("button", { name: /^deploy\b/i });
    expect(selectedRow).toHaveAttribute("aria-current", "true");
    expect(otherRow).not.toHaveAttribute("aria-current", "true");

    fireEvent.click(otherRow);
    expect(onSelect).toHaveBeenCalledWith("req-1");
  });
});
