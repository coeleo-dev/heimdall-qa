import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CollectionTree } from "@/components/CollectionTree";
import type { Labels, TreeNode } from "@/types";

const LABELS: Labels = {
  status_label: { pass: "Passou", fail: "Falhou", skip: "Pulado" },
  status_pill: {},
  kind_label: {
    project: "Projeto",
    directory: "Pasta",
    campaign: "Campanha",
    folder: "Fluxo",
    round: "Rodada",
    case: "Caso",
  },
  step_kind_label: {},
  phase_label: {},
  scope_label: {},
  scope_running: {},
  step_note: {},
  step_scope_label: {},
};

function node(partial: Partial<TreeNode> & { key: string; label: string }): TreeNode {
  return {
    kind: "case",
    status: "not_reviewed",
    children: [],
    path: null,
    round_id: null,
    endpoint: null,
    matrix: null,
    case_id: null,
    reason: null,
    startable: true,
    expanded: false,
    environment: "sandbox",
    step_kind: "",
    fail_count: 0,
    project: "proj",
    live: "",
    ...partial,
  };
}

/** A campaign with five flows — the shape that was collapsing to one. */
function campaignWithFlows(): TreeNode {
  return node({
    key: "campaign:proj:campaign-a",
    kind: "campaign",
    label: "campaign-a",
    path: "campaigns/campaign-a.yaml",
    expanded: true,
    children: ["A0", "A1", "A2", "A3", "A4"].map((matrix) =>
      node({
        key: `folder:proj:campaign-a:${matrix}`,
        kind: "folder",
        label: matrix,
        matrix,
        expanded: matrix === "A0",
        children: [
          node({
            key: `round:proj:rounds/${matrix}.yaml`,
            kind: "round",
            label: `round-${matrix}`,
            round_id: `round-${matrix}`,
            expanded: matrix === "A0",
            children: [
              node({
                key: `case:proj:rounds/${matrix}.yaml:${matrix}-H01`,
                label: `${matrix}-H01`,
                case_id: `${matrix}-H01`,
              }),
            ],
          }),
        ],
      }),
    ),
  });
}

function project(children: TreeNode[]): TreeNode {
  return node({
    key: "project:proj",
    kind: "project",
    label: "proj",
    expanded: true,
    children,
  });
}

const noop = () => Promise.resolve();

function renderTree(tree: TreeNode[], onSelect = vi.fn()) {
  render(
    <CollectionTree
      tree={tree}
      selected=""
      labels={LABELS}
      onSelect={onSelect}
      onCreateFolder={noop}
      onMoveCampaign={noop}
      onRemoveProject={noop}
    />,
  );
  return onSelect;
}

describe("CollectionTree", () => {
  it("renders every flow of a campaign, not only the first", () => {
    renderTree([project([campaignWithFlows()])]);

    // The campaign and all five of its flows, with no filter active.
    expect(screen.getByText("campaign-a")).toBeDefined();
    for (const matrix of ["A0", "A1", "A2", "A3", "A4"]) {
      expect(screen.getByText(matrix), `${matrix} is missing`).toBeDefined();
    }
  });

  it("renders every campaign of a project, not only the first", () => {
    const second = node({
      key: "campaign:proj:campaign-b",
      kind: "campaign",
      label: "campaign-b",
      path: "campaigns/campaign-b.yaml",
    });
    const third = node({
      key: "round:proj:rounds/values-consolidated.yaml",
      kind: "round",
      label: "values-consolidated",
      round_id: "values-consolidated",
    });

    renderTree([project([campaignWithFlows(), second, third])]);

    expect(screen.getByText("campaign-b")).toBeDefined();
    expect(screen.getByText("values-consolidated")).toBeDefined();
  });

  it("selects a case when its row in the tree is clicked", async () => {
    const user = userEvent.setup();
    const onSelect = renderTree([project([campaignWithFlows()])]);

    // The case lives inside A0, which the fixture leaves open; the click has to carry
    // the case's own key, not its round's or its flow's.
    await user.click(screen.getByText("A0-H01"));

    expect(onSelect).toHaveBeenCalledWith("case:proj:rounds/A0.yaml:A0-H01");
  });

  it("still hides what the filter excludes, once a branch is opened", async () => {
    const user = userEvent.setup();
    renderTree([project([campaignWithFlows()])]);

    // Only A0 is open by default, so A1's round is not on screen yet.
    expect(screen.queryByText("round-A1")).toBeNull();

    await user.click(screen.getByLabelText("Expandir A1"));

    expect(screen.getByText("round-A1")).toBeDefined();
    expect(screen.queryByText("round-A2")).toBeNull();
  });

  it("says the status in an icon and not in a chip on every row", () => {
    renderTree([
      project([
        node({
          key: "round:proj:rounds/A0.yaml",
          kind: "round",
          label: "round-A0",
          status: "fail",
          expanded: true,
          children: [node({ key: "case:proj:rounds/A0.yaml:A0-H01", label: "A0-H01" })],
        }),
      ]),
    ]);

    // The row no longer spells the status out: the word is what the icon announces,
    // and what the shared tooltip layer shows on hover.
    const row = screen.getByText("round-A0").closest("button");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).queryByText("Falhou")).toBeNull();

    const icon = within(row as HTMLElement).getByRole("img");
    expect(icon.getAttribute("data-tip")).toBe("Falhou");
    // The word still reaches a screen reader, which an icon-only row would otherwise
    // have taken away.
    expect(icon.getAttribute("aria-label")).toBe("Falhou");

    // And it is still a word somewhere: the filter that selects that status. Removing
    // the chip from the row is not removing the vocabulary from the screen.
    expect(screen.getByRole("button", { name: "Falhou", pressed: false })).toBeDefined();
  });

  it("puts the reason a round cannot run into the icon's tooltip", () => {
    renderTree([
      project([
        node({
          key: "round:proj:rounds/A0.yaml",
          kind: "round",
          label: "round-A0",
          status: "not_ready",
          reason: "TODO_SENTINEL: contract is TODO",
        }),
      ]),
    ]);

    // A chip that says "Não pronta" on a row whose reason is three lines long is a
    // dead end: the icon carries both, so hovering the mark answers "why not".
    const icon = screen.getByRole("img", { name: /Não pronta|not_ready/ });
    expect(icon.getAttribute("data-tip")).toContain("TODO_SENTINEL");
  });
});
