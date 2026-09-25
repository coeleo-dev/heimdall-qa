import { describe, expect, it } from "vitest";

import { ancestorsOf, flatten, nodesOfKind } from "@/lib/tree";
import type { NodeKind, TreeNode } from "@/types";

const node = (
  key: string,
  kind: NodeKind,
  extra: Partial<TreeNode> = {},
): TreeNode => ({
  key,
  kind,
  label: key,
  status: "pass",
  children: [],
  path: null,
  project: "proj",
  round_id: null,
  endpoint: null,
  matrix: null,
  case_id: null,
  reason: null,
  startable: false,
  expanded: false,
  environment: "",
  step_kind: "",
  fail_count: 0,
  live: "",
  ...extra,
});

const tree: TreeNode[] = [
  node("project:proj", "project", {
    label: "Projeto",
    children: [
      node("directory:proj:checkout", "directory", {
        label: "checkout",
        children: [
          node("campaign:proj:example-campaign", "campaign", {
            label: "example-campaign",
            children: [
              node("folder:proj:example-campaign:onboarding", "folder", {
                label: "onboarding",
                children: [
                  node("round:proj:rounds/walk-hn.yaml", "round", {
                    label: "walk-hn",
                    round_id: "walk-hn",
                    endpoint: "POST /api/tenants",
                    children: [
                      node("case:proj:rounds/walk-hn.yaml:post-tenants-H01", "case", {
                        label: "post-tenants",
                        case_id: "post-tenants-H01",
                      }),
                    ],
                  }),
                ],
              }),
            ],
          }),
        ],
      }),
    ],
  }),
];

describe("flatten", () => {
  it("walks the tree depth-first and keeps the order", () => {
    expect(flatten(tree).map((entry) => entry.key)).toEqual([
      "project:proj",
      "directory:proj:checkout",
      "campaign:proj:example-campaign",
      "folder:proj:example-campaign:onboarding",
      "round:proj:rounds/walk-hn.yaml",
      "case:proj:rounds/walk-hn.yaml:post-tenants-H01",
    ]);
  });
});

describe("ancestorsOf", () => {
  it("returns the whole chain from the root down, inclusive", () => {
    expect(
      ancestorsOf(tree, "case:proj:rounds/walk-hn.yaml:post-tenants-H01").map((e) => e.key),
    ).toEqual([
      "project:proj",
      "directory:proj:checkout",
      "campaign:proj:example-campaign",
      "folder:proj:example-campaign:onboarding",
      "round:proj:rounds/walk-hn.yaml",
      "case:proj:rounds/walk-hn.yaml:post-tenants-H01",
    ]);
  });
});

describe("nodesOfKind", () => {
  it("keeps tree order for the new folder kind", () => {
    expect(nodesOfKind(tree, "directory").map((entry) => entry.key)).toEqual([
      "directory:proj:checkout",
    ]);
  });

  it("finds the project root", () => {
    expect(nodesOfKind(tree, "project").map((entry) => entry.key)).toEqual(["project:proj"]);
  });

  it("keeps tree order", () => {
    expect(nodesOfKind(tree, "round").map((entry) => entry.key)).toEqual([
      "round:proj:rounds/walk-hn.yaml",
    ]);
  });
});
