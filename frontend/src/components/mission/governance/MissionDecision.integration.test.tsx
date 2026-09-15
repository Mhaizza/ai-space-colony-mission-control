import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ApprovalDetailResponse } from "@/api/generated/model";
import { MissionGovernanceDrawer } from "./MissionGovernanceDrawer";
import { useMissionDecision } from "./useMissionDecision";

vi.mock("@/auth/localAuth", () => ({
  isLocalAuthMode: () => true,
  getLocalAuthToken: () => "fixture-token",
}));
vi.mock("@/auth/clerk", () => ({
  useAuth: () => ({
    isLoaded: true,
    isSignedIn: true,
    userId: "local-user",
    sessionId: "local-session",
    getToken: async () => "fixture-token",
  }),
}));
vi.mock("@/lib/api-base", () => ({
  getApiBaseUrl: () => "http://localhost:8000",
}));
const alpha = {
  source_repo: "acme/alpha",
  kind: "issue" as const,
  number: 1,
  title: "Alpha",
};
const beta = {
  source_repo: "acme/beta",
  kind: "issue" as const,
  number: 2,
  title: "Beta",
};
const baseDetail: ApprovalDetailResponse = {
  request_id: "request-a",
  status: "pending",
  mission_source_repo: "acme/alpha",
  mission_card_kind: "issue",
  mission_card_number: 1,
  action_key: "deploy",
  policy_key: "p",
  policy_version: 1,
  decision_rule: "all",
  quorum_satisfied: false,
  quorum_requirements: [],
  missing_requirements: [],
  effective_decisions: [],
  lifecycle: [],
  created_at: "2026-09-11T00:00:00Z",
  expires_at: null,
  resolved_at: null,
  mission_effect: null,
  can_decide: true,
  current_principal_decision: null,
};
let detail: ApprovalDetailResponse;
const fetchMock = vi.fn<typeof fetch>();
let client: QueryClient;
function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
function Harness() {
  const controller = useMissionDecision();
  const [card, setCard] = useState<typeof alpha | null>(alpha);
  return (
    <>
      <button onClick={() => setCard(alpha)}>Open Alpha</button>
      <button onClick={() => setCard(beta)}>Open Beta</button>
      {card ? (
        <MissionGovernanceDrawer
          card={card}
          onClose={() => setCard(null)}
          decisionController={controller}
        />
      ) : null}
    </>
  );
}
function mount() {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  );
}
async function selectRequest() {
  fireEvent.click(await screen.findByTestId("approval-list-row"));
  await screen.findByTestId("approval-detail");
}
async function start() {
  fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
}
function serve(input: string | URL | Request, init?: RequestInit) {
  const url = new URL(String(input));
  if (init?.method === "POST") {
    const body = JSON.parse(String(init.body));
    const decisionId = url.pathname.endsWith("/supersede")
      ? `${body.supersedes_decision_id}-next`
      : "decision-a";
    detail = {
      ...detail,
      current_principal_decision: {
        decision_id: decisionId,
        decision: body.decision,
        reason: body.reason,
        created_at: detail.created_at,
      },
    };
    return Promise.resolve(
      json({
        request_id: "request-a",
        decision_id: decisionId,
        principal_id: "local",
        decision: body.decision,
        reason: body.reason,
        status: "pending",
        quorum_satisfied: false,
        mission_effect: null,
        created_at: detail.created_at,
      }),
    );
  }
  if (url.pathname.endsWith("/request-a")) return Promise.resolve(json(detail));
  if (url.pathname.endsWith("/request-b"))
    return Promise.resolve(
      json({
        ...baseDetail,
        request_id: "request-b",
        mission_source_repo: "acme/beta",
        mission_card_number: 2,
      }),
    );
  const b = url.searchParams.get("mission_source_repo") === "acme/beta";
  return Promise.resolve(
    json({
      items: [
        {
          ...baseDetail,
          request_id: b ? "request-b" : "request-a",
          mission_source_repo: b ? "acme/beta" : "acme/alpha",
          mission_card_number: b ? 2 : 1,
        },
      ],
      total: 1,
      limit: 200,
      offset: 0,
    }),
  );
}
beforeEach(() => {
  detail = { ...baseDetail };
  fetchMock.mockReset().mockImplementation(serve);
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  client.clear();
  vi.unstubAllGlobals();
});

