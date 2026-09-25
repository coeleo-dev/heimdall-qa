import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useHarness } from "@/hooks/useHarness";
import { selectNode } from "@/lib/api";
import type { Bootstrap, EngineView, StreamState } from "@/types";

// The hook owns two edges: the API it re-reads through and the stream it listens on.
// Both are mocked, because what is under test is *when* a bootstrap is re-read — the
// real `fetch` and the real `EventSource`-over-fetch would only add sockets.
const fetchBootstrap = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiRefusal: class ApiRefusal extends Error {},
  addProject: vi.fn(),
  cancelPlan: vi.fn(),
  createFolder: vi.fn(),
  fetchBootstrap: () => fetchBootstrap(),
  focusStep: vi.fn(),
  moveCampaign: vi.fn(),
  removeProject: vi.fn(),
  selectNode: vi.fn(),
  startPlan: vi.fn(),
  submitVerdict: vi.fn(),
}));

let emit: ((state: StreamState) => void) | null = null;
let resync: (() => void) | null = null;
let opens = 0;
vi.mock("@/lib/sse", () => ({
  openStream: (handlers: {
    onState: (state: StreamState) => void;
    onResync?: () => void;
    onLost?: (error: unknown) => void;
  }) => {
    opens += 1;
    emit = handlers.onState;
    resync = handlers.onResync ?? null;
    return () => {
      emit = null;
      resync = null;
    };
  },
}));

function engine(overrides: Partial<EngineView> = {}): EngineView {
  return {
    phase: "running",
    scope: "campaign",
    plan_label: "walk-hn",
    unit_index: 1,
    unit_total: 20,
    unit_label: "POST /auth/register",
    unit_round: "rounds/auth-register.yaml",
    unit_skips: [],
    case_total: 3,
    cases_done: 1,
    step_index: 1,
    step_total: 3,
    case_id: "auth-register-H01",
    run_dir: "/runs/one",
    counts: { pass: 1, fail: 0, skip: 0 },
    awaiting: false,
    busy: true,
    finished: false,
    error: null,
    events: [],
    elapsed_ms: 1_200,
    revision: 1,
    ...overrides,
  };
}

function bootstrap(state: EngineView): Bootstrap {
  return {
    tree: [],
    selected: `case:proj:rounds/auth-register.yaml:${state.case_id}`,
    pane: "review",
    engine: state,
    session: {
      phase: "step",
      queue: [],
      run_dir: state.run_dir,
      current_step_dir: "",
      mode: "walk",
      error: null,
      round_id: "auth-register",
      environment: "sandbox",
      dimensions: [],
      progress: [state.step_index, state.step_total],
      focus_index: 0,
      pending_index: 0,
      can_prev: false,
      can_next: false,
      awaiting_verdict: false,
    },
    unit: {
      key: "case:proj:rounds/auth-register.yaml:auth-register-H01",
      kind: "case",
      label: "auth-register-H01",
      status: "pass",
      path: null,
      round_id: "auth-register",
      case_id: "auth-register-H01",
      endpoint: "POST /auth/register",
      environment: "sandbox",
      step_kind: "",
      startable: true,
      reason: null,
      scopes: [],
      scope_labels: {},
      step_note: "",
      previous: null,
      project: "proj",
    },
    step: null,
    run: null,
    labels: {
      status_label: {},
      status_pill: {},
      kind_label: {},
      step_kind_label: {},
      phase_label: {},
      scope_label: {},
      scope_running: {},
      step_note: {},
      step_scope_label: {},
    },
    next_unreviewed: null,
    pending_key: "",
    error: null,
  };
}

/** A `fetchBootstrap` the test resolves by hand, so "in flight" is a real moment. */
function deferredBootstrap() {
  const pending: Array<(value: Bootstrap) => void> = [];
  fetchBootstrap.mockImplementation(
    () => new Promise<Bootstrap>((resolve) => pending.push(resolve)),
  );
  return {
    calls: () => fetchBootstrap.mock.calls.length,
    settle: async (index: number, state: EngineView) => {
      await act(async () => {
        pending[index](bootstrap(state));
        await Promise.resolve();
      });
    },
  };
}

