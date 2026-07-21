"use client";

export const dynamic = "force-dynamic";

import { useMemo } from "react";

import { SignedIn, SignedOut, useAuth } from "@/auth/clerk";
import { Activity, GitPullRequest, CircleDot, ShieldAlert, Database } from "lucide-react";

import { DashboardSidebar } from "@/components/organisms/DashboardSidebar";
import { DashboardShell } from "@/components/templates/DashboardShell";
import { SignedOutPanel } from "@/components/auth/SignedOutPanel";
import { ApiError } from "@/api/mutator";
import {
  type missionOverviewApiV1MissionOverviewGetResponse,
  useMissionOverviewApiV1MissionOverviewGet,
} from "@/api/generated/mission/mission";
import type {
  MissionCard,
  MissionQuarantineEntry,
  MissionSourceTypeCount,
  MissionWorkflowRecordSummary,
} from "@/api/generated/model";
import { formatRelativeTimestamp, formatTimestamp } from "@/lib/formatters";

const numberFormatter = new Intl.NumberFormat("en-US");
const DASH = "—";

type SyncStatus =
  | "idle"
  | "running"
  | "healthy"
  | "degraded"
  | "error"
  | "unknown";

type BadgeTone = "online" | "offline" | "neutral";

const KNOWN_SYNC_STATUSES: readonly SyncStatus[] = [
  "idle",
  "running",
  "healthy",
  "degraded",
  "error",
];

const normalizeSyncStatus = (status: string | null | undefined): SyncStatus => {
  if (status && (KNOWN_SYNC_STATUSES as readonly string[]).includes(status)) {
    return status as SyncStatus;
  }
  return "unknown";
};

const syncBadge = (status: SyncStatus): { label: string; tone: BadgeTone } => {
  switch (status) {
    case "healthy":
      return { label: "Healthy", tone: "online" };
    case "running":
      return { label: "Running", tone: "neutral" };
    case "idle":
      return { label: "Idle", tone: "neutral" };
    case "degraded":
      return { label: "Degraded", tone: "offline" };
    case "error":
      return { label: "Error", tone: "offline" };
    case "unknown":
      return { label: "Unknown", tone: "neutral" };
    default: {
      const _exhaustive: never = status;
      return _exhaustive;
    }
  }
};

const cardKindLabel = (kind: MissionCard["kind"]): string => {
  switch (kind) {
    case "issue":
      return "Issue";
    case "pull_request":
      return "Pull request";
    default: {
      const _exhaustive: never = kind;
      return _exhaustive;
    }
  }
};

const formatCount = (value: number | null | undefined): string =>
  typeof value === "number" && Number.isFinite(value)
    ? numberFormatter.format(Math.max(0, Math.round(value)))
    : "0";

function TopMetricCard({
  title,
  value,
  secondary,
  icon,
}: {
  title: string;
  value: string;
  secondary?: string;
  icon: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 md:p-6 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            {title}
          </p>
          <div className="mt-2 flex items-end gap-2">
            <p className="font-heading text-4xl font-bold text-slate-900">{value}</p>
            {secondary ? (
              <p className="pb-1 text-xs text-slate-500">{secondary}</p>
            ) : null}
          </div>
        </div>
        <div className="rounded-lg bg-slate-100 p-2 text-slate-600">{icon}</div>
      </div>
    </section>
  );
}

