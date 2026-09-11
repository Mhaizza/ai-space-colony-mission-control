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
  onConfirm,
  onClose,
}: {
  target: DecisionTarget;
  decision: "approve" | "reject";
  canConfirm: boolean;
  busy?: boolean;
  errorMessage?: string;
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
          <DialogTitle>Confirm {decision}</DialogTitle>
          <DialogDescription>
            {target.card.source_repo} · {target.card.kind} · #
            {target.card.number}
            <br />
            {target.action ?? "Approval request"} · {target.requestId}
          </DialogDescription>
        </DialogHeader>
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
