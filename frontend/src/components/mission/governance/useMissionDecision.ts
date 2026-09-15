"use client";

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import {
  getGetApprovalDetailApiV1MissionApprovalsRequestIdGetQueryKey as detailKey,
  getListApprovalsApiV1MissionApprovalsGetQueryKey as listKey,
  getApprovalDetailApiV1MissionApprovalsRequestIdGet as readDetail,
  listApprovalsApiV1MissionApprovalsGet as readList,
  submitApprovalDecisionApiV1MissionApprovalsRequestIdDecisionsPost as submitDecision,
  useSubmitApprovalDecisionApiV1MissionApprovalsRequestIdDecisionsPost as useSubmitDecision,
  supersedeApprovalDecisionApiV1MissionApprovalsRequestIdSupersedePost as supersedeDecision,
  useSupersedeApprovalDecisionApiV1MissionApprovalsRequestIdSupersedePost as useSupersedeDecision,
} from "@/api/generated/mission-approvals/mission-approvals";
import type {
  ApprovalDetailResponse,
  SubmitDecisionRequest,
  SupersedeDecisionRequest,
} from "@/api/generated/model";
import { ApiError } from "@/api/mutator";
import { useAuth } from "@/auth/clerk";
import { getLocalAuthToken, isLocalAuthMode } from "@/auth/localAuth";
import type { DecisionTarget } from "./DecisionDialog";
import { DECISION_FRESHNESS_MS } from "./decisionFreshness";

export type DecisionOperation = {
  target: DecisionTarget;
  key: string;
  phase: "sending" | "uncertain" | "rejected" | "refreshing" | "recorded";
  message: string;
  refreshFailed?: boolean;
  acknowledgedDecisionId?: string;
  decisionMismatch?: boolean;
} & (
  | { mode: "submit"; data: SubmitDecisionRequest }
  | { mode: "supersede"; data: SupersedeDecisionRequest }
);

function recordedMessage(
  operation: Pick<DecisionOperation, "refreshFailed" | "decisionMismatch">,
) {
  if (operation.decisionMismatch)
    return "Decision recorded, but the current decision has changed. Reload the page before making another decision.";
  return operation.refreshFailed
    ? "Decision recorded; some information could not be refreshed."
    : "Decision recorded.";
}

export function matchesDecisionTarget(
  detail: ApprovalDetailResponse,
  target: DecisionTarget,
) {
  return (
    detail.request_id === target.requestId &&
    detail.mission_source_repo === target.card.source_repo &&
    detail.mission_card_kind === target.card.kind &&
    detail.mission_card_number === target.card.number
  );
}

class SessionChanged extends Error {}

function rejectionMessage(error: ApiError): string {
  const data = error.data;
  const detail =
    data && typeof data === "object" && "detail" in data ? data.detail : null;
  const code =
    detail && typeof detail === "object" && "code" in detail
      ? detail.code
      : null;
  if (code === "idempotency_key_reused")
    return "This submission conflicts with an earlier attempt. Refresh the request before continuing.";
  if (error.status === 401 || error.status === 403)
    return "Your sign-in or permission has changed. Refresh the request before deciding.";
  if (error.status === 404)
    return "This approval request is no longer available.";
  if (error.status === 409)
    return "The request or your decision has changed. Refresh to see its current state.";
  return "The decision was not accepted. Refresh the request and check your input before confirming again.";
}

