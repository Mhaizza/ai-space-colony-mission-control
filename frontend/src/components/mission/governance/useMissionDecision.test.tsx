import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getGetApprovalDetailApiV1MissionApprovalsRequestIdGetQueryKey as detailKey,
  getListApprovalsApiV1MissionApprovalsGetQueryKey as listKey,
} from "@/api/generated/mission-approvals/mission-approvals";
import type { ApprovalDetailResponse } from "@/api/generated/model";
import { useMissionDecision } from "./useMissionDecision";

const auth = vi.hoisted(() => ({
  local: true,
  token: "test-token-a" as string | null,
  userId: "user-a",
  sessionId: "session-a",
  getToken: vi.fn(),
}));
vi.mock("@/auth/localAuth", () => ({
  isLocalAuthMode: () => auth.local,
  getLocalAuthToken: () => auth.token,
}));
vi.mock("@/auth/clerk", () => ({
  useAuth: () => ({
    isLoaded: true,
    isSignedIn: !!auth.token,
    userId: auth.userId,
    sessionId: auth.sessionId,
    getToken: auth.getToken,
  }),
}));
vi.mock("@/lib/api-base", () => ({
  getApiBaseUrl: () => "http://localhost:8000",
}));

const target = {
  requestId: "request-a",
  card: {
    source_repo: "acme/alpha",
    kind: "issue" as const,
    number: 42,
    title: "Launch",
  },
  action: "deploy",
};
const detail: ApprovalDetailResponse = {
  request_id: target.requestId,
  status: "pending",
  mission_source_repo: target.card.source_repo,
  mission_card_kind: "issue",
  mission_card_number: 42,
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
function response(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
const fetchMock = vi.fn<typeof fetch>();
let client: QueryClient;
function setup() {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: 3 } },
  });
  const hook = renderHook(() => useMissionDecision(), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  act(() => {
    client.setQueryData(detailKey(target.requestId), {
      status: 200,
      data: detail,
    });
  });
  return hook;
}
function successfulFetch(input: string | URL | Request, init?: RequestInit) {
  if (init?.method === "POST")
    return Promise.resolve(
      response({
        request_id: target.requestId,
        decision_id: "decision-a",
        principal_id: "user-a",
        decision: JSON.parse(String(init.body)).decision,
        reason: JSON.parse(String(init.body)).reason,
        status: "pending",
        quorum_satisfied: false,
        mission_effect: null,
        created_at: "2026-09-11T00:00:00Z",
      }),
    );
  return Promise.resolve(
    response(
      String(input).includes("request-a")
        ? {
            ...detail,
            current_principal_decision: {
              decision_id: "decision-a",
              decision: "approve",
              reason: null,
              created_at: "2026-09-11T00:00:00Z",
            },
          }
        : { items: [], total: 0, limit: 200, offset: 0 },
    ),
  );
}
beforeEach(() => {
  auth.local = true;
  auth.token = "test-token-a";
  auth.userId = "user-a";
  auth.sessionId = "session-a";
  auth.getToken.mockReset().mockResolvedValue("test-token-a");
  fetchMock.mockReset().mockImplementation(successfulFetch);
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  client?.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Mission decision intent", () => {
  it("blocks double confirmation before rerender and binds authorization without caching credentials", async () => {
    const { result } = setup();
    const post = deferred<Response>();
    fetchMock.mockImplementation((input, init) =>
      init?.method === "POST" ? post.promise : successfulFetch(input, init),
    );
    act(() => {
      void result.current.confirm(target, "approve", "");
      void result.current.confirm(target, "reject", "changed");
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const options = fetchMock.mock.calls[0][1]!;
    expect(options.body).toBe(
      JSON.stringify({ decision: "approve", reason: null }),
    );
    expect(new Headers(options.headers).get("Idempotency-Key")).toBeTruthy();
    expect(new Headers(options.headers).get("Authorization")).toBe(
      "Bearer test-token-a",
    );
    expect(
      JSON.stringify(
        client
          .getMutationCache()
          .getAll()
          .map((m) => m.state.variables),
      ),
    ).not.toContain("test-token-a");
    post.resolve(
      await successfulFetch("", {
        method: "POST",
        body: JSON.stringify({ decision: "approve", reason: null }),
      }),
    );
    await waitFor(() =>
      expect(result.current.operation(target.requestId)?.phase).toBe(
        "recorded",
      ),
    );
  });

  it("retains the exact key/payload for manual uncertain retry despite a global automatic retry setting", async () => {
    const { result } = setup();
    fetchMock.mockRejectedValueOnce(new TypeError("network lost"));
    await act(async () => {
      await result.current.confirm(target, "approve", "  exact  ");
    });
    expect(result.current.operation(target.requestId)?.phase).toBe("uncertain");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const first = fetchMock.mock.calls[0][1]!;
    await act(async () => {
      await result.current.confirm(target, "reject", "different");
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      await result.current.retry(target.requestId);
    });
    const second = fetchMock.mock.calls.find(
      (call, index) => index > 0 && call[1]?.method === "POST",
    )![1]!;
    expect(second.body).toBe(first.body);
    expect(new Headers(second.headers).get("Idempotency-Key")).toBe(
      new Headers(first.headers).get("Idempotency-Key"),
    );
  });

  it.each([
    { status: "approved" },
    { can_decide: false },
    { mission_source_repo: "other/repo" },
    {
      current_principal_decision: {
        decision_id: "old",
        decision: "reject",
        reason: null,
        created_at: "now",
      },
    },
  ])(
    "blocks initial submission for ineligible/mismatched detail %j",
    async (patch) => {
      const { result } = setup();
      client.setQueryData(detailKey(target.requestId), {
        status: 200,
        data: { ...detail, ...patch },
      });
      await act(async () => {
        await result.current.confirm(target, "approve", "");
      });
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("does not retry an unresolved intent after local token replacement without rerender", async () => {
    const { result } = setup();
    fetchMock.mockRejectedValueOnce(new TypeError("network lost"));
    await act(async () => {
      await result.current.confirm(target, "approve", "");
    });
    auth.token = "test-token-b";
    await act(async () => {
      await result.current.retry(target.requestId);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("blocks credential resolution that finishes after Clerk session replacement", async () => {
    auth.local = false;
    const token = deferred<string>();
    auth.getToken.mockReturnValue(token.promise);
    const { result, rerender } = setup();
    act(() => {
      void result.current.confirm(target, "approve", "");
    });
    auth.sessionId = "session-b";
    rerender();
    await act(async () => {
      token.resolve("old-token");
      await token.promise;
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reports unavailable secure key generation without making a request", async () => {
    const { result } = setup();
    vi.spyOn(crypto, "randomUUID").mockImplementation(() => {
      throw new Error("unavailable");
    });
    await act(async () => {
      await result.current.confirm(target, "approve", "");
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.operation(target.requestId)?.message).toContain(
      "secure",
    );
  });

  it("treats an incomplete success body as uncertain, not recorded", async () => {
    const { result } = setup();
    fetchMock.mockResolvedValueOnce(
      response({
        request_id: target.requestId,
        decision_id: "x",
        decision: "approve",
      }),
    );
    await act(async () => {
      await result.current.confirm(target, "approve", "");
    });
    expect(result.current.operation(target.requestId)?.phase).toBe("uncertain");
  });

  it.each(["stale", "invalidated", "fetching", "previous-page"])(
    "blocks %s cached detail",
    async (state) => {
      const { result } = setup();
      const query = client
        .getQueryCache()
        .find({ queryKey: detailKey(target.requestId), exact: true })!;
      if (state === "stale")
        query.setState({ dataUpdatedAt: Date.now() - 20_000 });
      if (state === "invalidated") query.invalidate();
      if (state === "fetching") query.setState({ fetchStatus: "fetching" });
      if (state === "previous-page")
        query.setState({ dataUpdatedAt: result.current.readEpoch - 1 });
      await act(async () => {
        await result.current.confirm(target, "approve", "");
      });
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("allows Clerk token refresh within the same user and session", async () => {
    auth.local = false;
    const { result } = setup();
    auth.getToken.mockResolvedValue("refreshed-test-token");
    await act(async () => {
      await result.current.confirm(target, "approve", "");
    });
    expect(
      new Headers(fetchMock.mock.calls[0][1]?.headers).get("Authorization"),
    ).toBe("Bearer refreshed-test-token");
    expect(result.current.operation(target.requestId)?.phase).toBe("recorded");
  });

  it("does not reconcile a late POST after page unmount", async () => {
    const { result, unmount } = setup();
    const post = deferred<Response>();
    fetchMock.mockReturnValue(post.promise);
    act(() => {
      void result.current.confirm(target, "approve", "");
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    unmount();
    await act(async () => {
      post.resolve(
        await successfulFetch("", {
          method: "POST",
          body: JSON.stringify({ decision: "approve", reason: null }),
        }),
      );
    });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("allocates a new key only for a new confirmation after a definitive rejection", async () => {
    const { result } = setup();
    fetchMock.mockImplementation((input, init) =>
      init?.method === "POST"
        ? Promise.resolve(response({ detail: [] }, 422))
        : successfulFetch(input, init),
    );
    await act(async () => {
      await result.current.confirm(target, "approve", "first");
    });
    client.setQueryData(detailKey(target.requestId), {
      status: 200,
      data: detail,
    });
    await act(async () => {
      await result.current.confirm(target, "reject", "corrected");
    });
    const posts = fetchMock.mock.calls.filter(
      (call) => call[1]?.method === "POST",
    );
    expect(posts).toHaveLength(2);
    expect(new Headers(posts[1][1]?.headers).get("Idempotency-Key")).not.toBe(
      new Headers(posts[0][1]?.headers).get("Idempotency-Key"),
    );
  });
});

describe("Decision reconciliation", () => {
  it("refreshes only the submitted detail and exact Mission list using backend values", async () => {
    const { result } = setup();
    client.setQueryData(["unrelated"], "untouched");
    await act(async () => {
      await result.current.confirm(target, "approve", "");
    });
    expect(
      fetchMock.mock.calls.filter((call) => call[1]?.method === "GET"),
    ).toHaveLength(2);
    expect(client.getQueryData(detailKey(target.requestId))).toMatchObject({
      data: {
        status: "pending",
        current_principal_decision: { decision_id: "decision-a" },
      },
    });
    expect(
      client.getQueryData(
        listKey({
          mission_source_repo: "acme/alpha",
          mission_card_kind: "issue",
          mission_card_number: 42,
        }),
      ),
    ).toMatchObject({ data: { items: [] } });
    expect(client.getQueryData(["unrelated"])).toBe("untouched");
  });

  it("keeps an acknowledged POST separate from failed refresh and retries GET only", async () => {
    const { result } = setup();
    fetchMock.mockImplementation((input, init) =>
      init?.method === "GET" && String(input).includes("request-a")
        ? Promise.resolve(response({ detail: "unavailable" }, 503))
        : successfulFetch(input, init),
    );
    await act(async () => {
      await result.current.confirm(target, "approve", "");
    });
    expect(result.current.operation(target.requestId)).toMatchObject({
      phase: "recorded",
      refreshFailed: true,
    });
    expect(client.getQueryData(detailKey(target.requestId))).toMatchObject({
      data: detail,
    });
    expect(result.current.canDecide(target)).toBe(false);
    fetchMock.mockImplementation(successfulFetch);
    await act(async () => {
      await result.current.refresh(target.requestId);
    });
    expect(result.current.operation(target.requestId)).toMatchObject({
      phase: "recorded",
      refreshFailed: false,
    });
    expect(
      fetchMock.mock.calls.filter((call) => call[1]?.method === "POST"),
    ).toHaveLength(1);
  });

  it("does not turn a known conflict into a new-key retry or expose its raw error message", async () => {
    const { result } = setup();
    fetchMock.mockResolvedValueOnce(
      response(
        {
          detail: {
            code: "idempotency_key_reused",
            message: "secret-key-and-diagnostics",
          },
        },
        409,
      ),
    );
    await act(async () => {
      await result.current.confirm(target, "approve", "");
    });
    expect(result.current.operation(target.requestId)?.phase).toBe("rejected");
    expect(result.current.operation(target.requestId)?.message).not.toContain(
      "secret-key",
    );
    await act(async () => {
      await result.current.retry(target.requestId);
    });
    expect(
      fetchMock.mock.calls.filter((call) => call[1]?.method === "POST"),
    ).toHaveLength(1);
  });
});