describe("useHarness — the stream", () => {
  beforeEach(() => {
    fetchBootstrap.mockReset();
    emit = null;
    resync = null;
    opens = 0;
  });

  it("serves the last frame of a burst, not the first", async () => {
    const deferred = deferredBootstrap();

    const { result } = renderHook(() => useHarness());
    await waitFor(() => expect(emit).not.toBeNull());

    // The mount refresh.
    await deferred.settle(0, engine({ revision: 0, step_index: 0 }));

    // A frame that moves the step: the pane's evidence is on disk, so it re-reads.
    await act(async () => emit!(stream(engine({ revision: 1, step_index: 1 }))));
    expect(deferred.calls()).toBe(2);

    // A second frame arrives while that read is still in flight. It must not be
    // dropped: the next frame only ever asks about a *later* revision, so dropping
    // this one would leave the pane on the first frame of the burst for good.
    await act(async () => emit!(stream(engine({ revision: 2, step_index: 2 }))));
    expect(deferred.calls()).toBe(2);

    await deferred.settle(1, engine({ revision: 1, step_index: 1 }));

    // The coalesced request runs once the first one lands, and the pane catches up.
    await waitFor(() => expect(deferred.calls()).toBe(3));
    await deferred.settle(2, engine({ revision: 2, step_index: 2 }));

    await waitFor(() => expect(result.current.bootstrap?.engine.step_index).toBe(2));
  });

  it("re-reads the screen on every frame the stream sends", async () => {
    const deferred = deferredBootstrap();

    renderHook(() => useHarness());
    await waitFor(() => expect(emit).not.toBeNull());
    await deferred.settle(0, engine({ revision: 0, step_index: 1, cases_done: 1 }));

    // The first frame is compared against nothing, so it re-reads — the client cannot
    // know it is already looking at that state.
    await act(async () => emit!(stream(engine({ revision: 1, step_index: 1, cases_done: 1 }))));
    expect(deferred.calls()).toBe(2);
    await deferred.settle(1, engine({ revision: 1, step_index: 1, cases_done: 1 }));

    // The counters standing still is *not* the test. A frame is the server saying its
    // revision moved, and what landed may be anywhere but in `engine`: the tree, the
    // session queue, the step pane. Re-reading is the only way to find out, so a frame
    // whose engine reads the same still costs a read.
    await act(async () =>
      emit!(stream(engine({ revision: 2, step_index: 1, cases_done: 1, elapsed_ms: 30_000 }))),
    );
    expect(deferred.calls()).toBe(3);

    // The one frame that is not news is a revision already applied: the stream
    // coalesces, so the same one can arrive twice.
    await act(async () =>
      emit!(stream(engine({ revision: 2, step_index: 1, cases_done: 1 }))),
    );
    expect(deferred.calls()).toBe(3);
  });

  it("reopens the stream when the socket dropped", async () => {
    const deferred = deferredBootstrap();

    const { result } = renderHook(() => useHarness());
    await waitFor(() => expect(emit).not.toBeNull());
    await deferred.settle(0, engine({ revision: 0 }));
    expect(opens).toBe(1);

    // What the chip's "Reconectar" does. A re-read alone is not enough: it makes the
    // screen current once, and leaves the connection that keeps it current dead — so
    // the next change is invisible exactly as before.
    await act(async () => result.current.reconnect());
    await waitFor(() => expect(opens).toBe(2));
    expect(deferred.calls()).toBe(2);
    await deferred.settle(1, engine({ revision: 7 }));
  });

  it("re-reads when a reconnect finds the stream up again", async () => {
    const deferred = deferredBootstrap();

    renderHook(() => useHarness());
    await waitFor(() => expect(emit).not.toBeNull());
    await deferred.settle(0, engine({ revision: 0 }));

    // Frames emitted while the socket was down are gone, and the harness can sit idle
    // afterwards — so the resync has to re-read on its own rather than wait for a
    // frame that may never come.
    await act(async () => resync?.());
    expect(deferred.calls()).toBe(2);
    await deferred.settle(1, engine({ revision: 4 }));
  });
});

