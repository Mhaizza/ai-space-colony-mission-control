import { defineConfig } from "orval";

const INPUT_TARGET = process.env.ORVAL_INPUT ?? "http://127.0.0.1:8000/openapi.json";

const OVERRIDE = {
  mutator: {
    path: "src/api/mutator.ts",
    name: "customFetch",
  },
  query: {
    useQuery: true,
    useMutation: true,
  },
};

export default defineConfig({
  api: {
    input: {
      target: INPUT_TARGET,
    },
    output: {
      mode: "tags-split",
      target: "src/api/generated/index.ts",
      schemas: "src/api/generated/model",
      client: "react-query",
      prettier: true,
      override: OVERRIDE,
    },
  },
  // Scoped to the `mission-approvals` tag only, with `headers: true` --
  // required so `Idempotency-Key` is generated as a typed function
  // argument (see `CreateApprovalApiV1MissionApprovalsPostHeaders` etc.)
  // instead of being buried in a generic `RequestInit` options bag.
  // Deliberately NOT applied to the default `api` project above: enabling
  // `headers: true` globally would also regenerate every other route's
  // signature repo-wide (e.g. the unrelated `agent` tag), which is out of
  // Checkpoint D's scope.
  missionApprovalsHeaders: {
    input: {
      target: INPUT_TARGET,
      filters: {
        tags: ["mission-approvals"],
      },
    },
    output: {
      mode: "tags-split",
      target: "src/api/generated/index.ts",
      schemas: "src/api/generated/model",
      client: "react-query",
      prettier: true,
      headers: true,
      override: OVERRIDE,
    },
  },
});