function StatusBadge({ tone, label }: { tone: BadgeTone; label: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${
        tone === "online"
          ? "bg-emerald-100 text-emerald-700"
          : tone === "offline"
            ? "bg-rose-100 text-rose-700"
            : "bg-slate-200 text-slate-700"
      }`}
    >
      {label}
    </span>
  );
}

export default function MissionControlPage() {
  const { isSignedIn } = useAuth();

  const overviewQuery = useMissionOverviewApiV1MissionOverviewGet<
    missionOverviewApiV1MissionOverviewGetResponse,
    ApiError
  >(
    {},
    {
      query: {
        enabled: Boolean(isSignedIn),
        refetchInterval: 15_000,
        refetchOnMount: "always",
      },
    },
  );

  const overview =
    overviewQuery.data?.status === 200 ? overviewQuery.data.data : null;

  const sourceCounts = useMemo<MissionSourceTypeCount[]>(
    () => overview?.projections.by_source_type ?? [],
    [overview],
  );
  const quarantineRecent = useMemo<MissionQuarantineEntry[]>(
    () => overview?.quarantine.recent ?? [],
    [overview],
  );
  const cards = useMemo<MissionCard[]>(
    () => overview?.workflow.cards ?? [],
    [overview],
  );
  const records = useMemo<MissionWorkflowRecordSummary[]>(
    () => overview?.workflow.records ?? [],
    [overview],
  );

  const status = normalizeSyncStatus(overview?.sync?.status);
  const badge = syncBadge(status);
  const adapter = overview?.adapter ?? null;
  const sync = overview?.sync ?? null;

  return (
    <DashboardShell>
      <SignedOut>
        <SignedOutPanel
          message="Sign in to access Mission Control."
          forceRedirectUrl="/mission"
          signUpForceRedirectUrl="/mission"
        />
      </SignedOut>
      <SignedIn>
        <DashboardSidebar />
        <main className="flex-1 overflow-y-auto bg-slate-50">
          <div className="p-4 md:p-8">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <h1 className="font-heading text-2xl font-bold text-slate-900">
                  Mission Control
                </h1>
                <p className="text-sm text-slate-500">
                  Read-only projection of the GitHub adapter (Slice 3.5).
                </p>
              </div>
              {adapter ? (
                <StatusBadge
                  tone={adapter.enabled ? "online" : "neutral"}
                  label={adapter.enabled ? "Adapter enabled" : "Adapter disabled"}
                />
              ) : null}
            </div>

            {overviewQuery.error ? (
              <div className="mb-4 rounded-lg border border-rose-300 bg-rose-50 p-3 text-sm text-rose-700">
                Load failed: {overviewQuery.error.message}
              </div>
            ) : null}

            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
              <TopMetricCard
                title="Live Projections"
                value={formatCount(overview?.projections.live)}
                secondary={`${formatCount(overview?.projections.tombstoned)} tombstoned`}
                icon={<Database className="h-4 w-4" />}
              />
              <TopMetricCard
                title="Cards Tracked"
                value={formatCount(overview?.workflow.cards_total)}
                secondary={`${formatCount(overview?.workflow.records_total)} workflow records`}
                icon={<CircleDot className="h-4 w-4" />}
              />
              <TopMetricCard
                title="Quarantined"
                value={formatCount(overview?.quarantine.total)}
                secondary={`${formatCount(overview?.quarantine.by_reason.length)} reasons`}
                icon={<ShieldAlert className="h-4 w-4" />}
              />
              <TopMetricCard
                title="Sync Failures"
                value={formatCount(sync?.consecutive_failures)}
                secondary="consecutive"
                icon={<Activity className="h-4 w-4" />}
              />
            </div>

            <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
              <section className="rounded-xl border border-slate-200 bg-white p-4 md:p-6 shadow-sm">
                <div className="mb-4 flex items-center justify-between gap-3">
                  <h3 className="text-lg font-semibold text-slate-900">Sync Health</h3>
                  <StatusBadge tone={badge.tone} label={badge.label} />
                </div>
                <div className="divide-y divide-slate-100 rounded-lg border border-slate-200">
                  <Row label="Adapter" value={adapter ? adapter.self_repo : DASH} />
                  <Row
                    label="Poll interval"
                    value={adapter ? `${formatCount(adapter.poll_interval_seconds)}s` : DASH}
                  />
                  <Row
                    label="Last success"
                    value={
                      sync?.last_success_at
                        ? formatRelativeTimestamp(sync.last_success_at)
                        : DASH
                    }
                  />
                  <Row
                    label="Last finished"
                    value={
                      sync?.last_finished_at
                        ? formatTimestamp(sync.last_finished_at)
                        : DASH
                    }
                  />
                  <Row
                    label="Last error"
                    value={sync?.last_error ?? DASH}
                    tone={sync?.last_error ? "danger" : "default"}
                  />
                </div>
              </section>

              <section className="rounded-xl border border-slate-200 bg-white p-4 md:p-6 shadow-sm">
                <h3 className="mb-4 text-lg font-semibold text-slate-900">
                  Projection Records
                </h3>
                {sourceCounts.length > 0 ? (
                  <div className="divide-y divide-slate-100 rounded-lg border border-slate-200">
                    {sourceCounts.map((item) => (
                      <div
                        key={item.source_type}
                        className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
                      >
                        <span className="min-w-0 truncate font-mono text-xs text-slate-600">
                          {item.source_type}
                        </span>
                        <span className="shrink-0 text-slate-800">
                          {formatCount(item.live)} live
                          {item.tombstoned > 0 ? (
                            <span className="ml-2 text-slate-400">
                              {formatCount(item.tombstoned)} tombstoned
                            </span>
                          ) : null}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <EmptyState label="No projection records yet." />
                )}
              </section>
            </div>

            <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
              <section className="rounded-xl border border-slate-200 bg-white p-4 md:p-6 shadow-sm">
                <h3 className="mb-4 text-lg font-semibold text-slate-900">Cards</h3>
                {cards.length > 0 ? (
                  <div className="max-h-[360px] space-y-2 overflow-y-auto pr-1">
                    {cards.map((card) => (
                      <div
                        key={`${card.kind}-${card.number}`}
                        className="rounded-lg border border-slate-200 px-3 py-2"
                      >
                        <div className="flex items-center justify-between gap-3">
                          <span className="min-w-0 truncate text-sm font-medium text-slate-900">
                            {card.kind === "pull_request" ? (
                              <GitPullRequest className="mr-1 inline h-3.5 w-3.5 text-slate-400" />
                            ) : (
                              <CircleDot className="mr-1 inline h-3.5 w-3.5 text-slate-400" />
                            )}
                            #{card.number} {card.title ?? ""}
                          </span>
                          <span className="shrink-0 text-xs text-slate-500">
                            {cardKindLabel(card.kind)}
                            {card.state ? ` · ${card.state}` : ""}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <EmptyState label="No project-linked cards yet." />
                )}
              </section>

              <section className="rounded-xl border border-slate-200 bg-white p-4 md:p-6 shadow-sm">
                <h3 className="mb-4 text-lg font-semibold text-slate-900">
                  Workflow Records
                </h3>
                {records.length > 0 ? (
                  <div className="max-h-[360px] space-y-2 overflow-y-auto pr-1">
                    {records.map((record) => (
                      <div
                        key={record.comment_source_id}
                        className="rounded-lg border border-slate-200 px-3 py-2 text-sm"
                      >
                        <div className="flex items-center justify-between gap-3">
                          <span className="min-w-0 truncate text-slate-800">
                            {record.record_type ?? "unparsed"}
                            {record.card ? ` · card #${record.card}` : ""}
                          </span>
                          {record.parsed_ok ? null : (
                            <StatusBadge tone="offline" label="unparsable" />
                          )}
                        </div>
                        <p className="mt-0.5 truncate text-xs text-slate-500">
                          {[record.worker, record.role, record.author]
                            .filter(Boolean)
                            .join(" · ") || "—"}
                        </p>
                      </div>
                    ))}
                  </div>
                ) : (
                  <EmptyState label="No workflow records yet." />
                )}
              </section>
            </div>

            <section className="mt-4 rounded-xl border border-slate-200 bg-white p-4 md:p-6 shadow-sm">
              <h3 className="mb-4 text-lg font-semibold text-slate-900">
                Recent Quarantine
              </h3>
              {quarantineRecent.length > 0 ? (
                <div className="divide-y divide-slate-100 rounded-lg border border-slate-200">
                  {quarantineRecent.map((entry) => (
                    <div
                      key={entry.id}
                      className="flex items-start justify-between gap-3 px-3 py-2 text-sm"
                    >
                      <div className="min-w-0">
                        <p className="truncate font-medium text-rose-700">
                          {entry.reason_code}
                        </p>
                        <p className="truncate text-xs text-slate-500">
                          {entry.message || entry.source_id || entry.source_type || DASH}
                        </p>
                      </div>
                      <span className="shrink-0 text-xs text-slate-500">
                        {formatRelativeTimestamp(entry.projected_at)}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState label="No quarantined records." tone="success" />
              )}
            </section>
          </div>
        </main>
      </SignedIn>
    </DashboardShell>
  );
}

function Row({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string;
  tone?: "default" | "danger";
}) {
  return (
    <div className="flex items-start justify-between gap-3 px-3 py-2">
      <span className="min-w-0 text-sm text-slate-500">{label}</span>
      <span
        className={`max-w-[65%] break-words text-right text-sm font-medium ${
          tone === "danger" ? "text-rose-700" : "text-slate-800"
        }`}
      >
        {value}
      </span>
    </div>
  );
}

function EmptyState({
  label,
  tone = "default",
}: {
  label: string;
  tone?: "default" | "success";
}) {
  return (
    <div
      className={`rounded-lg border p-3 text-sm ${
        tone === "success"
          ? "border-emerald-200 bg-emerald-50 text-emerald-700"
          : "border-slate-200 bg-slate-50 text-slate-500"
      }`}
    >
      {label}
    </div>
  );
}
