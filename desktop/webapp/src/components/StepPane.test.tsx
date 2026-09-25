import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { StepPane } from "@/components/StepPane";
import type { Labels, Pane, SessionView, StepView, UnitCard, VerdictStatus } from "@/types";

// Monaco is a browser editor and this is jsdom. Mocked rather than polyfilled: what
// these tests are about is which evidence the pane shows and in what order, and a fake
// that prints its value as text is the clearest way to assert on that.
vi.mock("@/components/JsonEditor", () => ({
  JsonEditor: ({ value }: { value: string }) => <pre data-testid="editor">{value}</pre>,
}));

const LABELS: Labels = {
  status_label: { pass: "Passou", fail: "Falhou", skip: "Pulado" },
  status_pill: {},
  kind_label: { campaign: "Campanha", round: "Rodada", case: "Caso", folder: "Pasta" },
  step_kind_label: { loop: "loop", probe_begin: "conferir antes" },
  phase_label: { idle: "Ocioso", running: "Rodando", awaiting: "Aguardando veredito" },
  scope_label: { round: "Rodar a rodada" },
  scope_running: {},
  step_note: {},
  step_scope_label: {},
};

const SESSION: SessionView = {
  phase: "step",
  queue: [
    {
      case_id: "toy-items-H01",
      status: "fail",
      current: true,
      reachable: true,
      key: "case:proj:rounds/smoke.yaml:#step-1",
      live: "awaiting",
    },
    {
      case_id: "toy-items-post-H01",
      status: "pending",
      current: false,
      reachable: true,
      key: "case:proj:rounds/smoke.yaml:#step-2",
      live: "",
    },
  ],
  run_dir: "/runs/2026-09-24T1200-smoke",
  current_step_dir: "/runs/2026-09-24T1200-smoke/steps/002-toy-items-H01",
  mode: "walk",
  error: null,
  round_id: "smoke",
  environment: "local",
  dimensions: ["items"],
  progress: [2, 4],
  focus_index: 1,
  pending_index: 1,
  can_prev: true,
  can_next: true,
  awaiting_verdict: true,
};

const UNIT: UnitCard = {
  key: "case:proj:rounds/smoke.yaml:toy-items-H01",
  kind: "case",
  label: "toy-items-H01",
  status: "fail",
  path: null,
  project: "proj",
  round_id: "smoke",
  case_id: "toy-items-H01",
  endpoint: "GET /toy/items",
  environment: "local",
  step_kind: "",
  startable: true,
  reason: null,
  scopes: ["round"],
  scope_labels: { round: "Rodar a rodada de novo" },
  step_note: "",
  previous: null,
};

function step(overrides: Partial<StepView> = {}): StepView {
  return {
    request_doc: {},
    response_doc: {},
    timing: {},
    packs: [],
    pack_alerts: [],
    pack_ok: [],
    logs_sources: [],
    logs_timeline: [],
    probe: {},
    probe_rows: [],
    is_probe: false,
    case_label: "toy-items-H01",
    http_status: 200,
    elapsed_ms: 12,
    request_method: "GET",
    request_url: "http://127.0.0.1:8110/toy/items",
    request_headers: { accept: "application/json" },
    request_body: null,
    response_headers: { "content-type": "application/json" },
    response_body: { items: [] },
    has_http: true,
    pause_reason: "",
    awaiting_verdict: true,
    recorded_verdict: {},
    body_open: false,
    ...overrides,
  };
}

function renderPane(
  overrides: Partial<StepView> = {},
  pane: Pane = "review",
): { onVerdict: ReturnType<typeof vi.fn>; onFocus: ReturnType<typeof vi.fn> } {
  const onVerdict = vi.fn(async () => true);
  const onFocus = vi.fn();
  render(
    <StepPane
      step={step(overrides)}
      session={{ ...SESSION, awaiting_verdict: overrides.awaiting_verdict ?? true }}
      unit={UNIT}
      labels={LABELS}
      pane={pane}
      busy={false}
      onVerdict={onVerdict as unknown as (s: VerdictStatus, c: string, k: boolean) => Promise<boolean>}
      onFocus={onFocus}
      onStart={vi.fn()}
    />,
  );
  return { onVerdict, onFocus };
}

describe("StepPane — the header", () => {
  it("puts the method, the url, the status and the time on one line", () => {
    renderPane();
    expect(screen.getByText("toy-items-H01")).toBeInTheDocument();
    expect(screen.getByText(/GET http:\/\/127\.0\.0\.1:8110\/toy\/items/)).toBeInTheDocument();
    expect(screen.getByText("HTTP 200")).toBeInTheDocument();
    expect(screen.getByText("12 ms")).toBeInTheDocument();
  });

  it("marks a 5xx as a failure and a 4xx as a warning", () => {
    renderPane({ http_status: 503 });
    expect(screen.getByText("HTTP 503")).toBeInTheDocument();
  });

  it("says where in the run this step sits", () => {
    renderPane();
    expect(screen.getByText("2/4")).toBeInTheDocument();
  });

  it("labels a step read from a finished run", () => {
    renderPane({}, "historical");
    expect(screen.getByText("run anterior")).toBeInTheDocument();
  });
});

