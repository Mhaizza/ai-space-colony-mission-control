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
let serverDecisionId = "decision-a";
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
  if (init?.method === "POST") {
    if (String(input).endsWith("/supersede")) serverDecisionId += "-next";
    return Promise.resolve(
      response({
        request_id: target.requestId,
        decision_id: serverDecisionId,
        principal_id: "user-a",
        decision: JSON.parse(String(init.body)).decision,
        reason: JSON.parse(String(init.body)).reason,
        status: "pending",
        quorum_satisfied: false,
        mission_effect: null,
        created_at: "2026-09-11T00:00:00Z",
      }),
    );
  }
  return Promise.resolve(
    response(
      String(input).includes("request-a")
        ? {
            ...detail,
            current_principal_decision: {
              decision_id: serverDecisionId,
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
  serverDecisionId = "decision-a";
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
  function seedPrior(
    id = "prior",
    overrides: Partial<ApprovalDetailResponse> = {},
  ) {
    client.setQueryData(detailKey(target.requestId), {
      status: 200,
      data: {
        ...detail,
        current_principal_decision: {
          decision_id: id,
          decision: "approve",
          reason: null,
          created_at: detail.created_at,
        },
        ...overrides,
      },
    });
  }

  it("shares the synchronous request lock and retries uncertain supersede bytes even after terminal reads", async () => {
    const { result } = setup();
    seedPrior();
    const post = deferred<Response>();
    fetchMock.mockImplementation(() => post.promise);
    let first!: Promise<unknown>;
    act(() => {
      first = result.current.confirm(target, "reject", "keep", "prior");
      void result.current.confirm(target, "approve", "other", "prior");
      void result.current.confirm(target, "approve", "initial");
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await act(async () => {
      post.resolve(response({}, 503));
      await first;
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    seedPrior("successor", { status: "rejected", can_decide: false });
    fetchMock.mockResolvedValue(response({}, 503));
    await act(async () => {
      await result.current.retry(target.requestId);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe(fetchMock.mock.calls[0][0]);
    expect(fetchMock.mock.calls[1][1]?.body).toBe(
      fetchMock.mock.calls[0][1]?.body,
    );
    expect(
      new Headers(fetchMock.mock.calls[1][1]?.headers).get("Idempotency-Key"),
    ).toBe(
      new Headers(fetchMock.mock.calls[0][1]?.headers).get("Idempotency-Key"),
    );
  });

  it("blocks supersede token resolution after session replacement and resets only approval caches", async () => {
    auth.local = false;
    const token = deferred<string | null>();
    auth.getToken.mockReturnValue(token.promise);
    const { result, rerender } = setup();
    seedPrior();
    client.setQueryData(listKey(), { status: 200, data: { items: [] } });
    client.setQueryData(["unrelated"], "keep");
    let submit!: Promise<unknown>;
    act(() => {
      submit = result.current.confirm(target, "reject", "", "prior");
    });
    await waitFor(() => expect(auth.getToken).toHaveBeenCalled());
    auth.sessionId = "session-b";
    rerender();
    await act(async () => {
      token.resolve("old-token");
      await submit;
    });
    await waitFor(() =>
      expect(client.getQueryData(detailKey(target.requestId))).toBeUndefined(),
    );
    expect(client.getQueryData(listKey())).toBeUndefined();
    expect(client.getQueryData(["unrelated"])).toBe("keep");
    expect(result.current.operation(target.requestId)).toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["detail", "list"])(
    "requires successful %s recovery before the next supersede",
    async (failedRead) => {
      const { result } = setup();
      seedPrior();
      fetchMock.mockImplementation((input, init) => {
        if (
          init?.method !== "POST" &&
          String(input).includes("request-a") === (failedRead === "detail")
        )
          return Promise.resolve(response({}, 503));
        return successfulFetch(input, init);
      });
      await act(async () => {
        await result.current.confirm(target, "reject", "", "prior");
      });
      expect(result.current.operation(target.requestId)?.phase).toBe(
        "recorded",
      );
      expect(result.current.canDecide(target, serverDecisionId)).toBe(false);
      fetchMock.mockImplementation(successfulFetch);
      await act(async () => {
        await result.current.refresh(target.requestId);
      });
      expect(result.current.canDecide(target, serverDecisionId)).toBe(true);
      expect(
        fetchMock.mock.calls.filter((call) => call[1]?.method === "POST"),
      ).toHaveLength(1);
    },
  );

  it.each(["approved", "rejected", "expired", "superseded"])(
    "blocks new supersede on terminal %s",
    async (status) => {
      const { result } = setup();
      seedPrior("prior", { status });
      await act(async () => {
        await result.current.confirm(target, "approve", "", "prior");
      });
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("supersedes only the captured effective decision with an explicit key and route", async () => {
    const { result } = setup();
    client.setQueryData(detailKey(target.requestId), {
      status: 200,
      data: {
        ...detail,
        current_principal_decision: {
          decision_id: "prior",
          decision: "approve",
          reason: null,
          created_at: detail.created_at,
        },
      },
    });
    await act(async () => {
      await result.current.confirm(target, "reject", "  changed  ", "prior");
    });
    const posts = fetchMock.mock.calls.filter(
      (call) => call[1]?.method === "POST",
    );
    expect(posts).toHaveLength(1);
    expect(String(posts[0][0])).toMatch(/\/request-a\/supersede$/);
    expect(JSON.parse(String(posts[0][1]?.body))).toEqual({
      supersedes_decision_id: "prior",
      decision: "reject",
      reason: "  changed  ",
    });
    expect(
      new Headers(posts[0][1]?.headers).get("Idempotency-Key"),
    ).toBeTruthy();
  });

  it("blocks a supersede whose captured prior ID is no longer current", async () => {
    const { result } = setup();
    client.setQueryData(detailKey(target.requestId), {
      status: 200,
      data: {
        ...detail,
        current_principal_decision: {
          decision_id: "newer",
          decision: "approve",
          reason: null,
          created_at: detail.created_at,
        },
      },
    });
    await act(async () => {
      await result.current.confirm(target, "reject", "", "prior");
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("unlocks a new supersede only after acknowledged successor reads and uses a new key", async () => {
    const { result } = setup();
    await act(async () => {
      await result.current.confirm(target, "approve", "");
    });
    expect(result.current.canDecide(target, "decision-a")).toBe(true);
    await act(async () => {
      await result.current.confirm(target, "reject", "", "decision-a");
    });
    const posts = fetchMock.mock.calls.filter(
      (call) => call[1]?.method === "POST",
    );
    expect(posts).toHaveLength(2);
    expect(new Headers(posts[1][1]?.headers).get("Idempotency-Key")).not.toBe(
      new Headers(posts[0][1]?.headers).get("Idempotency-Key"),
    );
    expect(String(posts[1][0])).toMatch(/supersede$/);
  });

  it.each([false, true])(
    "keeps an acknowledged write locked when GET returns a conflicting decision ID (supersede: %s)",
    async (supersede) => {
      const { result } = setup();
      if (supersede) seedPrior();
      fetchMock.mockImplementation((input, init) =>
        init?.method === "POST" || !String(input).includes("request-a")
          ? successfulFetch(input, init)
          : Promise.resolve(
              response({
                ...detail,
                current_principal_decision: {
                  decision_id: "other",
                  decision: "approve",
                  reason: null,
                  created_at: detail.created_at,
                },
              }),
            ),
      );
      await act(async () => {
        await result.current.confirm(
          target,
          "approve",
          "",
          supersede ? "prior" : undefined,
        );
      });
      expect(result.current.operation(target.requestId)?.phase).toBe(
        "recorded",
      );
      expect(result.current.operation(target.requestId)?.message).toMatch(
        /Reload the page/,
      );
      expect(result.current.canDecide(target, "other")).toBe(false);
      await act(async () => {
        await result.current.refresh(target.requestId);
      });
      expect(
        fetchMock.mock.calls.filter((call) => call[1]?.method === "POST"),
      ).toHaveLength(1);
      fetchMock.mockImplementation(successfulFetch);
      await act(async () => {
        await result.current.refresh(target.requestId);
      });
      expect(result.current.canDecide(target, serverDecisionId)).toBe(false);
      expect(result.current.operation(target.requestId)?.message).toMatch(
        /Reload the page/,
      );
    },
  );

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
  it.each([
    ["submit", "detail"],
    ["submit", "list"],
    ["supersede", "detail"],
    ["supersede", "list"],
  ] as const)(
    "blocks rejected %s after failed %s reads until GET-only refresh succeeds",
    async (mode, failedRead) => {
      const { result } = setup();
      const priorId = mode === "supersede" ? "decision-a" : undefined;
      const snapshot = {
        ...detail,
        current_principal_decision: priorId
          ? {
              decision_id: priorId,
              decision: "approve" as const,
              reason: null,
              created_at: detail.created_at,
            }
          : null,
      };
      act(() => {
        client.setQueryData(detailKey(target.requestId), {
          status: 200,
          data: snapshot,
        });
      });
      let failReads = true;
      fetchMock.mockImplementation((input, init) => {
        if (init?.method === "POST") {
          return Promise.resolve(response({ detail: "Invalid decision" }, 422));
        }
        const isDetail = String(input).includes("request-a");
        if (failReads && isDetail === (failedRead === "detail")) {
          return Promise.resolve(response({ detail: "unavailable" }, 503));
        }
        return isDetail
          ? Promise.resolve(response(snapshot))
          : successfulFetch(input, init);
      });
      await act(async () => {
        await result.current.confirm(target, "reject", "keep reason", priorId);
      });
      expect(result.current.operation(target.requestId)).toMatchObject({
        phase: "rejected",
        refreshFailed: true,
        data: { reason: "keep reason" },
      });
      expect(result.current.canDecide(target, priorId)).toBe(false);
      await act(async () => {
        await result.current.confirm(target, "approve", "blocked", priorId);
        await result.current.retry(target.requestId);
        await result.current.refresh(target.requestId);
      });
      expect(result.current.operation(target.requestId)).toMatchObject({
        phase: "rejected",
        refreshFailed: true,
      });
      expect(result.current.canDecide(target, priorId)).toBe(false);
      const callsBeforeRecovery = fetchMock.mock.calls.length;
      failReads = false;
      await act(async () => {
        await result.current.refresh(target.requestId);
      });
      expect(result.current.operation(target.requestId)).toMatchObject({
        phase: "rejected",
        refreshFailed: false,
        data: { reason: "keep reason" },
      });
      expect(result.current.canDecide(target, priorId)).toBe(true);
      const recoveryCalls = fetchMock.mock.calls.slice(callsBeforeRecovery);
      expect(recoveryCalls).toHaveLength(2);
      expect(recoveryCalls.every((call) => call[1]?.method === "GET")).toBe(
        true,
      );
      const posts = fetchMock.mock.calls.filter(
        (call) => call[1]?.method === "POST",
      );
      expect(posts).toHaveLength(1);
      expect(String(posts[0][0])).toMatch(
        mode === "supersede" ? /\/supersede$/ : /\/decisions$/,
      );
    },
  );

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
