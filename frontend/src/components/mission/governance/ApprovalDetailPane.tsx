"use client";

import { useGetApprovalDetailApiV1MissionApprovalsRequestIdGet } from "@/api/generated/mission-approvals/mission-approvals";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatTimestamp } from "@/lib/formatters";

import { statusBadgeVariant } from "./governanceStatus";

export function ApprovalDetailPane({
  selectedApprovalRequestId,
}: {
  selectedApprovalRequestId: string | null;
}) {
  const query = useGetApprovalDetailApiV1MissionApprovalsRequestIdGet(
    selectedApprovalRequestId ?? "",
    { query: { enabled: selectedApprovalRequestId !== null } },
  );

  if (selectedApprovalRequestId === null) {
    return (
      <p className="p-4 text-sm text-slate-500">
        Select an approval to view details
      </p>
    );
  }

  if (query.isLoading) {
    return (
      <div className="space-y-4 p-4" aria-label="Loading approval details">
        <div
          className="h-24 animate-pulse rounded-lg bg-slate-100"
          data-testid="approval-detail-skeleton"
        />
      </div>
    );
  }

  if (query.isError) {
    return (
      <div
        className="m-4 rounded-lg border border-rose-200 bg-rose-50 p-4"
        role="alert"
      >
        <p className="text-sm text-rose-700">Unable to load approval details</p>
        <Button
          className="mt-3"
          size="sm"
          variant="outline"
          onClick={() => query.refetch()}
        >
          Retry
        </Button>
      </div>
    );
  }

  const detail = query.data?.status === 200 ? query.data.data : null;

  if (!detail) {
    return null;
  }

  return (
    <article
      className="space-y-6 overflow-y-auto p-4 md:p-6"
      data-testid="approval-detail"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            {detail.request_id}
          </p>
          <h3 className="mt-1 text-lg font-semibold text-slate-900">
            {detail.action_key ?? "Approval request"}
          </h3>
          <p className="mt-1 text-sm text-slate-600">
            {detail.mission_source_repo} · {detail.mission_card_kind} · #
            {detail.mission_card_number}
          </p>
        </div>
        <Badge
          data-testid="approval-status"
          variant={statusBadgeVariant(detail.status)}
        >
          {detail.status}
        </Badge>
      </div>

      <section>
        <h4 className="text-sm font-semibold text-slate-900">
          Policy and quorum
        </h4>
        <dl className="mt-2 grid gap-2 text-sm sm:grid-cols-2">
          <DetailValue
            label="Policy"
            value={`${detail.policy_key} · v${detail.policy_version}`}
          />
          <DetailValue label="Decision rule" value={detail.decision_rule} />
          <DetailValue
            label="Quorum"
            value={detail.quorum_satisfied ? "Satisfied" : "Not satisfied"}
          />
        </dl>
        <ul className="mt-3 space-y-2">
          {detail.quorum_requirements.map((requirement) => (
            <li
              className="rounded-lg border border-slate-200 p-3 text-sm"
              key={requirement.slot}
            >
              <span className="font-medium text-slate-900">
                {requirement.slot}
              </span>
              <span className="ml-2 text-slate-500">
                {requirement.satisfied ? "satisfied" : "missing"} · eligible
                roles: {requirement.eligible_roles.join(", ") || "None"}
              </span>
            </li>
          ))}
        </ul>
        <div className="mt-3 text-sm text-slate-600">
          <span className="font-medium text-slate-900">
            Missing requirements:
          </span>{" "}
          {detail.missing_requirements.join(", ") || "None"}
        </div>
      </section>

      <section>
        <h4 className="text-sm font-semibold text-slate-900">
          Effective decisions
        </h4>
        {detail.effective_decisions.length > 0 ? (
          <ul className="mt-2 space-y-2">
            {detail.effective_decisions.map((decision) => (
              <li
                className="rounded-lg border border-slate-200 p-3 text-sm text-slate-600"
                key={`${decision.principal_id}-${decision.created_at}`}
              >
                <p className="font-medium text-slate-900">
                  {decision.principal_id} · {decision.decision}
                </p>
                <p>{decision.reason ?? "No reason provided"}</p>
                <p>
                  Roles: {decision.role_slugs_at_decision.join(", ") || "None"}{" "}
                  · {formatTimestamp(decision.created_at)}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-slate-500">No effective decisions</p>
        )}
      </section>

      <section>
        <h4 className="text-sm font-semibold text-slate-900">
          Lifecycle history
        </h4>
        <ul className="mt-2 space-y-2">
          {detail.lifecycle.map((event, index) => (
            <li
              className="rounded-lg border border-slate-200 p-3 text-sm text-slate-600"
              key={`${event.event_type}-${event.created_at}-${index}`}
            >
              <p className="font-medium text-slate-900">{event.event_type}</p>
              <p>Triggered by: {event.triggered_by_principal_id ?? "System"}</p>
              <p>
                Detail:{" "}
                {event.detail === null ? "None" : JSON.stringify(event.detail)}
              </p>
              <p>{formatTimestamp(event.created_at)}</p>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h4 className="text-sm font-semibold text-slate-900">
          Timeline and effect
        </h4>
        <dl className="mt-2 grid gap-2 text-sm sm:grid-cols-3">
          <DetailValue
            label="Created"
            value={formatTimestamp(detail.created_at)}
          />
          <DetailValue
            label="Expires"
            value={formatTimestamp(detail.expires_at, "Never")}
          />
          <DetailValue
            label="Resolved"
            value={formatTimestamp(detail.resolved_at, "Not resolved")}
          />
        </dl>
        <p className="mt-3 text-sm text-slate-600">
          <span className="font-medium text-slate-900">Mission effect:</span>{" "}
          {detail.mission_effect ?? "None"}
        </p>
      </section>

      <section className="rounded-lg bg-slate-50 p-3 text-sm text-slate-600">
        <p>
          {detail.can_decide
            ? "You can decide on this request"
            : "You cannot decide on this request"}
        </p>
        {detail.current_principal_decision ? (
          <div className="mt-2">
            <p className="font-medium text-slate-900">Your current decision</p>
            <p>Decision ID: {detail.current_principal_decision.decision_id}</p>
            <p>
              {detail.current_principal_decision.decision} ·{" "}
              {detail.current_principal_decision.reason ?? "No reason provided"}{" "}
              · {formatTimestamp(detail.current_principal_decision.created_at)}
            </p>
          </div>
        ) : (
          <p className="mt-2">No current decision</p>
        )}
      </section>
    </article>
  );
}

function DetailValue({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </dt>
      <dd className="mt-0.5 text-slate-900">{value}</dd>
    </div>
  );
}
