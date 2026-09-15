"use client";

import { useId, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { SelectedMissionCard } from "./types";
import type { ApprovalDetailResponse } from "@/api/generated/model";

export type PriorDecision = NonNullable<
  ApprovalDetailResponse["current_principal_decision"]
>;

export interface DecisionTarget {
  requestId: string;
  card: SelectedMissionCard;
  action: string | null;
}

export function DecisionDialog({
  target,
  decision,
  canConfirm,
  busy = false,
  errorMessage,
  priorDecision,
  onDecisionChange,
  onConfirm,
  onClose,
}: {
  target: DecisionTarget;
  decision: "approve" | "reject";
  canConfirm: boolean;
  busy?: boolean;
  errorMessage?: string;
  priorDecision?: PriorDecision;
  onDecisionChange?: (decision: "approve" | "reject") => void;
  onConfirm: (reason: string) => void;
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const reasonId = useId();
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <DialogContent
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          reasonRef.current?.focus();
        }}
        onEscapeKeyDown={(event) => {
          if (busy) event.preventDefault();
        }}
        onInteractOutside={(event) => {
          if (busy) event.preventDefault();
        }}
      >
        <DialogHeader>
          <DialogTitle>
            {priorDecision ? "Change decision" : `Confirm ${decision}`}
          </DialogTitle>
          <DialogDescription>
            {target.card.source_repo} · {target.card.kind} · #
            {target.card.number}
            <br />
            {target.action ?? "Approval request"} · {target.requestId}
          </DialogDescription>
        </DialogHeader>
        {priorDecision ? (
          <section className="mt-3 space-y-2 text-sm">
            <p>
              Previous decision: {priorDecision.decision} ·{" "}
              {priorDecision.decision_id}
            </p>
            <p>
              Previous reason: {priorDecision.reason ?? "No reason provided"}
            </p>
            <label className="block">
              New decision
              <select
                className="mt-1 block w-full rounded border p-2"
                value={decision}
                disabled={busy}
                onChange={(event) => {
                  if (
                    event.target.value === "approve" ||
                    event.target.value === "reject"
                  )
                    onDecisionChange?.(event.target.value);
                }}
              >
                <option value="approve">Approve</option>
                <option value="reject">Reject</option>
              </select>
            </label>
          </section>
        ) : null}
        <label className="mt-4 block text-sm font-medium" htmlFor={reasonId}>
          Reason (optional)
        </label>
        <textarea
          ref={reasonRef}
          id={reasonId}
          className="mt-2 w-full rounded-lg border border-slate-300 p-3"
          rows={3}
          disabled={busy}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
        {errorMessage ? (
          <p role="alert" className="text-sm text-rose-700">
            {errorMessage}
          </p>
        ) : null}
        {!canConfirm && !busy ? (
          <p role="status" className="text-sm text-slate-600">
            Refresh the request details before deciding. Your permission or the
            request may have changed.
          </p>
        ) : null}
        <DialogFooter className="mt-4">
          <Button variant="outline" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={busy || !canConfirm}
            onClick={() => onConfirm(reason)}
          >
            {busy ? "Sending…" : `Confirm ${decision}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
