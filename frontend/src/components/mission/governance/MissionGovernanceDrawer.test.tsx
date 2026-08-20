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

  it("starts with no approval selected, and remains so across a Mission switch", () => {
    const { rerender } = render(
      <MissionGovernanceDrawer card={issueCard} onClose={vi.fn()} />,
    );

    expect(screen.getByTestId("approval-detail-pane-mount")).toHaveAttribute(
      "data-selected-request-id",
      "",
    );

    // Deferred to Task 8 (real ApprovalListPane selection exists): the
    // stronger "non-null selection resets to null on Mission switch" case.
    rerender(<MissionGovernanceDrawer card={pullRequestCard} onClose={vi.fn()} />);

    expect(screen.getByTestId("approval-detail-pane-mount")).toHaveAttribute(
      "data-selected-request-id",
      "",
    );
    const drawer = screen.getByTestId("mission-governance-drawer");
    expect(drawer).toHaveTextContent(pullRequestCard.source_repo);
    expect(drawer).toHaveTextContent("Pull request");
    expect(drawer).toHaveTextContent(`#${pullRequestCard.number}`);
    expect(drawer).toHaveTextContent(pullRequestCard.title as string);
  });

  it("applies the responsive mount classes for the default (no-selection) shell state", () => {
    render(<MissionGovernanceDrawer card={issueCard} onClose={vi.fn()} />);

    // Deferred to Task 8/9 (real ApprovalListPane selection exists): the
    // selected-state class assertions.
    expect(screen.getByTestId("approval-list-pane-mount")).toHaveClass("block");
    expect(screen.getByTestId("approval-list-pane-mount")).not.toHaveClass("hidden");
    expect(screen.getByTestId("approval-detail-pane-mount")).toHaveClass(
      "hidden",
      "md:block",
    );
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
