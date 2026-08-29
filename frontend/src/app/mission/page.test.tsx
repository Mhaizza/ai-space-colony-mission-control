import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MissionControlPage from "./page";

const useMissionOverview = vi.fn();

vi.mock("@/auth/clerk", () => ({
  useAuth: () => ({ isSignedIn: true }),
  SignedIn: ({ children }: { children: ReactNode }) => <>{children}</>,
  SignedOut: () => null,
}));

vi.mock("@/components/templates/DashboardShell", () => ({
  DashboardShell: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock("@/components/organisms/DashboardSidebar", () => ({
  DashboardSidebar: () => null,
}));

vi.mock("@/api/generated/mission/mission", () => ({
  useMissionOverviewApiV1MissionOverviewGet: (...args: unknown[]) =>
    useMissionOverview(...args),
  useMissionAuditApiV1MissionAuditGet: () => ({ data: undefined, error: null }),
  useMissionPrStatusApiV1MissionPrStatusGet: () => ({
    data: undefined,
    error: null,
  }),
}));

vi.mock("@/api/generated/mission-approvals/mission-approvals", () => ({
  useListApprovalsApiV1MissionApprovalsGet: () => ({
    data: {
      status: 200,
      data: { items: [], total: 0, limit: 200, offset: 0 },
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useGetApprovalDetailApiV1MissionApprovalsRequestIdGet: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

const overviewResponse = {
  status: 200 as const,
  data: {
    generated_at: "2026-08-29T10:00:00Z",
    adapter: {
      enabled: true,
      project_owner: "acme",
      project_number: 1,
      self_repo: "acme/mission-control",
      poll_interval_seconds: 60,
    },
    sync: null,
    projections: { total: 2, live: 2, tombstoned: 0, by_source_type: [] },
    quarantine: { total: 0, by_reason: [], recent: [] },
    workflow: {
      cards_total: 2,
      records_total: 0,
      records: [],
      cards: [
        {
          source_repo: "acme/alpha",
          number: 12,
          kind: "issue" as const,
          title: "Prepare launch",
          url: "https://github.test/acme/alpha/issues/12",
          state: "open",
          updated_at: "2026-08-29T09:00:00Z",
        },
        {
          source_repo: "acme/beta",
          number: 34,
          kind: "pull_request" as const,
          title: "Deploy habitat",
          url: "https://github.test/acme/beta/pull/34",
          state: "open",
          updated_at: "2026-08-29T09:30:00Z",
        },
      ],
    },
  },
};

describe("Mission card governance selection", () => {
  beforeEach(() => {
    useMissionOverview.mockReturnValue({ data: overviewResponse, error: null });
  });

  it("opens the drawer for the exact selected Mission card", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MissionControlPage />
      </QueryClientProvider>,
    );

    expect(
      screen.queryByTestId("mission-governance-drawer"),
    ).not.toBeInTheDocument();

    const issueCard = screen.getByRole("button", {
      name: /#12 prepare launch/i,
    });
    expect(
      screen.getByRole("button", { name: /#34 deploy habitat/i }),
    ).toBeInTheDocument();

    await user.click(issueCard);

    const drawer = screen.getByTestId("mission-governance-drawer");
    expect(drawer).toHaveTextContent("acme/alpha");
    expect(drawer).toHaveTextContent("Issue");
    expect(drawer).toHaveTextContent("#12");
    expect(drawer).toHaveTextContent("Prepare launch");
  });
});
