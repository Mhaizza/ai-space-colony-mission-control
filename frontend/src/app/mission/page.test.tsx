import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import MissionControlPage from "./page";
import {
  useMissionAuditApiV1MissionAuditGet,
  useMissionOverviewApiV1MissionOverviewGet,
  useMissionPrStatusApiV1MissionPrStatusGet,
} from "@/api/generated/mission/mission";
import type { MissionCard, MissionOverview } from "@/api/generated/model";

vi.mock("next/navigation", () => ({
  usePathname: () => "/mission",
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
  }),
}));

vi.mock("@/auth/clerk", () => ({
  useAuth: () => ({ isSignedIn: true }),
  useUser: () => ({ isLoaded: true, isSignedIn: true, user: null }),
  SignedIn: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  SignedOut: () => null,
}));

vi.mock("@/api/generated/mission/mission", () => ({
  useMissionOverviewApiV1MissionOverviewGet: vi.fn(),
  useMissionAuditApiV1MissionAuditGet: vi.fn(),
  useMissionPrStatusApiV1MissionPrStatusGet: vi.fn(),
}));

const mockedUseOverview = vi.mocked(useMissionOverviewApiV1MissionOverviewGet);
const mockedUseAudit = vi.mocked(useMissionAuditApiV1MissionAuditGet);
const mockedUsePrStatus = vi.mocked(useMissionPrStatusApiV1MissionPrStatusGet);

const issueCard: MissionCard = {
  source_repo: "acme/mission-control",
  number: 101,
  kind: "issue",
  title: "Fix flaky projection test",
  url: null,
  state: "open",
  updated_at: null,
};

const pullRequestCard: MissionCard = {
  source_repo: "acme/frontend",
  number: 202,
  kind: "pull_request",
  title: "Add governance drawer",
  url: null,
  state: "open",
  updated_at: null,
};

function makeOverview(cards: MissionCard[]): MissionOverview {
  return {
    generated_at: "2026-08-01T00:00:00Z",
    adapter: {
      enabled: true,
      project_owner: "acme",
      project_number: 1,
      self_repo: "acme/mission-control",
      poll_interval_seconds: 60,
    },
    sync: {
      adapter_key: "github",
      status: "healthy",
      last_started_at: null,
      last_finished_at: null,
      last_success_at: null,
      last_error: null,
      consecutive_failures: 0,
    },
    projections: { total: 0, live: 0, tombstoned: 0, by_source_type: [] },
    quarantine: { total: 0, by_reason: [], recent: [] },
    workflow: {
      cards_total: cards.length,
      records_total: 0,
      cards,
      records: [],
    },
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function overviewResult(overview: MissionOverview): any {
  return {
    data: { status: 200, data: overview },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function emptyQueryResult(): any {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
}

const renderWithQueryClient = (ui: React.ReactNode) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
};

describe("MissionControlPage — card selection", () => {
  it("renders each Mission card as an interactive element", () => {
    mockedUseOverview.mockReturnValue(
      overviewResult(makeOverview([issueCard, pullRequestCard])),
    );
    mockedUseAudit.mockReturnValue(emptyQueryResult());
    mockedUsePrStatus.mockReturnValue(emptyQueryResult());

    renderWithQueryClient(<MissionControlPage />);

    expect(
      screen.getByRole("button", { name: /Fix flaky projection test/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Add governance drawer/i }),
    ).toBeInTheDocument();
  });

  it("renders no drawer before any card is clicked", () => {
    mockedUseOverview.mockReturnValue(
      overviewResult(makeOverview([issueCard, pullRequestCard])),
    );
    mockedUseAudit.mockReturnValue(emptyQueryResult());
    mockedUsePrStatus.mockReturnValue(emptyQueryResult());

    renderWithQueryClient(<MissionControlPage />);

    expect(
      screen.queryByTestId("mission-governance-drawer"),
    ).not.toBeInTheDocument();
  });

  it("clicking a card renders MissionGovernanceDrawer with that card's exact identity", () => {
    mockedUseOverview.mockReturnValue(
      overviewResult(makeOverview([issueCard, pullRequestCard])),
    );
    mockedUseAudit.mockReturnValue(emptyQueryResult());
    mockedUsePrStatus.mockReturnValue(emptyQueryResult());

    renderWithQueryClient(<MissionControlPage />);

    fireEvent.click(
      screen.getByRole("button", { name: /Add governance drawer/i }),
    );

    const drawer = screen.getByTestId("mission-governance-drawer");
    expect(drawer).toBeInTheDocument();
    expect(drawer).toHaveTextContent(pullRequestCard.source_repo);
    expect(drawer).toHaveTextContent("Pull request");
    expect(drawer).toHaveTextContent(`#${pullRequestCard.number}`);
    expect(drawer).toHaveTextContent(pullRequestCard.title as string);
    // Not the other card's identity.
    expect(drawer).not.toHaveTextContent(issueCard.source_repo);
  });
});