export function useMissionDecision() {
  const auth = useAuth();
  const client = useQueryClient();
  const local = isLocalAuthMode();
  const localCredential = local ? getLocalAuthToken() : null;
  const { isLoaded, isSignedIn, userId, sessionId } = auth;
  // Credentials stay in this private context, never in query keys or mutation variables.
  const context = useMemo(
    () => ({
      local,
      localCredential,
      isLoaded,
      isSignedIn,
      userId,
      sessionId,
      startedAt: Date.now(),
    }),
    [local, localCredential, isLoaded, isSignedIn, userId, sessionId],
  );
  const active = useRef(context);
  const mounted = useRef(false);
  const operations = useRef<Record<string, DecisionOperation>>({});
  const callers = useRef(
    new Map<
      string,
      { context: typeof context; getToken: () => Promise<string | null> }
    >(),
  );
  const [view, setView] = useState<{
    context: typeof context;
    operations: Record<string, DecisionOperation>;
  }>({ context, operations: {} });

  useLayoutEffect(() => {
    mounted.current = true;
    if (active.current !== context) {
      active.current = context;
      operations.current = {};
      const root = listKey()[0];
      // Cancel old-session reads before replacing caller-specific cached data.
      const predicate = (query: { queryKey: readonly unknown[] }) =>
        typeof query.queryKey[0] === "string" &&
        (query.queryKey[0] === root ||
          query.queryKey[0].startsWith(`${root}/`));
      void client
        .cancelQueries({ predicate })
        .then(() => client.resetQueries({ predicate }));
    }
    return () => {
      mounted.current = false;
    };
  }, [client, context]);

  function sameSession(expected = context) {
    if (
      !mounted.current ||
      active.current !== expected ||
      !expected.isLoaded ||
      !expected.isSignedIn
    )
      return false;
    if (expected.local)
      return (
        !!expected.localCredential &&
        getLocalAuthToken() === expected.localCredential
      );
    // The hook identity is primary. Also detect Clerk changes before a React render.
    const clerk = (
      window as unknown as {
        Clerk?: {
          session?: { id: string } | null;
          user?: { id: string } | null;
        };
      }
    ).Clerk;
    return (
      !!expected.userId &&
      !!expected.sessionId &&
      (!clerk ||
        (clerk.session?.id === expected.sessionId &&
          clerk.user?.id === expected.userId))
    );
  }

  function update(operation: DecisionOperation, expected = context) {
    if (!sameSession(expected)) return;
    operations.current = {
      ...operations.current,
      [operation.target.requestId]: operation,
    };
    setView({ context: expected, operations: operations.current });
  }

  function canDecide(target: DecisionTarget, priorId?: string) {
    if (!sameSession()) return false;
    const current = operations.current[target.requestId];
    if (
      current &&
      (current.refreshFailed ||
        current.decisionMismatch ||
        (current.phase !== "rejected" && current.phase !== "recorded"))
    )
      return false;
    const query = client.getQueryState<{
      status: number;
      data: ApprovalDetailResponse;
    }>(detailKey(target.requestId));
    const detail = query?.data?.status === 200 ? query.data.data : null;
    if (
      current?.phase === "recorded" &&
      (!current.acknowledgedDecisionId ||
        detail?.current_principal_decision?.decision_id !==
          current.acknowledgedDecisionId)
    )
      return false;
    return (
      !!detail &&
      query?.status === "success" &&
      query.fetchStatus === "idle" &&
      !query.isInvalidated &&
      query.dataUpdatedAt >= context.startedAt &&
      Date.now() - query.dataUpdatedAt < DECISION_FRESHNESS_MS &&
      matchesDecisionTarget(detail, target) &&
      detail.status === "pending" &&
      detail.can_decide &&
      (priorId !== undefined
        ? !!priorId &&
          detail.current_principal_decision?.decision_id === priorId
        : detail.current_principal_decision === null)
    );
  }

  const mutation = useSubmitDecision<unknown>({
    mutation: {
      retry: false,
      mutationFn: async (variables) => {
        const caller = callers.current.get(
          variables.headers["Idempotency-Key"],
        );
        if (!caller) throw new SessionChanged();
        const expected = caller.context;
        if (!sameSession(expected)) throw new SessionChanged();
        const token = expected.local
          ? getLocalAuthToken()
          : await caller.getToken();
        if (!token || !sameSession(expected)) throw new SessionChanged();
        // Explicit credentials prevent customFetch from resolving a different caller after an await.
        return submitDecision(
          variables.requestId,
          variables.data,
          variables.headers,
          {
            headers: { Authorization: `Bearer ${token}` },
          },
        );
      },
    },
  });

  const supersedeMutation = useSupersedeDecision<unknown>({
    mutation: {
      retry: false,
      mutationFn: async (variables) => {
        const caller = callers.current.get(
          variables.headers["Idempotency-Key"],
        );
        if (!caller || !sameSession(caller.context)) throw new SessionChanged();
        const token = caller.context.local
          ? getLocalAuthToken()
          : await caller.getToken();
        if (!token || !sameSession(caller.context)) throw new SessionChanged();
        return supersedeDecision(
          variables.requestId,
          variables.data,
          variables.headers,
          {
            headers: { Authorization: `Bearer ${token}` },
          },
        );
      },
    },
  });

  async function reconcile(operation: DecisionOperation) {
    const failed = {
      refreshFailed: true,
      decisionMismatch: !!operation.decisionMismatch,
    };
    if (!sameSession()) return failed;
    const params = {
      mission_source_repo: operation.target.card.source_repo,
      mission_card_kind: operation.target.card.kind,
      mission_card_number: operation.target.card.number,
    };
    try {
      const token = context.local ? getLocalAuthToken() : await auth.getToken();
      if (!token || !sameSession()) return failed;
      const keys = [detailKey(operation.target.requestId), listKey(params)];
      await Promise.all(
        keys.map((queryKey) => client.cancelQueries({ queryKey, exact: true })),
      );
      if (!sameSession()) return failed;
      await Promise.all(
        keys.map((queryKey) =>
          client.invalidateQueries({
            queryKey,
            exact: true,
            refetchType: "none",
          }),
        ),
      );
      if (!sameSession()) return failed;
      const results = await Promise.allSettled([
        client.fetchQuery({
          queryKey: detailKey(operation.target.requestId),
          retry: false,
          staleTime: 0,
          queryFn: async ({ signal }) => {
            if (!sameSession()) throw new SessionChanged();
            const result = await readDetail(operation.target.requestId, {
              signal,
              headers: { Authorization: `Bearer ${token}` },
            });
            if (!sameSession()) throw new SessionChanged();
            if (
              result.status !== 200 ||
              !matchesDecisionTarget(result.data, operation.target)
            )
              throw new Error("Invalid detail response");
            return result;
          },
        }),
        client.fetchQuery({
          queryKey: listKey(params),
          retry: false,
          staleTime: 0,
          queryFn: async ({ signal }) => {
            if (!sameSession()) throw new SessionChanged();
            const result = await readList(params, {
              signal,
              headers: { Authorization: `Bearer ${token}` },
            });
            if (!sameSession()) throw new SessionChanged();
            if (result.status !== 200 || !Array.isArray(result.data?.items))
              throw new Error("Invalid list response");
            return result;
          },
        }),
      ]);
      const read = results[0];
      const decisionMismatch =
        !!operation.decisionMismatch ||
        (!!operation.acknowledgedDecisionId &&
          read.status === "fulfilled" &&
          read.value.status === 200 &&
          read.value.data.current_principal_decision?.decision_id !==
            operation.acknowledgedDecisionId);
      return {
        refreshFailed: results.some((result) => result.status === "rejected"),
        decisionMismatch,
      };
    } catch {
      return failed;
    }
  }

  async function send(operation: DecisionOperation) {
    if (!sameSession()) return;
    update({ ...operation, phase: "sending", message: "Sending decision…" });
    callers.current.set(operation.key, { context, getToken: auth.getToken });
    try {
      const headers = { "Idempotency-Key": operation.key };
      const result = await (operation.mode === "supersede"
        ? supersedeMutation.mutateAsync({
            requestId: operation.target.requestId,
            data: operation.data,
            headers,
          })
        : mutation.mutateAsync({
            requestId: operation.target.requestId,
            data: operation.data,
            headers: { "Idempotency-Key": operation.key },
          }));
      if (!sameSession()) return;
      if (
        result.status !== 200 ||
        !result.data ||
        result.data.request_id !== operation.target.requestId ||
        typeof result.data.decision_id !== "string" ||
        !result.data.decision_id ||
        typeof result.data.principal_id !== "string" ||
        typeof result.data.status !== "string" ||
        typeof result.data.quorum_satisfied !== "boolean" ||
        typeof result.data.created_at !== "string" ||
        !(
          result.data.mission_effect === null ||
          typeof result.data.mission_effect === "string"
        ) ||
        result.data.reason !== operation.data.reason ||
        result.data.decision !== operation.data.decision
      )
        throw new Error("Unconfirmed response");
      const acknowledged = {
        ...operation,
        acknowledgedDecisionId: result.data.decision_id,
      };
      update({
        ...acknowledged,
        phase: "refreshing",
        message: "Decision recorded. Refreshing information…",
      });
      const reconciliation = await reconcile(acknowledged);
      update({
        ...acknowledged,
        phase: "recorded",
        ...reconciliation,
        message: recordedMessage(reconciliation),
      });
    } catch (error) {
      if (error instanceof SessionChanged || !sameSession()) return;
      if (
        error instanceof ApiError &&
        error.status >= 400 &&
        error.status < 500 &&
        error.status !== 408 &&
        error.status !== 429
      ) {
        update({
          ...operation,
          phase: "refreshing",
          message: rejectionMessage(error),
        });
        const reconciliation = await reconcile(operation);
        update({
          ...operation,
          phase: "rejected",
          ...reconciliation,
          message: rejectionMessage(error),
        });
      } else {
        update({
          ...operation,
          phase: "uncertain",
          message:
            "The result could not be confirmed. Retry the same decision to confirm its outcome.",
        });
      }
    } finally {
      callers.current.delete(operation.key);
    }
  }

  async function confirm(
    target: DecisionTarget,
    decision: "approve" | "reject",
    reason: string,
    priorId?: string,
  ) {
    if (!canDecide(target, priorId)) return;
    const intent =
      priorId !== undefined
        ? {
            mode: "supersede" as const,
            data: {
              supersedes_decision_id: priorId,
              decision,
              reason: reason === "" ? null : reason,
            },
          }
        : {
            mode: "submit" as const,
            data: { decision, reason: reason === "" ? null : reason },
          };
    let key: string;
    try {
      key = crypto.randomUUID();
    } catch {
      update({
        target,
        ...intent,
        key: "",
        phase: "rejected",
        message:
          "A secure submission key could not be created. Use a secure browser context before confirming again.",
      });
      return;
    }
    const operation: DecisionOperation = {
      target: { ...target, card: { ...target.card } },
      ...intent,
      key,
      phase: "sending",
      message: "Sending decision…",
    };
    await send(operation);
    return sameSession()
      ? operations.current[target.requestId]?.phase
      : undefined;
  }

  async function retry(requestId: string) {
    if (!sameSession()) return;
    const operation = operations.current[requestId];
    if (operation?.phase === "uncertain") await send(operation);
  }

  async function refresh(requestId: string) {
    if (!sameSession()) return;
    const operation = operations.current[requestId];
    if (
      !operation ||
      operation.phase === "sending" ||
      operation.phase === "refreshing"
    )
      return;
    update({ ...operation, phase: "refreshing" });
    const reconciliation = await reconcile(operation);
    update({
      ...operation,
      ...reconciliation,
      message:
        operation.phase === "recorded"
          ? recordedMessage(reconciliation)
          : operation.message,
    });
  }

  return {
    confirm,
    retry,
    refresh,
    canDecide,
    readEpoch: context.startedAt,
    signedIn: !!context.isLoaded && !!context.isSignedIn,
    operation: (requestId: string) =>
      view.context === context ? view.operations[requestId] : undefined,
  };
}

export type MissionDecisionController = ReturnType<typeof useMissionDecision>;
