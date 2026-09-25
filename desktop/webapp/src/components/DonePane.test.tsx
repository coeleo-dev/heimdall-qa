import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DonePane } from "@/components/DonePane";
import type {
  EngineView,
  Labels,
  RunAggregate,
  SessionView,
  UnitCard,
} from "@/types";

// Monaco needs a real DOM with layout; the `summary.json` disclosure is not what this
// test is about, so it is stubbed the same way the other pane tests stub it.
vi.mock("@/components/JsonEditor", () => ({
  JsonEditor: ({ value }: { value: string }) => <pre data-testid="editor">{value}</pre>,
}));

/**
 * The end-of-run pane, read from the run rather than from the engine.
 *
 * Two bugs are what these cover, and both come from the same mistake — asking the
 * engine what a run decided. A campaign's last round drew the *campaign's* totals under
 * the round's own name, and a round opened after the engine had moved on had no engine
 * counters at all to draw. The fixture makes the two disagree on purpose: `engine` says
 * ten cases passed, `run.summary` says three.
 */
const LABELS: Labels = {
  status_label: { pass: "Passou", fail: "Falhou", skip: "Pulado" },
  status_pill: {},
  kind_label: { round: "Endpoint", case: "Caso" },
  step_kind_label: {},
  phase_label: {},
  scope_label: { round: "Rodar este endpoint" },
  scope_running: {},
  step_note: {},
  step_scope_label: {},
};

const ROUND_KEY = "round:proj:rounds/items.yaml";

const UNIT: UnitCard = {
  key: ROUND_KEY,
  kind: "round",
  label: "POST /toy/items",
  status: "fail",
  path: "rounds/items.yaml",
  project: "proj",
  round_id: "items",
  case_id: null,
  endpoint: "POST /toy/items",
  environment: "sandbox",
  step_kind: "",
  startable: true,
  reason: null,
  scopes: ["round"],
  scope_labels: { round: "Rodar este endpoint" },
  step_note: "",
  previous: null,
};

const SESSION: SessionView = {
  phase: "done",
  queue: [],
  run_dir: "",
  current_step_dir: "",
  // Empty on purpose: a round opened from history has the *stored* session, and the
  // pane must not fall back to this for its mode or its name.
  mode: "",
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

const ENGINE: EngineView = {
  phase: "done",
  scope: "campaign",
  plan_label: "toy-smoke",
  unit_index: 2,
  unit_total: 2,
  unit_label: "POST /toy/items",
  unit_round: "rounds/items.yaml",
  unit_skips: [],
  case_total: 10,
  cases_done: 10,
  step_index: 0,
  step_total: 0,
  case_id: "",
  run_dir: "",
  counts: { pass: 10, fail: 0, skip: 0 },
  awaiting: false,
  busy: false,
  finished: true,
  error: null,
  events: [],
  elapsed_ms: 1_000,
  revision: 3,
};

const RUN: RunAggregate = {
  summary: {
    round_id: "items",
    mode: "walk",
    counts: { pass: 3, fail: 2, skip: 1, http_5xx: 0, instrument: 0 },
    packs: { fail: 4, warn: 1 },
    coverage_pct: 50,
    latency_ms: { p50: 20, p95: 60 },
    logs_incomplete: 1,
    human_reject_rate: 0.25,
    review_duration_ms: 12_000,
  },
  failed_cases: [
    { case_id: "items-H01", status: "fail", key: "case:proj:rounds/items.yaml:items-H01", reason: "http.baseline" },
    { case_id: "items-H02", status: "fail", key: "", step_dir: "/runs/x/steps/002-items-H02" },
  ],
  run_path: "/runs/2026-09-25T1440-items",
  unit_key: ROUND_KEY,
  unit_label: "POST /toy/items",
  unit_kind: "round",
};

function renderPane(overrides: Partial<Parameters<typeof DonePane>[0]> = {}) {
  const onStart = vi.fn();
  const onSelect = vi.fn();
  render(
    <DonePane
      run={RUN}
      engine={ENGINE}
      session={SESSION}
      unit={UNIT}
      labels={LABELS}
      busy={false}
      onSelect={onSelect}
      onStart={onStart}
      {...overrides}
    />,
  );
  return { onStart, onSelect };
}

describe("DonePane", () => {
  it("labels the run by the unit and not by what the engine remembers", () => {
    renderPane();

    expect(screen.getByText("POST /toy/items")).toBeInTheDocument();
    expect(screen.getByText("Endpoint")).toBeInTheDocument();
  });

  it("reads the KPIs from the run's summary, not from the engine's totals", () => {
    renderPane();

    // The engine says 10/0/0; the summary says 3/2/1. The summary is the run on screen.
    const strip = within(screen.getByTestId("kpi-strip"));
    expect(strip.getByText("Passou").nextElementSibling?.textContent).toBe("3");
    expect(strip.getByText("Falhou").nextElementSibling?.textContent).toBe("2");
    expect(strip.getByText("Pulado").nextElementSibling?.textContent).toBe("1");
    expect(strip.getByText("Packs fail").nextElementSibling?.textContent).toBe("4");
    expect(strip.getByText("50%")).toBeInTheDocument();
    expect(strip.getByText("20 ms")).toBeInTheDocument();
  });

  it("falls back to the engine's counters only when the run wrote none", () => {
    renderPane({ run: { ...RUN, summary: {} } });

    const strip = within(screen.getByTestId("kpi-strip"));
    expect(strip.getByText("Passou").nextElementSibling?.textContent).toBe("10");
  });

  it("offers the re-run buttons the unit's own scopes earn", () => {
    const { onStart } = renderPane();

    screen.getByRole("button", { name: /Rodar este endpoint/ }).click();

    // The mode is the run's own, so "run it again" repeats what was read.
    expect(onStart).toHaveBeenCalledWith("round", "walk");
  });

  it("links a failing case to its row, and leaves an unresolvable one inert", () => {
    const { onSelect } = renderPane();

    screen.getByText("items-H01").closest("button")?.click();
    expect(onSelect).toHaveBeenCalledWith("case:proj:rounds/items.yaml:items-H01");

    // A step the round no longer lists has no row to open; the directory it carries is
    // its tooltip, and the row is not a button that would open the wrong case.
    const orphan = screen.getByText("items-H02").closest("button");
    expect(orphan).toBeDisabled();
    expect(orphan).toHaveAttribute("title", "/runs/x/steps/002-items-H02");
  });
});
