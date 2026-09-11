import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DecisionDialog } from "./DecisionDialog";

const target = {
  requestId: "request-a",
  card: {
    source_repo: "acme/alpha",
    kind: "issue" as const,
    number: 42,
    title: "Launch",
  },
  action: "deploy-production",
};

describe("Decision confirmation", () => {
  it.each(["approve", "reject"] as const)(
    "confirms %s only after an explicit click and preserves the reason",
    async (decision) => {
      const user = userEvent.setup();
      const confirm = vi.fn();
      render(
        <DecisionDialog
          target={target}
          decision={decision}
          canConfirm
          onConfirm={confirm}
          onClose={vi.fn()}
        />,
      );
      expect(screen.getByRole("dialog")).toHaveAccessibleName(
        `Confirm ${decision}`,
      );
      expect(screen.getByRole("dialog")).toHaveTextContent("acme/alpha");
      expect(screen.getByRole("dialog")).toHaveTextContent("deploy-production");
      expect(confirm).not.toHaveBeenCalled();
      await user.type(
        screen.getByLabelText("Reason (optional)"),
        "  Reviewed  ",
      );
      await user.click(
        screen.getByRole("button", { name: `Confirm ${decision}` }),
      );
      expect(confirm).toHaveBeenCalledExactlyOnceWith("  Reviewed  ");
    },
  );

  it("cancels with no submission and initially focuses the reason", async () => {
    const user = userEvent.setup();
    const confirm = vi.fn();
    const close = vi.fn();
    render(
      <DecisionDialog
        target={target}
        decision="approve"
        canConfirm
        onConfirm={confirm}
        onClose={close}
      />,
    );
    expect(screen.getByLabelText("Reason (optional)")).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(close).toHaveBeenCalledOnce();
    expect(confirm).not.toHaveBeenCalled();
  });

  it("locks fields and dismissal while sending", () => {
    const close = vi.fn();
    render(
      <DecisionDialog
        target={target}
        decision="approve"
        canConfirm
        busy
        onConfirm={vi.fn()}
        onClose={close}
      />,
    );
    expect(screen.getByLabelText("Reason (optional)")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Sending…" })).toBeDisabled();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(close).not.toHaveBeenCalled();
  });

  it("does not permit confirmation after capability becomes unavailable", () => {
    render(
      <DecisionDialog
        target={target}
        decision="reject"
        canConfirm={false}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Confirm reject" }),
    ).toBeDisabled();
  });
});
