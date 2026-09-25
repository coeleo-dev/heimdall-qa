import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CampaignPane } from "@/components/CampaignPane";
import type { EngineView, Labels, SessionView, TreeNode, UnitCard } from "@/types";

/** What the campaign roll-up shows once a plan is over.
 *
 * Every field the pane reads is here rather than a cast of a partial: the bug these
 * tests cover was a finished plan that still answered "yes" to "is a plan running in
 * this item", and a fixture that omitted `finished` would have agreed with it. */
const LABELS: Labels = {
  status_label: { pass: "Passou", fail: "Falhou", not_reviewed: "Não reviewado" },
  status_pill: {},
  kind_label: { campaign: "Campanha", round: "Rodada", case: "Caso", folder: "Pasta" },
  step_kind_label: {},
  phase_label: { idle: "Ocioso", running: "Rodando", awaiting: "Aguardando veredito", done: "Concluído" },
  scope_label: { campaign: "Rodar a campanha inteira" },
  scope_running: {},
  step_note: {},
  step_scope_label: {},
};

const PROJECT = "toy-provider-abc123";
const CAMPAIGN_KEY = `campaign:${PROJECT}:toy-smoke`;
const ROUND_KEY = `round:${PROJECT}:rounds/smoke.yaml`;

const UNIT: UnitCard = {
  key: CAMPAIGN_KEY,
  kind: "campaign",
  label: "toy-smoke",
  status: "pass",
  path: "campaigns/smoke.yaml",
  project: PROJECT,
  round_id: null,
  case_id: null,
  endpoint: null,
  environment: "",
  step_kind: "",
  startable: true,
  reason: null,
  scopes: ["campaign"],
  scope_labels: { campaign: "Rodar a campanha inteira" },
  step_note: "",
  previous: null,
};

function tree(live: string): TreeNode[] {
  return [
    {
      key: CAMPAIGN_KEY,
      kind: "campaign",
      label: "toy-smoke",
      status: "pass",
      path: "campaigns/smoke.yaml",
      round_id: null,
      endpoint: null,
      matrix: null,
      case_id: null,
      reason: null,
      startable: true,
      expanded: true,
      environment: "",
      step_kind: "",
      fail_count: 0,
      project: PROJECT,
      live,
      children: [
        {
          key: ROUND_KEY,
          kind: "round",
          label: "GET /toy/health",
          status: "pass",
          path: "rounds/smoke.yaml",
          round_id: "smoke",
          endpoint: "GET /toy/health",
          matrix: null,
          case_id: null,
          reason: null,
          startable: true,
          expanded: false,
          environment: "",
          step_kind: "",
          fail_count: 0,
          project: PROJECT,
          live,
          children: [],
        },
      ],
    },
  ];
}

const SESSION: SessionView = {
  phase: "idle",
  queue: [],
  run_dir: "",
  current_step_dir: "",
  mode: "walk",
  error: null,
  round_id: "",
  environment: "",
  dimensions: [],
  progress: [0, 0],
  focus_index: 0,
  pending_index: 0,
  can_prev: false,
  can_next: false,
  awaiting_verdict: false,
};

function engine(overrides: Partial<EngineView>): EngineView {
  return {
    phase: "done",
    scope: "campaign",
    plan_label: "toy-smoke",
    unit_index: 1,
    unit_total: 1,
    unit_label: "GET /toy/health",
    unit_round: "rounds/smoke.yaml",
    unit_skips: [],
    case_total: 3,
    cases_done: 3,
    step_index: 3,
    step_total: 3,
    case_id: "",
    run_dir: "/runs/2026-09-25T1351-smoke",
    counts: { pass: 3, fail: 0, skip: 0 },
    awaiting: false,
    busy: false,
    finished: true,
    error: null,
    events: [],
    elapsed_ms: 30_000,
    revision: 15,
    ...overrides,
  };
}

function renderPane(view: EngineView, live: string) {
  const onStart = vi.fn();
  const onSelect = vi.fn();
  render(
    <CampaignPane
      unit={UNIT}
      engine={view}
      session={SESSION}
      tree={tree(live)}
      labels={LABELS}
      busy={view.busy}
      nextUnreviewed={null}
      onStart={onStart}
      onSelect={onSelect}
    />,
  );
  return { onStart, onSelect };
}

describe("CampaignPane", () => {
  it("offers the Start buttons again once the campaign's plan has finished", () => {
    // `plan_label` outlives the plan — the strip names the last one — so this is the
    // exact frame after a run: end of the walk, engine done, label still matching.
    renderPane(engine({ phase: "done", finished: true, busy: false }), "");

    expect(screen.getByRole("button", { name: /Rodar a campanha inteira/ })).toBeInTheDocument();
    expect(screen.queryByText(/O plano em andamento é deste item/)).not.toBeInTheDocument();
  });

  it("keeps the live card, and hides the Start buttons, while that plan runs", () => {
    renderPane(engine({ phase: "running", finished: false, busy: true }), "running");

    expect(screen.queryByRole("button", { name: /Rodar a campanha inteira/ })).not.toBeInTheDocument();
    expect(screen.getByText(/O plano em andamento é deste item/)).toBeInTheDocument();
  });

  it("names the plan on the live card before the wire marks a row", () => {
    // The frames between `start` and the first live mark: the label is the only thing
    // that says the run belongs to this campaign, which is why the fallback exists.
    renderPane(engine({ phase: "running", finished: false, busy: true }), "");

    expect(screen.getByText(/O plano em andamento é deste item/)).toBeInTheDocument();
  });
});