function stream(state: EngineView): StreamState {
  return { engine: state, pane: "review", selected: `case:proj:rounds/auth-register.yaml:${state.case_id}` };
}

/** The same screen, with the tree on a different row. */
function onSelection(state: EngineView, selected: string): Bootstrap {
  return { ...bootstrap(state), selected };
}

describe("useHarness — a click is not overtaken by a frame's read", () => {
  beforeEach(() => {
    fetchBootstrap.mockReset();
    vi.mocked(selectNode).mockReset();
    emit = null;
    resync = null;
    opens = 0;
  });

  /** `selectNode` answered by hand, so "the click's answer has not landed" is a moment. */
  function deferredSelect() {
    let answer: ((value: Bootstrap) => void) | null = null;
    vi.mocked(selectNode).mockImplementation(
      () =>
        new Promise<Bootstrap>((resolve) => {
          answer = resolve;
        }),
    );
    return { answer: (value: Bootstrap) => answer?.(value) };
  }

  const CLICKED = "case:proj:rounds/auth-register.yaml:auth-register-N-01";
  /** The selection the screen had before the click: `bootstrap` takes it off the case. */
  const BEFORE = "auth-register-H01";

  it("keeps the row a click opened when an older read lands after the answer", async () => {
    const reads = deferredBootstrap();
    const select = deferredSelect();

    const { result } = renderHook(() => useHarness());
    await waitFor(() => expect(emit).not.toBeNull());
    await reads.settle(0, engine({ revision: 0 }));

    // A frame starts a read of a screen that still has the old case selected.
    await act(async () => emit!(stream(engine({ revision: 1 }))));
    expect(reads.calls()).toBe(2);

    // The reviewer clicks a case, and the click's own answer comes back first.
    let selection: Promise<void> = Promise.resolve();
    await act(async () => {
      selection = result.current.select(CLICKED);
    });
    await act(async () => {
      select.answer(onSelection(engine({ revision: 1 }), CLICKED));
      await selection;
    });
    expect(result.current.bootstrap?.selected).toBe(CLICKED);

    // Now the read that was already out lands, still carrying the old selection.
    await reads.settle(1, engine({ revision: 1, case_id: BEFORE }));

    // It must not put the reviewer back on the row they had before clicking: the click
    // was an instruction, the read was an observation, and the observation is stale.
    expect(result.current.bootstrap?.selected).toBe(CLICKED);
  });

  it("still applies a read that was answered after the click, because it is fresher", async () => {
    const reads = deferredBootstrap();
    const select = deferredSelect();

    const { result } = renderHook(() => useHarness());
    await waitFor(() => expect(emit).not.toBeNull());
    await reads.settle(0, engine({ revision: 0 }));

    let selection: Promise<void> = Promise.resolve();
    await act(async () => {
      selection = result.current.select(CLICKED);
    });
    await act(async () => {
      select.answer(onSelection(engine({ revision: 1 }), CLICKED));
      await selection;
    });

    // A frame that arrives *after* the write reads the screen the write made, and
    // dropping it would freeze the run's own progress behind the click.
    await act(async () => emit!(stream(engine({ revision: 2, case_id: "auth-register-N-01" }))));
    await reads.settle(1, engine({ revision: 2, case_id: "auth-register-N-01" }));

    expect(result.current.bootstrap?.selected).toBe(CLICKED);
    expect(result.current.bootstrap?.engine.revision).toBe(2);
  });
});
