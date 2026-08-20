import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { MissionGovernanceDrawer } from "./MissionGovernanceDrawer";
import type { SelectedMissionCard } from "./types";

const issueCard: SelectedMissionCard = {
  source_repo: "acme/mission-control",
  kind: "issue",
  number: 101,
  title: "Fix flaky projection test",
};

const pullRequestCard: SelectedMissionCard = {
  source_repo: "acme/frontend",
  kind: "pull_request",
  number: 202,
  title: "Add governance drawer",
};

describe("MissionGovernanceDrawer", () => {
  it("renders the header with source_repo, human-readable kind, number, and title", () => {
    render(<MissionGovernanceDrawer card={issueCard} onClose={vi.fn()} />);

    const drawer = screen.getByTestId("mission-governance-drawer");
    expect(drawer).toHaveTextContent(issueCard.source_repo);
    expect(drawer).toHaveTextContent("Issue");
    expect(drawer).toHaveTextContent(`#${issueCard.number}`);
    expect(drawer).toHaveTextContent(issueCard.title as string);
  });

  it("renders mount points for the approval list pane and the approval detail pane", () => {
    render(<MissionGovernanceDrawer card={issueCard} onClose={vi.fn()} />);

    expect(screen.getByTestId("approval-list-pane-mount")).toBeInTheDocument();
    expect(screen.getByTestId("approval-detail-pane-mount")).toBeInTheDocument();
  });

  it("resets selectedApprovalRequestId to null when the card prop changes", () => {
    const { rerender } = render(
      <MissionGovernanceDrawer card={issueCard} onClose={vi.fn()} />,
    );

    fireEvent.click(screen.getByTestId("stub-select-approval"));
    expect(screen.getByTestId("approval-detail-pane-mount")).toHaveAttribute(
      "data-selected-request-id",
      "stub-request-id",
    );

    rerender(<MissionGovernanceDrawer card={pullRequestCard} onClose={vi.fn()} />);

    expect(screen.getByTestId("approval-detail-pane-mount")).toHaveAttribute(
      "data-selected-request-id",
      "",
    );
  });

  it("applies the responsive mount classes for the unselected state", () => {
    render(<MissionGovernanceDrawer card={issueCard} onClose={vi.fn()} />);

    expect(screen.getByTestId("approval-list-pane-mount")).toHaveClass("block");
    expect(screen.getByTestId("approval-list-pane-mount")).not.toHaveClass("hidden");
    expect(screen.getByTestId("approval-detail-pane-mount")).toHaveClass(
      "hidden",
      "md:block",
    );
  });

  it("applies the responsive mount classes for the selected state", () => {
    render(<MissionGovernanceDrawer card={issueCard} onClose={vi.fn()} />);

    fireEvent.click(screen.getByTestId("stub-select-approval"));

    expect(screen.getByTestId("approval-list-pane-mount")).toHaveClass(
      "hidden",
      "md:block",
    );
    expect(screen.getByTestId("approval-detail-pane-mount")).toHaveClass("block");
    expect(screen.getByTestId("approval-detail-pane-mount")).not.toHaveClass("hidden");
  });

  it("calls onClose when the close control is activated, and it is keyboard-accessible", () => {
    const onClose = vi.fn();
    render(<MissionGovernanceDrawer card={issueCard} onClose={onClose} />);

    const closeButton = screen.getByRole("button", { name: /close/i });
    expect(closeButton.tagName).toBe("BUTTON");
    fireEvent.click(closeButton);

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