describe("StepPane — packs", () => {
  it("alerts on the packs that failed and folds the ones that passed", () => {
    renderPane({
      packs: [
        { pack_id: "http.success", status: "fail", detail: "expected 200, got 500" },
        { pack_id: "security.leak", status: "pass" },
      ],
      pack_alerts: [{ pack_id: "http.success", status: "fail", detail: "expected 200, got 500" }],
      pack_ok: [{ pack_id: "security.leak", status: "pass" }],
    });
    const alert = screen.getByText("http.success").closest("li");
    expect(alert).not.toBeNull();
    expect(within(alert as HTMLElement).getByText(/expected 200, got 500/)).toBeInTheDocument();
    // Folded, not absent: the passing pack is still counted on screen.
    expect(screen.getByText("Packs que passaram (1)")).toBeInTheDocument();
  });

  it("says so when a step declares no packs at all", () => {
    renderPane({ packs: [] });
    expect(screen.getByText("Nenhum pack neste passo.")).toBeInTheDocument();
  });
});

describe("StepPane — the probe table", () => {
  const rows = [
    {
      id: "balance",
      before: "100",
      after: "110",
      delta: "10",
      esperado: "10",
      lido: "10",
      money: true,
      matched: true,
    },
    {
      id: "ledger",
      before: "1",
      after: "2",
      delta: "1",
      esperado: "2",
      lido: "1",
      money: false,
      matched: false,
    },
  ];

  it("shows every column of the comparison", () => {
    renderPane({ probe_rows: rows, is_probe: true, has_http: true });
    const table = screen.getByRole("table");
    for (const header of ["Superfície", "Antes", "Depois", "Delta", "Esperado", "Lido"]) {
      expect(within(table).getByText(header)).toBeInTheDocument();
    }
    // Both columns carry the currency: the comparison is the row, not the formatting.
    expect(within(table).getAllByText("R$ 10")).toHaveLength(2);
  });

  it("says which rows did not match, rather than leaving the reader to compare", () => {
    renderPane({ probe_rows: rows, is_probe: true, has_http: true });
    const mismatched = screen.getByText("ledger").closest("tr");
    expect(mismatched?.className).toContain("status-fail");
  });

  it("says out loud that a probe step is not a POST", () => {
    renderPane({ probe_rows: rows, is_probe: true, has_http: false });
    expect(screen.getByText("Este passo é conferência, não um POST.")).toBeInTheDocument();
  });
});

describe("StepPane — the verdict", () => {
  it("offers the three answers the engine knows", () => {
    renderPane();
    expect(screen.getByRole("button", { name: /Aprovar/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Reprovar e seguir/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Reprovar e parar/ })).toBeInTheDocument();
  });

  it("sends the comment with a reprove", async () => {
    const { onVerdict } = renderPane();
    await userEvent.type(screen.getByRole("textbox"), "the item never appeared");
    await userEvent.click(screen.getByRole("button", { name: /Reprovar e seguir/ }));
    expect(onVerdict).toHaveBeenCalledWith("fail", "the item never appeared", true);
  });

  it("refuses a reprove with no comment, before it reaches the server", async () => {
    // The engine would refuse it too, but a round trip to be told what is already on
    // screen is the slowest possible way to say "this field is required".
    const { onVerdict } = renderPane();
    await userEvent.click(screen.getByRole("button", { name: /Reprovar e seguir/ }));
    expect(onVerdict).not.toHaveBeenCalled();
    expect(await screen.findByText("Reprovar exige comentário.")).toBeInTheDocument();
  });

  it("lets a comment arrive after the refusal, and then accepts it", async () => {
    const { onVerdict } = renderPane();
    await userEvent.click(screen.getByRole("button", { name: /Reprovar e parar/ }));
    expect(onVerdict).not.toHaveBeenCalled();
    await userEvent.type(screen.getByRole("textbox"), "duplicate item returned");
    expect(screen.queryByText("Reprovar exige comentário.")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Reprovar e parar/ }));
    expect(onVerdict).toHaveBeenCalledWith("fail_stop", "duplicate item returned", true);
  });

  it("does not require a comment to approve", async () => {
    const { onVerdict } = renderPane();
    await userEvent.click(screen.getByRole("button", { name: /Aprovar/ }));
    expect(onVerdict).toHaveBeenCalledWith("pass", "", true);
  });

  it("shows the recorded verdict instead of the form once a step is decided", () => {
    renderPane({
      awaiting_verdict: false,
      recorded_verdict: { status: "fail", comment: "wrong total" },
    });
    expect(screen.getByText(/wrong total/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Aprovar/ })).not.toBeInTheDocument();
  });
});

describe("StepPane — the bodies", () => {
  it("keeps the bodies shut on a step that passed", () => {
    renderPane({ body_open: false });
    expect(screen.getByText("Body da response")).toBeInTheDocument();
    expect(screen.queryByTestId("editor")).not.toBeInTheDocument();
  });

  it("opens the gateway to a body that is already open", () => {
    renderPane({ body_open: true });
    expect(screen.getAllByTestId("editor").length).toBeGreaterThan(0);
  });

  it("says the recorded bodies are redacted, so nobody hunts for a real password", () => {
    renderPane({ body_open: true });
    expect(screen.getByText(/\[REDACTED\]/)).toBeInTheDocument();
  });
});

describe("StepPane — logs", () => {
  it("explains an empty log as a declaration, not as a failure", () => {
    renderPane({ logs_sources: [] });
    expect(
      screen.getByText("Nenhuma fonte de log declarada — a evidência é só o HTTP."),
    ).toBeInTheDocument();
  });

  it("distinguishes a trace that was not asked for from one that never arrived", () => {
    renderPane({
      body_open: true,
      logs_sources: [
        { id: "worker", text: "", propagate: false, role: "processa o evento", reason: "async" },
        { id: "web", text: "", propagate: true, role: "recebe", reason: "always" },
      ],
      logs_timeline: [],
    });
    expect(screen.getByText("o projeto declarou que o trace não chega aqui")).toBeInTheDocument();
    expect(screen.getByText("nenhuma linha para este trace")).toBeInTheDocument();
  });
});
