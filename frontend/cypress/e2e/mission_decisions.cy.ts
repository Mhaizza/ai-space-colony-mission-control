/// <reference types="cypress" />

import { setupCommonPageTestHooks } from "../support/testHooks";
import type { ApprovalDetailResponse } from "../../src/api/generated/model";

// All API traffic is intercepted. These scenarios do not make real decisions.
describe("Mission decisions — mocked API", () => {
  setupCommonPageTestHooks("**/api/v1");
  let detail: ApprovalDetailResponse;
  let writes: number;
  let keys: string[];
  beforeEach(() => {
    writes = 0;
    keys = [];
    detail = {
      request_id: "request-a",
      status: "pending",
      mission_source_repo: "acme/alpha",
      mission_card_kind: "issue",
      mission_card_number: 42,
      action_key: "deploy-habitat",
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
    cy.intercept("**/api/v1/**", (request) => {
      if (request.method !== "GET") {
        expect(request.method).to.eq("POST");
        expect(new URL(request.url).pathname).to.match(
          /^\/api\/v1\/mission\/approvals\/request-a\/(decisions|supersede)$/,
        );
        request.reply({ statusCode: 500, body: {} });
      } else request.reply({ statusCode: 200, body: { items: [], total: 0 } });
    });
    // Register specific stubs after the catch-all (Cypress matches newest first).
    cy.intercept("GET", "**/api/v1/users/me*", {
      id: "u1",
      clerk_user_id: "local-auth-user",
      name: "Local User",
      timezone: "UTC",
    });
    cy.intercept("GET", "**/api/v1/organizations/me/list*", []);
    cy.intercept("GET", "**/api/v1/mission/overview*", {
      generated_at: detail.created_at,
      adapter: {
        enabled: true,
        project_owner: "acme",
        project_number: 1,
        self_repo: "acme/alpha",
        poll_interval_seconds: 60,
      },
      sync: null,
      projections: { total: 1, live: 1, tombstoned: 0, by_source_type: [] },
      quarantine: { total: 0, by_reason: [], recent: [] },
      workflow: {
        cards_total: 1,
        records_total: 0,
        records: [],
        cards: [
          {
            source_repo: "acme/alpha",
            kind: "issue",
            number: 42,
            title: "Launch habitat",
            url: "https://github.test/acme/alpha/issues/42",
            state: "open",
            updated_at: detail.created_at,
          },
        ],
      },
    });
    cy.intercept("GET", "**/api/v1/mission/approvals?*", (request) => {
      expect(request.query).to.include({
        mission_source_repo: "acme/alpha",
        mission_card_kind: "issue",
        mission_card_number: "42",
      });
      request.reply({ items: [detail], total: 1, limit: 200, offset: 0 });
    });
    cy.intercept("GET", "**/api/v1/mission/approvals/request-a", (request) =>
      request.reply(detail),
    ).as("detail");
    cy.intercept(
      "POST",
      "**/api/v1/mission/approvals/request-a/decisions",
      (request) => {
        writes += 1;
        keys.push(String(request.headers["idempotency-key"]));
        expect(request.headers["idempotency-key"]).to.be.a("string");
        expect(
          String(request.headers["idempotency-key"]).length,
        ).to.be.greaterThan(0);
        expect(request.body).to.have.all.keys("decision", "reason");
        detail = {
          ...detail,
          current_principal_decision: {
            decision_id: "d1",
            decision: request.body.decision,
            reason: request.body.reason,
            created_at: detail.created_at,
          },
        };
        request.reply({
          request_id: "request-a",
          decision_id: "d1",
          principal_id: "p1",
          decision: request.body.decision,
          reason: request.body.reason,
          status: "pending",
          quorum_satisfied: false,
          mission_effect: null,
          created_at: detail.created_at,
        });
      },
    ).as("decision");
    cy.intercept(
      "POST",
      "**/api/v1/mission/approvals/request-a/supersede",
      (request) => {
        expect(request.body).to.have.all.keys(
          "supersedes_decision_id",
          "decision",
          "reason",
        );
        expect(request.body.supersedes_decision_id).to.eq(
          detail.current_principal_decision?.decision_id,
        );
        expect(request.headers["idempotency-key"]).to.be.a("string");
        const key = String(request.headers["idempotency-key"]);
        expect(key.length).to.be.greaterThan(0);
        expect(keys).not.to.include(key);
        keys.push(key);
        writes += 1;
        const decisionId = `d${writes}`;
        detail = {
          ...detail,
          status: request.body.decision === "reject" ? "rejected" : "pending",
          current_principal_decision: {
            decision_id: decisionId,
            decision: request.body.decision,
            reason: request.body.reason,
            created_at: detail.created_at,
          },
        };
        request.reply({
          request_id: "request-a",
          decision_id: decisionId,
          principal_id: "p1",
          decision: request.body.decision,
          reason: request.body.reason,
          status: detail.status,
          quorum_satisfied: false,
          mission_effect: null,
          created_at: detail.created_at,
        });
      },
    ).as("supersede");
  });

  function openRequest() {
    cy.visit("/mission", {
      onBeforeLoad(win) {
        win.sessionStorage.setItem(
          "mc_local_auth_token",
          "synthetic-browser-test-token",
        );
      },
    });
    cy.contains("button", "Launch habitat").click();
    cy.get('[data-testid="approval-list-row"]').click();
    cy.wait("@detail");
  }

  for (const [width, height] of [
    [1440, 1000],
    [390, 844],
  ]) {
    it(`change decision at ${width}px: cancel, sequential successors and terminal lock`, () => {
      cy.viewport(width, height);
      openRequest();
      cy.contains("button", /^Approve$/).click();
      cy.contains("button", "Confirm approve").click();
      cy.wait("@decision");
      cy.contains("button", "Change decision").should("be.enabled").click();
      cy.get('[role="dialog"]').should(
        "contain.text",
        "Previous decision: approve · d1",
      );
      cy.get("textarea").should("have.focus").type("{esc}");
      cy.then(() => expect(writes).to.eq(1));
      cy.contains("button", "Change decision").click();
      cy.get("textarea").type("Updated reason");
      cy.screenshot(`checkpoint-d-change-${width}`, { capture: "viewport" });
      cy.contains("button", "Confirm approve").click();
      cy.wait("@supersede")
        .its("request.body.supersedes_decision_id")
        .should("eq", "d1");
      cy.contains("button", "Change decision").should("be.enabled").click();
      cy.get('[role="dialog"]')
        .should("contain.text", "approve · d2")
        .and("contain.text", "Updated reason");
      cy.get("select").select("reject");
      cy.contains("button", "Confirm reject").click();
      cy.wait("@supersede")
        .its("request.body.supersedes_decision_id")
        .should("eq", "d2");
      cy.get('[data-testid="approval-status"]').should("have.text", "rejected");
      cy.contains("button", "Change decision").should("not.exist");
      cy.then(() => expect(writes).to.eq(3));
    });
  }

  it("desktop: confirms a decision, preserves backend status, and hides initial actions afterward", () => {
    cy.viewport(1440, 1000);
    openRequest();
    cy.get('[data-testid="approval-list-pane"]').should("be.visible");
    cy.get('[data-testid="approval-detail-pane"]').should("be.visible");
    cy.contains("button", /^Approve$/).click();
    cy.get('[role="dialog"]')
      .should("be.visible")
      .and("contain.text", "Confirm approve");
    cy.get("textarea").should("have.focus").type("Reviewed in browser");
    cy.then(() => expect(writes).to.eq(0));
    cy.contains("button", "Confirm approve").click();
    cy.wait("@decision").its("request.body").should("deep.equal", {
      decision: "approve",
      reason: "Reviewed in browser",
    });
    cy.contains("Decision recorded.").scrollIntoView().should("be.visible");
    cy.get('[data-testid="approval-status"]').should("have.text", "pending");
    cy.contains("Your current decision").should("be.visible");
    cy.contains("button", /^Approve$/).should("not.exist");
    cy.then(() => expect(writes).to.eq(1));
  });

  it("mobile: cancels with Escape, rejects with confirmation, and returns to the list", () => {
    cy.viewport(390, 844);
    openRequest();
    cy.get('[data-testid="approval-list-pane"]').should("not.be.visible");
    cy.contains("button", /^Reject$/).click();
    cy.get("textarea").should("have.focus").type("{esc}");
    cy.get('[role="dialog"]').should("not.exist");
    cy.then(() => expect(writes).to.eq(0));
    cy.contains("button", /^Reject$/).click();
    cy.contains("button", "Confirm reject").click();
    cy.wait("@decision");
    cy.contains("Decision recorded.").scrollIntoView().should("be.visible");
    cy.contains("button", /^Back$/).click();
    cy.get('[data-testid="approval-list-pane"]').should("be.visible");
    cy.get('[data-testid="approval-detail-pane"]').should("not.be.visible");
  });
});