describe("Mission decision UI with real query hooks", () => {
  it("shows acknowledged-ID conflict as recorded, locks new intent, and refreshes GET only", async () => {
    fetchMock.mockImplementation(async (input, init) => {
      const result = await serve(input, init);
      if (init?.method === "POST") {
        detail = {
          ...detail,
          current_principal_decision: {
            ...detail.current_principal_decision!,
            decision_id: "external-successor",
          },
        };
      }
      return result;
    });
    mount();
    await selectRequest();
    await start();
    fireEvent.click(screen.getByRole("button", { name: "Confirm approve" }));
    await screen.findByText(
      /Decision recorded, but the current decision has changed/,
    );
    expect(
      screen.getByRole("button", { name: "Change decision" }),
    ).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "Retry same decision" }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Refresh information" }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Refresh information" }),
      ).toBeEnabled(),
    );
    expect(
      fetchMock.mock.calls.filter((call) => call[1]?.method === "POST"),
    ).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: "Change decision" }),
    ).toBeDisabled();
  });

  it("preserves a supersede retry across Back and drawer close/reopen without leaking to another Mission", async () => {
    detail = {
      ...baseDetail,
      current_principal_decision: {
        decision_id: "prior",
        decision: "approve",
        reason: "old",
        created_at: baseDetail.created_at,
      },
    };
    fetchMock.mockImplementation((input, init) =>
      init?.method === "POST"
        ? Promise.resolve(json({}, 503))
        : serve(input, init),
    );
    mount();
    await selectRequest();
    fireEvent.click(screen.getByRole("button", { name: "Change decision" }));
    fireEvent.change(screen.getByLabelText("New decision"), {
      target: { value: "reject" },
    });
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "new reason" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm reject" }));
    await screen.findByRole("button", { name: "Retry same decision" });
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await selectRequest();
    fireEvent.click(
      screen.getByRole("button", { name: "Close governance drawer" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Open Beta" }));
    await selectRequest();
    expect(screen.queryByText("new reason")).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Close governance drawer" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Open Alpha" }));
    await selectRequest();
    fireEvent.click(
      screen.getByRole("button", { name: "Retry same decision" }),
    );
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter((call) => call[1]?.method === "POST"),
      ).toHaveLength(2),
    );
    const posts = fetchMock.mock.calls.filter(
      (call) => call[1]?.method === "POST",
    );
    expect(posts[1][0]).toBe(posts[0][0]);
    expect(posts[1][1]?.body).toBe(posts[0][1]?.body);
    expect(new Headers(posts[1][1]?.headers).get("Idempotency-Key")).toBe(
      new Headers(posts[0][1]?.headers).get("Idempotency-Key"),
    );
  });

  it("offers Change decision after initial success and captures the current prior ID", async () => {
    mount();
    await selectRequest();
    await start();
    fireEvent.click(screen.getByRole("button", { name: "Confirm approve" }));
    const change = await screen.findByRole("button", {
      name: "Change decision",
    });
    await waitFor(() => expect(change).toBeEnabled());
    fireEvent.click(change);
    expect(screen.getByRole("dialog")).toHaveTextContent("decision-a");
    fireEvent.change(screen.getByLabelText("New decision"), {
      target: { value: "reject" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm reject" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter((call) => call[1]?.method === "POST"),
      ).toHaveLength(2),
    );
    const lastPost = fetchMock.mock.calls.filter(
      (call) => call[1]?.method === "POST",
    )[1];
    expect(String(lastPost[0])).toMatch(/supersede$/);
    expect(JSON.parse(String(lastPost[1]?.body))).toEqual({
      supersedes_decision_id: "decision-a",
      decision: "reject",
      reason: null,
    });
  });

  it("blocks a captured change-decision draft when the current ID changes", async () => {
    detail = {
      ...baseDetail,
      current_principal_decision: {
        decision_id: "prior",
        decision: "approve",
        reason: "old reason",
        created_at: baseDetail.created_at,
      },
    };
    mount();
    await selectRequest();
    fireEvent.click(
      await screen.findByRole("button", { name: "Change decision" }),
    );
    detail = {
      ...detail,
      current_principal_decision: {
        ...detail.current_principal_decision!,
        decision_id: "newer",
      },
    };
    await act(async () => {
      await client.refetchQueries({ type: "active" });
    });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Confirm approve" }),
      ).toBeDisabled(),
    );
    expect(screen.getByRole("dialog")).toHaveTextContent("prior");
    fireEvent.click(screen.getByRole("button", { name: "Confirm approve" }));
    expect(
      fetchMock.mock.calls.filter((call) => call[1]?.method === "POST"),
    ).toHaveLength(0);
  });

  it("opens confirmation without POST, sends once on confirmation and retains backend detail", async () => {
    mount();
    await selectRequest();
    await start();
    expect(
      fetchMock.mock.calls.filter((call) => call[1]?.method === "POST"),
    ).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Confirm approve" }));
    await screen.findByText("Decision recorded.");
    expect(screen.getByTestId("approval-status")).toHaveTextContent("pending");
    expect(screen.getByText("Your current decision")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Approve" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /change decision/i }),
    ).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter((call) => call[1]?.method === "POST"),
    ).toHaveLength(1);
  });

  it.each(["approved", "rejected", "expired", "superseded"])(
    "never offers decisions for %s even if can_decide is true",
    async (status) => {
      detail = { ...detail, status };
      mount();
      await selectRequest();
      expect(
        screen.queryByRole("button", { name: "Approve" }),
      ).not.toBeInTheDocument();
    },
  );

  it("retains uncertain intent across Back and drawer close/reopen and retries exactly", async () => {
    fetchMock.mockImplementation((input, init) =>
      init?.method === "POST"
        ? Promise.reject(new TypeError("lost response"))
        : serve(input, init),
    );
    mount();
    await selectRequest();
    await start();
    fireEvent.change(screen.getByLabelText("Reason (optional)"), {
      target: { value: "Original reason" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm approve" }));
    await screen.findByRole("button", { name: "Retry same decision" });
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await selectRequest();
    expect(
      screen.getByRole("button", { name: "Retry same decision" }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Close governance drawer" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Open Alpha" }));
    await selectRequest();
    fireEvent.click(
      screen.getByRole("button", { name: "Retry same decision" }),
    );
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter((call) => call[1]?.method === "POST"),
      ).toHaveLength(2),
    );
    const posts = fetchMock.mock.calls.filter(
      (call) => call[1]?.method === "POST",
    );
    expect(posts[1][1]?.body).toBe(posts[0][1]?.body);
    expect(new Headers(posts[1][1]?.headers).get("Idempotency-Key")).toBe(
      new Headers(posts[0][1]?.headers).get("Idempotency-Key"),
    );
  });

  it.each([false, true])(
    "keeps the reason editable after a validation rejection (supersede: %s)",
    async (supersede) => {
      if (supersede)
        detail = {
          ...baseDetail,
          current_principal_decision: {
            decision_id: "prior",
            decision: "approve",
            reason: "old reason",
            created_at: baseDetail.created_at,
          },
        };
      fetchMock.mockImplementation((input, init) =>
        init?.method === "POST"
          ? Promise.resolve(json({ detail: "invalid reason" }, 422))
          : serve(input, init),
      );
      mount();
      await selectRequest();
      if (supersede)
        fireEvent.click(
          screen.getByRole("button", { name: "Change decision" }),
        );
      else await start();
      fireEvent.change(screen.getByRole("textbox"), {
        target: { value: "Keep this reason" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Confirm approve" }));
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "Confirm approve" }),
        ).toBeEnabled(),
      );
      expect(screen.getByRole("textbox")).toHaveValue("Keep this reason");
      expect(
        within(screen.getByRole("dialog")).getByRole("alert"),
      ).toHaveTextContent("not accepted");
    },
  );

  it("does not show a late result on a different Mission", async () => {
    let complete!: (response: Response) => void;
    fetchMock.mockImplementation((input, init) =>
      init?.method === "POST"
        ? new Promise<Response>((resolve) => {
            complete = resolve;
          })
        : serve(input, init),
    );
    mount();
    await selectRequest();
    await start();
    fireEvent.click(screen.getByRole("button", { name: "Confirm approve" }));
    await waitFor(() => expect(complete).toBeDefined());
    // Simulate a parent-driven selection change while the modal blocks user navigation.
    fireEvent.click(screen.getByText("Open Beta"));
    await selectRequest();
    await act(async () => {
      complete(
        await serve("http://localhost/request-a/decisions", {
          method: "POST",
          body: JSON.stringify({ decision: "approve", reason: null }),
        }),
      );
    });
    await waitFor(() =>
      expect(
        within(screen.getByTestId("approval-detail")).getByText("request-b"),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("Decision recorded.")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("blocks confirmation if fresh backend capability changes while the dialog is open", async () => {
    mount();
    await selectRequest();
    await start();
    detail = { ...detail, can_decide: false };
    await act(async () => {
      await client.refetchQueries({ type: "active" });
    });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Confirm approve" }),
      ).toBeDisabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirm approve" }));
    expect(
      fetchMock.mock.calls.filter((call) => call[1]?.method === "POST"),
    ).toHaveLength(0);
  });

  it("keeps details visible and offers read-only refresh after a recorded decision cannot refresh", async () => {
    let submitted = false;
    fetchMock.mockImplementation((input, init) => {
      if (init?.method === "POST") {
        submitted = true;
        return serve(input, init);
      }
      if (submitted && String(input).endsWith("/request-a"))
        return Promise.resolve(json({}, 503));
      return serve(input, init);
    });
    mount();
    await selectRequest();
    await start();
    fireEvent.click(screen.getByRole("button", { name: "Confirm approve" }));
    await screen.findByText(
      "Decision recorded; some information could not be refreshed.",
    );
    expect(screen.getByTestId("approval-detail")).toHaveTextContent(
      "Policy and quorum",
    );
    expect(
      screen.queryByRole("button", { name: "Retry same decision" }),
    ).not.toBeInTheDocument();
    fetchMock.mockImplementation(serve);
    fireEvent.click(
      screen.getByRole("button", { name: "Refresh information" }),
    );
    await screen.findByText("Decision recorded.");
    expect(
      fetchMock.mock.calls.filter((call) => call[1]?.method === "POST"),
    ).toHaveLength(1);
  });
});
